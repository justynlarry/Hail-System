import csv
import io

from datetime import datetime, timedelta, timezone
from itertools import groupby
from urllib.parse import urlsplit

from flask import flash, g, redirect, Blueprint, render_template, abort, request, session, url_for, Response

from hailsys.web.auth import hash_password, login_required, verify_password, MIN_PASSWORD_LENGTH, role_required

from hailsys.matching.matcher import match_storm
from hailsys.rentcast.estimate import estimate_pull
from hailsys.web.jobs import start_pull
from hailsys.db import get_connection
from hailsys.formatting import magnitude
from hailsys.queries import activity, matches,storms, workstate, quota, exports
from hailsys.tuning import (
    DISPLAY_TZ,
    RECENT_PULL_WINDOW_DAYS,
    denver_day_bounds,
    miles_to_metres,
)

bp = Blueprint("main", __name__)

DAY_RANGES = (30, 90, 365)
MAX_RANGE_DAYS = 400
DEFAULT_DAYS = 30
GROUP_BYS = ("zip", "city")
POINTS_SQL_LIMIT = 2000
FEED_PANEL_LIMIT = 5
PAGE_SIZE = 50

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

    # Only the days shortcut was validated against DAY_RANGES; an explicit
    # start/end came straight from the query string, so a URL could ask for
    # any span at all. The date pickers can produce one too. After the
    # fallback and the swap, so this only ever sees two valid, ordered dates.
    # Clamped rather than a 400 so a bookmarked URL keeps working. Recorded
    # on g, not flashed here: this also serves the map, CSV and fragment
    # requests, whose flash would surface on some later page.
    if (end_day - start_day).days > MAX_RANGE_DAYS:
        start_day = end_day - timedelta(days=MAX_RANGE_DAYS)
        g.range_clamped = True

    window_start, _ = denver_day_bounds(start_day)
    _, window_end = denver_day_bounds(end_day)
    return start_day, end_day, window_start, window_end

def _flash_if_clamped():
    """Tell the user _window_from_args shortened their range. Call from the
    full-page routes only."""
    if g.get("range_clamped"):
        flash(f"Range limited to {MAX_RANGE_DAYS} days.")


def _list_url(raw):
    """A same-site storm-list URL to return to after an action, or None.
    Only '/' and its query string are accepted, so a crafted value can't send
    a user to another page or another site.
    """
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.netloc and parts.netloc != request.host:
        return None
    if parts.path != "/":
        return None
    return "/?" + parts.query if parts.query else "/"


def _actionable_from_args():
    if "submitted" in request.args:
        return "actionable" in request.args
    return True

def _dnc_from_args():
    """Suppressed agents are excluded unless explicitly asked for.

    Download taken into someone's own email tool never passes the send-time
    suppression check. 
    """
    return request.args.get("dnc") != "include"

def _csv_response(columns, rows, filename):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows([row[col] for col in columns] for row in rows)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

def _stamp():
    # In the filename because a .csv is a snapshot.
    return datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")


# ------ ------ ------ @BP.Routes ------ ------ ------ 

