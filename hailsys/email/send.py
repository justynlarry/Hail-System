"""Phase A of a send:  from selected storm days to queued rows.

queue_batch() turns the selected Denver storm days into one rendered email per realtor and writes
it.  A sent_emails row, one send_log row per match, status 'queued'.  It calls nobody and
sends nothing, and doesn't commit.  Caller commits after it returns and rolls back if it 
raises.  Everything after the commit (DNC re-check, Constant Contact) is Phase B.

Guards:
* Unsubscribe sync must have succeeded just before.  Sync itself is the caller's job, a 
  failed sync stops the send before anything is written.
* allowed_emails is required.
* contacts must hold the Constant Contact state of every recipient, previously looked up.
  None = not in Constant Contact, ad dict = already there, an address that's 'unsubscribed'
  is skipped.
"""


import uuid
from datetime import timedelta

from hailsys.email import render, templatestore
from hailsys.queries import sendlist

SYNC_MAX_AGE = timedelta(minutes=15)


class SendError(Exception):
    """A reason the batch can't be queued.  Message is safe to display"""


def _check_syn(conn, run_id, now):
    row = conn.execute("SELECT status, finished_at FROM cc_sync_runs WHERE run_id = %s",
                       (run_id,)).fetchone()
    if (row is None or row["status"] != "ok" or row["finished_at"] is None
            or now - row["finished_at"] > SYNC_MAX_AGE):
        raise SendError("The unsubscribe sync did not succeed just before this send.")


def queue_batch(conn, *, user_id, storm_days, email_settings, now, sync_run_id,
                allowed_emails, contacts, override_cap=False,
                max_age_days=sendlist.MAX_AGE_DAYS, cap_days=sendlist.CAP_DAYS,
                freshness_days=sendlist.FRESH_DAYS):
    """Queue one email per eligible realtor.  Returns what was queued and what was left out."""
    _check_syn(conn, sync_run_id, now)
    found = sendlist.build_send_list(
        conn, storm_days, now=now, max_age_days=max_age_days,
        cap_days=None if override_cap else cap_days, freshness_days=freshness_days)

    allowed = {e.strip().lower() for e in allowed_emails}
    skipped = {"not_allowed": 0, "unsubscribed_in_cc": 0, "unrenderable": []}
    subject_src, body_src = templatestore.load_files()

    plan = []
    for r in found["realtors"]:
        if r["email"] not in allowed:
            skipped["not_allowed"] +=1
            continue
        if r["email"] not in contacts:
            raise SendError("A recipient was not looked up in Constant Contact first.")
        contact = contacts[r["email"]]
        if contact is not None and contact["permission"] == "unsubscribed":
            skipped["unsubscribed_in_cc"] += 1
            continue
        try:
            context = render.build_context(r["agent_name"], r["events"], email_settings)
            subject, html = render.render(subject_src, body_src, context)
        except render.RenderError:
            skipped["unrenderable"].append(r["realtor_id"])     # ids only, no text
            continue
        plan.append((r, contact, subject, html))

    summary = {"batch_id": None, "email_ids": [], "emails": 0, "matches": 0, 
               "considered": found["considered"], "excluded": found["excluded"],
               "skipped": skipped}
    if not plan:
        return summary

    template_id = templatestore.ensure_current(conn, created_by=user_id)
    batch_id = uuid.uuid4()
    for r, contact, subject, html in plan:
        email_id = conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
            "sync_run_id, permission_asserted, created_by, subject, html_body, cc_contact_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING email_id",
            (batch_id, r["realtor_id"], r["email"], template_id, sync_run_id,
             "none" if contact else "implicit", user_id, subject, html,
             contact["contact_id"] if contact else None)).fetchone()["email_id"]
        rows = [(r["realtor_id"], r["email"], match_id, template_id, user_id, email_id)
                for event in r["events"] for match_id in event["match_ids"]]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, "
                "sent_by, email_id) VALUES (%s, %s, %s, %s, %s, %s)", rows)
        summary["email_ids"].append(email_id)
        summary["matches"] += len(rows)
    summary["batch_id"] = str(batch_id)
    summary["emails"] = len(plan)
    return summary