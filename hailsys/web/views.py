import csv
import io

from datetime import datetime, timedelta
from itertools import groupby

from flask import flash, redirect, Blueprint, render_template, abort, request, session, url_for, Response

from hailsys.web.auth import login_required, verify_password

from hailsys.matching.matcher import match_storm
from hailsys.rentcast.estimate import estimate_pull
from hailsys.web.jobs import start_pull
from hailsys.db import get_connection
from hailsys.queries import activity, matches,storms, workstate
from hailsys.tuning import (
    DEFAULT_MATCH_RADIUS_MILES,
    DEFAULT_ZIP_RADIUS_MILES,
    DISPLAY_TZ,
    RECENT_PULL_WINDOW_DAYS,
    denver_day_bounds,
    miles_to_metres,
)

bp = Blueprint("main", __name__)

DAY_RANGES = (30, 90, 365)
DEFAULT_DAYS = 30
GROUP_BYS = ("zip", "city")
POINTS_SQL_LIMIT = 2000
FEED_PANEL_LIMIT = 5

def _previous_login():
    """Login before currnet one, from the session, or None on user's first login
    """
    raw = session.get("previous_login_at")
    return datetime.fromisoformat(raw) if raw else None


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
    today = datetime.now(DISPLAY_TZ).date()
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
        work_state = workstate.fetch_work_state(
            conn,
            window_start=window_start,
            window_end=window_end,
            today=today,
        )
        since = _previous_login()
        feed = activity.build_feed(
            conn,
                since=since,
                today=today,
                radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
        ) if since else None
        
    for row in rows:
        row["work_state"] = workstate.state_for(
            work_state, row["storm_date"], row["report_text"], today
        )

    return render_template(
        "storms.html",
        rows=rows,
        types=types,
        start_day=start_day,
        end_day=end_day,
        selected_type=report_text,
        actionable_only=actionable_only,
        feed=feed,
        feed_since=since,
        feed_limit=FEED_PANEL_LIMIT,
        display_tz=DISPLAY_TZ,

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
            "SELECT emp_id, password_hash, role, is_active, last_login_at"
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

    session["previous_login_at"] = (
        row["last_login_at"].isoformat() if row["last_login_at"] else None
    )

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
    group_by = request.args.get("group_by", "city")
    if group_by not in GROUP_BYS:
        group_by = "city"

    start_day, end_day, window_start, window_end = _window_from_args()
    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()

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
        start_day=start_day,
        end_day=end_day,
        selected_type=report_text,
        actionable_only=actionable_only,
    )

@bp.route("/territory/days")
@login_required
def territory_days():
    area_name = request.args.get("area_name")
    if not area_name:
        abort(400)
    _, _, window_start, window_end = _window_from_args()
    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()

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
    _, _, window_start, window_end = _window_from_args()
    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()

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

@bp.route("/pull/estimate")
@login_required
def pull_estimate():
    """What would a pull cost before any request is made"""
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        result = estimate_pull(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
        )

    return render_template(
        "pull_estimate.html",
        storm_date=day,
        report_text=report_text,
        actionable_only=actionable_only,
        result=result,
        recent_window_days=RECENT_PULL_WINDOW_DAYS,
    )

@bp.route("/pull", methods=["POST"])
@login_required
def pull_start():
    try:
        day = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        expected_zip_count = int(request.form["zip_count"])
    except (KeyError, ValueError):
        abort(400)

    report_text = request.form.get("type") or None
    if "submitted" in request.form:
        actionable_only = "actionable" in request.form
    else:
        actionable_only = True
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        result = estimate_pull(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
        )
    if result["zip_count"] != expected_zip_count:
        flash(f"The zip list changed since this estimate was shown "
              f"({expected_zip_count} to {result['zip_count']}). "
              f"Nothing was pulled.  Review and confirm again.")
        return redirect(url_for("main.pull_estimate",
                                date=day.isoformat(), type=report_text or ""))
    start_pull(
        emp_id=session["emp_id"],
        storm_date=day,
        report_text=report_text,
        zip_codes=result["zips"],
        estimated_api_calls=result["estimated_api_calls"],
        window_start=window_start,
        window_end=window_end,
    )

    flash(f"Pull started for {day} {report_text or 'all types'} "
          f"({result['zip_count']} zips).")
    return redirect(url_for("main.index"))

@bp.route("/match", methods=["POST"])
@login_required
def match_start():
    try:
        day = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.form.get("type") or None
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        new_matches = match_storm(
            conn,
            emp_id=session["emp_id"],
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
        )
    if new_matches:
        flash(f"Matched {day} {report_text or 'all types'}: "
              f"{new_matches} new match{'' if new_matches == 1 else 'es'}")
    else:
        flash(f"No new matches for {day} {report_text or 'all types'}."
              f"Either nothing was within range, or it was already matched.")

    return redirect(url_for("main.index"))

@bp.route("/storms/matches")
@login_required
def storm_matches():
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.args.get("type") or None
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        rows = matches.fetch_match_detail(
            conn,
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            radius_miles=DEFAULT_MATCH_RADIUS_MILES,
        )

        storm_zip_rows = storms.fetch_zips(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=True,
        )
        storm_zips = sorted({r["zcta5"] for r in storm_zip_rows})
        coverage = matches.fetch_pull_coverage(conn, storm_zips)

    unpulled_zips = [z for z in storm_zips if z not in coverage]
    oldest_pull = min(coverage.values()) if coverage else None

    groups = [
        (realtor_id, list(listings))
        for realtor_id, listings in groupby(rows, key=lambda r: r["realtor_id"])
    ]

    return render_template(
        "matches.html",
        rows=rows,
        storm_date=day,
        report_text=report_text,
        radius_miles=DEFAULT_MATCH_RADIUS_MILES,
        listing_count=len(rows),
        agent_count=len({r["realtor_id"] for r in rows
                         if r["realtor_id"] is not None}),
        storm_zip_count=len(storm_zips),
        unpulled_zips=unpulled_zips,
        oldest_pull=oldest_pull,
        display_tz=DISPLAY_TZ,
    )

@bp.route("/activity")
@login_required
def activity_page():
    today = datetime.now(DISPLAY_TZ).date()
    since = _previous_login()

    feed = None
    if since:
        with get_connection() as conn:
            feed = activity.build_feed(
                conn,
                since=since,
                today=today,
                radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            )
    return render_template(
        "activity.html",
        feed=feed,
        feed_since=since,
        feed_limit=None,
        display_tz=DISPLAY_TZ,
    )