@bp.route("/")
@login_required
def index():
    today = datetime.now(DISPLAY_TZ).date()
    start_day, end_day, window_start, window_end = _window_from_args()
    _flash_if_clamped()
    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()

    try:
        page =max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    watch = session.pop("pull_watch", None)
    if watch and (datetime.now(timezone.utc)
                  - datetime.fromisoformat(watch["since"])) > workstate.PULL_STALE_AFTER:
        watch = None
    banner = None

    with get_connection() as conn:
        # Count first so an out-of-range ?page= is clamped before the
        # offset is computed, not after an empty page has been fetched.
        total_days = storms.fetch_day_count(
            conn,
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
        )
        total_pages = max((total_days + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        page = min(page, total_pages)

        rows = storms.fetch_recent_days(
            conn,
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
        )
        types = storms.fetch_report_types(conn)
        work_state = workstate.fetch_work_state(
            conn,
            window_start=window_start,
            window_end=window_end,
            today=today,
            now=datetime.now(timezone.utc),
        )
        since = _previous_login()
        feed = activity.build_feed(
            conn,
                since=since,
                today=today,
                radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
        ) if since else None
        usage = quota.fetch_usage(
            conn,
            today=today,
            billing_day=g.settings["rentcast_billing_day"],
            quota=g.settings["rentcast_monthly_quota"],
            day_bounds=denver_day_bounds,
        ) if g.user["role"] in ("sender", "admin") else None
        if watch:
            banner = workstate.fetch_pull_banner(
                conn,
                storm_date=datetime.strptime(watch["date"], "%Y-%m-%d").date(),
                report_text=watch["type"],
                zips=watch["zips"],
                since=datetime.fromisoformat(watch["since"]),
                now=datetime.now(timezone.utc),
                )

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
        usage=usage,
        banner=banner,
        page=page,
        total_pages=total_pages,
        total_days=total_days,
        filter_args={k: v for k, v in request.args.items() if k != "page"},
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
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
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
    # UTC and aware: auth.load_current_user compares this against
    # sessions_invalidated_at, a TIMESTAMPTZ, and needs a real aware
    # datetime on both sides.
    session["issued_at"] = datetime.now(timezone.utc).isoformat()

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
    _flash_if_clamped()
    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()

    with get_connection() as conn:
        if group_by == "city":
            rows = storms.fetch_cities(
                conn,
                radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
                window_start=window_start,
                window_end=window_end,
                report_text=report_text,
                actionable_only=actionable_only,
            )
        else:
            rows = storms.fetch_zips(
                conn,
                radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
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
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
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
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
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
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
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
                    # Formatted server-side so map.js has no formatting of its own.
                    "magnitude_display": magnitude(r["magnitude"], r["mag_unit"]),
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
@role_required("sender", "admin")
def pull_estimate():
    """What would a pull cost before any request is made"""
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.args.get("type") or None
    actionable_only = _actionable_from_args()
    window_start, window_end = denver_day_bounds(day)

    back = _list_url(request.args.get("back")) or _list_url(request.referrer)

    with get_connection() as conn:
        result = estimate_pull(
            conn,
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            actionable_only=actionable_only,
        )
        usage = quota.fetch_usage(
            conn,
            today=datetime.now(DISPLAY_TZ).date(),
            billing_day=g.settings["rentcast_billing_day"],
            quota=g.settings["rentcast_monthly_quota"],
            day_bounds=denver_day_bounds,
        )

    return render_template(
        "pull_estimate.html",
        storm_date=day,
        report_text=report_text,
        actionable_only=actionable_only,
        result=result,
        recent_window_days=RECENT_PULL_WINDOW_DAYS,
        usage=usage,
        back=back,
    )

@bp.route("/pull", methods=["POST"])
@role_required("sender", "admin")
def pull_start():
    try:
        day = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        expected_zip_count = int(request.form["zip_count"])
    except (KeyError, ValueError):
        abort(400)

    report_text = request.form.get("type") or None
    back = _list_url(request.form.get("back"))
    if "submitted" in request.form:
        actionable_only = "actionable" in request.form
    else:
        actionable_only = True
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        result = estimate_pull(
            conn,
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
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
                                date=day.isoformat(), type=report_text or "",
                                submitted="1",
                                actionable="1" if actionable_only else None,
                                back=back))
    start_pull(
        emp_id=session["emp_id"],
        storm_date=day,
        report_text=report_text,
        zip_codes=result["zips"],
        estimated_api_calls=result["estimated_api_calls"],
        window_start=window_start,
        window_end=window_end,
    )

    session["pull_watch"] = {
        "date": day.isoformat(),
        "type": report_text,
        "zips": result["zip_count"],
        "since": datetime.now(timezone.utc).isoformat(),
    }
     
    return redirect(back or url_for("main.index"))

@bp.route("/match", methods=["POST"])
@role_required("sender", "admin")
def match_start():
    try:
        day = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = (request.form.get("type") or "").strip()
    if not report_text:
        abort(400)
    
    window_start, window_end = denver_day_bounds(day)

    already_matched = False
    with get_connection() as conn:
        new_matches = match_storm(
            conn,
            emp_id=session["emp_id"],
            storm_date=day,
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
        )
        if new_matches == 0:
            cur = conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM storm_listing_matches m
                      JOIN iem_data i on i.iem_id = m.iem_id
                     WHERE i.utc_datetime >= %s
                       AND i.utc_datetime < %s
                       AND i.report_text = %s
                ) AS has_matches
                """,
                (window_start, window_end, report_text),
            )
            already_matched = cur.fetchone()["has_matches"]

    if new_matches:
        flash(f"Matched {day} {report_text}: "
              f"{new_matches} new match{'' if new_matches == 1 else 'es'}")
    elif already_matched:
        flash(f"No new matches for {day} {report_text}: already matched.")
    else:
        flash(f"No listings within range for {day} {report_text}.")

    return redirect(_list_url(request.referrer) or url_for("main.index"))

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
            radius_miles=g.settings["match_radius_miles"],
        )

        storm_zip_rows = storms.fetch_zips(
            conn,
            radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
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
        groups=groups,
        storm_date=day,
        report_text=report_text,
        radius_miles=g.settings["match_radius_miles"],
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
                radius_m=miles_to_metres(g.settings["zip_radius_miles"]),
            )
    return render_template(
        "activity.html",
        feed=feed,
        feed_since=since,
        feed_limit=None,
        display_tz=DISPLAY_TZ,
    )

@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "GET":
        return render_template("change_password.html",
                                min_password_length=MIN_PASSWORD_LENGTH)
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE emp_id = %s",
                    (g.user["emp_id"],))
        row = cur.fetchone()
    
    errors = []
    if not verify_password(current, row["password_hash"]):
        errors.append("Current password is incorrect.")
    if len(new) < MIN_PASSWORD_LENGTH:
        errors.append(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
    elif new != confirm:
        errors.append("New passwords do not match.")
    elif new == current:
        errors.append("New password must be different from the current one.")
    
    if errors:
        for e in errors:
            flash(e)
        return render_template("change_password.html",
                               min_password_length=MIN_PASSWORD_LENGTH), 400
    
    new_hash = hash_password(new)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
                SET password_hash = %s,
                    sessions_invalidated_at = now()
            WHERE emp_id = %s
        RETURNING sessions_invalidated_at
            """,
            (new_hash, g.user["emp_id"]),
        )
        invalidated_at = cur.fetchone()["sessions_invalidated_at"]
        conn.commit()
    
    session["issued_at"] = invalidated_at.isoformat()

    flash("Password changed, you have been signed out of all other sessions.")
    return redirect(url_for("main.index"))
    

