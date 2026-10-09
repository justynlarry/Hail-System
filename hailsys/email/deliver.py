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
from datetime import datetime, timezone

from hailsys.constantcontact import campaigns, unsubs
from hailsys.constantcontact.api import ApiError
from hailsys.db import get_connection
from hailsys.email import render, send
from hailsys.queries import sendlist

logger = logging.getLogger(__name__)

# Column names are interpolated into SQL, only these are passed.
_ID_COLUMNS = frozenset({"cc_contact_id", "cc_list_id", "cc_campaign_id", "cc_activity_id"})
_MARK_COLUMNS = frozenset({"scheduled_at", "list_deleted_at"})

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
             "remaining": 0, "stopped": False}
    for n, email_id in enumerate(ids):
        if max_emails is not None and n >= max_emails:
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
             sync=unsubs.run_sync, cc=campaigns, now=None):
    """One send click.  Sync unsubscribes, look up recipients, queue, then deliver.

    Failed sync raises before anything is written or sent.  Returns the queue summary with a
    'delivery' entry (counts) when anything was queued.
    """
    result = sync(triggered_by=user_id)
    now = now or datetime.now(timezone.utc)
    allowed = {e.strip().lower() for e in allowed_emails}
    tuning = dict(max_age_days=max_age_days, freshness_days=freshness_days,
                  cap_days=None if override_cap else cap_days)

    with get_connection() as conn:
        found = sendlist.build_send_list(conn, storm_days, now=now, **tuning)
        conn.commit()
    candidates = sorted({r["email"] for r in found["realtors"]} & allowed)
    contacts = lookup_contacts(candidates, cc=cc)

    with get_connection() as conn:
        summary = send.queue_batch(
            conn, user_id=user_id, storm_days=storm_days, email_settings=email_settings,
            now=now, sync_run_id=result["run_id"], allowed_emails=allowed,
            contacts=contacts, override_cap=override_cap, max_age_days=max_age_days,
            cap_days=cap_days, freshness_days=freshness_days)
        conn.commit()           # Queued rows are on disk before anything is sent
    if summary["batch_id"]:
        summary["delivery"] = deliver_batch(summary["batch_id"], sender=sender, cc=cc,
                                            max_emails=max_emails)
    return summary