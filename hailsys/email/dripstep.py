"""One drip step, from a person's click to queued and delivered emails.

run_step() chains pieces that exist already:  the unsubscribe sync, the RentCast re-check of
the listings (hailsys/rentcast/dripstep.py), the follow-up list builder, queue_batch and
deliver_batch.  The caller holds the send:run lock (web/sendjobs.py), so only one runs at a
time.

Order, and why:
    pre-flight     nothing is spent or written if the step cannot run
    run row        drip_step_runs 'running'; a stale 'running' row is closed as abandoned
    sync           a failed sync closes the run row as failed and raises, before a call is spent
    re-check       RentCast calls, one drip_listing_checks row per listing
    rebuild        the follow-up list is built again from what the database holds now
    queue          one transaction:  queue_batch, step marked sent, stops written, campaign
                   completed if this was the last step.  Then the run row is closed.
    deliver        after the run row is closed, so a crash here leaves nothing 'running'

Nothing here sends on its own:  every step starts from a person's click.
"""

import logging
import time
from datetime import datetime, timezone

from hailsys.constantcontact import campaigns, unsubs
from hailsys.db import get_connection
from hailsys.email import deliver, send, templatestore
from hailsys.queries import dripfollowup, sendlist
from hailsys.rentcast import client, dripplan
from hailsys.rentcast import dripstep as rentcast_checks
from hailsys.tuning import DISPLAY_TZ

logger = logging.getLogger(__name__)

# queue_batch refuses a sync older than 15 minutes (send.SYNC_MAX_AGE).  The re-check can take
# longer than that, so the sync is run again if this many seconds have gone by.
RESYNC_AFTER = 600

_STEP_SQL = """
SELECT c.status, s.template_name, s.batch_id,
       (SELECT count(*) FROM drip_steps WHERE campaign_id = c.campaign_id) AS step_count,
       (s.step_no = 1 OR EXISTS (
            SELECT 1 FROM drip_steps p
            WHERE p.campaign_id = s.campaign_id AND p.step_no = s.step_no - 1
              AND p.batch_id IS NOT NULL)) AS previous_sent,
       (SELECT count(DISTINCT e.email_id)
         FROM sent_emails e JOIN send_log l ON l.email_id = e.email_id
         WHERE l.send_status = 'queued' AND e.error_detail IS NULL
           AND e.batch_id = CASE WHEN s.step_no = 1 THEN c.first_batch_id
                                 ELSE (SELECT p.batch_id FROM drip_steps p
                                       WHERE p.campaign_id = s.campaign_id
                                         AND p.step_no = s.step_no - 1) END) AS queued_before
FROM drip_campaigns c
JOIN drip_steps s ON s.campaign_id = c.campaign_id
WHERE c.campaign_id = %(campaign_id)s AND s.step_no = %(step_no)s
"""

_ABANDON_SQL = """
UPDATE drip_step_runs SET status = 'abandoned', detail = 'found still running by a later job'
WHERE campaign_id = %(campaign_id)s AND step_no = %(step_no)s AND status = 'running'
"""

_START_SQL = """
INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
VALUES (%(campaign_id)s, %(step_no)s, %(user_id)s)
RETURNING run_id
"""

_FINISH_SQL = """
UPDATE drip_step_runs
SET status = %(status)s, method = %(method)s, calls_made = %(calls)s, emails = %(emails)s,
    detail = %(detail)s
WHERE run_id = %(run_id)s
"""

_STOP_SQL = """
UPDATE drip_recipients SET stop_reason = %(reason)s
WHERE campaign_id = %(campaign_id)s AND realtor_id = %(realtor_id)s AND stopped_at IS NULL
"""

_SEND_STEP_SQL = """
UPDATE drip_steps SET batch_id = %(batch_id)s, started_by = %(user_id)s
WHERE campaign_id = %(campaign_id)s AND step_no = %(step_no)s
"""

_COMPLETE_SQL = """
UPDATE drip_campaigns SET status = 'complete', status_changed_by = %(user_id)s
WHERE campaign_id = %(campaign_id)s
"""


class StepRefused(Exception):
    """The step cannot be run now.  The message is safe to show a person."""


def preflight(conn, campaign_id, step_no):
    """The step's row, or StepRefused.  Read-only:  nothing is spent or written."""
    step = conn.execute(_STEP_SQL, {"campaign_id": campaign_id, "step_no": step_no}).fetchone()
    if step is None:
        raise StepRefused("That step does not exist.")
    if step["status"] != "active":
        raise StepRefused(f"The campaign is {step['status']}.")
    if step["batch_id"] is not None:
        raise StepRefused("That step was already sent.")
    if not step["previous_sent"]:
        raise StepRefused(f"Step {step_no - 1} has to be sent first.")
    if step["queued_before"]:
        n = step["queued_before"]
        before = "the first send" if step_no == 1 else f"follow-up {step_no -1}"
        raise StepRefused(f"{n} email{'' if n == 1 else 's'} of {before} "
                          f"{'is' if n == 1 else 'are'} still queued: resume or discard "
                          f"{'it' if n == 1 else 'them'} first.")
    try:
        templatestore.load(conn, step["template_name"])
    except templatestore.TemplateNotFound:
        raise StepRefused("The template for this step is no longer available.") from None
    try:
        client._api_key()
    except client.RentCastAuthError:
        raise StepRefused("The RentCast key is not set.") from None
    return step


def _start_run(conn, campaign_id, step_no, user_id):
    where = {"campaign_id": campaign_id, "step_no": step_no}
    conn.execute(_ABANDON_SQL, where)
    run_id = conn.execute(_START_SQL, dict(where, user_id=user_id)).fetchone()["run_id"]
    conn.commit()
    return run_id


