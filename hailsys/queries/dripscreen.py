"""Reads for the drip campaign screens.

Database reads only.  The step preview is what the click does, worked out from the same
functions the click uses (dripfollowup, dripstep.plan_method).

Follow-up N is step_no N.  Send prior to step 1 is the campaign's first batch, so a step's
"previous send" is the latest delivery in the previous step's batch (first batch for step 1).
A step is due the Denver Date of that send plus gap_days, a due step doesn't expire, it just 
gets later.
"""

from datetime import timedelta

from hailsys.queries import dripfollowup
from hailsys.rentcast import dripstep as checks
from hailsys.tuning import DISPLAY_TZ

_CAMPAIGNS_SQL = """
SELECT c.campaign_id, c.name, c.status, c.created_at,
    u.emp_fname || ' ' || u.emp_lname AS created_by_name,
    (SELECT count(*) FROM drip_steps s
      WHERE s.campaign_id = c.campaign_id) AS step_count,
    (SELECT count(*) FROM drip_steps s
      WHERE s.campaign_id = c.campaign_id AND s.batch_id IS NOT NULL) AS steps_sent,
    (SELECT count(*) FROM drip_recipients r
      WHERE r.campaign_id = c.campaign_id) AS recipients,
    (SELECT count(*) FROM drip_recipients r
      WHERE r.campaign_id = c.campaign_id AND r.stopped_at IS NULL) AS still_going
FROM drip_campaigns c
JOIN users u ON u.emp_id = c.created_by
ORDER BY c.created_at DESC, c.campaign_id DESC
"""

_CAMPAIGN_SQL = """
SELECT c.campaign_id, c.name, c.status, c.created_at, c.first_batch_id,
    c.status_changed_at,
    u.emp_fname || ' ' || u.emp_lname AS created_by_name,
    v.emp_fname || ' ' || v.emp_lname AS status_changed_by_name
FROM drip_campaigns c
JOIN users u ON u.emp_id = c.created_by
LEFT JOIN users v ON v.emp_id = c.status_changed_by
WHERE c.campaign_id = %(campaign_id)s
"""

_STEPS_SQL = """
SELECT s.campaign_id, s.step_no, s.gap_days, s.template_name, s.batch_id, s.started_at,
    (SELECT max(l.sent_at)
      FROM sent_emails e
      JOIN send_log l ON l.email_id = e.email_id
    WHERE e.batch_id = CASE WHEN s.step_no = 1 THEN c.first_batch_id ELSE p.batch_id END)
      AS previous_sent_at
FROM drip_steps s
JOIN drip_campaigns c ON c.campaign_id = s.campaign_id
LEFT JOIN drip_steps p ON p.campaign_id = s.campaign_id AND p.step_no = s.step_no - 1
WHERE (%(campaign_id)s::bigint IS NULL OR s.campaign_id = %(campaign_id)s)
ORDER BY s.campaign_id, s.step_no
"""

_RUNS_SQL = """
SELECT r.run_id, r.step_no, r.status, r.started_at, r.finished_at, r.method,
    r.calls_made, r.emails, r.detail,
    u.emp_fname || ' ' || u.emp_lname AS started_by_name
FROM drip_step_runs r
JOIN users u ON u.emp_id = r.started_by
WHERE r.campaign_id = %(campaign_id)s
ORDER BY r.run_id DESC
"""

_RECIPIENTS_SQL = """
SELECT r.realtor_id, ra.agent_name, r.recipient_email, r.stopped_at, r.stop_reason,
    u.emp_fname || ' ' || u.emp_lname AS stopped_by_name
FROM drip_recipients r
JOIN realtors ra ON ra.realtor_id = r.realtor_id
LEFT JOIN users u ON u.emp_id = r.stopped_by
WHERE r.campaign_id = %(campaign_id)s
ORDER BY (r.stopped_at IS NOT NULL), ra.agent_name, r.realtor_id
"""


