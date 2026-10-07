"""Constant Contact unsubscribe sync:  copy Constant Contact's unsubscribed
contacts into dnc_list.

Read-only toward Constant Contact;  the only writes are dnc_list and cc_sync_runs.
Only narrows who can be emailed.  Called before every send from an admin button;
a failed sync stops the send, no separate age limit.

Rules:

* One run at a time: a session-level advisory lock for the whole run, taken
  with try, so a second caller gets SyncBusy and a crash releases it.  A
  'running' row seen at the start therefore belongs to a dead process.
* The run row is committed before any work, so a crash leaves a trace.
* An address already in dnc_list is never touched.  If an admin REMOVED it (the
  row has removed_at) and Constant Contact still says unsubscribed, that is a
  conflict: counted and reported, never undone.  The read-before-write rule in
  the add flow blocks that person anyway.
* updated_after filters on the contact's updated_at, not the opt-out date, so
  the watermark is the last good run's start minus an overlap.
* A failed run records fixed text only: the message of our own two error types,
  otherwise just the exception's class name.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from hailsys.constantcontact import api, oauth
from hailsys.db import get_connection

logger = logging.getLogger(__name__)

OVERLAP = timedelta(days=1)
COMMIT_EVERY = 100
ABANDONED = "abandoned:  the process ended before this run finished"

LOCK_SQL = "SELECT pg_try_advisory_lock(hashtext('cc_unsubscribe_sync')) AS got"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext('cc_unsubscribe_sync'))"
SYSTEM_USER_SQL = "SELECT emp_id FROM users WHERE role = 'system'"
ABANDON_SQL = ("UPDATE cc_sync_runs SET status = 'failed', finished_at = now(), "
                "error_detail = %s WHERE status = 'running'")
LAST_OK_SQL = ("SELECT started_at FROM cc_sync_runs WHERE status = 'ok' "
               "ORDER BY run_id DESC LIMIT 1")
START_SQL = ("INSERT INTO cc_sync_runs (triggered_by, watermark) "
             "VALUES (%s, %s) RETURNING run_id")
FINISH_SQL = ("UPDATE cc_sync_runs SET status = %s, finished_at = now(), "
              "fetched = %s, inserted = %s, already_present = %s,"
              "conflicts = %s, error_detail = %s WHERE run_id = %s")

# Sources date in added_at, ON CONFLICT DO NOTHING.  Realtor is linked by address
# at insert time.
_INSERT_SQL = """
INSERT INTO dnc_list (email_raw, added_at, added_by, source, reason, realtor_id)
SELECT %(email)s, %(opted_at)s::timestamptz, %(added_by)s, 'unsubscribe',
        %(reason)s,
        (SELECT realtor_id FROM realtors WHERE email_norm = lower(trim(%(email)s)))
ON CONFLICT (email_norm) DO NOTHING
RETURNING dnc_id
"""


_OUTCOME_KEY = {"inserted": "inserted", "already_present": "already_present",
                "conflict": "conflicts", "skipped": "skipped"}


class SyncBusy(Exception):
    """Another sync holds the lock."""

def _parse_when(text):
    """An ISO timestamp as an aware datetime, or None if unreadable."""
    if not isinstance(text, str):
        return None
    try:
        when = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)

def _clean(value):
    """Short text from Constant Contact that is safe to store, or 'unknown'."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9 _.-]{1,40}", value):
        return value
    return "unknown"

def _iso(when):
    if when is None:
        return None
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _failure_text(exc):
    if isinstance(exc, (api.ApiError, oauth.OAuthError)):
        return f"{type(exc).__name__}: {exc}"[:300]
    return type(exc).__name__

def _record(conn, contact, added_by):
    """Write one unsubscribed contact.  Returns the outcome, doesn't commit"""
    ea = contact.get("email_address") or {}
    address = (ea.get("address") or "").strip()
    if "@" not in address:
        logger.warning("event=cc_sync_skip reason=no_address")
        return "skipped"
    when = _parse_when(ea.get("opt_out_date"))
    reason = ("Constant Contact: unsubscribed "
              f"(opt_out_source={_clean(ea.get('opt_out_source'))}, "
              f"reason={_clean(ea.get('opt_out_reason'))})")
    if when is None:
        reason += " (date unreadable)"
        when = datetime.now(timezone.utc)
    row = conn.execute(_INSERT_SQL, {"email": address, "opted_at": when,
                                     "added_by": added_by,
                                     "reason": reason}).fetchone()
    if row:
        return "inserted"
    existing = conn.execute(
        "SELECT removed_at FROM dnc_list WHERE email_norm = lower(trim(%s))",
        (address,)).fetchone()
    if existing and existing["removed_at"] is not None:
        return "conflict"
    return "already_present"

def _watermark(conn):
    row = conn.execute(LAST_OK_SQL).fetchone()
    return row["started_at"] - OVERLAP if row else None

def _run(conn, triggered_by):
    system = conn.execute(SYSTEM_USER_SQL).fetchone()
    if system is None:
        raise RuntimeError("no system user in users")
    # Hold the lock so no one else runs
    conn.execute(ABANDON_SQL, (ABANDONED,))
    watermark = _watermark(conn)
    run_id = conn.execute(START_SQL, (triggered_by, watermark)).fetchone()["run_id"]
    conn.commit()       # run is visible before work starts

    counts = {"fetched": 0, "inserted": 0, "already_present": 0,
              "conflicts": 0, "skipped": 0}
    try:
        for contact in api.iter_unsubscribed(updated_after=_iso(watermark)):
            counts["fetched"] += 1
            counts[_OUTCOME_KEY[_record(conn, contact, system["emp_id"])]] += 1
            if counts["fetched"] % COMMIT_EVERY == 0:
                conn.commit()
        conn.execute(FINISH_SQL, ("ok", counts["fetched"], counts["inserted"],
                                  counts["already_present"], counts["conflicts"],
                                  None, run_id))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        try:
            conn.execute(FINISH_SQL, ("failed", counts["fetched"],
                                      counts["inserted"],
                                      counts["already_present"],
                                      counts["conflicts"], _failure_text(exc),
                                      run_id))
            conn.commit()
        except Exception:
            conn.rollback()
            logger.error("event=cc_sync_close_failed run_id=%s", run_id)
        raise
    logger.info("event=cc_sync_done run_id=%s fetched=%s inserted=%s "
                "already_present=%s conflicts=%s skipped=%s", run_id, 
                counts["fetched"], counts["inserted"],
                counts["already_present"], counts["conflicts"], counts["skipped"])
    if counts["conflicts"]:
        logger.warning("event=cc_sync_conflicts run_id=%s count=%s",
                       run_id, counts["conflicts"])
    return {"run_id": run_id, **counts}

def run_sync(*, triggered_by):
    """One sync.  Returns {'run_id': ..., counts}.  Raises SyncBusy, or whatever
    stopped the run (ApiError, OAuthError, a database error) after recording it.
    """
    with get_connection() as conn:
        if not conn.execute(LOCK_SQL).fetchone()["got"]:
            raise SyncBusy("A Constant Contact sync is already running. "
                           "Try again in a moment.")
        try:
            return _run(conn, triggered_by)
        finally:
            conn.rollback()
            conn.execute(UNLOCK_SQL)
            conn.commit()


def last_success(conn):
    """The newest good run, for the Admin page, or None."""
    return conn.execute(
        "SELECT run_id, started_at, finished_at, fetched, inserted, "
        "already_present, conflicts FROM cc_sync_runs WHERE status = 'ok' "
        "ORDER BY run_id DESC LIMIT 1").fetchone()