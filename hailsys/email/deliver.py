"""Phase B of a Send:  from queued rows to a finished Constant Contact campaign.

queue_batch() (send.py) has already committed the sent_emails and send_log rows.  Here
each email goes through Constant Contact in this order, writing what happened to the 
database after every step, so a crash can be resumed without repeating anything that
can't be repeated.

Outcomes of deliver_email():
    sent         Campaign reached DONE
    failed       Nothing was sent.  send_log rows are 'failed', so a later send can retry
    needs_review May have been sent (schedule call or wait after didn't end cleanly).
                 send_log rows stay 'queued' and count as emailed, a user needs to verify
                 status in Constant Contact before retrying.
    skipped      already finished, failed or parked

Prior to schedule call an unknown outcome is a stray draft.  After it moves to needs_review.
A schedule() can't be taken back.  A resume asks Constant Contact for the campaign's status
first and doesn't schedule a campaign that isn't a draft.

Auth or quota errors stop the whole batch (BatchStopped).  Any other Constant Contact error
stops only that email.  Database error or crash isn't caught here.
"""


import logging
from datetime import datetime, timedelta, timezone

from hailsys.constantcontact import budget, campaigns, unsubs
from hailsys.constantcontact.api import ApiError
from hailsys.db import get_connection
from hailsys.email import dripstart, render, send, templatestore
from hailsys.queries import sendlist

logger = logging.getLogger(__name__)

# Column names are interpolated into SQL, only these are passed.
_ID_COLUMNS = frozenset({"cc_contact_id", "cc_list_id", "cc_campaign_id", "cc_activity_id"})
_MARK_COLUMNS = frozenset({"scheduled_at", "list_deleted_at"})

# A resume re-sends queued emails, its content and eligibility are only trusted for this period of time.
RESUME_MAX_DAYS = 7

_EMAIL_SQL = """
SELECT e.email_id, e.batch_id, e.recipient_email, e.subject, e.html_body,
       e.cc_contact_id, e.cc_list_id, e.cc_campaign_id, e.cc_activity_id,
       e.scheduled_at, e.list_deleted_at, e.error_detail, r.agent_name,
       (SELECT count(*) FROM send_log s
            WHERE s.email_id = e.email_id AND s.send_status = 'queued') AS queued
FROM sent_emails e
JOIN realtors r USING (realtor_id)
WHERE e.email_id = %s
"""


class BatchStopped(Exception):
    """Credentials or quota: nothing further in this batch can work."""

class BatchBusy(Exception):
    """Another run is delivering this batch"""

class NothingToResume(Exception):
    """The batch has no queued email that can still be delivered."""

class BatchTooOld(Exception):
    """Queued emails are past the resume window."""



# ------ small database steps (each commits, so no transaction is open across an HTTP call) ----

def _store_ids(conn, email_id, **ids):
    assert ids and set(ids) <= _ID_COLUMNS
    columns = sorted(ids)
    conn.execute(
        "UPDATE sent_emails SET " + ", ".join(f"{c} = %s" for c in columns) +
        " WHERE email_id = %s", [*(ids[c] for c in columns), email_id])
    conn.commit()


def _mark(conn, email_id, column):
    assert column in _MARK_COLUMNS
    conn.execute(f"UPDATE sent_emails SET {column} = now() WHERE email_id = %s", (email_id,))
    conn.commit()


def _on_dnc(conn, email):
    hit = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM dnc_list "
        " WHERE email_norm = lower(trim(%s)) AND removed_at IS NULL) AS hit",
        (email,)).fetchone()["hit"]
    conn.commit()
    return hit


def _fail(conn, email_id, detail):
    """Nothing was sent, matches become eligible again for a later send."""
    conn.execute("UPDATE sent_emails SET error_detail = %s "
                 "WHERE email_id = %s AND error_detail IS NULL", (detail, email_id))
    conn.execute("UPDATE send_log SET send_status = 'failed', error_detail = %s, "
                 "status_updated_at = now() WHERE email_id = %s AND send_status = 'queued'",
                 (detail, email_id))
    conn.commit()


def _park(conn, email_id, detail):
    """It may have been sent, send_log rows stay 'queued': still counted as emailed."""
    conn.execute("UPDATE sent_emails SET error_detail = %s "
                 "WHERE email_id = %s AND error_detail is NULL", (detail, email_id))
    conn.commit()