def _finish_run(conn, run_id, status, *, method=None, calls=None, emails=None, detail=None):
    conn.execute(_FINISH_SQL, {"run_id": run_id, "status": status, "method": method,
                               "calls": calls, "emails": emails,
                               "detail": detail[:300] if detail else None})
    conn.commit()


def _write_stops(conn, campaign_id, to_stop):
    for stop in to_stop:
        conn.execute(_STOP_SQL, {"campaign_id": campaign_id, "realtor_id": stop["realtor_id"],
                                 "reason": stop["reason"]})


def run_step(*, user_id, campaign_id, step_no, email_settings, sender, freshness_days,
             override_cap=False, max_emails=None, cap_days=sendlist.CAP_DAYS,
             sync=unsubs.run_sync, cc=campaigns, checks=rentcast_checks,
             clock=time.monotonic, now=None):
    """One click on a due step.  Returns what happened; raises StepRefused before anything
    is written, and whatever a failed sync raises (after recording it).
    """
    start = now or datetime.now(timezone.utc)
    today = start.astimezone(DISPLAY_TZ).date()
    cap = None if override_cap else cap_days

    with get_connection() as conn:
        step = preflight(conn, campaign_id, step_no)
        conn.commit()
        run_id = _start_run(conn, campaign_id, step_no, user_id)
        synced_at = clock()
        try:
            result = sync(triggered_by=user_id)
        except Exception as exc:
            # The class name only:  a message may carry text from a response.
            _finish_run(conn, run_id, "failed",
                        detail=f"the unsubscribe sync failed ({type(exc).__name__})")
            raise
        method = None
        try:
            hits = dripfollowup.fetch_hits(conn, campaign_id, now=start, cap_days=cap)
            dead = dripfollowup.fetch_dead(conn, campaign_id, step_no)
            listings = dripfollowup.listings_to_check(hits, dead)
            skip = checks.recent_definite(conn, campaign_id, step_no)
            todo = [i for i in listings if i["listing_id"] not in skip]
            zips = checks.listing_zips(conn, [i["listing_id"] for i in todo]) if todo else {}
            plan = dripplan.choose_method(
                listings=len(todo),
                zip_calls=dripplan.zip_calls_estimate(conn, {z for z in zips.values() if z}))
            conn.commit()

            calls = 0
            if todo:
                # A listing with no zip on file can only be checked by id.
                no_zip = len(zips) < len(todo) or None in zips.values()
                method = dripplan.BY_ID if no_zip else plan["method"]
                if method == dripplan.BY_ID:
                    done = checks.check_by_id(
                        conn, campaign_id=campaign_id, step_no=step_no, listings=listings,
                        skip=skip, today=today, freshness_days=freshness_days)
                else:
                    done = checks.check_by_zip_pull(
                        conn, campaign_id=campaign_id, step_no=step_no, listings=listings,
                        skip=skip, zips=zips, emp_id=user_id, estimated_calls=plan["calls"],
                        today=today, freshness_days=freshness_days)
                calls = done["calls"]

            found = dripfollowup.build_followup_list(conn, campaign_id, step_no, now=start,
                                                     cap_days=cap)
            conn.commit()
            outcome = {"status": "nothing_to_send", "run_id": run_id, "method": method,
                       "calls": calls, "emails": 0, "stopped": len(found["to_stop"]),
                       "batch_id": None}

            if found["realtors"]:
                if clock() - synced_at > RESYNC_AFTER:
                    result = sync(triggered_by=user_id)
                allowed = {r["email"] for r in found["realtors"]}
                contacts = deliver.lookup_contacts(sorted(allowed), cc=cc)
                summary = send.queue_batch(
                    conn, user_id=user_id, email_settings=email_settings,
                    now=now or datetime.now(timezone.utc), sync_run_id=result["run_id"],
                    allowed_emails=allowed, contacts=contacts, send_list=found,
                    template_name=step["template_name"])
                if summary["batch_id"]:
                    _write_stops(conn, campaign_id, found["to_stop"])
                    params = {"campaign_id": campaign_id, "step_no": step_no,
                              "user_id": user_id, "batch_id": summary["batch_id"]}
                    conn.execute(_SEND_STEP_SQL, params)
                    if step_no == step["step_count"]:
                        conn.execute(_COMPLETE_SQL, params)
                    conn.commit()
                    outcome.update(status="complete", emails=summary["emails"],
                                   batch_id=summary["batch_id"])
                else:
                    conn.rollback()

            if outcome["status"] == "nothing_to_send":
                _write_stops(conn, campaign_id, found["to_stop"])
                conn.commit()
            _finish_run(conn, run_id, outcome["status"], method=method, calls=calls,
                        emails=outcome["emails"],
                        detail=None if outcome["batch_id"] else "nobody was left to email")
        except checks.StepStopped as exc:
            conn.rollback()
            _finish_run(conn, run_id, "failed", method=method, calls=exc.calls, detail=exc.reason)
            logger.warning("event=drip_step_stopped campaign=%s step=%s calls=%s",
                           campaign_id, step_no, exc.calls)
            return {"status": "failed", "run_id": run_id, "method": method, "calls": exc.calls,
                    "emails": 0, "stopped": 0, "batch_id": None, "reason": exc.reason}
        except Exception:
            conn.rollback()
            _finish_run(conn, run_id, "failed", method=method,
                        detail="stopped by an unexpected error, see the log")
            raise

    if outcome["batch_id"]:
        outcome["delivery"] = deliver.deliver_batch(outcome["batch_id"], sender=sender, cc=cc,
                                                    max_emails=max_emails)
    logger.info("event=drip_step_done campaign=%s step=%s status=%s method=%s calls=%s emails=%s",
                campaign_id, step_no, outcome["status"], method, outcome["calls"],
                outcome["emails"])
    return outcome
