"""Constant Contact OAuth tokens:  encryption and the oauth_tokens table.

Tokens are encrypted here before they reach the database.  The key is 
HAIL_TOKEN_KEY from .env, database never sees plaintext.  If the key
is lost the grant is lost and authorization is re-run by hand.

Rules this module keeps:
* A token never reaches a log line or an exception message.  Messages
    here are fixed text, 'from None' drops the underlying exception, and
    GRANT hides its token fields from repr().  A traceback in a log is the 
    likeliest way that a live credential would leak.
* save_token() doesn't commit.  Refresh flow takes an advisory lock, calls
    the provider, inserts the new row and commits once, BEFORE using the 
    new token.  Rotating refresh tokens invalidate the old one when the
    new one is issued, so if a new token isn't committed the grant is lost.
* Newest row by token_id is the current grant, not created_at:  two refreshes
    in one transaction share a timestamp.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

PROVIDER = "constant_contact"
KEY_VERSION = 1

class TokenError(Exception):
    """Base for everything this module raises, messages never hold a token."""

class TokenCryptoError(TokenError):
    """Missing or invalid key, or a value that does not decrypt."""


@dataclass(frozen=True)
class Grant:
    token_id: int
    account_id: str
    scope: str
    access_expires_at: datetime
    created_at: datetime
    # repr=False so printing a Grant or logging it cannot show a credential
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _fernet():
    key = os.environ.get("HAIL_TOKEN_KEY")
    if not key:
        raise TokenCryptoError("HAIL_TOKEN_KEY is not set in the environment")
    try:
        return Fernet(key.encode())
    except ValueError:
        raise TokenCryptoError("HAIL_TOKEN_KEY is not a valid Fernet key") from None


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode())


def decrypt(ciphertext, key_version):
    if key_version != KEY_VERSION:
        raise TokenCryptoError(
            f"row sealed with key version {key_version}; this code holds "
            f"version {KEY_VERSION}")
    try:
        return _fernet().decrypt(bytes(ciphertext)).decode()
    except InvalidToken:
        raise TokenCryptoError(
            "stored token does not decrypt with HAIL_TOKEN_KEY "
            "(wrong key, or the row is damaged)") from None


def latest_token(conn, account_id=None):
    """Current grant, or None if authorization has not been done yet.
    
    Ordered by token_id, not created_at.  'account_id' narrows it once
    more than one account exists.
    """
    sql = ("SELECT token_id, account_id, access_token_enc, refresh_token_enc, "
           "        key_version, scope, access_expires_at, created_at "
           "FROM oauth_tokens WHERE provider = %s")
    params = [PROVIDER]
    if account_id is not None:
        sql += " AND account_id = %s"
        params.append(account_id)
    sql += " ORDER BY token_id DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return Grant(
        token_id=row["token_id"],
        account_id=row["account_id"],
        scope=row["scope"],
        access_expires_at=row["access_expires_at"],
        created_at=row["created_at"],
        access_token=decrypt(row["access_token_enc"], row["key_version"]),
        refresh_token=decrypt(row["refresh_token_enc"], row["key_version"]),
    )


def save_token(conn, *, account_id, access_token, refresh_token, scope,
               access_expires_at):
    """Insert a new grant row and return its token_id.  Doesn't commit.
    Caller commits before using the token (see module docstring).
    """
    if access_expires_at.tzinfo is None:
        #TIMESTAMPTZ stored in UTC.  A naive value would be read in the
        # session's zone and the refresh decision would be off.
        raise ValueError("access_expires_at must be timezone-aware")
    row = conn.execute(
        "INSERT INTO oauth_tokens (provider, account_id, access_token_enc,"
        "                           refresh_token_enc, key_version, scope,"
        "                           access_expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING token_id",
        (PROVIDER, account_id, encrypt(access_token), encrypt(refresh_token),
         KEY_VERSION, scope, access_expires_at),
    ).fetchone()
    return row["token_id"]


def purge_old(conn, account_id):
    """Delete this account's rows older than 30 days that have a newer row.
    
    Returns how many were deleted.  The 30 days lives in the database guard
    (sql/034).  The 'token_id <' clause keeps the current grant
    out of the statement even when it is older than 30 days.
    """
    cur = conn.execute(
        "DELETE FROM oauth_tokens "
        "WHERE provider = %s AND account_id = %s "
        "  AND created_at < now() - interval '30 days' "
        "  AND token_id < (SELECT max(token_id) FROM oauth_tokens"
        "                   WHERE provider = %s AND account_id = %s)",
        (PROVIDER, account_id, PROVIDER, account_id),
    )
    return cur.rowcount
