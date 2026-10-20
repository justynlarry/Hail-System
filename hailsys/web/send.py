"""Send Screen.  Admin Only 10/8/2026.

Preview and confirm pages are read only:  they don't write anything and don't import
the send engine.  sendjobs (start_send, start_resume) is the only door to the engine, reached
by the Send-now and resume POST
"""

import logging
import re
from decimal import Decimal
from datetime import datetime, timezone

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, session,
                   url_for)

from hailsys.constantcontact import api, oauth, statuses
from hailsys.db import get_connection
from hailsys.email import dripstart, render, templatestore
from hailsys.queries import sendlist, sendstatus, templatelist
from hailsys.tuning import DISPLAY_TZ
from hailsys.web import sendjobs
from hailsys.web.auth import require_role

logger = logging.getLogger(__name__)

send_bp = Blueprint("send", __name__, url_prefix="/send")

DEFAULT_MAX_EMAILS = 5
MAX_EMAILS_LIMIT = 1000
CALLS_PER_EMAIL = 10
SAMPLE_AGENT = "Pat Example"
SAMPLE_MATCHES = [{"listing_id": 0, "address": "123 Example St, Denver, CO 80202",
                  "utc_datetime": datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc),
                  "magnitude": Decimal("1.75"), "distance_miles": Decimal("1.2")}]

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
    "duplicate_address": "Same address already covered by another listing",
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


def _days_from_form():
    days = set()
    for raw in request.form.getlist("day"):
        try:
            days.add(datetime.strptime(raw, "%Y-%m-%d").date())
        except ValueError:
            abort(400)
    return sorted(days)


def _template_page():
    try:
        return int(request.args.getlist("tpage")[-1])
    except (IndexError, ValueError):
        return 1


def _chosen_name():
    """The template picked on the page:  a radio click wins, then the value carried from the last
    view of the page, then the built-in."""
    return (request.args.get("template_pick") or request.args.get("template")
            or templatestore.TEMPLATE_NAME)


def _builtin_row():
    """The built-in template as a list row, for a database that has no saved version of it yet
    (first send saves it).  Reads the files, does not write."""
    subject_src, body_src = templatestore.load_files()
    return {"template_id": None, "template_name": templatestore.TEMPLATE_NAME,
            "description": None, "subject": subject_src, "body": body_src,
            "report_type": None, "report_text": None, "created_at": None,
            "emp_fname": None, "emp_lname": None, "versions": 0, "last_used": None,
            "sends": 0}


def _row_sources(row):
    """The built-in template renders from its files, any other from its saved row, the same
    rule as templatestore.load."""
    if row["template_name"] == templatestore.TEMPLATE_NAME:
        return templatestore.load_files()
    return row["subject"], row["body"]


def _render_row(row, context):
    """(subject, html, problem) for one template row.  Never raises."""
    subject_src, body_src = _row_sources(row)
    try:
        subject, html = render.render(subject_src, body_src, context)
    except render.RenderError as exc:
        return None, None, str(exc)
    return subject, html, None


def _sample_context(found):
    """(context, where it came from, problem): the first realtor on the send list, or stand-in
    data when no storm day has been previewed.  Never raises."""
    try:
        email_settings, _ = sendjobs.load_config()
        if found and found["realtors"]:
            first = found["realtors"][0]
            return (render.build_context(first["agent_name"], first["events"], email_settings),
                    "the first realtor on the list", None)
        return (render.build_context(SAMPLE_AGENT, SAMPLE_MATCHES, email_settings),
                "stand-in storm data", None)
    except (render.SettingsError, render.RenderError) as exc:
        return None, None, str(exc)


def _template_available(name):
    """The built-in always is (it comes from its files), any other needs an active row."""
    if name == templatestore.TEMPLATE_NAME:
        return True
    with get_connection() as conn:
        row = templatelist.fetch_one(conn, name)
        conn.rollback()
    return row is not None


