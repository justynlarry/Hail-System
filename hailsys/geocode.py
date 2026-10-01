"""Census Geocoder client:  one-line address lookup with throttling and error
classification.

Census Geocoder is the same TIGER address data postgis_tiger_geocoder
would use locally, served by Census.  No API key, US-only, street-level
addresses only.  Cannot match solely by city/state.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client

logger = logging.getLogger(__name__)

BASE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"

# Census doesn't publish a documented rate limit, this is self-imposed
# and conservative.
RATE_LIMIT_PER_SECOND = 2
MIN_REQUEST_INTERVAL = 1.0 / RATE_LIMIT_PER_SECOND
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.0
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
TIMEOUT = 20


# Address vintage Census geocodes against.  Needs verification against
# https://geocoding.geo.census.gov/geocoder/benchmarks before changing,
# benchmark names are versioned and retired.
BENCHMARK = "Public_AR_Current"


class GeocodeError(Exception):
    """Base for everything this module raises.  'user_message' is safe
    to show users."""
    user_message = "The address lookup failed for an unknown reason."

    def __init__(self, message, *, attempts=0, status=None):
        super().__init__(message)
        self.attempts = attempts
        self.status = status


class GeocodeServerError(GeocodeError):
    """Retries exhausted against a retryable status."""
    user_message = ("The Census address service is unavailable or busy. "
                    "Safe to try again in a minute.")


class GeocodeRequestError(GeocodeError):
    """Census rejected the request with a non-retryable status (400, 403...).
    Retrying the same request will not help."""
    user_message = ("The Census address service rejected the request. "
                    "Check the address and try again.")


class GeocodeConnectionError(GeocodeError):
    """No HTTP response:  DNS failure, timeout, connection refused."""
    user_message = ("Couldn't reach the Census address service. "
                    "Check network connectivity.")


class GeocodeResponseError(GeocodeError):
    """200, but body could not be read or parsed."""
    user_message = ("The Census address service returned something we "
                    "couldn't read.  Safe to retry.")


_last_request_time = 0.0
# Flask serves requests on threads, so without this two simultaneous searches
# could both see "enough time elapsed" and fire together.  Held across the
# sleep on purpose: concurrent callers queue up.
_throttle_lock = threading.Lock()


def _throttle():
    """Sleep just long enough to stay under self-imposed limit."""
    global _last_request_time
    with _throttle_lock:
        elapsed = time.monotonic() - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


def geocode(address: str) -> list[dict]:
    """Geocode one address.  Returns a list of matches, best confidence
    first as Census orders them.  No results means Census found nothing.

    Each match is a dict: matched_address, latitude, longitude, tiger_line_id.
    """
    params = {
        "address": address,
        "benchmark": BENCHMARK,
        "format": "json",
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    attempt = 0
    while True:
        attempt += 1
        _throttle()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code in RETRYABLE_STATUSES and attempt <= MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "census geocoder status=%s attempt=%s retrying_in=%.1f",
                    exc.code, attempt, delay,
                )
                time.sleep(delay)
                continue
            # Retryable status with retries spent is a service problem;
            # anything else (400, 403...) is a request problem.
            error_class = (GeocodeServerError if exc.code in RETRYABLE_STATUSES
                           else GeocodeRequestError)
            raise error_class(
                f"Census geocoder returned {exc.code}",
                attempts=attempt, status=exc.code,
            ) from exc
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            if attempt <= MAX_RETRIES:
                delay = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "Census geocoder unreachable attempt=%s retrying_in=%.1f err=%s",
                    attempt, delay, exc,
                )
                time.sleep(delay)
                continue
            raise GeocodeConnectionError(
                f"Could not reach the Census geocoder: {exc}", attempts=attempt,
            ) from exc

    try:
        payload = json.loads(body)
        raw_matches = payload["result"]["addressMatches"]
    except (ValueError, KeyError, TypeError) as exc:
        raise GeocodeResponseError(
            f"Could not parse the Census geocoder response: {exc}",
            attempts=attempt,
        ) from exc

    matches = []
    for raw in raw_matches:
        try:
            # Census returns x=longitude, y=latitude.
            coords = raw["coordinates"]
            matches.append({
                "matched_address": raw["matchedAddress"],
                "longitude": float(coords["x"]),
                "latitude": float(coords["y"]),
                "tiger_line_id": (raw.get("tigerLine") or {}).get("tigerLineId"),
            })
        except (KeyError, TypeError, ValueError):
            logger.warning("census geocoder: skipping unparseable match")
            continue
    logger.info("census geocoder matches=%s attempts=%s", len(matches), attempt)
    return matches