@bp.route("/storms/matches.csv")
@login_required
def storm_matches_csv():
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.args.get("type") or None
    window_start, window_end = denver_day_bounds(day)
    dnc_exclude = _dnc_from_args()

    with get_connection() as conn:
        rows = exports.fetch_matches(
            conn,
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            radius_miles=g.settings["match_radius_miles"],
            dnc_exclude=dnc_exclude,
        )

    label = (report_text or "ALL").replace("/", "-").replace(" ", "_")
    return _csv_response(
        exports.MATCHES_COLUMNS, rows,
        f"matches_{day.isoformat()}_{label}_{_stamp()}.csv",
    )


@bp.route("/exports")
@login_required
def exports_page():
    start_day, end_day, window_start, window_end = _window_from_args()
    _flash_if_clamped()
    report_text = request.args.get("type") or None
    dnc_exclude = _dnc_from_args()
    submitted =request.args.get("submitted") == "1"

    match_count = realtor_count = None
    with get_connection() as conn:
        types = storms.fetch_report_types(conn)
        if submitted:
            match_count = exports.count_matches(
                conn,
                window_start=window_start,
                window_end=window_end,
                report_text=report_text,
                radius_miles=g.settings["match_radius_miles"],
                dnc_exclude=dnc_exclude,
            )
            realtor_count = exports.count_realtors(
                conn, dnc_exclude=dnc_exclude)
    return render_template(
        "exports.html",
        start_day=start_day,
        end_day=end_day,
        types=types,
        selected_type=report_text,
        dnc_exclude=dnc_exclude,
        submitted=submitted,
        match_count=match_count,
        realtor_count=realtor_count,
        radius_miles=g.settings["match_radius_miles"],
    )


@bp.route("/exports/matches.csv")
@login_required
def exports_matches_csv():
    start_day, end_day, window_start, window_end = _window_from_args()
    report_text = request.args.get("type") or None

    with get_connection() as conn:
        rows = exports.fetch_matches(
            conn,
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
            radius_miles=g.settings["match_radius_miles"],
            dnc_exclude=_dnc_from_args(),
        )

    label = (report_text or "ALL").replace("/", "-").replace(" ", "_")
    return _csv_response(
        exports.MATCHES_COLUMNS, rows,
        f"matches_{start_day.isoformat()}_to_{end_day.isoformat()}"
        f"_{label}_{_stamp()}.csv",
    )


# Sender/admin only: every agent's email and phone in one file is 
# sensitive information.

@bp.route("/exports/realtors.csv")
@role_required("sender", "admin")
def exports_realtors_csv():
    with get_connection() as conn:
        rows = exports.fetch_realtors(conn, dnc_exclude=_dnc_from_args())
    return _csv_response(
        exports.REALTORS_COLUMNS, rows, f"realtors_{_stamp()}.csv")

@bp.route("/storms/state")
@login_required
def storm_state():
    """One storm row's Status cell, re-rendered.  Returns same
    fragment the page rendered initially, so that the cell's
    appearance is decided in one template rather than rebuilt 
    in Javascript.
    """
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.args.get("type") or None
    today = datetime.now(DISPLAY_TZ).date()
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        work_state = workstate.fetch_work_state(
            conn, window_start=window_start, window_end=window_end,
            today=today, now=datetime.now(timezone.utc),
        )

    row = {
        "storm_date": day,
        "report_text": report_text,
        "work_state": workstate.state_for(
            work_state, day, report_text, today),
    }
    return render_template("_status_cell.html", row=row,
                           can_pull=g.user["role"] in ("sender", "admin"),
                           actionable_only=_actionable_from_args(),
                           display_tz=DISPLAY_TZ)

@bp.route("/storms/banner")
@login_required
def storm_banner():
    """Banner under the Recent Storm Days Heading, re-rendered while the pull
    the user just initiated is running.  Returns same fragment the page 
    rendered initially, so wording all lives in one template.
    """
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
        zips = int(request.args["zips"])
        since = datetime.fromisoformat(request.args["since"])
    except (KeyError, ValueError):
        abort(400)
    if since.tzinfo is None:
        abort(400)

    report_text = request.args.get("type") or None

    with get_connection() as conn:
        banner = workstate.fetch_pull_banner(
            conn, storm_date=day, report_text=report_text, zips=zips,
            since=since, now=datetime.now(timezone.utc),
        )
    return render_template("_pull_banner.html", banner=banner)