def _campaign_templates():
    """The names a follow-up can use:  the built-in first, then every active template."""
    with get_connection() as conn:
        rows = templatelist.fetch_all(conn)
        conn.rollback()
    names = [templatestore.TEMPLATE_NAME]
    names += [r["template_name"] for r in rows if r["template_name"] != templatestore.TEMPLATE_NAME]
    return names


@send_bp.route("/")
def index():
    now = datetime.now(timezone.utc)
    s = g.settings
    tuning = dict(max_age_days=s["match_max_age_days"],
                  freshness_days=s["listing_freshness_days"])
    days = _days_from_args()
    chosen = _chosen_name()
    with get_connection() as conn:
        available = sendlist.fetch_send_days(
            conn, today=now.astimezone(DISPLAY_TZ).date(), max_age_days=tuning["max_age_days"])
        found = None
        if days:
            found = sendlist.build_send_list(
                conn, days, now=now, cap_days=s["email_cap_days"], **tuning)
        listing = templatelist.fetch_page(conn, _template_page())
        selected = templatelist.fetch_one(conn, chosen)
        if selected is None and chosen != templatestore.TEMPLATE_NAME:
            flash(f"The template '{chosen[:60]}' is not available.  Showing the built-in template.")
            chosen = templatestore.TEMPLATE_NAME
            selected = templatelist.fetch_one(conn, chosen)
        conn.rollback()                     # read only: nothing to keep
    if selected is None:
        selected = _builtin_row()
        if listing["total"] == 0:
            listing["rows"] = [selected]
    context, sample_source, context_problem = _sample_context(found)

    def sample_of(row):
        if context is None:
            return None, None, context_problem
        return _render_row(row, context)

    templates = []
    for row in listing["rows"]:
        subject, html, problem = sample_of(row)
        builtin = row["template_name"] == templatestore.TEMPLATE_NAME
        templates.append({
            "row": row, "subject": subject, "html": html, "problem": problem,
            "chosen": row["template_name"] == chosen,
            "file_changed": (builtin and row["template_id"] is not None
                             and (row["subject"], row["body"]) != templatestore.load_files())})

    listing_count = 0
    sample_subject = sample_html = sample_problem = None
    if found and found["realtors"]:
        listing_count = len({e["listing_id"] for r in found["realtors"] for e in r["events"]})
        sample_subject, sample_html, sample_problem = sample_of(selected)
    return render_template(
        "send.html", available=available, ticked=set(days), found=found,
        reasons=[(r, REASON_LABELS[r]) for r in sendlist.REASONS],
        listing_count=listing_count, sample_subject=sample_subject,
        sample_html=sample_html, sample_problem=sample_problem, budget=sendjobs.calls_budget(),
        templates=templates, chosen=chosen, selected=selected, sample_source=sample_source,
        tpage=listing["page"], tpages=listing["pages"], ttotal=listing["total"],
        display_tz=DISPLAY_TZ,
        default_max_emails=DEFAULT_MAX_EMAILS, max_emails_limit=MAX_EMAILS_LIMIT,
        busy=sendjobs.is_busy())


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
    return redirect(url_for("send.index", day=[d.isoformat() for d in days],
                            template=request.form.get("template") or None))


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
    s = g.settings
    campaign, campaign_problem = None, None
    try:
        campaign = dripstart.parse_plan(
            request.form, max_followups=s["drip_max_followups"],
            max_gap_days=s["drip_max_gap_days"], template_ok=_template_available)
    except dripstart.CampaignError as exc:
        campaign_problem = str(exc)
    form = {"days": sorted(days), "allowed": allowed, "max_emails": max_emails,
            "override_cap": "override_cap" in request.form,
            "template": request.form.get("template") or templatestore.TEMPLATE_NAME,
            "campaign": campaign}
    if not days:
        problem = "Tick at least one storm day."
    elif bad:
        problem = "These are not email addresses: " + ", ".join(b[:40] for b in bad[:5])
    elif not allowed:
        problem = ("Enter the addresses this send may go to. "
                   "There is no send-to-everyone option.")
    elif not 1 <= max_emails <= MAX_EMAILS_LIMIT:
        problem = f"'Send at most' must be between 1 and {MAX_EMAILS_LIMIT}."
    elif not _template_available(form["template"]):
        problem = "That template is not available."
    elif campaign_problem:
        problem = campaign_problem
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
    with get_connection() as conn:
        template_row = templatelist.fetch_one(conn, form["template"])
        conn.rollback()
    s = g.settings
    first, last = form["days"][0], form["days"][-1]
    default_name = f"Storm {first}" if first == last else f"Storm {first} to {last}"
    return render_template(
        "send_confirm.html", form=form, mine=mine, unmatched=unmatched, sender=sender,
        going=going, calls=going * CALLS_PER_EMAIL, budget=sendjobs.calls_budget(),
        template_row=template_row, busy=sendjobs.is_busy(),
        max_followups=s["drip_max_followups"], max_gap_days=s["drip_max_gap_days"],
        campaign_templates=_campaign_templates() if s["drip_max_followups"] else [],
        default_name=default_name)


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
            max_emails=form["max_emails"], template_name=form["template"],
            max_age_days=s["match_max_age_days"], campaign=form["campaign"],
            cap_days=s["email_cap_days"], freshness_days=s["listing_freshness_days"])
    except sendjobs.SendInProgress:
        flash("A send is already running.  Nothing new was started.")
        return _back(form["days"])
    made = ""
    if form["campaign"]:
        n = len(form["campaign"]["steps"])
        made = (f" Campaign '{form['campaign']['name'][:60]}' with {n} "
                f"follow-up{'' if n == 1 else 's'} is created with it.")
    flash(f"Send started: up to {min(len(mine), form['max_emails'])} of {len(mine)} emails. "
          f"It runs in the background. This page shows progress.{made}")
    return redirect(url_for("send.batches"))


