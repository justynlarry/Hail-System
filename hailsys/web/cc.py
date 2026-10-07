"""Constant Contact connection page and OAuth routes.  Admin only.

The callback is a GET that changes state, which is how OAuth redirects work, so
it is guarded by the `state` value instead of a CSRF token: one random value
per attempt, kept in the session, used once, compared in constant time.
"""

import hmac
import logging
import secrets

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)

from hailsys.constantcontact import api, oauth, tokens, unsubs
from hailsys.db import get_connection
from hailsys.tuning import DISPLAY_TZ
from hailsys.web.auth import require_role

cc_bp = Blueprint("cc", __name__, url_prefix="/cc")
logger = logging.getLogger(__name__)

STATE_KEY = "cc_oauth_state"


@cc_bp.before_request
def _admin_only():
    return require_role("admin")



@cc_bp.route("/")
def index():
    # Metadata only, token columns are not read, or decrypted.
    with get_connection() as conn:
        grant = conn.execute(
            "SELECT token_id, account_id, scope, access_expires_at, created_at "
            "FROM oauth_tokens WHERE provider = %s "
            "ORDER BY token_id DESC LIMIT 1", (tokens.PROVIDER,)).fetchone()
        sync_ok = unsubs.last_success(conn)
        last_run = conn.execute(
            "SELECT run_id, status, started_at, finished_at, error_detail "
            "FROM cc_sync_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        conflicts = conn.execute(
            "SELECT d.email_raw, d.removed_at FROM cc_sync_conflicts c "
            "JOIN dnc_list d USING (dnc_id) "
            "WHERE c.run_id = (SELECT run_id FROM cc_sync_runs "
            "                   WHERE status = 'ok' ORDER BY run_id DESC LIMIT 1) "
            "ORDER BY d.email_norm").fetchall()
    return render_template("cc.html", grant=grant, display_tz=DISPLAY_TZ,
                           sync_ok=sync_ok, last_run=last_run,
                           conflicts=conflicts)



@cc_bp.route("/connect", methods=["POST"])
def connect():
    state = secrets.token_urlsafe(32)
    try:
        url = oauth.authorize_url(state)
    except oauth.OAuthError as exc:
        flash(str(exc))
        return redirect(url_for("cc.index"))
    session[STATE_KEY] = state
    return redirect(url)

@cc_bp.route("/callback")
def callback():
    # pop, a state is good for one attempt.
    expected = session.pop(STATE_KEY, None)
    got = request.args.get("state", "")
    if not expected or not hmac.compare_digest(expected.encode(), got.encode()):
        flash("Constant Contact sign-in was rejected:  the state check failed. "
              "Start again from this page.")
        return redirect(url_for("cc.index"))
    # The error text is not echoed, it comes from the URL
    if "error" in request.args or not request.args.get("code"):
        flash("Constant Contact did not grant access.  Start again if that was "
              "a mistake.")
        return redirect(url_for("cc.index"))

    try:
        with get_connection() as conn:
            account_id = oauth.complete_authorization(
                conn, request.args["code"])
    except oauth.OAuthError as exc:
            flash(str(exc))
    else:
        flash(f"Connected to Constant Contact account {account_id}")
    return redirect(url_for("cc.index"))



@cc_bp.route("/refresh", methods=["POST"])
def refresh():
    try:
        with get_connection() as conn:
            oauth.get_access_token(conn, force=True)
    except oauth.OAuthError as exc:
        flash(str(exc))
    else:
        flash("Refreshed the Constant Contact Token.")
    return redirect(url_for("cc.index"))

@cc_bp.route("/sync", methods=["POST"])
def sync():
    try:
        result = unsubs.run_sync(triggered_by=g.user["emp_id"])
    except unsubs.SyncBusy as exc:
        flash(str(exc))
    except (api.ApiError, oauth.OAuthError) as exc:
        flash(f"The sync failed: {exc}")
    except Exception:
        logger.exception("event=cc_sync_unexpected")
        flash("The sync failed unexpectedly; see the server log.")
    else:
        flash(f"Sync finished: {result['fetched']} unsubscribed contacts "
              f"checked, {result['inserted']} new to the DNC list, "
              f"{result['already_present']} already there, "
              f"{result['conflicts']} conflicts.")
    return redirect(url_for("cc.index"))