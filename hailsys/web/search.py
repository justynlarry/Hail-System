"""Address Search:  does this address have storm reports nearby.

Informational only:  does not touch listings, realtors, or send
pipeline.  Geocoding comes from the Census Geocoder (hailsys/geocode.py)
cached in geocode_cache.
"""

import logging

from datetime import datetime, timedelta

import psycopg
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from hailsys import geocode
from hailsys.db import get_connection
from hailsys.queries import jurisdiction, storms
from hailsys.web.auth import login_required, role_required
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

# What the check form offers.  The keys are jurisdiction_checks.cause's CHECK values (sql/069).
CHECK_CAUSES = {
    "geocoder": "The address landed in the wrong place",
    "boundary": "The boundary data is wrong",
    "book_outdated": "The book is out of date (an annexation, a new build)",
    "unknown": "Not known yet",
}


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
RETURNING search_id
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
                                raw="", outcome=None,
                                start_day=start_day, end_day=end_day,
                                types=types, selected_type=report_text)

    outcome = None
    reports = []
    match = None
    geocode_id = None
    address_key = None
    error_message = None
    jur = None

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
            settings = fetch_settings(conn)
            radius_miles = settings["match_radius_miles"]

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
            # The same point the reports were found around:  one geocode answers both.
            jur = jurisdiction.lookup(conn, match["latitude"], match["longitude"],
                                      settings["jurisdiction_near_boundary_m"])
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
        search_id = cur.fetchone()["search_id"]

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
        jurisdiction=jur,
        boundaries_date=(jur["boundaries_loaded_at"].astimezone(DISPLAY_TZ).date().isoformat()
                         if jur and jur["loaded"] else None),
        search_id=search_id,
        can_check=g.user["role"] == "admin",
        check_causes=CHECK_CAUSES,
    )


_CHECK_SEARCH_SQL = """
SELECT s.search_id, s.query_raw, c.latitude, c.longitude
    FROM address_searches s
    JOIN geocode_cache c ON c.geocode_id = s.geocode_id
  WHERE s.search_id = %(search_id)s
    AND s.outcome = 'matched'
"""

_CHECK_INSERT_SQL = """
INSERT INTO jurisdiction_checks
        (search_id, place_count, place_fips, place_name, county_fips, county_name,
         nearest_line_kind, nearest_line_name, nearest_line_m,
         near_boundary, near_boundary_m, boundaries_loaded_at,
         verdict, book_answer, cause, new_build, note, checked_by)
VALUES (%(search_id)s, %(place_count)s, %(place_fips)s, %(place_name)s,
        %(county_fips)s, %(county_name)s,
        %(line_kind)s, %(line_name)s, %(line_m)s,
        %(near)s, %(near_m)s, %(loaded_at)s,
        %(verdict)s, %(book_answer)s, %(cause)s, %(new_build)s, %(note)s, %(checked_by)s)
"""


@search_bp.route("/check", methods=["POST"])
@role_required("admin")
def record_check():
    """Record the jurisdiction book's answer against the system's, for one search (item 190).

    The system's side is looked up again here, from the search's own geocoded point, and never
    taken from the form:  the form carries only the search and the book's side.  Afterwards it is
    back to an empty search form, ready for the next address.
    """
    try:
        search_id = int(request.form.get("search_id") or "")
    except ValueError:
        flash("That check had no search to go with it.  Nothing was recorded.")
        return redirect(url_for("search.search"))

    verdict = request.form.get("verdict")
    book_answer = (request.form.get("book_answer") or "").strip() or None
    cause = request.form.get("cause") or None
    note = (request.form.get("note") or "").strip() or None
    new_build = request.form.get("new_build") == "1"

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(_CHECK_SEARCH_SQL, {"search_id": search_id})
        found = cur.fetchone()
        if found is None:
            flash("That search has no location to check.  Nothing was recorded.")
            return redirect(url_for("search.search"))

        # Searching again makes a new search, which can be checked:  the way back when the
        # form was not filled in.
        again = url_for("search.search", address=found["query_raw"])
        if verdict == "agrees":
            cause = None        # the drop-down may still show a reason; agreeing has none
        elif verdict == "differs":
            if cause not in CHECK_CAUSES or book_answer is None:
                flash("When the book differs, say what it says and pick a reason.  "
                      "Nothing was recorded.")
                return redirect(again)
        else:
            flash("Pick whether the book says the same.  Nothing was recorded.")
            return redirect(again)

        near_m = fetch_settings(conn)["jurisdiction_near_boundary_m"]
        answer = jurisdiction.lookup(conn, found["latitude"], found["longitude"], near_m)
        if not answer["loaded"]:
            flash("City boundaries aren't loaded, so there is nothing to check.  "
                  "Nothing was recorded.")
            return redirect(url_for("search.search"))

        first = answer["places"][0] if answer["places"] else None
        county = answer["county"]
        line = answer["nearest_line"]
        try:
            cur.execute(_CHECK_INSERT_SQL, {
                "search_id": search_id,
                "place_count": len(answer["places"]),
                "place_fips": first["place_fips"] if first else None,
                "place_name": first["name"] if first else None,
                "county_fips": county["county_fips"] if county else None,
                "county_name": county["name"] if county else None,
                "line_kind": line["kind"] if line else None,
                "line_name": line["name"] if line else None,
                "line_m": line["distance_m"] if line else None,
                "near": answer["near_boundary"],
                "near_m": near_m,
                "loaded_at": answer["boundaries_loaded_at"],
                "verdict": verdict,
                "book_answer": book_answer,
                "cause": cause,
                "new_build": new_build,
                "note": note,
                "checked_by": g.user["emp_id"],
            })
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash("That search was already checked.  Search the address again to check it again.")
            return redirect(url_for("search.search"))
        except psycopg.errors.CheckViolation:
            conn.rollback()
            flash("The book's answer can be up to 200 characters, and the note up to 1,000.  "
                  "Nothing was recorded.")
            return redirect(again)
        conn.commit()

    said = "the same" if verdict == "agrees" else f"something else ({CHECK_CAUSES[cause].lower()})"
    flash(f"Check recorded for {found['query_raw']}: the book says {said}.")
    return redirect(url_for("search.search"))