@send_bp.route("/batches")
def batches():
    with get_connection() as conn:
        rows = sendstatus.fetch_batches(conn)
        sync = sendstatus.fetch_last_sync(conn)
        check = statuses.last_success(conn)
        conn.rollback()
    return render_template("send_batches.html", batches=rows, sync=sync, check=check,
                           busy=sendjobs.is_busy(), display_tz=DISPLAY_TZ)


@send_bp.route("/batch/<uuid:batch_id>")
def batch(batch_id):
    with get_connection() as conn:
        found = sendstatus.fetch_batch(conn, batch_id)
        conn.rollback()
    if found is None:
        abort(404)
    return render_template("send_batch.html", emails=found["emails"], counts=found["counts"],
                           batch_id=batch_id, max_emails_limit=MAX_EMAILS_LIMIT,
                           resume_max_days=sendjobs.RESUME_MAX_DAYS, budget=sendjobs.calls_budget(),
                           discardable=sendjobs.count_discardable(batch_id),
                           busy=sendjobs.is_busy(), display_tz=DISPLAY_TZ)

@send_bp.route("/batch/<uuid:batch_id>/resume", methods=["POST"])
def resume(batch_id):
    """Deliver the queued remainder of one batch.  User-Click only, not
    automated.  Count shown on the page must still match."""
    def back():
        return redirect(url_for("send.batch", batch_id=batch_id))

    try:
        expected = int(request.form["expected"])
    except (KeyError, ValueError):
        abort(400)
    try:
        max_emails = int(request.form.get("max_emails") or "")
    except ValueError:
        max_emails = 0
    if not 1 <= max_emails <= MAX_EMAILS_LIMIT:
        flash(f"'Send at most' must be between 1 and {MAX_EMAILS_LIMIT}")
        return back()
    with get_connection() as conn:
        found = sendstatus.fetch_batch(conn, batch_id)
        conn.rollback()
    if found is None:
        abort(404)
    resumable = found["counts"]["queued"] + found["counts"]["in_progress"]
    if resumable != expected:
        flash(f"The batch changed since you looked at it ({expected} to {resumable} "
              "resumable emails).  Nothing was sent.  Review it again.")
        return back()
    try:
        _, sender = sendjobs.load_config()
    except render.SettingsError as exc:
        flash(f"Sending is not configured: {exc}")
        return back()
    try:
        sendjobs.start_resume(user_id=session["emp_id"], batch_id=batch_id,
                              sender=sender, max_emails=max_emails)
    except sendjobs.SendInProgress:
        flash("A send is already running.  Nothing new was started.")
    except (sendjobs.NothingToResume, sendjobs.BatchTooOld) as exc:
        flash(str(exc))
    else:
        flash(f"Resume started: up to {min(max_emails, resumable)} of {resumable} queued "
              "emails. It runs in the background. This page shows progress.")
    return back()

