"""RentCast API client:  Sale-listings search with pagination, throttling,
and error classification.  
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rentcast.io/v1/listings/sale"
MAX_PAGE_SIZE = 500     # Rentcast Ceiling:  Billing is per call
                        # Not per record.  Smaller page = more 
                        # calls for the same data

RATE_LIMIT_PER_SECOND = 20  # RentCast's per-key limit
MIN_REQUEST_INTERVAL = 1.0 / RATE_LIMIT_PER_SECOND
MAX_RETRIES = 4
RETRY_BACKOFF_BASE = 1.0
RETRYABLE_STATUSES = {429, 500, 503, 504}
TIMEOUT = 30

class RentCastError(Exception):
    """Base for everything this module raises.  'user_message' is
    safe to show a person in a popup"""
    user_message = "Rentcast request failed for an unknown reason."

    def __init__(self, message, *, attempts=0, status=None):
        super().__init__(message)
        self.attempts = attempts
        self.status = status

class RentCastAuthError(RentCastError):
    """401/403: a retry won't fix this"""
    user_message = ("Rentcast rejected this request (Bad API key, billing "
                    "issue, or key restriction.). Check the RentCast Dashboard.")

class RentCastValidationError(RentCastError):
    """400/405: The request was malformed"""
    user_message = "The request to RentCast was malformed - this is an internal system bug."

class RentCastServerError(RentCastError):
    """429/500/503/504: After retries were exhausted.

    user_message varies by status -- see docs/data-sources.md's RentCast
    HTTP status code table, which is the source of truth for this wording.
    """
    _MESSAGES = {
        429: ("RentCast kept rate-limiting this request even after "
              "retrying. Safe to retry again shortly."),
        500: "RentCast had an error on their end. Safe to retry -- try again in a minute.",
        503: "RentCast is temporarily unavailable. Safe to retry -- try again in a minute.",
        504: "RentCast didn't respond in time. Safe to retry.",
    }
    user_message = "RentCast is unavailable or rate-limiting. Safe to retry shortly."

    def __init__(self, message, *, attempts=0, status=None):
        super().__init__(message, attempts=attempts, status=status)
        if status in self._MESSAGES:
            self.user_message = self._MESSAGES[status]

class RentCastConnectionError(RentCastError):
    """Never got an HTTP response: DNS, timeout, connection refused."""
    user_message = "Couldn't reach RentCast's servers.  Check Network Connectivity."

_last_request_time = 0.0

class RentCastResponseError(RentCastError):
    """200, but body could not be read or parsed.

    Request was served and billed, so this should carry
    attempts like any other RentCastError    
    """
    user_message = ("RentCast returned a response we couldn't read "
                    "Safe to retry.")

def _throttle():
    """Sleep for just long enough to stay under RentCast's 20 req/sec limit"""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.monotonic()

def _api_key() -> str:
    try:
        return os.environ["RENTCAST_KEY"]
    except KeyError as exc:
        raise RentCastAuthError("RENTCAST_KEY is not set in the environment") from exc

def _get(params: dict) -> tuple[list, int]:
    """One logical request to /listings/sale.  Returns (listings, attempts):
    the parsed listing array, empty if RentCast reports 0 matches, and how
    many physical requests it took, retries included."""
    url =f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"X-Api-Key": _api_key(), "Accept": "application/json"}
    )

    attempt = 0
    while True:
        attempt += 1
        _throttle()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = json.loads(response.read())
                if not isinstance(data, list):
                    # Raised, not returned as []: an empty list would log
                    # the zip as a clean 200 with no listings, and read the
                    # same as a zip that genuinely has none.
                    logger.error("event=rentcast_unexpected_shape type=%s", type(data).__name__)
                    raise RentCastResponseError(
                        f"expected a list, got {type(data).__name__}",
                        attempts=attempt, status=response.status)
                return data, attempt

        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = json.loads(exc.read())
            except (json.JSONDecodeError, ValueError):
                body = {}

            if status == 404:
                return [], attempt
            
            if status in (401, 403):
                # A retry can't fix a bad key or a billing/restriction
                # issue, so this raises immediately -- 'attempt' is 1 here,
                # the one request that was ever going to be made.
                logger.error("event=rentcast_auth_error status=%s body=%r", status, body)
                raise RentCastAuthError(f"status={status} body={body}", 
                                        attempts=attempt, status=status) from exc

            if status in (400, 405):
                # Same reasoning as 401/403: a malformed request or wrong
                # method won't succeed on retry, so this is immediate too.
                logger.error("event=rentcast_validation_error status=%s body=%r params=%r",
                              status, body, params)
                raise RentCastValidationError(f"status={status} body={body}",
                                                attempts=attempt, status=status) from exc

            if status in RETRYABLE_STATUSES:
                if attempt >= MAX_RETRIES + 1:
                    logger.error("event=rentcast_retries_exhausted status=%s body=%r", status, body)
                    raise RentCastServerError(
                        f"status={status} after {attempt} attempts",
                        attempts=attempt, status=status
                    ) from exc
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning("event=rentcast_retry status=%s attempt=%d/%d sleep=%.1fs",
                                status, attempt, MAX_RETRIES, backoff)
                time.sleep(backoff)
                continue

            logger.error("event=rentcast_unexpected_status status=%s body=%r", status, body)
            raise RentCastError(f"unexpected status={status} body={body}",
                                attempts=attempt, status=status) from exc

        except urllib.error.URLError as exc:
            if attempt >= MAX_RETRIES + 1:
                logger.error("event=rentcast_unreachable attempts=%d reason=%s", attempt, exc.reason)
                raise RentCastConnectionError(str(exc.reason), attempts=attempt) from exc
            backoff = RETRY_BACKOFF_BASE * (2 **(attempt - 1))
            logger.warning("event=rentcast_unreachable_retry reason=%s attempt=%d sleep=%.1fs",
                            exc.reason, attempt, backoff)
            time.sleep(backoff)
            continue

        except (json.JSONDecodeError, ValueError, OSError, http.client.HTTPException) as exc:
            # Reqeust sent and answered, ready or parsing the body failed
            # not retried.  No way to know whether a partial read means data
            # is recoverable, and RentCast billed call either way
            logger.error("event=rentcast_unreadable_response attempts=%d error=%s",
                         attempt, exc)
            # status stays None: a body that couldn't be read or parsed
            # produced no usable answer, and NULL in api_call_log.http_status
            # says exactly that. (The non-list case above does record the
            # status, because there the response itself was complete.)
            raise RentCastResponseError(str(exc), attempts=attempt) from exc

def search_sale_listings(zip_code: str, status: str = "Active", 
                        days_old: int | None = None) -> tuple[list, int]:
    """Fetch every listing for one zip/status/days_old combination
    Returns (listings, calls_made).  On failure, re-raises whatever _get
    raised, but with .attempts adjusted to include every call already made
    """
    listings = []
    calls_made = 0
    offset = 0
    while True:
        params = {"zipCode": zip_code, "status": status,
                  "limit": MAX_PAGE_SIZE, "offset": offset}
        if days_old is not None:
            params["daysOld"] = days_old
        try:
            page, attempts = _get(params)
        except RentCastError as exc:
            exc.attempts = calls_made + exc.attempts
            raise

        calls_made += attempts
        if not page:
            break
        listings.extend(page)
        if len(page) < MAX_PAGE_SIZE:
            break
        offset += MAX_PAGE_SIZE
        
    return listings, calls_made

