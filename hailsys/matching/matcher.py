"""Storm-to-Listing matching, writes storm_listing_matches
showing that this listing was within N miles of this storm report

Runs two ways: automatically after a user-initiated pull, by
hailsys/web/jobs.py, and on demand from the /match POST.  It is never
scheduled -- nothing here runs without a person having started a pull or
clicked Match.

Eligibility, decided 2026-09-18:
  - Active listings only (what's actually for sale now)
  - New Construction excluded -- a new roof is not a hail claim, and the
    only play there is representing a buyer after purchase
  - actionable_only reports (roof-relevant, at or above min_magnitude),
    matching how the storm browser already filters

Added 2026-09-22 (decision log, "Land matches removed"):
  - Land excluded -- vacant land has no roof and no hail claim.  Written as
    IS DISTINCT FROM so a property with no recorded type is still matched.
    Governs future matching only; it does not remove existing matches.
"""

import logging

from hailsys.settings import fetch_settings
from hailsys.tuning import miles_to_metres

logger = logging.getLogger(__name__)

# Distance is point-to-point, coordinates -> coordinates.
# It's NOT the nearest-edge-of-zip used by storms.py, this
# is how far is this property from a reported weather event

_DISTANCE_EXPR = "(ST_Distance(i.geom::geography, p.geom::geography) / 1609.344)"

_MATCH_SQL = f"""
INSERT INTO storm_listing_matches
    (iem_id, listing_id, distance_miles, radius_used, matched_at, emp_id)
SELECT
    i.iem_id,
    l.listing_id,
    round({_DISTANCE_EXPR}::numeric, 2),
    %(radius_miles)s,
    now(),
    %(emp_id)s
FROM iem_data i
JOIN report_types t
    ON t.report_type = i.report_type
    AND t.report_text = i.report_text
JOIN properties p
    ON ST_DWithin(i.geom::geography, p.geom::geography, %(radius_m)s)
JOIN listings l
    on l.rentcast_id = p.rentcast_id
WHERE i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
    -- actionable_only, storm browser applies this rule as well
    AND t.roof_relevant
    AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
    AND l.list_status = 'Active'
    AND (l.list_type IS DISTINCT FROM 'New Construction')
    AND (p.property_type IS DISTINCT FROM 'Land')
ON CONFLICT (iem_id, listing_id, radius_used) DO NOTHING
RETURNING match_id
"""

_RUN_START_SQL = """
INSERT INTO match_runs
    (emp_id, storm_date, report_text, radius_miles, run_status)
VALUES
    (%(emp_id)s, %(storm_date)s, %(report_text)s, %(radius_miles)s, 'running')
RETURNING match_run_id
"""

_RUN_FINISH_SQL = """
UPDATE match_runs
    SET finished_at = now(),
        matches_created = %(matches_created)s,
        run_status = 'complete'
 WHERE match_run_id = %(match_run_id)s
"""

_RUN_FAIL_SQL = """
UPDATE match_runs
    SET finished_at = now(),
        run_status = 'failed',
        error_detail = %(error_detail)s
 WHERE match_run_id = %(match_run_id)s
"""


def match_storm(conn, *, emp_id, storm_date, window_start, window_end, report_text=None,
                radius_miles=None):
    """Compute and store matches for 1 storm window, and return number of
    NEW match rows written, idempotent.

    report_text is required, a match_runs row has to name one storm day and
    one type, or the work-state query can't tell which badge to change.
    """
    if radius_miles is None:
        radius_miles = fetch_settings(conn)["match_radius_miles"]

    with conn.cursor() as cur:
        cur.execute(_RUN_START_SQL, {
            "emp_id": emp_id,
            "storm_date": storm_date,
            "report_text": report_text,
            "radius_miles": radius_miles,
         })
        match_run_id = cur.fetchone()["match_run_id"]
    conn.commit()

    try:
        with conn.cursor() as cur:
            cur.execute(_MATCH_SQL, {
                "emp_id": emp_id,
                "radius_miles": radius_miles,
                "radius_m": miles_to_metres(radius_miles),
                "window_start": window_start,
                "window_end": window_end,
                "report_text": report_text,
            })
            new_matches = len(cur.fetchall())
            cur.execute(_RUN_FINISH_SQL, {
                "match_run_id": match_run_id,
                "matches_created": new_matches,
            })
        conn.commit()
    except Exception as e:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(_RUN_FAIL_SQL, {
                "match_run_id": match_run_id,
                "error_detail": str(e)[:1000],
            })
        # Commit before re-raising: the caller's `with get_connection()`
        # rolls back on the way out, which would undo this UPDATE and leave
        # the run at 'running' forever.
        conn.commit()
        raise

    logger.info("event=match_complete emp_id=%s window_start=%s report_text=%s "
                "radius_miles=%s new matches=%d",
                emp_id, window_start, report_text, radius_miles, new_matches)
    return new_matches