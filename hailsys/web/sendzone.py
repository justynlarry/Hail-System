"""The hail-zones mode of the send page.  Admin only, via send.py

Read only.  It builds the zone send list, counts it by level and basis,
and renders a sample.  It doesn't write anything or import the send engine.
The set in use is read once and carried through the review form, so a send uses
the set that was previewed.
"""

from datetime import datetime, timezone

from flask import abort, flash, g, redirect, render_template, request, session, url_for

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


# ------ Review and Start --------------------------------------------------------------------------

def read_form(parse_allow_list, max_emails_limit):
    """(form, problem) for the zone review and start POSTs.  Every value is checked again here.
    The set id is the one the preview showed, and plan() checks it against the database.
    """
    days = days_from(request.form.getlist("day"))
    levels, level_problem = parse_levels(request.form.getlist("level"))
    allowed, bad = parse_allow_list(request.form.get("allowed"))
    try:
        max_emails = int(request.form.get("max_emails") or "")
    except ValueError:
        max_emails = 0
    try:
        set_id = int(request.form.get("set_id") or "")
    except ValueError:
        abort(400)
    form = {"days": days, "levels": levels, "set_id": set_id, "allowed": allowed,
            "max_emails": max_emails,
            "template": request.form.get("template") or templatestore.ZONE_TEMPLATE_NAME}
    if not days:
        problem = "Tick at least one storm day."
    elif level_problem:
        problem = level_problem
    elif bad:
        problem = "These are not email addresses: " + ", ".join(b[:40] for b in bad[:5])
    elif not allowed:
        problem = ("Enter the addresses this send may go to. "
                   "There is no send-to-everyone option.")
    elif not 1 <= max_emails <= max_emails_limit:
        problem = f"'Send at most' must be between 1 and {max_emails_limit}."
    else:
        problem = None
    return form, problem


def back(form):
    """To the zone preview, with what was chosen."""
    return redirect(url_for("send.index", mode="zones", levels_shown=1,
                            day=[d.isoformat() for d in form["days"]],
                            level=form["levels"], template=form["template"]))


def plan(form, now):
    """(found, mine, unmatched, problem).  The list is rebuilt for the set the preview
    showed.  A newer set, or a template that is not a zone template, is a problem, and
    nothing is built."""
    s = g.settings
    with get_connection() as conn:
        current = zonesend.current_set(conn)
        names = zone_template_names(conn)
        found = None
        if current["set_id"] == form["set_id"] and form["template"] in names:
            found = zonesend.build_zone_send_list(
                conn, form["days"], now=now, set_id=form["set_id"], levels=form["levels"],
                max_age_days=s["match_max_age_days"], cap_days=s["email_cap_days"],
                freshness_days=s["listing_freshness_days"])
        conn.rollback()
    if current["set_id"] != form["set_id"]:
        return None, [], [], (f"The hail-zone set changed since the preview (set "
                              f"{form['set_id']} to {current['set_id']}).  Nothing was sent. "
                              "Preview again.")
    if found is None:
        return None, [], [], "That template is not a zone template."
    allowed = set(form["allowed"])
    mine = [r for r in found["realtors"] if r["email"] in allowed]
    unmatched = sorted(allowed - {r["email"] for r in found["realtors"]})
    return found, mine, unmatched, None


def zone_confirm(parse_allow_list, max_emails_limit, calls_per_email):
    """POST /send/zones/confirm: the review page.  Writes nothing."""
    form, problem = read_form(parse_allow_list, max_emails_limit)
    if problem:
        flash(problem)
        return back(form)
    try:
        _, sender = sendjobs.load_config()
    except render.SettingsError as exc:
        flash(f"Sending is not configured: {exc}")
        return back(form)
    found, mine, unmatched, problem = plan(form, datetime.now(timezone.utc))
    if problem:
        flash(problem)
        return back(form)
    if not mine:
        flash("None of those addresses is on the zone send list for the selected days, "
              "so there is nothing to send.")
        return back(form)
    going = min(len(mine), form["max_emails"])
    return render_template(
        "send_zone_confirm.html", form=form, mine=mine, unmatched=unmatched,
        sender=sender, going=going, calls=going * calls_per_email,
        budget=sendjobs.calls_budget(), busy=sendjobs.is_busy(), zone_set=found["set"],
        level_labels=LEVEL_LABELS, basis_labels=BASIS_LABELS)


def zone_start(parse_allow_list, max_emails_limit):
    """POST /send/zones/start: rebuild the list, refuse if anything moved, then hand the
    send to sendjobs, the only door to the engine."""
    form, problem = read_form(parse_allow_list, max_emails_limit)
    if problem:
        flash(problem)
        return back(form)
    try:
        expected = int(request.form["expected"])
    except (KeyError, ValueError):
        abort(400)
    try:
        email_settings, sender = sendjobs.load_config()
    except render.SettingsError as exc:
        flash(f"Sending is not configured: {exc}")
        return back(form)
    _, mine, _, problem = plan(form, datetime.now(timezone.utc))
    if problem:
        flash(problem)
        return back(form)
    if len(mine) != expected:
        flash(f"The list changed since you reviewed it ({expected} to {len(mine)} emails). "
              "Nothing was sent.  Review it again.")
        return back(form)
    s = g.settings
    try:
        sendjobs.start_send(
            user_id=session["emp_id"], storm_days=form["days"],
            email_settings=email_settings, sender=sender, allowed_emails=form["allowed"],
            max_emails=form["max_emails"], template_name=form["template"],
            max_age_days=s["match_max_age_days"], cap_days=s["email_cap_days"],
            freshness_days=s["listing_freshness_days"],
            zone={"set_id": form["set_id"], "levels": form["levels"]})
    except sendjobs.SendInProgress:
        flash("A send is already running.  Nothing new was started.")
        return back(form)
    flash(f"Zone send started: up to {min(len(mine), form['max_emails'])} of {len(mine)} "
          "emails.  It runs in the background.  This page shows progress.")
    return redirect(url_for("send.batches"))