def _finish(conn, email_id, activity_id):
    conn.execute("UPDATE send_log SET send_status = 'sent', sent_at = now(), "
                 "provider_message_id = %s, status_updated_at = now() "
                 "WHERE email_id = %s AND send_status = 'queued'", (activity_id, email_id))
    conn.commit()

# ------ Constant Contact Steps ------

def lookup_contacts(emails, *, cc=campaigns):
    """{address: None | {'contact_id', 'permission'}} for queue_batch (read-only)."""
    return {email: cc.find_contact(email) for email in emails}

def _put_contact_on_list(conn, row, list_id, cc):
    """None when contact is on list, else reason this email shouldn't go."""
    email, contact_id = row["recipient_email"], row["cc_contact_id"]
    if contact_id is not None:
        cc.add_to_list(contact_id, list_id)
        return None
    try:
        contact_id = cc.create_contact(email, render.first_name(row["agent_name"]), list_id)
    except ApiError as exc:
        if exc.status != 409:
            raise
        # It exists already
        found = cc.find_contact(email)
        if found is None:
            return "Constant Contact says the contact exists but it could not be read"
        if found["permission"] == "unsubscribed":
            return "unsubscribed in Constant Contact"
        _store_ids(conn, row["email_id"], cc_contact_id=found["contact_id"])
        cc.add_to_list(found["contact_id"], list_id)
        return None
    _store_ids(conn, row["email_id"], cc_contact_id=contact_id)
    return None

def _drop_list(conn, email_id, list_id, cc):
    """Housekeeping, a list left behind is clutter, not a failed email."""
    try:
        cc.delete_list(list_id)
    except ApiError:
        logger.warning("event=cc_list_not_deleted email_id=%s", email_id)
        return
    _mark(conn, email_id, "list_deleted_at")


def deliver_email(conn, email_id, *, sender, cc=campaigns):
    """One queued email through Constant Contact, return the outcome."""
    row = conn.execute(_EMAIL_SQL, (email_id,)).fetchone()
    conn.commit()
    if row is None:
        raise ValueError(f"no sent_emails row {email_id}")
    if row["error_detail"] is not None or row["queued"] == 0:
        return "skipped"

    to = row["recipient_email"]
    name = f"hail-{str(row['batch_id'])[:8]}-{email_id}"
    fields = cc.campaign_fields(subject=row["subject"], html=row["html_body"], **sender)
    resuming = row["cc_activity_id"] is not None
    past_the_point = False              # True once the campaign may have been scheduled
    try:
        if _on_dnc(conn, to):
            _fail(conn, email_id, "on the DNC list at send time")
            return "failed"

        if row["cc_campaign_id"] is None:
            list_id = row["cc_list_id"]
            if list_id is None:
                list_id = cc.create_list(name)
                _store_ids(conn, email_id, cc_list_id=list_id)
            problem = _put_contact_on_list(conn, row, list_id, cc)
            if problem:
                _fail(conn, email_id, problem)
                return "failed"
            made = cc.create_campaign(name, fields)
            activity_id = made["activity_id"]
            # Stored before scheduling, activity id is on disk.
            _store_ids(conn, email_id, cc_campaign_id=made["campaign_id"],
                       cc_activity_id=activity_id)
        else:
            list_id, activity_id = row["cc_list_id"], row["cc_activity_id"]

        if row["scheduled_at"] is None:
            cc.update_campaign(activity_id, fields, list_id)
            # A resume can't know whether the schedule call went through before a crash.
            already_scheduled = resuming and cc.campaign_status(activity_id) != "DRAFT"
            if not already_scheduled:
                if _on_dnc(conn, to):           # last look before the call, can't be undone
                    _fail(conn, email_id, "on the DNC list at send time")
                    return "failed"
                try:
                    cc.schedule(activity_id)
                except ApiError as exc:
                    past_the_point = cc.is_unknown_outcome(exc)
                    raise
            past_the_point = True
            _mark(conn, email_id, "scheduled_at")
        else:
            past_the_point = True
        cc.wait_until_done(activity_id)
        _finish(conn, email_id, activity_id)
        _drop_list(conn, email_id, list_id, cc)
        return "sent"
    except ApiError as exc:
        if past_the_point:
            _park(conn, email_id, "needs review: scheduled or possibly scheduled, "
                                  f"not confirmed done ({exc})")
            outcome = "needs_review"
        else:
            extra = (" (outcome unknown; a stray draft may exist in Constant Contact)"
                    if cc.is_unknown_outcome(exc) else "")
            _fail(conn, email_id, f"{exc}{extra}")
            outcome = "failed"
        if cc.stops_batch(exc):
            raise BatchStopped(str(exc)) from exc
        return outcome

