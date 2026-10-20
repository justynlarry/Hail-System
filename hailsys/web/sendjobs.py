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
from hailsys.email import deliver, dripstep, emailconfig
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
StepRefused = dripstep.StepRefused

def load_config():
    """(email_settings, sender) from database, render.SettingsError names problem."""
    with get_connection() as conn:
        return emailconfig.load_settings(conn), emailconfig.load_sender(conn)


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


def _run_drip_step(user_id, campaign_id, step_no, run_kwargs):
    """Thread body for a drip step, same lock as _run so a step and a send don't overlap.
    Logs ids and counts only."""
    try:
        with get_connection() as conn:
            if not conn.execute(_TRY_LOCK, (LOCK_KEY,)).fetchone()["got"]:
                logger.warning("event=drip_step_not_started reason=busy campaign=%s step=%s",
                               campaign_id, step_no)
                return
            conn.commit()
            try:
                out = dripstep.run_step(user_id=user_id, campaign_id=campaign_id,
                                        step_no=step_no, **run_kwargs)
                logger.info("event=drip_step_job_complete campaign=%s step=%s status=%s "
                            "method=%s calls=%s emails=%s delivery=%s", campaign_id, step_no,
                            out["status"], out["method"], out["calls"], out["emails"],
                            out.get("delivery"))
            except dripstep.StepRefused as exc:
                logger.warning("event=drip_step_refused campaign=%s step=%s reason=%s",
                               campaign_id, step_no, exc)
            finally:
                conn.rollback()
                conn.execute(_UNLOCK, (LOCK_KEY,))
                conn.commit()
    except Exception:
        logger.exception("event=drip_step_job_failed campaign=%s step=%s", campaign_id, step_no)


def start_drip_step(*, user_id, campaign_id, step_no, **run_kwargs):
    """Run one drip step in a thread, on a user's click.
    
    Raises SendInProgress if a send, resume or step is running, and StepRefused if the step 
    cannot be run now, both before a thread starts so the route can explain.
    """
    if is_busy():
        raise SendInProgress("a send is already running")
    with get_connection() as conn:
        dripstep.preflight(conn, campaign_id, step_no)
        conn.commit()
    thread = threading.Thread(target=_run_drip_step,
                              args=(user_id, campaign_id, step_no, run_kwargs), daemon=True)
    thread.start()


def step_problem(campaign_id, step_no):
    """Why this step cannot be started now (the pre-flight message) or None.  Read-Only."""
    with get_connection() as conn:
        try:
            dripstep.preflight(conn, campaign_id, step_no)
        except dripstep.StepRefused as exc:
            return str(exc)
        finally:
            conn.rollback()
    return None


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
