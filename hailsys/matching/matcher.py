"""Storm-to-Listing matching, writes storm_listing_matches
showing that this listing was within N miles of this storm report

Explicit manual step, not automated after a pull.

Eligibility, decided 2026-09-18:
  - Active listings only (what's actually for sale now)
  - New Construction excluded -- a new roof is not a hail claim, and the
    only play there is representing a buyer after purchase
  - actionable_only reports (roof-relevant, at or above min_magnitude),
    matching how the storm browser already filters
"""

import logging

from hailsys.tuning import DEFAULT_MATCH_RADIUS_MILES, miles_to_metres

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
ON CONFLICT (iem_id, listing_id, radius_used) DO NOTHING
RETURNING match_id
"""

def match_storm(conn, *, emp_id, window_start, window_end, report_text=None,
                radius_miles=DEFAULT_MATCH_RADIUS_MILES):
    """Compute and store matches for 1 storm window, and return number of
    NEW match rows written, idempotent.
    """
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
    conn.commit()

    logger.info("event=match_complete emp_id=%s window_start=%s report_text=%s "
                "radius_miles=%s new matches=%d",
                emp_id, window_start, report_text, radius_miles, new_matches)
    return new_matches