# ------ A Whole Batch ------

def _deliver_all(conn, batch_id, sender, cc, max_emails):
    ids = [r["email_id"] for r in conn.execute(
        "SELECT e.email_id FROM sent_emails e WHERE e.batch_id = %s AND e.error_detail IS NULL "
        "AND EXISTS (SELECT 1 FROM send_log s "
        "WHERE s.email_id = e.email_id AND s.send_status = 'queued') ORDER BY e.email_id",
        (batch_id,)).fetchall()]
    conn.commit()
    counts = {"sent": 0, "failed": 0, "needs_review": 0, "skipped": 0,
             "remaining": 0, "stopped": False, "budget_stop": False}
    for n, email_id in enumerate(ids):
        if max_emails is not None and n >= max_emails:
            counts["remaining"] = len(ids) - n
            break
        if budget.exhausted():
            counts["budget_stop"] = True
            counts["remaining"] = len(ids) - n
            break
        try:
            counts[deliver_email(conn, email_id, sender=sender, cc=cc)] += 1
        except BatchStopped:
            counts["stopped"] = True
            counts["remaining"] = len(ids) - n - 1
            break
    return counts

def deliver_batch(batch_id, *, sender, cc=campaigns, max_emails=None):
    """Deliver the queued emails of one batch, at most max_emails of them (the rest stay
    queued for an explicit later run: the daily call budget, item 180).  One run per batch at
    a time, under a session advisory lock."""
    key = f"deliver:{batch_id}"
    with get_connection() as conn:
        got = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS got",
                           (key,)).fetchone()["got"]
        conn.commit()
        if not got:
            raise BatchBusy("This batch is already being delivered.")
        try:
            return _deliver_all(conn, batch_id, sender, cc, max_emails)
        finally:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
            conn.commit()

# ------ The Whole Click ------

def run_send(*, user_id, storm_days, email_settings, sender, allowed_emails,
             override_cap=False, max_emails=None, max_age_days=sendlist.MAX_AGE_DAYS,
             cap_days=sendlist.CAP_DAYS, freshness_days=sendlist.FRESH_DAYS,
             template_name=templatestore.TEMPLATE_NAME, campaign=None, zone=None,
             sync=unsubs.run_sync, cc=campaigns, now=None):
    """One send click.  Sync unsubscribes, look up recipients, queue, then deliver.

    Failed sync raises before anything is written or sent.  Returns the queue summary with a
    'delivery' entry (counts) when anything was queued.  campaign, from dripstart.parse_plan,
    makes this send step 1 of a drip campaign.  It is created in the queue transaction, so if it
    cannot be made nothing is queued.
    """
    if zone is not None and (override_cap or campaign is not None):
        raise send.SendError("A send from the hail zones takes no cap override and no campaign.")
    result = sync(triggered_by=user_id)
    now = now or datetime.now(timezone.utc)
    allowed = {e.strip().lower() for e in allowed_emails}
    tuning = dict(max_age_days=max_age_days, freshness_days=freshness_days,
                  cap_days=None if override_cap else cap_days)

    with get_connection() as conn:
        found = send.build_list(conn, storm_days, now=now, zone=zone, **tuning)
        conn.commit()
    candidates = sorted({r["email"] for r in found["realtors"]} & allowed)
    contacts = lookup_contacts(candidates, cc=cc)

    with get_connection() as conn:
        summary = send.queue_batch(
            conn, user_id=user_id, storm_days=storm_days, email_settings=email_settings,
            now=now, sync_run_id=result["run_id"], allowed_emails=allowed,
            contacts=contacts, override_cap=override_cap, max_age_days=max_age_days,
            cap_days=cap_days, freshness_days=freshness_days, template_name=template_name,
            zone=zone)
        if campaign is not None and summary["batch_id"]:
            summary["campaign_id"] = dripstart.create_campaign(
                conn, user_id=user_id, batch_id=summary["batch_id"], **campaign)
        conn.commit()           # Queued rows are on disk before anything is sent
    if summary["batch_id"]:
        summary["delivery"] = deliver_batch(summary["batch_id"], sender=sender, cc=cc,
                                            max_emails=max_emails)
    return summary

