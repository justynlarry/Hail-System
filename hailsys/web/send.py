"""Send Screen.  Admin Only 10/8/2026.

Preview and confirm pages are read only:  they don't write anything and don't import
the send engine.  sendjobs.start_send() is the only door to the engine, reached
by the Send-now POST
"""

import re
from datetime import datetime, timezone

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, session,
                   url_for)

from hailsys.db import get_connection
from hailsys.email import render, templatestore
from hailsys.queries import sendlist
from hailsys.tuning import DISPLAY_TZ
from hailsys.web import sendjobs
from hailsys.web.auth import require_role



send_bp = Blueprint("send", __name__, url_prefix="/send")

DEFAULT_MAX_EMAILS = 5
MAX_EMAILS_LIMIT = 1000
CALLS_PER_EMAIL = 10
_ADDRESS = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")



# What a person reads for each rule in sendlist.REASONS (a test keeps the two in step).
REASON_LABELS = {
    "too_old": "Storm is older than the age limit",
    "inactive_listing": "Listing is no longer active",
    "stale_listing": "RentCast has not seen the listing recently (pull again)",
    "no_agent_email": "No agent email on the listing",
    "already_emailed": "Already emailed for this match",
    "on_dnc_list": "Address is on the do-not-contact list",
    "within_cap": "Emailed within the cap window",
}


@send_bp.before_request
def _admin_only():
    return require_role("admin")


def _days_from_args():
    days = set()
    for raw in request.args.getlist("day"):
        try:
            days.add(datetime.strptime(raw, "%Y-%m-%d").date())
        except ValueError:
            abort(400)
    return sorted(days)


def _sample(realtor):
    """(subject, html, problem) for one realtor's email.  Never raises: a page that cannot show
    the sample should say why, not fail."""
    try:
        email_settings = render.EmailSettings.from_env()
        context = render.build_context(realtor["agent_name"], realtor["events"], email_settings)
        subject_src, body_src = templatestore.load_files()
        subject, html = render.render(subject_src, body_src, context)
    except (render.SettingsError, render.RenderError) as exc:
        return None, None, str(exc)
    return subject, html, None


@send_bp.route("/")
def index():
    now = datetime.now(timezone.utc)
    s = g.settings
    tuning = dict(max_age_days=s["match_max_age_days"],
                  freshness_days=s["listing_freshness_days"])
    days = _days_from_args()
    with get_connection() as conn:
        available = sendlist.fetch_send_days(
            conn, today=now.astimezone(DISPLAY_TZ).date(), max_age_days=tuning["max_age_days"])
        found = None
        if days:
            found = sendlist.build_send_list(
                conn, days, now=now, cap_days=s["email_cap_days"], **tuning)
        conn.rollback()                     # read only: nothing to keep

    listing_count = 0
    sample_subject = sample_html = sample_problem = None
    if found and found["realtors"]:
        listing_count = len({e["listing_id"] for r in found["realtors"] for e in r["events"]})
        sample_subject, sample_html, sample_problem = _sample(found["realtors"][0])
    return render_template(
        "send.html", available=available, ticked=set(days), found=found,
        reasons=[(r, REASON_LABELS[r]) for r in sendlist.REASONS],
        listing_count=listing_count, sample_subject=sample_subject,
        sample_html=sample_html, sample_problem=sample_problem,
        default_max_emails=DEFAULT_MAX_EMAILS, max_emails_limit=MAX_EMAILS_LIMIT)


def parse_allow_list(text):
    """(emails, bad): the unique lower-cased addresses in order, and the pieces that
    are not addresses, split on whitespace, commas, and semicolons."""
    emails, bad = [], []
    for piece in re.split(r"[\s,;]+", text or ""):
        piece = piece.strip().lower()
        if not piece:
            continue
        if _ADDRESS.fullmatch(piece):
            if piece not in emails:
                emails.append(piece)
        else:
            bad.append(piece)
    return emails, bad


def _back(days):
    return redirect(url_for("send.index", day=[d.isoformat() for d in days]))

