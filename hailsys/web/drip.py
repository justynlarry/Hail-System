"""Campaign Screens.  Admin Only.

Pages only read the database, nothing here calls RentCast or Constant Contact
on a page load.  Starting a step is a POST that hands the work to sendjobs.start_drip_step,
and the other POSTS go through dripactions.  Every start is a person's click.
"""

import logging
from datetime import datetime, timezone

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, session,
                   url_for)

from hailsys.db import get_connection
from hailsys.email import dripactions, render
from hailsys.queries import dripscreen, quota
from hailsys.tuning import DISPLAY_TZ, denver_day_bounds
from hailsys.web import sendjobs
from hailsys.web.auth import require_role
from hailsys.web.send import CALLS_PER_EMAIL, DEFAULT_MAX_EMAILS, MAX_EMAILS_LIMIT

logger = logging.getLogger(__name__)

drip_bp = Blueprint("drip", __name__, url_prefix="/campaigns")

MOVED = {"pause": "Campaign paused.", "resume": "Campaign is active again.",
         "cancel": "Campaign cancelled."}


@drip_bp.before_request
def _admin_only():
    return require_role("admin")


def _today():
    return datetime.now(DISPLAY_TZ).date()


def _to_campaign(campaign_id):
    return redirect(url_for("drip.detail", campaign_id=campaign_id))


def _to_step(campaign_id, step_no):
    return redirect(url_for("drip.step", campaign_id=campaign_id, step_no=step_no))


@drip_bp.route("/")
def index():
    with get_connection() as conn:
        rows = dripscreen.list_campaigns(conn, today=_today())
        conn.rollback()
    return render_template("campaigns.html", campaigns=rows, display_tz=DISPLAY_TZ)


@drip_bp.route("/<int:campaign_id>")
def detail(campaign_id):
    with get_connection() as conn:
        found = dripscreen.campaign_detail(conn, campaign_id, today=_today())
        conn.rollback()
    if found is None:
        abort(404)
    return render_template("campaign.html", busy=sendjobs.is_busy(), display_tz=DISPLAY_TZ,
                           reasons=dripactions.REASONS, **found)


@drip_bp.route("/<int:campaign_id>/step/<int:step_no>")
def step(campaign_id, step_no):
    s = g.settings
    with get_connection() as conn:
        found = dripscreen.campaign_detail(conn, campaign_id, today=_today())
        conn.rollback()
    if found is None:
        abort(404)
    row = next((x for x in found["steps"] if x["step_no"] == step_no), None)
    if row is None:
        abort(404)
    problem = sendjobs.step_problem(campaign_id, step_no)
    preview = usage = None
    if problem is None:
        with get_connection() as conn:
            preview = dripscreen.step_preview(
                conn, campaign_id, step_no, now=datetime.now(timezone.utc),
                cap_days=s["email_cap_days"])
            usage = quota.fetch_usage(
                conn, today=_today(), billing_day=s["rentcast_billing_day"],
                quota=s["rentcast_monthly_quota"], day_bounds=denver_day_bounds)
            conn.rollback()
    going = min(preview["emails_at_most"], DEFAULT_MAX_EMAILS) if preview else 0
    return render_template(
        "campaign_step.html", campaign=found["campaign"], step=row, problem=problem,
        preview=preview, usage=usage, busy=sendjobs.is_busy(), budget=sendjobs.calls_budget(),
        default_max=DEFAULT_MAX_EMAILS, max_limit=MAX_EMAILS_LIMIT, going=going,
        calls=going * CALLS_PER_EMAIL, display_tz=DISPLAY_TZ)


@drip_bp.route("/<int:campaign_id>/step/<int:step_no>/start", methods=["POST"])
def start(campaign_id, step_no):
    """The click.  Recomputes the preview and refuses if it no longer matches what was shown."""
    try:
        expected = (int(request.form["expected_checks"]), int(request.form["expected_emails"]))
    except (KeyError, ValueError):
        abort(400)
    try:
        max_emails = int(request.form.get("max_emails") or "")
    except ValueError:
        max_emails = 0
    if not 1 <= max_emails <= MAX_EMAILS_LIMIT:
        flash(f"'Send at most' must be between 1 and {MAX_EMAILS_LIMIT}.")
        return _to_step(campaign_id, step_no)
    try:
        email_settings, sender = sendjobs.load_config()
    except render.SettingsError as exc:
        flash(f"Sending is not configured: {exc}")
        return _to_step(campaign_id, step_no)
    s = g.settings
    with get_connection() as conn:
        preview = dripscreen.step_preview(
            conn, campaign_id, step_no, now=datetime.now(timezone.utc),
            cap_days=s["email_cap_days"])
        conn.rollback()
    if (preview["to_check"], preview["emails_at_most"]) != expected:
        flash("The list changed since you reviewed it. Nothing was started. Review it again.")
        return _to_step(campaign_id, step_no)
    try:
        sendjobs.start_drip_step(
            user_id=session["emp_id"], campaign_id=campaign_id, step_no=step_no,
            email_settings=email_settings, sender=sender, max_emails=max_emails,
            freshness_days=s["listing_freshness_days"], cap_days=s["email_cap_days"])
    except sendjobs.SendInProgress:
        flash("A send is already running.  Nothing new was started.")
        return _to_step(campaign_id, step_no)
    except sendjobs.StepRefused as exc:
        flash(f"{exc} Nothing was started.")
        return _to_step(campaign_id, step_no)
    flash(f"Follow-up {step_no} started.  It checks the listings with RentCast, then sends up "
          f"to {max_emails}. It runs in the background:  this page shows how it went.")
    return _to_campaign(campaign_id)


@drip_bp.route("/<int:campaign_id>/recipients/<int:realtor_id>/stop", methods=["POST"])
def stop_recipient(campaign_id, realtor_id):
    with get_connection() as conn:
        try:
            dripactions.stop_recipient(
                conn, campaign_id=campaign_id, realtor_id=realtor_id,
                reason=request.form.get("reason"), user_id=session["emp_id"])
            conn.commit()
        except dripactions.ActionError as exc:
            conn.rollback()
            flash(str(exc))
        else:
            flash("Recipient stopped.  They get no more follow-ups from this campaign.")
    return _to_campaign(campaign_id)


@drip_bp.route("/<int:campaign_id>/stop-addresses", methods=["POST"])
def stop_addresses(campaign_id):
    with get_connection() as conn:
        try:
            out = dripactions.stop_by_addresses(
                conn, campaign_id=campaign_id, text=request.form.get("addresses"),
                reason=request.form.get("reason"), user_id=session["emp_id"])
            conn.commit()
        except dripactions.ActionError as exc:
            conn.rollback()
            flash(str(exc))
        else:
            parts = [f"Stopped {len(out['stopped'])}."]
            if out["already"]:
                parts.append("Already stopped: " + ", ".join(out["already"][:10]) + ".")
            if out["unknown"]:
                parts.append("Not in this campaign: " + ", ".join(out["unknown"][:10]) + ".")
            flash(" ".join(parts))
    return _to_campaign(campaign_id)


@drip_bp.route("/<int:campaign_id>/status", methods=["POST"])
def status(campaign_id):
    move = request.form.get("move")
    with get_connection() as conn:
        try:
            dripactions.set_status(conn, campaign_id=campaign_id, move=move,
                                   user_id=session["emp_id"])
            conn.commit()
        except dripactions.ActionError as exc:
            conn.rollback()
            flash(str(exc))
        else:
            flash(MOVED[move])
    return _to_campaign(campaign_id)