def with_due(step, today):
    """Step plus 'due' (Denver Date) and 'days_late' (0 if not yet due).  Both are None
    for a step already sent, or one whose previous step has not been delivered.
    """
    out = dict(step, due=None, days_late=None)
    if step["batch_id"] is None and step["previous_sent_at"] is not None:
        sent_on = step["previous_sent_at"].astimezone(DISPLAY_TZ).date()
        out["due"] = sent_on + timedelta(days=step["gap_days"])
        out["days_late"] = max((today - out["due"]).days, 0)
    return out


def _next_step(steps):
    """The first step not yet sent, or None.  Steps go in order, so this is the one to click."""
    return next((s for s in steps if s["batch_id"] is None), None)


def list_campaigns(conn, *, today):
    """Every campaign, newest first, each with 'next_step' (the step to click, with its due date)."""
    campaigns = conn.execute(_CAMPAIGNS_SQL).fetchall()
    by_campaign = {}
    for s in conn.execute(_STEPS_SQL, {"campaign_id": None}).fetchall():
        by_campaign.setdefault(s["campaign_id"], []).append(with_due(s, today))
    return [dict(c, next_step=_next_step(by_campaign.get(c["campaign_id"], [])))
            for c in campaigns]


def due_steps(conn, *, today):
    """The follow-ups ready to click, for the banner on the send page:  the next step of each
    active campaign, if it is due today or earlier, the most overdue first.
    Display only: nothing is started, and a due step never expires.
    """
    rows = [{"campaign_id": c["campaign_id"], "name": c["name"],
             "step_no": c["next_step"]["step_no"], "due": c["next_step"]["due"],
             "days_late": c["next_step"]["days_late"]}
            for c in list_campaigns(conn, today=today)
            if c["status"] == "active" and c["next_step"]
            and c["next_step"]["due"] is not None and c["next_step"]["due"] <= today]
    return sorted(rows, key=lambda r: (-r["days_late"], r["campaign_id"]))


def campaign_detail(conn, campaign_id, *, today):
    """One campaign with its steps (due dates), run history, recipients: None if it does not exist."""
    campaign = conn.execute(_CAMPAIGN_SQL, {"campaign_id": campaign_id}).fetchone()
    if campaign is None:
        return None
    params = {"campaign_id": campaign_id}
    steps = [with_due(s, today) for s in conn.execute(_STEPS_SQL, params).fetchall()]
    return {"campaign": campaign, "steps": steps, "next_step": _next_step(steps),
            "runs": conn.execute(_RUNS_SQL, params).fetchall(),
            "recipients": conn.execute(_RECIPIENTS_SQL, params).fetchall()}


def step_preview(conn, campaign_id, step_no, *, now, cap_days=dripfollowup.CAP_DAYS):
    """What a click on this step would do, from what the database holds now.
    
    'emails_at_most' assumes every listing still to be checked comes back active, so it is an
    upper bound.  Answers from the last 24 hours (which the click reuses) are counted as they are.
    """
    hits = dripfollowup.fetch_hits(conn, campaign_id, now=now, cap_days=cap_days)
    dead = dripfollowup.fetch_dead(conn, campaign_id, step_no)
    listings = dripfollowup.listings_to_check(hits, dead)
    skip = checks.recent_definite(conn, campaign_id, step_no)
    todo = [i for i in listings if i["listing_id"] not in skip]
    zips, plan = checks.plan_method(conn, todo)

    assumed = {i["listing_id"]: "active" for i in listings}
    known = dripfollowup.fetch_verdicts(conn, campaign_id, step_no)
    assumed.update({lid: v for lid, v in known.items() if lid in skip})
    found = dripfollowup.select_followup(hits, dead, assumed)
    return {"listings": len(listings), "reused": len(listings) - len(todo),
            "to_check": len(todo), "plan": plan, "emails_at_most": len(found["realtors"]),
            "will_stop": len(found["to_stop"]), "excluded": found["excluded"],
            "considered": found["considered"]}