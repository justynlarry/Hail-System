from datetime import datetime, timedelta

from flask import Blueprint, render_template, abort, request

from hailsys.db import get_connection
from hailsys.queries import storms
from hailsys.tuning import (
    DEFAULT_ZIP_RADIUS_MILES,
    DISPLAY_TZ,
    denver_day_bounds,
    miles_to_metres,
)

bp = Blueprint("main", __name__)

@bp.route("/")
def index():
    # NOT date.today(), the container's clock is UTC, so after 6pm Denver
    # that returns 'tomorrow' and the newest storm day falls outside
    # the window.  Any 'what day is it' question goes through DISPLAY_TZ.
    today = datetime.now(DISPLAY_TZ).date()

    window_start, _ = denver_day_bounds(today - timedelta(days=365))
    _, window_end = denver_day_bounds(today)

    with get_connection() as conn:
        rows = storms.fetch_recent_days(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=None,
            actionable_only=True,
            limit=10,   
        )

    return render_template("storms.html", rows=rows)

@bp.route("/storms/zips")
def storm_zips():
    # Query Parameters, not path segments.  report_text contains spaces and
    # slashes ('NON-TSTM WIND GST'), slash segment is a route boundary.
    try:
        day = datetime.strptime(request.args["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        abort(400)

    report_text = request.args.get("type") or None
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        rows = storms.fetch_zips(
            conn,
            radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start,
            window_end=window_end,
            report_text=report_text,
        )

    return render_template("_zips.html", rows=rows)