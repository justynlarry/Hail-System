"""The Address Search map's data for one point (item 313).

Read only and informational:  nothing here reaches a pull, a match, an email or an export.  The radar and
likelihood layers themselves come from the Territory map's URLs (/map/radar.geojson, /map/likelihood.geojson);
this module says which days are worth offering for one address, what is at the address on each, and which city
and county lines to draw near it.
"""

import json

from hailsys.formatting import magnitude

# Hail-size bands for the report dots, smallest first.  Display only, not a tuning value, so they stay in code
# (decision log 2026-11-17).  1 in is the Actionable Only floor, 1.75 in golf ball, 2.5 in tennis ball.  The edges
# are exact in binary, so comparing them with a Decimal magnitude is exact.
HAIL_BANDS = (
    {"key": "h1", "label": "under 1 in", "min_in": None},
    {"key": "h2", "label": "1 to 1.75 in", "min_in": 1.0},
    {"key": "h3", "label": "1.75 to 2.5 in", "min_in": 1.75},
    {"key": "h4", "label": "2.5 in and up", "min_in": 2.5},
)

# The lines are simplified to about 2 m:  small beside the geocoder's block-level error, and it brings a
# Denver-metro box from 438 KB to about 57 KB (checked 2026-11-17).
LINE_SIMPLIFY_DEGREES = 0.00002

# Strongest first, so a point in both core and margin reads as core.
_RADAR_RANK = "ARRAY['core', 'margin', 'radar_indicated', 'outer']"
_LEVEL_RANK = "ARRAY['agree', 'one_source', 'context']"

# ST_MakePoint takes longitude first.
_POINT = "ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)"

# The box reaches twice the radius each way, so the lines run past the circle to the map's edges.  The filter is
# inside each branch so each table's GiST index is used.
_BOUNDARIES_SQL = f"""
WITH box AS (
    SELECT ST_Envelope(ST_Buffer({_POINT}::geography, %(radius_m)s * 2)::geometry) AS g
)
SELECT 'municipal' AS kind, m.name,
       ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(m.geom, box.g), %(tol)s), 6) AS geojson
    FROM municipal_boundaries m, box
  WHERE m.geom && box.g AND ST_Intersects(m.geom, box.g)
UNION ALL
SELECT 'county' AS kind, c.name,
       ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Intersection(c.geom, box.g), %(tol)s), 6) AS geojson
    FROM county_boundaries c, box
  WHERE c.geom && box.g AND ST_Intersects(c.geom, box.g)
  ORDER BY kind, name
"""

# A day is offered when any zone of the set in use touches the circle, so a storm that clipped the edge is listed;
# what is at the address itself is said beside it.  A NULL likelihood set (none built yet) matches no rows.
_DAYS_SQL = f"""
WITH pt AS (SELECT {_POINT} AS g),
circle AS (SELECT ST_Buffer(pt.g::geography, %(radius_m)s)::geometry AS g FROM pt),
days AS (
    SELECT z.day FROM radar_zones z, circle
      WHERE z.set_id = %(radar_set)s AND z.day BETWEEN %(first)s AND %(last)s
        AND ST_Intersects(z.geom, circle.g)
    UNION
    SELECT l.day FROM likelihood_zones l, circle
      WHERE l.set_id = %(likelihood_set)s AND l.day BETWEEN %(first)s AND %(last)s
        AND ST_Intersects(l.geom, circle.g)
)
SELECT days.day,
       (SELECT z.layer FROM radar_zones z, pt
          WHERE z.day = days.day AND z.set_id = %(radar_set)s AND ST_Intersects(z.geom, pt.g)
          ORDER BY array_position({_RADAR_RANK}, z.layer) LIMIT 1) AS radar,
       (SELECT l.level FROM likelihood_zones l, pt
          WHERE l.day = days.day AND l.set_id = %(likelihood_set)s AND ST_Intersects(l.geom, pt.g)
          ORDER BY array_position({_LEVEL_RANK}, l.level) LIMIT 1) AS level
    FROM days
  ORDER BY days.day DESC
"""


def hail_band(report_text, value, unit):
    """The dot's band key:  h1 to h4 for a hail report with a size in inches, 'other' for anything else.

    is_finite() first:  an ordered comparison on a Decimal NaN raises (CLAUDE.md, known data traps).
    """
    if report_text != "HAIL" or value is None or unit != "inches" or not value.is_finite():
        return "other"
    band = "other"
    for b in HAIL_BANDS:
        if b["min_in"] is None or value >= b["min_in"]:
            band = b["key"]
    return band


def report_point(r):
    """One row of search.py's reports query as the map draws it.  Formatted here, so the script formats nothing."""
    return {
        "lat": float(r["latitude"]),
        "lon": float(r["longitude"]),
        "local_time": r["local_time"].strftime("%Y-%m-%d %H:%M"),
        "report_text": r["report_text"],
        "magnitude_display": magnitude(r["magnitude"], r["mag_unit"]),
        "distance": f"{r['distance_miles']:.1f} mi",
        "report_source": r["report_source"],
        "band": hail_band(r["report_text"], r["magnitude"], r["mag_unit"]),
    }


def boundaries_geojson(conn, lat, lon, radius_m):
    """The city and county lines near the point, clipped and simplified, as a FeatureCollection whose features carry
    kind ('municipal' or 'county') and name."""
    rows = conn.execute(_BOUNDARIES_SQL, {"lat": lat, "lon": lon, "radius_m": radius_m,
                                          "tol": LINE_SIMPLIFY_DEGREES}).fetchall()
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"kind": r["kind"], "name": r["name"]},
             "geometry": json.loads(r["geojson"])}
            for r in rows
        ],
    }


def radar_days(conn, lat, lon, radius_m, first, last, radar_set_id, likelihood_set_id):
    """[{day, radar, level}], newest first:  each day from first to last (inclusive) whose radar or likelihood zones
    touch the circle, with the strongest radar layer and likelihood level at the point itself (None for none)."""
    return conn.execute(_DAYS_SQL, {"lat": lat, "lon": lon, "radius_m": radius_m, "first": first, "last": last,
                                    "radar_set": radar_set_id, "likelihood_set": likelihood_set_id}).fetchall()


def default_day(days):
    """The day the picker opens on:  the newest with Core or Agree at the address, else the newest listed, else None."""
    for d in days:
        if d["radar"] == "core" or d["level"] == "agree":
            return d["day"]
    return days[0]["day"] if days else None
