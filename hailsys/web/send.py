"""The send screen.  Admin only for now.

This is the read-only preview.  Nothing here writes to the database or calls Constant Contact,
and it deliberately does not import the send engine (hailsys.email.deliver): the send itself will
be a separate POST that a person clicks.
"""

from datetime import datetime, timezone

from flask import Blueprint, abort, g, render_template, request

from hailsys.db import get_connection
from hailsys.email import render, templatestore
from hailsys.queries import sendlist
from hailsys.tuning import DISPLAY_TZ
from hailsys.web.auth import require_role

send_bp = Blueprint("send", __name__, url_prefix="/send")

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
        sample_html=sample_html, sample_problem=sample_problem)
