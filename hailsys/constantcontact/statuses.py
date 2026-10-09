"""Constant Contact bounce check:  record bounces of recent sends in send_log.

Read-Only toward Constant Contact, write are send_log status, dnc_list (hard bounces
only) and cc_status_runs.  Run from an admin button.

Rules:
* One run at a time: a session-level advisory lock for the whole run, taken with 
  a try, a second caller gets StatusBusy.
* The run row is committed before any work, so a crash leaves a trace.
* Every email still 'sent' within WINDOW_DAYS is asked about again each run.
  Nothing is remembered between runs, so a repeat run is a no-op.
* Codes:
    B/S         address doesn't exist
    V           Vacation autoreply
    D/F/X/Z     Bounced, unknown codes are treated as X
* Address already in dnc_list is never touched.
* error_detail text comes from table below, not Constant Contact.
"""


import logging

from hailsys.constantcontact import api, oauth
from hailsys.db import get_connection

logger = logging.getLogger(__name__)

WINDOW_DAYS = 14
ABANDONED = "abandoned: the process ended before this run finished"

MEANING = {
    "B": "address does not exist",
    "D": "undeliverable, no response from provider",
    "F": "mailbox full",
    "S": "suspended, reported as non-existent",
    "X": "other",
    "Z": "blocked by the recipient's provider",
}

HARD = {"B", "S"}
DELIVERED = {"V"}

LOCK_SQL = "SELECT pg_try_advisory_lock(hashtext('cc_status_check')) AS got"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext('cc_status_check'))"
SYSTEM_USER_SQL = "SELECT emp_id FROM users WHERE role = 'system'"
ABANDON_SQL = ("UPDATE cc_status_runs SET status = 'failed', finished_at = now(), "
              "error_detail = %s WHERE status = 'running'")
START_SQL = "INSERT INTO cc_status_runs (triggered_by) VALUES (%s) RETURNING run_id"
FINISH_SQL = ("UPDATE cc_status_runs SET status = %s, finished_at = now(), checked = %s, "
              "bounced = %s, suppressed = %s, error_detail = %s WHERE run_id = %s")

_TO_CHECK_SQL = """
SELECT e.email_id, e.cc_activity_id, e.recipient_email
FROM sent_emails e
WHERE e.cc_activity_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM send_log s
              WHERE s.email_id = e.email_id AND s.send_status = 'sent'
                AND s.sent_at > now() - make_interval(days => %s))
ORDER BY e.email_id
"""

_BOUNCE_SQL = """
UPDATE send_log SET send_status = 'bounced', status_updated_at = now(), error_detail = %s
WHERE email_id = %s AND send_status = 'sent'
"""

_SUPPRESS_SQL = """
INSERT INTO dnc_list (email_raw, added_by, source, reason, realtor_id)
SELECT %(email)s, %(added_by)s, 'hard_bounce', %(reason)s,
        (SELECT realtor_id FROM realtors WHERE email_norm = lower(trim(%(email)s)))
ON CONFLICT (email_norm) DO NOTHING
RETURNING dnc_id
"""


class StatusBusy(Exception):
    """Another bounce check holds the lock."""


def _failure_text(exc):
    if isinstance(exc, (api.ApiError, oauth.OAuthError)):
        return f"{type(exc).__name__}: {exc}"[:300]
    return type(exc).__name__


def fetch_bounces(activity_id):
    """Bounce records of one campaign activity, newest first.
    
    One email goes to one recipient, so there's one page; a next link means
    something unexpected, so the system stops.
    """
    _, doc = api.request("GET", f"/reports/email_reports/{activity_id}/tracking/bounces",
                         query={"limit": 50})
    doc = doc or {}
    if (doc.get("_links") or {}).get("next"):
        raise api.ApiError("Constant Contact returned more bounce pages than expected.")
    return doc.get("tracking_activities") or []

def worst_code(records, recipient):
    """The bounce code to record for this recipient, or None if nothing counts as a bounce
    
    Records for any other address are ignored."""
    codes = []
    for rec in records:
        if (rec.get("email_address") or "").strip().lower() != recipient.strip().lower():
            continue
        code = rec.get("bounce_code")
        if code in DELIVERED:
            continue
        codes.append(code if code in MEANING else "X")
    for code in codes:
        if code in HARD:
            return code
    return codes[0] if codes else None


def _check_one(conn, email, system_id, fetch):
    """Returns (bounced, suppressed) as 0 or 1 each.  Doesn't commit."""
    code = worst_code(fetch(email["cc_activity_id"]), email["recipient_email"])
    if code is None:
        return 0, 0
    detail = f"Constant Contact bounce code {code}: {MEANING[code]}"
    changed = conn.execute(_BOUNCE_SQL, (detail, email["email_id"])).rowcount
    suppressed = 0
    if code in HARD:
        row = conn.execute(_SUPPRESS_SQL, {
            "email": email["recipient_email"], "added_by": system_id,
            "reason": detail}).fetchone()
        suppressed = 1 if row else 0
    return (1 if changed else 0), suppressed


def _run(conn, triggered_by, fetch):
    system = conn.execute(SYSTEM_USER_SQL).fetchone()
    if system is None:
        raise RuntimeError("no system user in users")
    conn.execute(ABANDON_SQL, (ABANDONED,))
    run_id = conn.execute(START_SQL, (triggered_by,)).fetchone()["run_id"]
    conn.commit()           # the run is visible before work starts

    checked = bounced = suppressed = 0
    try:
        emails = conn.execute(_TO_CHECK_SQL, (WINDOW_DAYS,)).fetchall()
        conn.rollback()
        for email in emails:
            b, s = _check_one(conn, email, system["emp_id"], fetch)
            conn.commit()   # one email at a time: a later failure keeps earlier results
            checked += 1
            bounced += b
            suppressed += s
        conn.execute(FINISH_SQL, ("ok", checked, bounced, suppressed, None, run_id))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        try:
            conn.execute(FINISH_SQL, ("failed", checked, bounced, suppressed,
                                      _failure_text(exc), run_id))
            conn.commit()
        except Exception:
            conn.rollback()
            logger.error("event=cc_status_close_failed run_id=%s", run_id)
        raise
    logger.info("event=cc_status_done run_id=%s checked=%s bounced=%s suppressed=%s",
                run_id, checked, bounced, suppressed)
    return {"run_id": run_id, "checked": checked, "bounced": bounced,
            "suppressed": suppressed}


def run_check(*, triggered_by, fetch=fetch_bounces):
    """One bounce check.  Returns {'run_id', 'checked', 'bounced', 'suppressed'}.  Raises
    StatusBusy, or whatever stopped the run (ApiError, OAuthError, a database error) after
    recording it.  `fetch` is replaceable so tests need no network."""
    with get_connection() as conn:
        if not conn.execute(LOCK_SQL).fetchone()["got"]:
            raise StatusBusy("A bounce check is already running. Try again in a moment.")
        try:
            return _run(conn, triggered_by, fetch)
        finally:
            conn.rollback()
            conn.execute(UNLOCK_SQL)
            conn.commit()


def last_success(conn):
    """The newest good check, for the history page, or None."""
    return conn.execute(
        "SELECT run_id, started_at, finished_at, checked, bounced, suppressed "
        "FROM cc_status_runs WHERE status = 'ok' ORDER BY run_id DESC LIMIT 1").fetchone()