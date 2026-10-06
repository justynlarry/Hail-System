"""Constant Contact OAuth2: the HTTP calls, and refresh discipline.

Routes are in hailsys/web/cc.py, encryption and table are in tokens.py.

Rules:
* No response body, token, code, or secret is logged or put in an 
  exception.  All messages here are fixed text, error codes are the only
  things taken from here.
* Rotating refresh token dies when its successor is issued.  So 
  get_access_token() takes a database lock, re-reads, refreshes, writes the
  new row and commits before it returns the token for use.  If that write fails
  after the provider has answered, the grant is lost and the authorization must
  be redone by hand, and is logged at CRITICAL.
* Nothing here runs on page load or from a timer, a refresh happens when a 
  user asks for one, or when a send needs a token.
"""

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from hailsys.constantcontact import tokens

logger = logging.getLogger(__name__)

AUTHZ_URL = "https://authz.constantcontact.com/oauth2/default/v1/authorize"
TOKEN_URL = "https://authz.constantcontact.com/oauth2/default/v1/token"
ACCOUNT_URL = "https://api.cc.email/v3/account/summary"

# account_read: needed once to learn account id.  offline_access: without it
# no refresh token is issued at all.

SCOPES = "account_read contact_data campaign_data offline_access"
TIMEOUT = 30
# Cloudflare, in front of Constant Contact's auth server, answers 403 to
# urllib's default "Puthon-urllib/x.y" agent before OAuth sees the request.
USER_AGENT = "hail-system/1.0 (Roof Brokers Inc. storm outreach)"

# Refresh when less than this is left, so a token cannot lapse mid-request.
REFRESH_MARGIN = timedelta(minutes=5)

LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('oauth_tokens:constant_contact'))"

class OAuthError(Exception):
    """message is fixed text, safe to show admin, never a response body."""

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Issued:
    expires_at: datetime
    scope: str
    #repr=False: printing or logging this cannot show a credential.
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _config():
    try:
        return (os.environ["CC_API_KEY"], os.environ["CC_API_SECRET"],
                os.environ["CC_REDIRECT_URI"])
    except KeyError as exc:
        raise OAuthError(f"{exc.args[0]} is not set in the environment") from None


def _error_code(exc):
    """The short 'error' field of an error response, or 'unknown'."""
    try:
        body = json.loads(exc.read())
        code = body.get("error") or body.get("errorCode")
    except (ValueError, AttributeError, OSError):
        return "unknown"
    if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code):
        return code
    return "unknown"


def _send(req, event):
    """Run one request and return the parsed JSON."""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        code = _error_code(exc)
        logger.error("event=%s status=%s, code=%s", event, exc.code, code)
        raise OAuthError(
            f"Constant Contact refused the request ({exc.code}, {code}).",
            status=exc.code, code=code) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.error("event=%s_unreachable kind=%s", event, type(exc).__name__)
        raise OAuthError(
            "Couldn't get a usable answer from Constant Contact.") from None


def _post_token(fields):
    key, secret, _ = _config()
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept":"application/json", 
                 "Content-Type": "application/x-www-form-urlencoded",
                 "Authorization": f"Basic {basic}", 
                 "User-Agent": USER_AGENT},
        method="POST")
    return _issued(_send(req, "cc_token_error"))


def _issued(body):
    try:
        access, refresh = body["access_token"], body["refresh_token"]
        if not (isinstance(access, str) and access
                and isinstance(refresh, str) and refresh):
            raise ValueError
        expires_in = int(body["expires_in"])
        return Issued(
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            scope=str(body.get("scope") or SCOPES),
            access_token=access, refresh_token=refresh)
    except (KeyError, TypeError, ValueError):
        logger.error("event=cc_token_shape")
        raise OAuthError("Constant Contact's answer was missing a token.") from None

def authorize_url(state):
    key, _, redirect_uri = _config()
    query = urllib.parse.urlencode(
        {"client_id": key, "redirect_uri": redirect_uri,
         "response_type": "code", "state": state, "scope": SCOPES},
         quote_via=urllib.parse.quote)
    return f"{AUTHZ_URL}?{query}"


def exchange_code(code):
    _, _, redirect_uri = _config()
    return _post_token({"grant_type": "authorization_code", "code": code,
                        "redirect_uri": redirect_uri})


def refresh_with(refresh_token):
    return _post_token({"grant_type": "refresh_token",
                        "refresh_token": refresh_token})


def fetch_account_id(access_token):
    req = urllib.request.Request(
        ACCOUNT_URL,
        headers={"Accept": "application/json",
                 "Authorization": f"Bearer {access_token}", "User-Agent": USER_AGENT,})
    body = _send(req, "cc_account_error")
    account_id = body.get("encoded_account_id") if isinstance(body, dict) else None 
    if not isinstance(account_id, str) or not account_id:
        logger.error("event=cc_account_shape")
        raise OAuthError("Constant Contact did not return an account id.")
    return account_id


def complete_authorization(conn, code):
    """First connection: code -> tokens -> account id -> a new row, committed.

    Returns the account id.  A failure after the exchange loses nothing that
    existed before, but the code is spent, so the admin starts again.
    """
    issued = exchange_code(code)
    account_id = fetch_account_id(issued.access_token)
    tokens.save_token(conn, account_id=account_id,
                      access_token=issued.access_token,
                      refresh_token=issued.refresh_token,
                      scope=issued.scope, access_expires_at=issued.expires_at)
    conn.commit()
    return account_id


def _stale(grant, now):
    return grant.access_expires_at - now < REFRESH_MARGIN


def get_access_token(conn, *, force=False, now=None):
    """A usable access token, refreshing first if it is close to expiring.

    `force` refreshes regardless (an admin asked).  Returns the token string;
    the caller must not log it.
    """
    now = now or datetime.now(timezone.utc)
    grant = tokens.latest_token(conn)
    if grant is None:
        raise OAuthError("Constant Contact is not connected yet.")
    if not force and not _stale(grant, now):
        return grant.access_token

    # One refresher at a time.  The lock lasts to the end of this transaction,
    # which ends at the commit below, after the new row is stored.
    conn.execute(LOCK_SQL)
    try:
        # Re-Read:  another request may have refreshed while waiting.
        grant = tokens.latest_token(conn)
        if not force and not _stale(grant, now):
            conn.commit()
            return grant.access_token
        issued = refresh_with(grant.refresh_token)
    except Exception:
        conn.rollback()
        raise

    try:
        tokens.save_token(conn, account_id=grant.account_id,
                          access_token=issued.access_token,
                          refresh_token=issued.refresh_token,
                          scope=issued.scope,
                          access_expires_at=issued.expires_at)
        conn.commit()       # stored prior to use
    except Exception as exc:
        conn.rollback()
        logger.critical("event=cc_refresh_not_persisted kind=%s -- the old "
                        "refresh token is dead.  Re-authorize by hand",
                        type(exc).__name__)
        raise OAuthError("Constant Contact answered but the new token could "
                         "not be saved.  Reconnect Constant Contact.") from None

    _purge(conn, grant.account_id)
    return issued.access_token


def _purge(conn, account_id):
    """Housekeeping after the refresh is safe."""
    try:
        tokens.purge_old(conn, account_id)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.warning("event=cc_purge_failed kind=%s", type(exc).__name__)


