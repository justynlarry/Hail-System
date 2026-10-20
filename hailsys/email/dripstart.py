"""Starting a drip campaign:  rows that hang off a step-1 send.

parse_plan() reads the follow-up rows of the confirm form and checks them against the admin
limits.  create_campaign() writes the campaign, its steps and its recipients.  The caller
(deliver.run_send) runs it inside the queue transaction, after queue_batch and before the commit,
so a campaign that cannot be made also stops the send:  nothing is queued.  The database holds the
rules (sql/046);  these checks are here so a person reads a sentence, not a constraint name.
"""

NAME_MAX = 100

_CAMPAIGN_SQL = """
INSERT INTO drip_campaigns (name, first_batch_id, created_by)
VALUES (%(name)s, %(batch_id)s::uuid, %(user_id)s)
RETURNING campaign_id
"""

_STEP_SQL = """
INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
VALUES (%(campaign_id)s, %(step_no)s, %(gap_days)s, %(template_name)s)
"""

_RECIPIENTS_SQL = """
INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email)
SELECT %(campaign_id)s, realtor_id, recipient_email FROM sent_emails
WHERE batch_id = %(batch_id)s::uuid
"""


class CampaignError(Exception):
    """The campaign cannot be made as requested."""


def parse_plan(data, *, max_followups, max_gap_days, template_ok):
    """None when box is not ticked, else {'name', 'steps': [{'gap_days', 'template_name'}]}.

    data is the posted form.  Rows are follow-up 1, 2, ... and are used in order up to the 
    first empty gap.  A filled row after an empty row is refused.
    """
    if not data.get("campaign"):
        return None
    if max_followups < 1:
        raise CampaignError("Campaigns are turned off in the admin settings.")
    name = (data.get("campaign_name") or "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise CampaignError(f"Give the campaign a name of 1 to {NAME_MAX} characters.")

    steps, ended = [], False
    for n in range(1, max_followups + 1):
        text = (data.get(f"gap_{n}") or "").strip()
        if not text:
            ended = True
            continue
        if ended:
            raise CampaignError(f"Fill the follow-ups in order:  follow-up {n} has a gap "
                                "but an earlier one does not.")
        # isascii first:  str.isdigit() is true for characters int() refuses.
        gap = int(text) if text.isascii() and text.isdigit() else None
        if gap is None or not 1 <= gap <= max_gap_days:
            raise CampaignError(f"The gap for follow-up {n} must be a whole number of days "
                                f"from 1 to {max_gap_days}.")
        template_name = (data.get(f"template_{n}") or "").strip()
        if not template_name or not template_ok(template_name):
            raise CampaignError(f"The template for follow-up {n} is not available.")
        steps.append({"gap_days": gap, "template_name": template_name})
    if not steps:
        raise CampaignError("Enter a gap for at least one follow-up.")
    return {"name": name, "steps": steps}


def create_campaign(conn, *, user_id, batch_id, name, steps):
    """Write the campaign for a step-1 batch queued on this connection.  Returns id.  The caller
    commits.  Everyone emailed in the batch is a recipient, a recipient whose email later fails
    is left out of the follow-ups by the follow-up builder.
    """
    campaign_id = conn.execute(_CAMPAIGN_SQL, {"name": name, "batch_id": batch_id,
                                               "user_id": user_id}).fetchone()["campaign_id"]
    for step_no, step in enumerate(steps, 1):
        conn.execute(_STEP_SQL, {"campaign_id": campaign_id, "step_no": step_no,
                                 "gap_days": step["gap_days"],
                                 "template_name": step["template_name"]})
    conn.execute(_RECIPIENTS_SQL, {"campaign_id": campaign_id, "batch_id": batch_id})
    return campaign_id