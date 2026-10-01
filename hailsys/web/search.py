"""Address Search:  does this address have storm reports nearby.

Informational only:  does not touch listings, realtors, or send
pipeline.  Geocoding comes from the Census Geocoder (hailsys/geocode.py)
cached in geocode_cache.
"""

import logging

from datetime import datetime, timedelta

from flask import Blueprint, g, render_template, request

from hailsys import geocode
from hailsys.db import get_connection
from hailsys.queries import storms
from hailsys.web.auth import login_required
from hailsys.tuning import (
    DISPLAY_TZ, METRES_PER_MILE, denver_day_bounds, miles_to_metres,
)
from hailsys.settings import fetch_settings

logger = logging.getLogger(__name__)

search_bp = Blueprint("search", __name__, url_prefix="/search")

# This is one point against a GiST index, so the 400-day clamp in
# views._window_from_args doesn't apply.  A year back is the default
# because that is the span a homeowner or agent asks about.
DEFAULT_SEARCH_DAYS = 365


def _parse_day(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None

def _search_window():
    """(start_day, end_day, window_start, window_end), unclamped
    """
    # Container clock is UTC, so date.today() rolls over at 6pm
    # Denver and loses the newest storm day.
    today = datetime.now(DISPLAY_TZ).date()

    start_day = _parse_day(request.args.get("start"))
    end_day = _parse_day(request.args.get("end"))

    if start_day is None or end_day is None:
        start_day = today - timedelta(days=DEFAULT_SEARCH_DAYS)
        end_day = today

    if start_day > end_day:
        start_day, end_day = end_day, start_day

    window_start, _ = denver_day_bounds(start_day)
    _, window_end = denver_day_bounds(end_day)
    return start_day, end_day, window_start, window_end

# address_key() computed in SQL, not Python.  Reads address_standardizer's
# reference tables, and any approximation could diverge into cache misses
# and duplicate rows.
_CACHE_LOOKUP_SQL = """
SELECT geocode_id, matched_address, latitude, longitude,
        address_key(%(raw)s) AS address_key
    FROM geocode_cache
  WHERE address_key = address_key(%(raw)s)
"""

# When the cache misses, the system still needs the key, and address_key() may
# return NULL (or no house number).
_KEY_ONLY_SQL = "SELECT address_key(%(raw)s) AS address_key"

_CACHE_INSERT_SQL = """
INSERT INTO geocode_cache
        (address_key, query_raw, matched_address,
        latitude, longitude, tiger_line_id)
VALUES (%(address_key)s, %(raw)s, %(matched_address)s,
        %(latitude)s, %(longitude)s, %(tiger_line_id)s)
ON CONFLICT (address_key) DO NOTHING
    RETURNING geocode_id
"""

# hail_app has INSERT but not UPDATE on geocode_cache, and DO UPDATE needs
# UPDATE privilege even when nothing conflicts.  DO NOTHING returns no row on
# a conflict, so the id is fetched separately.
_CACHE_ID_SQL = "SELECT geocode_id FROM geocode_cache WHERE address_key = %(address_key)s"

_REPORTS_SQL = """
SELECT  i.iem_id,
        (i.utc_datetime AT TIME ZONE 'America/Denver') AS local_time,
        i.report_text,
        i.magnitude,
        t.mag_unit,
        i.report_source,
        i.remark,
        ST_Distance(i.geom::geography,
                    ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography)
                / %(metres_per_mile)s AS distance_miles
    FROM iem_data i
    JOIN report_types t
      ON t.report_type = i.report_type
      AND t.report_text = i.report_text
  WHERE ST_DWithin(i.geom::geography,
                    ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
                    %(radius_m)s)
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
    AND i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
  ORDER BY i.utc_datetime DESC
"""


_LOG_SQL = """
INSERT INTO address_searches
        (query_raw, address_key, geocode_id, outcome,
         reports_found, range_start, range_end, searched_by)
VALUES (%(raw)s, %(address_key)s, %(geocode_id)s, %(outcome)s,
        %(reports_found)s, %(range_start)s, %(range_end)s, %(searched_by)s)
"""


@search_bp.route("/")
@login_required
def search():
    raw = (request.args.get("address") or "").strip()
    start_day, end_day, window_start, window_end = _search_window()

    # Fetched on every render, including the empty form, so the dropdown is
    # never blank.  Same list the storm-days page offers.
    with get_connection() as conn:
        types = storms.fetch_report_types(conn)

    # Parameterized, so an arbitrary value is safe, but one outside the list
    # would silently return zero reports.  Treat it as "All".
    report_text = request.args.get("type") or None
    if report_text not in types:
        report_text = None

    if not raw:
        return render_template("search.html",
                                start_day=start_day, end_day=end_day,
                                types=types, selected_type=report_text)

    outcome = None
    reports = []
    match = None
    geocode_id = None
    address_key = None
    error_message = None

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(_CACHE_LOOKUP_SQL, {"raw": raw})
        cached = cur.fetchone()

        if cached:
            geocode_id = cached["geocode_id"]
            address_key = cached["address_key"]
            match = cached
        else:
            cur.execute(_KEY_ONLY_SQL, {"raw": raw})
            address_key = cur.fetchone()["address_key"]

            if address_key is None:
                outcome = "unparseable"
                matches = []
            else:
                try:
                    matches = geocode.geocode(raw)
                except geocode.GeocodeError as exc:
                    logger.warning("address search failed raw=%r err=%s", raw, exc)
                    outcome = "service_error"
                    error_message = exc.user_message
                    matches = []

                if outcome is None and not matches:
                    outcome = "no_match"
                elif outcome is None:
                    match = matches[0]
                    cur.execute(_CACHE_INSERT_SQL, {
                        "address_key": address_key,
                        "raw": raw,
                        "matched_address": match["matched_address"],
                        "latitude": match["latitude"],
                        "longitude": match["longitude"],
                        "tiger_line_id": match["tiger_line_id"],
                    })
                    inserted = cur.fetchone()
                    if inserted is None:
                        # Another request cached this address first.
                        cur.execute(_CACHE_ID_SQL, {"address_key": address_key})
                        inserted = cur.fetchone()
                    geocode_id = inserted["geocode_id"]
        if match is not None:
            radius_miles = fetch_settings(conn)["match_radius_miles"]

            cur.execute(_REPORTS_SQL, {
                "lat": match["latitude"],
                "lon": match["longitude"],
                "radius_m": miles_to_metres(radius_miles),
                "metres_per_mile": METRES_PER_MILE,
                "report_text": report_text,
                "window_start": window_start,
                "window_end": window_end,
            })
            reports = cur.fetchall()
            outcome = "matched"
        else:
            radius_miles = None

        cur.execute(_LOG_SQL, {
            "raw": raw,
            "address_key": address_key,
            "geocode_id": geocode_id,
            "outcome": outcome,
            "reports_found": len(reports) if outcome == "matched" else None,
            "range_start": start_day,
            "range_end": end_day,
            "searched_by": g.user["emp_id"],
        })

    return render_template(
        "search.html",
        raw=raw,
        outcome=outcome,
        match=match,
        reports=reports,
        radius_miles=radius_miles,
        start_day=start_day,
        end_day=end_day,
        types=types,
        selected_type=report_text,
        error_message=error_message,
    )
