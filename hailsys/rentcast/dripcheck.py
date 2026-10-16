"""One Rentcast lookup by id for a drip campaign:  answer description and log row
    recording it.

classify() and classify_failure() turn Rentcast's answer (the way it failed) into
a reason with one of 4 outcomes for drip_listing_checks.  Anything that cannot be
read is an 'error,' and a listing with 'error' is left out of the follow-up.

    active      Rentcast says active, has not recorded it as removed, and last saw it
                inside the freshness window (same rule as sendlist's stale_listing, a
                Denver calendar date earlier than today minus freshness_days is stale).
    inactive    do not email about this listing, RentCast says it is not Active, or 
                records a removal, or has not seen it inside the window.  rc_status
                keeps what RentCast replied with, so a listing it still calls Active
                but has not seen lately is 'inactive' with rc_status 'Active' and a 
                detail explaining the reason.
    not_found   RentCast has no listing with that id (a 404)
    error       lookup failed, or answer couldn't be used
"""

import logging
from datetime import datetime, timedelta

from hailsys.rentcast import client
from hailsys.tuning import DISPLAY_TZ

logger = logging.getLogger(__name__)

# These two would repeat for every other listing in the step, so they stop the job after
# being logged.  Every other failure is logged for its listing and step continues.
FATAL = (client.RentCastAuthError, client.RentCastValidationError)

_INSERT_SQL = """
INSERT INTO drip_listing_checks
    (campaign_id, step_no, listing_id, calls_made, http_status, outcome, rc_status,
    last_seen, removed_at, detail)
VALUES
    (%(campaign_id)s, %(step_no)s, %(listing_id)s, %(calls_made)s, %(http_status)s, %(outcome)s,
    %(rc_status)s, %(last_seen)s, %(removed_at)s, %(detail)s)
"""


def _verdict(outcome, *, rc_status=None, last_seen=None, removed_at=None, http_status=None,
             calls_made=0, detail=None):
    return {"outcome": outcome, "rc_status": rc_status, "last_seen": last_seen,
            "removed_at": removed_at, "http_status": http_status, "calls_made": calls_made,
            "detail": detail}


def _when(value):
    """RentCast's date-time text as an aware datetime, or None if there is none.  Raises
    ValueError for anything else, so a garbled date is an error.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("a date that is not text")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("a date with no time zone")
    return moment


def classify(listing, *, today, freshness_days):
    """The verdict for a listing RentCast returned.  today is a Denver date, the caller
    adds calls_made and http_status.
    """
    status = listing.get("status")
    if not isinstance(status, str) or not status.strip():
        return _verdict("error", detail="the answer has no status")
    shown = status.strip()[:40]
    try:
        last_seen = _when(listing.get("lastSeenDate"))
        removed_at = _when(listing.get("removedDate"))
    except ValueError:
        return _verdict("error", rc_status=shown, detail="the answer has an unreadable date")

    def verdict(outcome, detail=None):
        return _verdict(outcome, rc_status=shown, last_seen=last_seen, removed_at=removed_at,
                        detail=detail)
    if status != "Active":
        return verdict("inactive", "RentCast says the listing is not active")
    if removed_at is not None:
        return verdict("inactive", "RentCast records a removal date")
    if last_seen is None:
        return verdict("error", "the answer has no last-seen date")
    seen_day = last_seen.astimezone(DISPLAY_TZ).date()
    if seen_day < today - timedelta(days=freshness_days):
        return verdict("inactive", f"not seen since {seen_day}")
    return verdict("active")


def classify_failure(exc):
    """The verdict for a lookup that raised a RentCastError.  Only the class and the status are
    kept:  never the response body.
    """
    if isinstance(exc, client.RentCastNotFound):
        return _verdict("not_found", http_status=exc.status, calls_made=exc.attempts)
    return _verdict("error", http_status=exc.status, calls_made=exc.attempts,
                    detail=f"{type(exc).__name__} status={exc.status}")


def record_check(conn, *, campaign_id, step_no, listing_id, verdict):
    """Write the log row and commit it.  The call has been made and billed, and the record
    must survive a job that dies afterwards.
    """
    values = dict(verdict, campaign_id=campaign_id, step_no=step_no, listing_id=listing_id)
    values["detail"] = (verdict["detail"] or None) and verdict["detail"][:300]
    with conn.cursor() as cur:
        cur.execute(_INSERT_SQL, values)
    conn.commit()


def check_listing(conn, *, campaign_id, step_no, listing_id, rentcast_id, today, freshness_days):
    """One lookup by id, logged.  Returns verdict, raises only for a FATAL error, after
    it has been logged.
    """
    where = {"campaign_id": campaign_id, "step_no": step_no, "listing_id":listing_id}
    try:
        found, attempts = client.get_sale_listing(rentcast_id)
    except ValueError:
        # No usable RentCast id, so no request was made.
        verdict = _verdict("error", detail="the listing has no RentCast id")
        record_check(conn, verdict=verdict, **where)
        return verdict
    except client.RentCastError as exc:
        verdict = classify_failure(exc)
        record_check(conn, verdict=verdict, **where)
        logger.warning("event=drip_check_failed campaign=%s step=%s listing=%s outcome=%s status=%s",
                       campaign_id, step_no, listing_id, verdict["outcome"], exc.status)
        if isinstance(exc, FATAL):
            raise
        return verdict
    verdict = classify(found, today=today, freshness_days=freshness_days)
    verdict["calls_made"] = attempts
    verdict["http_status"] = 200
    record_check(conn, verdict=verdict, **where)
    return verdict