def _read_form():
    """(form, problem:  review/start form)"""
    days = set()
    for raw in request.form.getlist("day"):
        try: 
            days.add(datetime.strptime(raw, "%Y-%m-%d").date())
        except ValueError:
            abort(400)
    allowed, bad = parse_allow_list(request.form.get("allowed"))
    try:
        max_emails = int(request.form.get("max_emails") or "")
    except ValueError:
        max_emails = 0
    form = {"days": sorted(days), "allowed": allowed, "max_emails": max_emails,
            "override_cap": "override_cap" in request.form}
    if not days:
        problem = "Tick at least one storm day."
    elif bad:
        problem = "These are not email addresses: " + ", ".join(b[:40] for b in bad[:5])
    elif not allowed:
        problem = ("Enter the addresses this send may go to. "
                   "There is no send-to-everyone option.")
    elif not 1 <= max_emails <= MAX_EMAILS_LIMIT:
        problem = f"'Send at most' must be between 1 and {MAX_EMAILS_LIMIT}."
    else:
        problem = None
    return form, problem


def _plan(form, now):
    """(found, mine, unmatched): send list, the realtors on it that the allow-list name,
    and the allow-listed addresses that are not on the list."""
    s = g.settings
    with get_connection() as conn:
        found = sendlist.build_send_list(
            conn, form["days"], now=now,
            cap_days=None if form["override_cap"] else s["email_cap_days"],
            max_age_days=s["match_max_age_days"], freshness_days=s["listing_freshness_days"])
        conn.rollback()
    allowed = set(form["allowed"])
    mine = [r for r in found["realtors"] if r["email"] in allowed]
    unmatched = sorted(allowed - {r["email"] for r in found["realtors"]})
    return found, mine, unmatched


@send_bp.route("/confirm", methods=["POST"])
def confirm():
    form, problem = _read_form()
    if problem:
        flash(problem)
        return _back(form["days"])
    try:
        _, sender = sendjobs.load_config()
    except render.SettingsError as exc:
        flash(f"Sending is not configured: {exc}")
        return _back(form["days"])
    _, mine, unmatched = _plan(form, datetime.now(timezone.utc))
    if not mine:
        flash("None of those addresses is on the send list for the selected days, "
              "so there is nothing to send.")
        return _back(form["days"])
    going = min(len(mine), form["max_emails"])
    return render_template(
        "send_confirm.html", form=form, mine=mine, unmatched=unmatched, sender=sender,
        going=going, calls=going * CALLS_PER_EMAIL, busy=sendjobs.is_busy())


@send_bp.route("/start", methods=["POST"])
def start():
    form, problem = _read_form()
    if problem:
        flash(problem)
        return _back(form["days"])
    try:
        expected = int(request.form["expected"])
    except (KeyError, ValueError):
        abort(400)
    try:
        email_settings, sender = sendjobs.load_config()
    except render.SettingsError as exc:
        flash(f"Sending is not configured: {exc}")
        return _back(form["days"])
    _, mine, _ = _plan(form, datetime.now(timezone.utc))
    if len(mine) != expected:
        flash(f"The list changed since you reviewed it ({expected} to {len(mine)} emails). "
              "Nothing was sent.  Review it again.")
        return _back(form["days"])
    s = g.settings
    try:
        sendjobs.start_send(
            user_id=session["emp_id"], storm_days=form["days"], email_settings=email_settings,
            sender=sender, allowed_emails=form["allowed"], override_cap=form["override_cap"],
            max_emails=form["max_emails"], max_age_days=s["match_max_age_days"],
            cap_days=s["email_cap_days"], freshness_days=s["listing_freshness_days"])
    except sendjobs.SendInProgress:
        flash("A send is already running.  Nothing new was started.")
        return _back(form["days"])
    flash(f"Send started: up to {min(len(mine), form['max_emails'])} of {len(mine)} emails. "
          "It runs in the background.  The result is in the log until the progress view is built.")
    return _back(form["days"])