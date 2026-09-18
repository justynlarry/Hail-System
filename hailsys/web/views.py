import csv
import io

from datetime import datetime, timedelta

from flask import flash, redirect, Blueprint, render_template, abort, request, session, url_for, Response

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
DEFAULT_DAYS = 30
GROUP_BYS = ("zip", "city")
POINTS_SQL_LIMIT = 2000

def _parse_day(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None

def _window_from_args():
    """Resolve date-range filter, returns (start_day, end_day, window_start
    window_end).

    end_day is INCLUSIVE as the user means it, denver_day_bounds(end_day)
    returns midnight of the following day.
    """
    # NOT date.today(), the container's clock is UTC, so after 6pm Denver
    # that returns 'tomorrow' and the newest storm day falls outside
    # the window.  Any 'what day is it' question goes through DISPLAY_TZ.
    today = datetime.now(DISPLAY_TZ).date()

    start_day = _parse_day(request.args.get("start"))
    end_day = _parse_day(request.args.get("end"))

    if start_day is None or end_day is None:
        try:
            days = int(request.args.get("days", DEFAULT_DAYS))
        except ValueError:
            days = DEFAULT_DAYS
        if days not in DAY_RANGES:
            days = DEFAULT_DAYS
        start_day = today - timedelta(days=days)
        end_day = today

    if start_day > end_day:
        start_day, end_day = end_day, start_day

    window_start, _ = denver_day_bounds(start_day)
    _, window_end = denver_day_bounds(end_day)
    return start_day, end_day, window_start, window_end

def _actionable_from_args():
    if "submitted" in request.args:
        return "actionable" in request.args
    return True


@bp.route("/")
@login_required
def index():
    start_day, end_day, window_start, window_end = _window_from_args()
    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()

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
        start_day=start_day,
        end_day=end_day,
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

@bp.route("/export.csv")
@login_required
def export_csv():
    start_day, end_day, window_start, window_end = _window_from_args()
    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()

    with get_connection() as conn:
        rows = storms.fetch_zips(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(storms.ZIPS_COLUMNS)
    writer.writerows(
        [row[col] for col in storms.ZIPS_COLUMNS] for row in rows
    )

    label = (report_text or "ALL").replace("/", "-").replace(" ", "_")
    filename = (f"storm_zips_{start_day.isoformat()}_to_"
               f"{end_day.isoformat()}_{label}.csv") 

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/map/points.geojson")
@login_required
def map_points():
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
        rows = storms.fetch_report_points(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
            limit=POINTS_SQL_LIMIT,
        )

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "iem_id": r["iem_id"],
                    "local_time": r["local_time"].strftime("%m-%d-%Y %H:%M"),
                    "report_text": r["report_text"],
                    "magnitude": float(r["magnitude"]) if r["magnitude"] is not None else None,
                    "mag_unit": r["mag_unit"],
                    "report_source": r["report_source"],
                    "report_source_norm": r["report_source_norm"],
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r["longitude"]), float(r["latitude"])],
                },
            }
            for r in rows
        ],
    }