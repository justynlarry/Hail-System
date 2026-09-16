from datetime import datetime, timedelta

from flask import flash, redirect, Blueprint, render_template, abort, request, session, url_for

from hailsys.web.auth import login_required, verify_password

from hailsys.db import get_connection
from hailsys.queries import storms
from hailsys.tuning import (
    DEFAULT_ZIP_RADIUS_MILES,
    DISPLAY_TZ,
    denver_day_bounds,
    miles_to_metres,
)

bp = Blueprint("main", __name__)

DAY_RANGES = (30, 90, 365)
GROUP_BYS = ("zip", "city")

@bp.route("/")
@login_required
def index():
    # NOT date.today(), the container's clock is UTC, so after 6pm Denver
    # that returns 'tomorrow' and the newest storm day falls outside
    # the window.  Any 'what day is it' question goes through DISPLAY_TZ.
    today = datetime.now(DISPLAY_TZ).date()

    # Validate against a fixed set.
    try:
        days = int(request.args.get("days", 90))
    except ValueError:
        days = 90
    if days not in DAY_RANGES:
        days = 90

    report_text = request.args.get("type") or None

    # Checkbox
    if "submitted" in request.args:
        actionable_only = "actionable" in request.args
    else:
        actionable_only = True
    
    window_start, _ = denver_day_bounds(today - timedelta(days=days))
    _, window_end = denver_day_bounds(today)

    with get_connection() as conn:
        rows = storms.fetch_recent_days(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
            limit=50,   
        )

        types = storms.fetch_report_types(conn)

    return render_template(
        "storms.html",
        rows=rows,
        types=types,
        day_ranges=DAY_RANGES,
        selected_days=days,
        selected_type=report_text,
        actionable_only=actionable_only,
    )

@bp.route("/storms/zips")
@login_required
def storm_zips():
    # Query Parameters, not path segments.  report_text contains spaces and
    # slashes ('NON-TSTM WIND GST'), slash segment is a route boundary.
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.args.get("type") or None

    if "submitted" in request.args:
        actionable_only = "actionable" in request.args
    else:
        actionable_only = True

    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        rows = storms.fetch_zips(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
        )

    return render_template("_zips.html", rows=rows)

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    user_name = (request.form.get("user_name") or "" ).strip().lower()
    password = request.form.get("password") or ""

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT emp_id, password_hash, role, is_active"
            "  FROM users WHERE user_name = %s",
            (user_name,),
        )
        row = cur.fetchone()

    # One message for each failure: no user, incorrect password, deactivated account.

    if (row is None
            or not row["is_active"]
            or not verify_password(password, row["password_hash"])):
        flash("Invalid User Name or Password.")
        return render_template("login.html"), 401

    # Clear before setting.
    session.clear()
    session["emp_id"] = row["emp_id"]
    session["user_name"] = user_name
    session["role"] = row["role"]

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET last_login_at = now() WHERE emp_id = %s",
            (row["emp_id"],),
        )
        conn.commit()

    return redirect(url_for("main.index"))

@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))



@bp.route("/territory")
@login_required
def territory():
    today = datetime.now(DISPLAY_TZ).date()

    group_by = request.args.get("group_by", "city")
    if group_by not in GROUP_BYS:
        group_by = "city"

    try:
        days = int(request.args.get("days", 90))
    except ValueError:
        days = 90
    if days not in DAY_RANGES:
        days = 90

    report_text = request.args.get("type") or None

    if "submitted" in request.args:
        actionable_only = "actionable" in request.args
    else:
        actionable_only = True

    window_start, _ = denver_day_bounds(today - timedelta(days=days))
    _, window_end = denver_day_bounds(today)

    with get_connection() as conn:
        if group_by == "city":
            rows = storms.fetch_cities(
                conn,
                radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
                window_start=window_start,
                window_end=window_end,
                report_text=report_text,
                actionable_only=actionable_only,
            )
        else:
            rows = storms.fetch_zips(
                conn,
                radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
                window_start=window_start,
                window_end=window_end,
                report_text=report_text,
                actionable_only=actionable_only,
            )
        types = storms.fetch_report_types(conn)

    return render_template(
        "territory.html",
        rows=rows,
        types=types,
        group_by=group_by,
        group_bys=GROUP_BYS,
        day_ranges=DAY_RANGES,
        selected_days=days,
        selected_type=report_text,
        actionable_only=actionable_only,
    )

@bp.route("/territory/days")
@login_required
def territory_days():
    area_name = request.args.get("area_name")
    if not area_name:
        abort(400)

    today = datetime.now(DISPLAY_TZ).date()

    try:
        days = int(request.args.get("days", 90))
    except ValueError:
        days = 90
    if days not in DAY_RANGES:
        days = 90

    report_text = request.args.get("type") or None

    if "submitted" in request.args:
        actionable_only = "actionable" in request.args
    else:
        actionable_only = True

    window_start, _ = denver_day_bounds(today - timedelta(days=days))
    _, window_end = denver_day_bounds(today)

    with get_connection() as conn:
        rows = storms.fetch_city_days(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
            area_name=area_name,
        )

    return render_template("_city_days.html", rows=rows)