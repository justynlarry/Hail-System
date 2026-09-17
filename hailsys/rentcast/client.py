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

    def __init__(self, message, status=None):
        super().__init__(message)
        if status in self._MESSAGES:
            self.user_message = self._MESSAGES[status]

class RentCastConnectionError(RentCastError):
    """Never got an HTTP response: DNS, timeout, connection refused."""
    user_message = "Couldn't reach RentCast's servers.  Check Network Connectivity."

_last_request_time = 0.0

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

def _get(params: dict) -> list:
    """One logical reqeust to /listings/sale.  Returns the parsed listing array, which 
    is empty if RentCast reports 0 matches"""
    url =f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"X-Api-Key": _api_key(), "Accept": "application/json"}
    )

    attempt = 0
    while True:
        _throttle()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = json.loads(response.read())
                if not isinstance(data, list):
                    logger.error("event=rentcast_unexpected_shape type=%s", type(data).__name__)
                    return []
                return data
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = json.loads(exc.read())
            except (json.JSONDecodeError, ValueError):
                body = {}

            if status == 404:
                return []
            
            if status in (401, 403):
                # A retry can't fix a bad key or a billing/restriction
                # issue, so this raises immediately -- 'attempt' is still
                # 0 here and stays out of the message on purpose.
                logger.error("event=rentcast_auth_error status=%s body=%r", status, body)
                raise RentCastAuthError(f"status={status}") from exc

            if status in (400, 405):
                # Same reasoning as 401/403: a malformed request or wrong
                # method won't succeed on retry, so this is immediate too.
                logger.error("event=rentcast_validation_error status=%s body=%r params=%r",
                              status, body, params)
                raise RentCastValidationError(f"status={status}") from exc

            if status in RETRYABLE_STATUSES:
                attempt += 1
                if attempt > MAX_RETRIES:
                    logger.error("event=rentcast_retries_exhausted status=%s body=%r", status, body)
                    raise RentCastServerError(
                        f"status={status} after {attempt} attempts", status=status
                    ) from exc
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning("event=rentcast_retry status=%s attempt=%d/%d sleep=%.1fs",
                                status, attempt, MAX_RETRIES, backoff)
                time.sleep(backoff)
                continue
            logger.error("event=rentcase_unexpected_status status=%s body=%r", status, body)
            raise RentCastError(f"unexpected status={status} body={body}") from exc

        except urllib.error.URLError as exc:
            attempt += 1
            if attempt > MAX_RETRIES:
                logger.error("event=rentcast_unreachable attempts=%d reason %s", attempt, exc.reason)
                raise RentCastConnectionError(str(exc.reason)) from exc
            backoff = RETRY_BACKOFF_BASE * (2 **(attempt - 1))
            logger.warning("event=rentcast_unreachable_retry reason %s attempt=%d sleep=%.1fs",
                            exc.reason, attempt, backoff)
            time.sleep(backoff)
            continue

def search_sale_listings(zip_code: str, status: str = "Active", days_old: int | None = None):
    """Yield every sale listing for one zip code and one status value.
    'days_old' maps to RentCast's filter"""
    offset = 0
    while True:
        params = {"zipCode": zip_code, "status": status,
                  "limit": MAX_PAGE_SIZE, "offset": offset}
        if days_old is not None:
            params["daysOld"] = days_old
        
        page = _get(params)
        if not page:
            return
        yield from page
        if len(page) < MAX_PAGE_SIZE:
            return
        offset += MAX_PAGE_SIZE