# ------ Resume -----------------------

# Same test as _deliver_all, queued rows and no error recorded.  needs_review emails
# (error_detail set) are never picked up.

_OLDEST_QUEUED_SQL = """
SELECT min(e.created_at) AS oldest
FROM sent_emails e
WHERE e.batch_id = %s AND e.error_detail IS NULL
    AND EXISTS (SELECT 1 FROM send_log s
        WHERE s.email_id = e.email_id AND s.send_status = 'queued')
"""

def check_resumable(batch_id, *, now=None):
    """Raise NothingToResume or BatchTooOld, else return.  Read-Only.
    
    Created as its own function so the web route can refuse before a thread
    starts.
    """
    now = now or datetime.now(timezone.utc)
    with get_connection() as conn:
        oldest = conn.execute(_OLDEST_QUEUED_SQL, (batch_id,)).fetchone()["oldest"]
        conn.commit()
    if oldest is None:
        raise NothingToResume("This batch has no queued emails to deliver.")
    if now - oldest > timedelta(days=RESUME_MAX_DAYS):
        raise BatchTooOld(f"The queued emails date from {oldest:%Y-%m-%d}, more than "
                          f"{RESUME_MAX_DAYS} days ago.  They are too old to resume.")

def run_resume(*, user_id, batch_id, sender, max_emails=None,
               sync=unsubs.run_sync, cc=campaigns, now=None):
    """Finish queued emails of an earlier batch, on user click.
    
    Refuses a batch whose oldest queued email is more than RESUME_MAX_DAYS old.
    Unsubscribe sync runs first, someone who unsubscribed since the batch was
    queued has to reach the dnc_list before the per-email check.  A failed sync
    raises before anything is sent.  Returns the delivery counts.
    """
    check_resumable(batch_id, now=now)
    sync(triggered_by=user_id)
    return deliver_batch(batch_id, sender=sender, cc=cc, max_emails=max_emails)


# ------ Discard ------------

# An email is 'untouched' when nothing has been recorded for it in Constant Contact.  It
# doesn't have a list, campaign, activity, schedule, or error.  An in_progress email is 
# left alone, to determine next steps resume queries Constant Contact.

_UNTOUCHED_SQL = """
e.batch_id = %s AND e.error_detail IS NULL
AND e.cc_list_id IS NULL AND e.cc_campaign_id IS NULL AND e.cc_activity_id IS NULL
AND e.scheduled_at IS NULL AND e.list_deleted_at IS NULL
AND EXISTS(SELECT 1 FROM send_log s
    WHERE s.email_id = e.email_id AND s.send_status = 'queued')
"""

def count_untouched(batch_id):
    """How many queued emails of this batch discard_queued would discard."""
    with get_connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM sent_emails e WHERE" + _UNTOUCHED_SQL,
                        (batch_id,)).fetchone()["n"]
        conn.commit()
        return n

def discard_queued(batch_id, *, user_id, now=None):
    """Give up on untouched emails of a batch, on user click.  Returns the count.
    
    send_log rows become 'failed', so matches are eligible for a later send, same as 
    _fail.  A row is either discarded whole or not at all.  The WHERE repeats the 
    untouched test so an email that started is not discarded.  Raises BatchBusy while
    the batch is being delivered."""
    now = now or datetime.now(timezone.utc)
    detail = f"Discarded by user {user_id} on {now:%Y-%m-%d}"
    key = f"deliver:{batch_id}"
    with get_connection() as conn:
        got = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS got",
                           (key,)).fetchone()["got"]
        conn.commit()
        if not got:
            raise BatchBusy("This batch is being delivered, try again when it has finished.")
        try:
            n = conn.execute(
                "WITH hit AS (UPDATE sent_emails e SET error_detail = %s WHERE "
                + _UNTOUCHED_SQL + "RETURNING e.email_id), "
                "logged AS (UPDATE send_log SET send_status = 'failed', error_detail = %s, "
                "status_updated_at = now() WHERE send_status = 'queued' "
                "AND email_id IN (SELECT email_id FROM hit) RETURNING 1) "
                "SELECT (SELECT count(*) FROM hit) AS n",
                (detail, batch_id, detail)).fetchone()["n"]
            conn.commit()
            return n
        finally:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
            conn.commit()
