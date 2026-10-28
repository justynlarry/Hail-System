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

from hailsys.email import render, templatestore, zonecontext
from hailsys.queries import sendlist, zonesend

SYNC_MAX_AGE = timedelta(minutes=15)


class SendError(Exception):
    """A reason the batch can't be queued.  Message is safe to display"""


def _check_syn(conn, run_id, now):
    row = conn.execute("SELECT status, finished_at FROM cc_sync_runs WHERE run_id = %s",
                       (run_id,)).fetchone()
    if (row is None or row["status"] != "ok" or row["finished_at"] is None
            or now - row["finished_at"] > SYNC_MAX_AGE):
        raise SendError("The unsubscribe sync did not succeed just before this send.")


def build_list(conn, storm_days, *, now, zone=None, max_age_days, cap_days,
               freshness_days):
    """The send list for the ticked days:  report matches, or the hail zones.

    zone is {'set_id', 'levels'}.  The set must be pinned by the caller:  a send
    never takes whatever set happens to be newest.
    """
    if zone is None:
        return sendlist.build_send_list(
            conn, storm_days, now=now, max_age_days=max_age_days,
            cap_days=cap_days, freshness_days=freshness_days)
    if zone.get("set_id") is None:
        raise SendError("A send from the hail zones needs its parameter set pinned.")
    return zonesend.build_zone_send_list(
        conn, storm_days, now=now, set_id=zone["set_id"], levels=zone["levels"],
        max_age_days=max_age_days, cap_days=cap_days, freshness_days=freshness_days)


def queue_batch(conn, *, user_id, storm_days=None, email_settings, now, sync_run_id,
                allowed_emails, contacts, override_cap=False, send_list=None,
                max_age_days=sendlist.MAX_AGE_DAYS, cap_days=sendlist.CAP_DAYS,
                freshness_days=sendlist.FRESH_DAYS,
                template_name=templatestore.TEMPLATE_NAME, zone=None):
    """Queue one email per eligible realtor.  Returns what was queued and what was left out.

    send_list is a list already built, a drip follow-up (dripfollowup.build_followup_list).  The
    storm days, the cap override and the age, cap and freshness arguments are then not used.

    zone, {'set_id', 'levels'}, makes this a send from the hail zones.  The list comes from zonesend
    for that pinned set, the template must be zone-kind, and each send_log row records its own match's
    basis, level and set.  No cap override and no ready-made list:  a zone send is not a follow-up.
    """
    if zone is not None and (override_cap or send_list is not None):
        raise SendError("A send from the hail zones takes no cap override and no ready list.")
    kind = "report" if zone is None else "zone"
    _check_syn(conn, sync_run_id, now)
    if send_list is not None:
        found = send_list
    elif storm_days is not None:
        found = build_list(
            conn, storm_days, now=now, zone=zone, max_age_days=max_age_days,
            cap_days=None if override_cap else cap_days, freshness_days=freshness_days)
    else:
        raise ValueError("queue_batch needs storm_days or a send_list")

    allowed = {e.strip().lower() for e in allowed_emails}
    skipped = {"not_allowed": 0, "unsubscribed_in_cc": 0, "unrenderable": []}
    try:
        template_kind = templatestore.kind_of(conn, template_name)
        subject_src, body_src, saved_id = templatestore.load(conn, template_name)
    except templatestore.TemplateNotFound:
        raise SendError("That template is not available.") from None
    if template_kind != kind:
        raise SendError(f"That is a {template_kind} template: this send needs a {kind} one.")

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
            if zone is None:
                context = render.build_context(r["agent_name"], r["events"], email_settings)
            else:
                context = zonecontext.build_context(
                    r["agent_name"], r["events"], email_settings, found["set"])
            subject, html = render.render(subject_src, body_src, context, kind=kind)
        except render.RenderError:
            skipped["unrenderable"].append(r["realtor_id"])     # ids only, no text
            continue
        plan.append((r, contact, subject, html))

    summary = {"batch_id": None, "email_ids": [], "emails": 0, "matches": 0, 
               "considered": found["considered"], "excluded": found["excluded"],
               "skipped": skipped}
    if not plan:
        return summary
    # name= : the built-in zone template must be saved under its own name.
    template_id = saved_id or templatestore.ensure_current(
        conn, created_by=user_id, name=template_name, subject_src=subject_src,
        body_src=body_src, match_kind=kind)
    batch_id = uuid.uuid4()
    report_level = {}
    if zone is None:
        # The level at each house, looked up now against the set in use (item 266).  The
        # report send list never reads the zones;  only this snapshot does.
        current = zonesend.current_set(conn)
        report_level = zonesend.report_levels(
            conn, [m for r, *_ in plan for e in r["events"] for m in e["match_ids"]],
            current["set_id"] if current else None)
    for r, contact, subject, html in plan:
        email_id = conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
            "sync_run_id, permission_asserted, created_by, subject, html_body, cc_contact_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING email_id",
            (batch_id, r["realtor_id"], r["email"], template_id, sync_run_id,
             "none" if contact else "implicit", user_id, subject, html,
             contact["contact_id"] if contact else None)).fetchone()["email_id"]
        with conn.cursor() as cur:
            if zone is None:
                rows = [(r["realtor_id"], r["email"], match_id, template_id, user_id,
                         email_id, *report_level.get(match_id, (None, None)))
                        for event in r["events"] for match_id in event["match_ids"]]
                cur.executemany(
                    "INSERT INTO send_log (realtor_id, recipient_email, match_id, "
                    "template_id, sent_by, email_id, level, zone_set_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)
            else:
                # The snapshot is the match's own basis, level and set: sql/060 checks it.
                rows = [(r["realtor_id"], r["email"], m["match_id"], template_id, user_id,
                         email_id, m["basis"], m["level"], m["set_id"])
                        for event in r["events"] for m in event["matches"]]
                cur.executemany(
                    "INSERT INTO send_log (realtor_id, recipient_email, match_id, "
                    "template_id, sent_by, email_id, basis, level, zone_set_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
        summary["email_ids"].append(email_id)
        summary["matches"] += len(rows)
    summary["batch_id"] = str(batch_id)
    summary["emails"] = len(plan)
    return summary