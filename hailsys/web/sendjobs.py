"""Background execution for a send, and the only place the web app reaches the send engine.

hailsys/web/send.py doesn't import the engine, the Send-now route calls start_send()
here.  A thread dies with its process, a restart mid-send leaves queued rows on disk,
and deliver_email resumes from them without scheduling anything twice.

One send at a time for whatever days it covers.  Two sends over overlapping days would
have the same matches before either had committed, and queue the same realtor twice.
A session advisory lock held by the thread for the whole run enforces.
"""

import logging
import threading

from hailsys.db import get_connection
from hailsys.email import deliver, render
from hailsys.constantcontact import budget

logger = logging.getLogger(__name__)

LOCK_KEY = "send:run"
_TRY_LOCK = "SELECT pg_try_advisory_lock(hashtext(%s)) AS got"
_UNLOCK = "SELECT pg_advisory_unlock(hashtext(%s))"


class SendInProgress(Exception):
    """Another send is running."""

# Re-exported so hailsys/web/send.py doesn't import the engine itself.
NothingToResume = deliver.NothingToResume
BatchTooOld = deliver.BatchTooOld
RESUME_MAX_DAYS = deliver.RESUME_MAX_DAYS

BatchBusy = deliver.BatchBusy

def load_config():
    """(email_settings, sender) from environment, render.SettingsError names variable."""
    return render.EmailSettings.from_env(), deliver.sender_from_env()


def is_busy():
    """True if a send holds the lock."""
    with get_connection() as conn:
        got = conn.execute(_TRY_LOCK, (LOCK_KEY,)).fetchone()["got"]
        if got:
            conn.execute(_UNLOCK, (LOCK_KEY,))
        conn.commit()
    return not got


def calls_budget():
    """Constant Contact calls used in last 24 hours, for the screens.
    
    'usable' is what can still be spent before the engine stops starting new emails.
    Headroom is kept back for an email already sent."""
    used = budget.used()
    return {"used": used, "limit": budget.DAILY_LIMIT, "headroom": budget.HEADROOM,
            "remaining": max(budget.DAILY_LIMIT - used, 0),
            "usable": max(budget.DAILY_LIMIT - budget.HEADROOM - used, 0)}


def _run(user_id, run_kwargs):
    """Thread body, holds the lock for the whole run.  Logs counts only."""
    try:
        with get_connection() as conn:
            if not conn.execute(_TRY_LOCK, (LOCK_KEY,)).fetchone()["got"]:
                logger.warning("event=send_not_started reason=busy")
                return
            conn.commit()
            try:
                summary = deliver.run_send(user_id=user_id, **run_kwargs)
                logger.info("event=send_job_complete batch_id=%s emails=%s delivery=%s",
                            summary["batch_id"], summary["emails"], summary.get("delivery"))
            finally:
                conn.rollback()
                conn.execute(_UNLOCK, (LOCK_KEY,))
                conn.commit()
    except Exception:
        logger.exception("event=send_job_failed")


def start_send(*, user_id, **run_kwargs):
    """Start a send in a thread.  Raises SendInProgress if one is already running."""
    if is_busy():
        raise SendInProgress("a send is already running")
    thread = threading.Thread(target=_run, args=(user_id, run_kwargs), daemon=True)
    thread.start()

def _run_resume(user_id, batch_id, run_kwargs):
    """Thread body for a resume, same lock as _run so a resume and send don't overlap."""
    try:
        with get_connection() as conn:
            if not conn.execute(_TRY_LOCK, (LOCK_KEY,)).fetchone()["got"]:
                logger.warning("event=resume_not_started reason=busy")
                return
            conn.commit()
            try:
                counts = deliver.run_resume(user_id=user_id, batch_id=batch_id, **run_kwargs)
                logger.info("event=resume_job_complete batch_id=%s delivery=%s", batch_id, counts)
            finally:
                conn.rollback()
                conn.execute(_UNLOCK, (LOCK_KEY,))
                conn.commit()
    except Exception:
        logger.exception("event=resume_job_failed")


def start_resume(*, user_id, batch_id, **run_kwargs):
    """Resume a batch's queued emails in a thread.
    
    Raises SendInProgress if a send is running, and deliver.NothingToResume or
    deliver.BatchTooOld, before a thread starts so route can explain.
    """
    if is_busy():
        raise SendInProgress("a send is already running")
    deliver.check_resumable(batch_id)
    thread = threading.Thread(target=_run_resume, args=(user_id, batch_id, run_kwargs),
                              daemon=True)
    thread.start()

def count_discardable(batch_id):
    """Untouched queued emails of a batch that discard_queued would discard (read-only)."""
    return deliver.count_untouched(batch_id)


def discard_queued(*, user_id, batch_id):
    """Discard a batch's untouched queued emails now (one UPDATE, so no thread).

    Raises SendInProgress while a send or resume holds the lock, and BatchBusy if this batch
    is being delivered.  Returns how many emails were discarded.
    """
    with get_connection() as conn:
        if not conn.execute(_TRY_LOCK, (LOCK_KEY,)).fetchone()["got"]:
            raise SendInProgress("a send is already running")
        conn.commit()
        try:
            return deliver.discard_queued(batch_id, user_id=user_id)
        finally:
            conn.rollback()
            conn.execute(_UNLOCK, (LOCK_KEY,))
            conn.commit()