@send_bp.route("/batch/<uuid:batch_id>/discard", methods=["POST"])
def discard(batch_id):
    """Give up on untouched queued emails of one batch, so their matches can be sent
    again.  User-Click only, the count shown on the page must match."""
    def back():
        return redirect(url_for("send.batch", batch_id=batch_id))
    try:
        expected = int(request.form["expected"])
    except (KeyError, ValueError):
        abort(400)
    if request.form.get("confirm") != "yes":
        flash("Tick the box to confirm.  Nothing was discarded.")
        return back()
    with get_connection() as conn:
        found = sendstatus.fetch_batch(conn, batch_id)
        conn.rollback()
    if found is None:
        abort(404)
    discardable = sendjobs.count_discardable(batch_id)
    if discardable != expected:
        flash(f"The batch changed since you looked at it ({expected} to {discardable} "
              "untouched queued emails).  Nothing was discarded.  Review it again.")
        return back()
    try:
        n = sendjobs.discard_queued(user_id=session["emp_id"], batch_id=batch_id)
    except sendjobs.SendInProgress:
        flash("A send is already running.  Nothing was discarded.")
    except sendjobs.BatchBusy as exc:
        flash(str(exc))
    else:
        flash(f"Discarded {n} queued email{'' if n == 1 else 's'}.  Their matches can be "
              "sent again.")
    return back()


@send_bp.route("/templates/<int:template_id>/description", methods=["POST"])
def template_description(template_id):
    """Save the description typed on a template row.  The page it came from is rebuilt from the
    same hidden fields, so the ticked days, the chosen template and the page are not lost."""
    days = _days_from_form()
    try:
        with get_connection() as conn:
            templatestore.set_description(conn, template_id, request.form.get("description"))
            conn.commit()
    except ValueError as exc:
        flash(str(exc))
    except templatestore.DescriptionFrozen as exc:
        flash(str(exc))
    except templatestore.TemplateNotFound:
        abort(404)
    else:
        flash("Description saved.")
    return redirect(url_for("send.index", day=[d.isoformat() for d in days],
                            template=request.form.get("template") or None,
                            tpage=request.form.get("tpage") or None))


@send_bp.route("/check", methods=["POST"])
def check():
    """Ask Constant Contact about bounces for recent sends.  Read-only toward Constant
    Contact: it sends nothing."""
    try:
        result = statuses.run_check(triggered_by=session["emp_id"])
    except statuses.StatusBusy as exc:
        flash(str(exc))
    except (api.ApiError, oauth.OAuthError) as exc:
        flash(f"The bounce check stopped: {exc}")
    except Exception:
        logger.exception("event=cc_status_unexpected")
        flash("The bounce check failed unexpectedly; see the server log.")
    else:
        flash(f"Bounce check finished: {result['checked']} emails checked, "
              f"{result['bounced']} bounced, {result['suppressed']} added to the DNC list.")
    return redirect(url_for("send.batches"))
