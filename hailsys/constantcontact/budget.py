"""Constant Contact's daily call budget.

10,000 API calls per day allowed by Constant Contact.  Each attempt is counted in cc_api_calls by
api.request.  The window is the last 24 hours, not a calendar day.  Reset time is unverified and a 
rolling count isn't lower than a calendar-day count.

Engine stops starting new emails once HEADROOM calls are left.  A 429 from Constant Contact stops
the batch.
"""

from datetime import timedelta

from hailsys.db import get_connection

DAILY_LIMIT = 10000
HEADROOM = 200
WINDOW = timedelta(hours=24)

_INSERT = "INSERT INTO cc_api_calls (method, endpoint) VALUES (%s, %s)"
_USED = "SELECT count(*) AS n FROM cc_api_calls WHERE called_at > now() - %s"


def record_call(method, path):
    """Count one attempt before it's made.  Failure raises, so call isn't made."""
    endpoint = path.split("?")[0].strip("/").split("/")[0] or "root"
    with get_connection() as conn:
        conn.execute(_INSERT, (method, endpoint))
        conn.commit()


def used():
    """Calls made in the last 24 hours."""
    with get_connection() as conn:
        n = conn.execute(_USED, (WINDOW,)).fetchone()["n"]
        conn.commit()
    return n


def remaining():
    return max(DAILY_LIMIT - used(), 0)


def exhausted():
    """True once no new email should be started."""
    return used() >= DAILY_LIMIT - HEADROOM