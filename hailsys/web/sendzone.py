"""The hail-zones mode of the send page.  Admin only, via send.py

Read only.  It builds the zone send list, counts it by level and basis,
and renders a sample.  It doesn't write anything or import the send engine.
The set in use is read once and carried through the review form, so a send uses
the set that was previewed.
"""

from datetime import datetime, timezone

from flask import abort, flash, g, render_template, request

from hailsys.db import get_connection
from hailsys.email import render, templatestore, zonecontext
from hailsys.queries import sendlist, templatelist, zonesend
from hailsys.tuning import DISPLAY_TZ
from hailsys.web import sendjobs

LEVEL_LABELS = {"agree": "Agree", "one_source": "One source"}
BASIS_LABELS = {"radar": "Radar", "observer": "Observer report", "both": "Radar and observer"}
BLOCKED_LABEL = "Observer wording is not approved yet (admin page, Hail zones)"
SAMPLE_AGENT = "Pat Example"
SAMPLE_EVENTS = [{"listing_id": 0, "address": "123 Example St, Denver, CO 80202",
                  "utc_datetime": datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc),
                  "bases": ["radar"]}]


def parse_levels(values):
    """(levels, problem):  ticked levels in zonesend's order.  Context, or anything else
    not offered, is refused rather than dropped."""
    picked = set(values)
    if picked - set(zonesend.LEVELS):
        return [], "Only Agree and One source can be sent."
    if not picked:
        return [], "Tick at least one level."
    return [level for level in zonesend.LEVELS if level in picked], None


def days_from(values):
    days = set()
    for raw in values:
        try:
            days.add(datetime.strptime(raw, "%Y-%m-%d").date())
        except ValueError:
            abort(400)
    return sorted(days)


def zone_template_names(conn):
    """The zone-kind templates:  the built-in first (from its files until a send saves it),
    then any other saved one."""
    names = [templatestore.ZONE_TEMPLATE_NAME]
    names += [r["template_name"] for r in templatelist.fetch_all(conn, kind="zone")
              if r["template_name"] != templatestore.ZONE_TEMPLATE_NAME]
    return names


def counts(found):
    """Matches on the list by level and by basis."""
    out = {"level": {}, "basis": {}}
    for r in (found or {}).get("realtors", []):
        for e in r["events"]:
            for m in e["matches"]:
                out["level"][m["level"]] = out["level"].get(m["level"], 0) + 1
                out["basis"][m["basis"]] = out["basis"].get(m["basis"], 0) + 1
    return out


def sample(found, current, subject_src, body_src):
    """{'subject', 'html', 'source', 'problem'}: the first realtor on the list, or stand-in
    data when nothing is listed.  Never raises."""
    try:
        email_settings, _ = sendjobs.load_config()
        if found and found["realtors"]:
            first = found["realtors"][0]
            agent, events = first["agent_name"], first["events"]
            source = "the first realtor on the list"
        else:
            agent, events, source = SAMPLE_AGENT, SAMPLE_EVENTS, "stand-in data"
        context = zonecontext.build_context(agent, events, email_settings, current)
        subject, html = render.render(subject_src, body_src, context, kind="zone")
    except (render.SettingsError, render.RenderError) as exc:
        return {"subject": None, "html": None, "source": None, "problem": str(exc)}
    return {"subject": subject, "html": html, "source": source, "problem": None}


def zone_index(reason_labels, default_max_emails, max_emails_limit):
    """GET /send?mode=zones"""
    now = datetime.now(timezone.utc)
    s = g.settings
    days = days_from(request.args.getlist("day"))
    # The form was sent:  take its ticks as they are, even none.
    if "levels_shown" in request.args:
        levels, problem = parse_levels(request.args.getlist("level"))
        if problem:
            flash(problem)
    else:
        levels = list(zonesend.DEFAULT_LEVELS)
    with get_connection() as conn:
        current = zonesend.current_set(conn)
        names = zone_template_names(conn)
        chosen = request.args.get("template")
        if chosen not in names:
            chosen = templatestore.ZONE_TEMPLATE_NAME
        subject_src, body_src, _ = templatestore.load(conn, chosen)
        available = zonesend.fetch_zone_send_days(
            conn, today=now.astimezone(DISPLAY_TZ).date(), set_id=current["set_id"],
            max_age_days=s["match_max_age_days"])
        found = None
        if days and levels:
            found = zonesend.build_zone_send_list(
                conn, days, now=now, set_id=current["set_id"], levels=levels,
                max_age_days=s["match_max_age_days"], cap_days=s["email_cap_days"],
                freshness_days=s["listing_freshness_days"])
        conn.rollback()
    reasons = [(r, reason_labels[r]) for r in sendlist.REASONS]
    reasons.append((zonesend.BLOCKED, BLOCKED_LABEL))
    listing_count = len({e["listing_id"] for r in (found or {}).get("realtors", [])
                         for e in r["events"]})

    return render_template(
        "send_zone.html", available=available, ticked=set(days), levels=levels,
        level_labels=LEVEL_LABELS, basis_labels=BASIS_LABELS, current=current,
        found=found, counts=counts(found), reasons=reasons, listing_count=listing_count,
        templates=names, chosen=chosen, sample=sample(found, current, subject_src, body_src),
        budget=sendjobs.calls_budget(), busy=sendjobs.is_busy(),
        default_max_emails=default_max_emails, max_emails_limit=max_emails_limit,
        display_tz=DISPLAY_TZ)
