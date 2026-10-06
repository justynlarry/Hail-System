"""Constant Contact connection page and OAuth routes.  Admin only.

The callback is a GET that changes state, which is how OAuth redirects work, so
it is guarded by the `state` value instead of a CSRF token: one random value
per attempt, kept in the session, used once, compared in constant time.
"""

import hmac
import secrets

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from hailsys.constantcontact import oauth, tokens
from hailsys.db import get_connection
from hailsys.tuning import DISPLAY_TZ
from hailsys.web.auth import require_role

cc_bp = Blueprint("cc", __name__, url_prefix="/cc")

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
    return render_template("cc.html", grant=grant, display_tz=DISPLAY_TZ)



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
