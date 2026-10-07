"""Constant Contact API client:  request function, throttled, retired, quiet.

The token comes from oauth.get_access_token on a short connection of its own,
so no databse connection is held open across an HTTP call.

Rules:
* No response body, token, id from a response reaches logs or an exception.
  Messages are fixed text.
* POST is not retired after an ambiguous failure, so no duplicates are sent.
  Throttled 429 is retried for every method, it has been refused in testing
  before anything happened.  GET, PUT, and DELETE are retried on 5xx and 
  network errors.
* Paging links are followed only when they stay under /contacts, token 
  travels with every request, so a link anywhere else is refused.
* Throttle is per process.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from hailsys.constantcontact import oauth
from hailsys.db import get_connection

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cc.email/v3"
TIMEOUT = 30
MIN_INTERVAL = 0.3      # Limit is 4 request/second
MAX_RETRIES = 3
RETRY_BASE = 1.0
MAX_PAGES = 1000

IDEMPOTENT = frozenset({"GET", "PUT", "DELETE"})
RETRY_STATUSES = frozenset({500, 502, 503, 504})

_last_request = 0.0

class ApiError(Exception):
    """Message is fixed text, safe to show admin."""
    def __init__(self, message, *, status=None, key=None):
        super().__init__(message)
        self.status = status
        self.key = key


def _throttle():
    global _last_request
    wait = MIN_INTERVAL = (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

def _token():
    with get_connection() as conn:
        return oauth.get_access_token(conn)


def _error_key(exc):
    """Short 'error_key' of error answer or 'unknown'"""
    try:
        body = json.loads(exc.read())
        if isinstance(body, list) and body:
            body = body[0]
        key = body.get("error_key")
    except (ValueError, AttributeError, OSError):
        return "unknown"
    if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", key):
        return key
    return "unknown"

def request(method, path, *, body=None, query=None,
            expect=(200,201,202,204)):
    """One request, returns (status, parsed JSON or None).
    
    A status in 'expect' is returned, anything else raises ApiError.
    """
    url = BASE_URL + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    safe_path  = path.split("?")[0]
    attempt = 0
    while True:
        attempt += 1
        _throttle()
        headers = {"Accept": "application/json",
                   "Authorization": f"Bearer {_token()}",
                   "User-Agent": oauth.USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, key = exc.code, _error_key(exc)
            if status in expect:
                return status, None
            retry = ((status == 429 and key =="throttled")
                    or (status in RETRY_STATUSES and method in IDEMPOTENT))
            if retry and attempt <= MAX_RETRIES:
                time.sleep(RETRY_BASE * 2 ** (attempt - 1))
                continue
            logger.error("event=cc_api_error method=%s path=%s status=%s key=%s",
                        method, safe_path, status, key)
            raise ApiError(
                f"Constant Contact refused the request ({status}, {key}).",
                status=status, key=key) from None
        except (urllib.error.URLError, OSError) as exc:
            if method in IDEMPOTENT and attempt <=MAX_RETRIES:
                time.sleep(RETRY_BASE * 2 ** (attempt -1))
                continue
            logger.error("event=cc_api_unreachable method=%s path=%s kind=%s",
                         method, safe_path, type(exc).__name__)
            raise ApiError("Couldn't reach Constant Contact.") from None

        if status not in expect:
            logger.error("event=cc_api_unexpected method=%s path=%s status=%s",
            method, safe_path, status)

        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except ValueError:
            logger.error("event=cc_api_unreadable method=%s path=%s",
                         method, safe_path)
            raise ApiError("Constant Contact's answer could not be read.",
                           status=status) from None


def wait_for_activity(activity_id, *, timeout=120.0, interval=2.0,
                      sleep=time.sleep):
    """Poll a bulk activity until 'completed'.  
    
    Bulk adds list deletes are activities.  Instant for on contact in the
    spike, "30 seconds to 15+ minutes" at volume.
    """
    deadline = time.monotonic() + timeout
    while True:
        _, doc = request("GET", f"/activities/{activity_id}")
        doc = doc or {}
        if doc.get("activity_errors"):
            logger.error("event=cc_activity_errors activity=%s", activity_id)
            raise ApiError("A Constant Contact background job reported errors.")
        if doc.get("state") == "completed":
            return doc
        if time.monotonic() >= deadline:
            raise ApiError(
                "A Constant Contact background job did not finish in time.")
        sleep(interval)

def _next_path(href):
    """A paging link as a path under BASE_URL, or refuse it."""
    path = href[3:] if href.startswith("/v3/") else href
    if not path.startswith("/contacts"):
        raise ApiError("Constant Contact returned an unexpected paging link.")
    return path

def iter_unsubscribed(updated_after=None, limit=500):
    """Every contact whose permission is 'unsubscribed', page by page.
    
    'updated_after' filters on the contact's updated_at, not the opt-out date,
    so callers pass an overlapping watermark.
    """
    query = {"status": "unsubscribed", "limit": limit}
    if updated_after:
        query["updated_after"] = updated_after
    path = "/contacts"
    pages = 0
    while path:
        pages += 1
        if pages > MAX_PAGES:
            raise ApiError("Constant Contact paging did not end.")
        _, doc = request("GET", path, query=query)
        doc = doc or {}
        for contact in doc.get("contacts", []):
            yield contact
        href = ((doc.get("_links") or {}).get("next") or {}).get("href")
        path = _next_path(href) if href else None
        query = None