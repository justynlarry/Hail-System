"""Reads for the findings panel under the Territory map.

Read only.  Everything is for one local day and the parameter set in use (the newest row of
radar_parameter_sets), the same rule as radar_map.py.  A day built with another set has no 
zones under the set in use, so its numbers read as 'none'.  The caller checks that first
(radar_map.zones_geojson names such days).
"""

from hailsys.queries.radar_map import in_use_set
from hailsys.tuning import DISPLAY_TZ, denver_day_bounds

# Highest first.  A report in two layers is counted once, in the first of these it is in.
# The layers are not expected to overlap, but the NWS warnings are drawn from a different
# source, so the order is fixed here rather than assumed.
PRECEDENCE = ("core", "margin", "radar_indicated", "outer")

# The day's shapes by layer.  The stored zones, plus for a day with no stored outer layer the 
# raw warning polygons valid during its local day.  A day built with warnings_used NULL counts
# as having none stored.  Same rule as radar_map._LAYERS_SQL, if one changes both do.

_REPORTS_SQL = """
WITH picked AS (
    SELECT z.layer, z.geom
    FROM radar_zones z
    WHERE z.day = %(day)s AND z.set_id = %(set_id)s
  UNION ALL
    SELECT 'outer', p.geom
    FROM warning_polygons p
    WHERE p.poly_begin < %(day_end)s AND p.poly_end > %(day_start)s
      AND NOT EXISTS (SELECT 1 FROM radar_zone_builds b
                      WHERE b.day = %(day)s AND b.set_id = %(set_id)s AND b.warnings_used IS NOT NULL)
), layers AS (
    SELECT layer, ST_Union(geom) AS g FROM picked GROUP BY layer
), reports AS (
    SELECT i.iem_id, i.magnitude, i.geom
    FROM iem_data i
    JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
    WHERE i.utc_datetime >= %(day_start)s AND i.utc_datetime < %(day_end)s
      AND i.report_text = %(report_text)s
      AND t.roof_relevant
      AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
)
SELECT r.iem_id, r.magnitude,
    (SELECT l.layer FROM layers l
      WHERE ST_Intersects(l.g, r.geom)
      ORDER BY array_position(%(precedence)s::text[], l.layer)
      LIMIT 1) AS layer
FROM reports r
"""


def reports_by_layer(conn, day, report_text="HAIL", tz=DISPLAY_TZ):
    """The actionable reports of one type on one local day, each counted once under the highest layer it is in.

    'actionable' is the matcher's rule.  The type is roof relevant and the size is at least
    the type's minimum.  Returns {'total', 'largest_in', 'by_layer'}.  'largest_in' is the
    largest counted size as a float, None when there are no reports.  'by_layer' always has
    every layer and 'none' as keys.
    """
    day_start, day_end = denver_day_bounds(day)
    rows = conn.execute(_REPORTS_SQL, {
        "day": day, "set_id": in_use_set(conn)["set_id"], "day_start": day_start, "day_end": day_end,
        "report_text": report_text, "precedence": list(PRECEDENCE)}).fetchall()
    by_layer = {name: 0 for name in PRECEDENCE}
    by_layer["none"] = 0
    for r in rows:
        by_layer[r["layer"] or "none"] += 1
    sizes = [float(r["magnitude"]) for r in rows if r["magnitude"] is not None]
    return {"total": len(rows), "largest_in": max(sizes) if sizes else None, "by_layer": by_layer}

# Cold season, October -> March.  Radar hail that no report confirms is expected in summer
# and suspicious in winter, so winter days carry a flag.  A code constant for now, item 238 says
# it should become a setting.
COLD_SEASON_MONTHS = (10, 11, 12, 1, 2, 3)

_MM_PER_INCH = 25.4

_OVERVIEW_SQL = """
SELECT d.status, d.max_mm, b.set_id IS NOT NULL AS built, b.patches, b.patches_anchored,
    EXISTS (SELECT 1 FROM radar_zone_builds o
            WHERE o.day = %(day)s AND o.set_id <> %(set_id)s) AS built_with_other,
    (SELECT count(*) FROM warning_polygons p
      WHERE p.poly_begin < %(day_end)s AND p.poly_end > %(day_start)s) AS warnings
FROM (SELECT 1) one
LEFT JOIN radar_days d ON d.day = %(day)s
LEFT JOIN radar_zone_builds b ON b.day = %(day)s AND b.set_id = %(set_id)s
"""

_AREAS_SQL = """
SELECT layer, area_km2 FROM radar_zones WHERE day = %(day)s AND set_id = %(set_id)s
"""


def day_overview(conn, day, tz=DISPLAY_TZ):
    """What the radar job stored for one local day, under the parameter set in use.
    
    'status' is the radar_days status ('loaded', 'missing', 'suspect') or None when the day
    was never fetched.  'built' is whether zones exist under the set in use.  'built_other_set'
    is a day built only with an older set (nothing is shown for it).  'max_in' is the largest
    radar value of the day in inches (MESH is stored in mm), None without a row.  'areas_km2'
    has every layer, 0.0 for one the day does not have.  'warnings' counts the NWS warning
    polygons valid during the local day.
    """
    day_start, day_end = denver_day_bounds(day)
    args = {"day": day, "set_id": in_use_set(conn)["set_id"], "day_start": day_start, "day_end": day_end}
    row = conn.execute(_OVERVIEW_SQL, args).fetchone()
    areas = {name: 0.0 for name in PRECEDENCE}
    for r in conn.execute(_AREAS_SQL, args).fetchall():
        areas[r["layer"]] = float(r["area_km2"])
    return {
        "day": day, "status": row["status"], "built": row["built"],
        "built_other_set": row["built_with_other"] and not row["built"],
        "patches": row["patches"], "patches_anchored": row["patches_anchored"],
        "max_in": None if row["max_mm"] is None else round(float(row["max_mm"]) / _MM_PER_INCH, 2),
        "areas_km2": areas, "warnings": row["warnings"],
    }

# The circles are the match radius around each actionable report, the same reports reports_by_layer
# counts (same rule as _REPORTS_SQL, if one changes both do).  Core and margin are the layers
# the radar itself supports.  radar_indicated has no report near it by definition and the 
# warnings are context, so neither confirms a circle.  The EMPTY polygon stands in for 
# 'no circles' so a day with none still reports its core and margin as beyond them.
_CIRCLES_SQL = """
WITH radar AS (
    SELECT ST_Union(geom) AS g FROM radar_zones
    WHERE day = %(day)s AND set_id = %(set_id)s AND layer IN ('core', 'margin')
), circles AS (
    SELECT coalesce(ST_Union(ST_Buffer(i.geom::geography, %(radius_m)s)::geometry),
                             ST_GeomFromText('POLYGON EMPTY', 4326)) AS g
    FROM iem_data i
    JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
    WHERE i.utc_datetime >= %(day_start)s AND i.utc_datetime < %(day_end)s
        AND i.report_text = %(report_text)s
        AND t.roof_relevant
        AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
)
SELECT coalesce(ST_Area(c.g::geography), 0) / 1000000.0 AS circle_km2,
       coalesce(ST_Area(ST_Intersection(c.g, r.g)::geography), 0) / 1000000.0 AS confirmed_km2,
       coalesce(ST_Area(ST_Difference(r.g, c.g)::geography), 0) / 1000000.0 AS beyond_km2
FROM circles c CROSS JOIN radar r
"""

def circle_coverage(conn, day, radius_m, report_text="HAIL", tz=DISPLAY_TZ):
    """The day's report circles against the radar's core and margin, areas in km2.
    
    'circle_km2' is the union of the circles (overlaps counted once).  'confirmed_km2' is the part
    of it inside core or margin and 'confirmed_share' that as a fraction of the circles, None when
    there are no circles.  'beyond_km2' is core and margin area outside every circle: radar hail
    no report covers.
    """
    day_start, day_end = denver_day_bounds(day)
    row = conn.execute(_CIRCLES_SQL, {
        "day": day, "set_id": in_use_set(conn)["set_id"], "day_start": day_start, "day_end": day_end,
        "report_text": report_text, "radius_m": radius_m}).fetchone()
    circle = float(row["circle_km2"])
    confirmed = float(row["confirmed_km2"])
    return {"circle_km2": circle, "confirmed_km2": confirmed,
            "confirmed_share": confirmed / circle if circle > 0 else None,
            "beyond_km2": float(row["beyond_km2"])}


def day_flags(overview, reports, day):
    """The names of what to warn about for this day, from day_overview and reports_by_layer.

    Why the radar has nothing to show comes first and is one cause only.  A suspect day, a day
    built with an older set, or no radar zones.  Then 'no_reports'.  Then 'cold_season_unreported'.
    October -> March, radar hail with no report anywhere that day, which in winter is more likely
    a radar artefact than hail.    
    """
    flags = []
    if overview["status"] == "suspect":
        flags.append("suspect_day")
    elif overview["built_other_set"]:
        flags.append("older_set")
    elif not overview["built"]:
        flags.append("no_radar")
    if reports["total"] == 0:
        flags.append("no_reports")
        if day.month in COLD_SEASON_MONTHS and overview["areas_km2"]["radar_indicated"] > 0:
            flags.append("cold_season_unreported")
    return flags


# Distance bands for the listing matrix, in miles.  The upper edge belongs to the lower band.
# A house exactly 1 mile from the nearest report is 'le_1'.  Reports farther than the last edge
# are 'over_5', 'none within 5 miles'.
BANDS = ("le_1", "1_3", "3_5", "over_5")
MATRIX_ROWS = PRECEDENCE + ("none",)
_METRES_PER_MILE = 1609.344

# Held houses as the matcher sees them (matcher.py _MATCH_SQL, if one changes, both do).
# A house is held if one of its listings is Active and not New Construction and the property
# is not land.  Written as IS DISTINCT FROM so a house with no recorded type is still held.
# One row per house, a relisted house has several listing rows and is one house for the
# one-email-per-house rule.
_HELD_HOUSES = """
    SELECT p.rentcast_id, p.geom
    FROM properties p
    WHERE p.property_type IS DISTINCT FROM 'Land'
      AND EXISTS (SELECT 1 FROM listings l
                  WHERE l.rentcast_id = p.rentcast_id
                  AND l.list_status = 'Active'
                  AND l.list_type IS DISTINCT FROM 'New Construction')
"""

_BOX_SQL = """
SELECT box_lon_min AS lon_min, box_lat_min AS lat_min, box_lon_max AS lon_max, box_lat_max AS lat_max
FROM radar_days WHERE day = %(day)s AND status = 'loaded'
"""

_UNPLACED_SQL = f"""
WITH houses AS ({_HELD_HOUSES})
SELECT count(*) FILTER (WHERE h.geom IS NULL) AS no_location,
       count(*) FILTER (WHERE h.geom IS NOT NULL
                        AND NOT ST_Intersects(h.geom, ST_MakeEnvelope(%(lon_min)s, %(lat_min)s,
                        %(lon_max)s, %(lat_max)s, 4326))) AS outside_box
FROM houses h
"""

# The day's shapes are picked as in _REPORTS_SQL.  Only houses inside the day's crop box are placed.
# Outside it the radar wasn't loaded, so 'none' would be a claim the radar did not make.  The layer
# subquery runs only for the rows that pass the box test.
# layers and reports are MATERIALIZED on purpose.  Each is used once, so Postgres would otherwise fold
# it into the per-house subqueries and redo the ST_Union and the report scan for every house:
# minutes instead of seconds.
_MATRIX_SQL = f"""
WITH picked AS (
    SELECT z.layer, z.geom
    FROM radar_zones z
    WHERE z.day = %(day)s AND z.set_id = %(set_id)s
  UNION ALL
    SELECT 'outer', p.geom
    FROM warning_polygons p
    WHERE p.poly_begin < %(day_end)s AND p.poly_end > %(day_start)s
      AND NOT EXISTS (SELECT 1 FROM radar_zone_builds b
                      WHERE b.day = %(day)s AND b.set_id = %(set_id)s AND b.warnings_used IS NOT NULL)
), layers AS MATERIALIZED (
    SELECT layer, ST_Union(geom) AS g FROM picked GROUP BY layer
), reports AS MATERIALIZED (
    SELECT i.geom
    FROM iem_data i
    JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
    WHERE i.utc_datetime >= %(day_start)s AND i.utc_datetime < %(day_end)s
      AND i.report_text = %(report_text)s
      AND t.roof_relevant
      AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
), houses AS ({_HELD_HOUSES})
SELECT coalesce(x.layer, 'none') AS layer,
    CASE WHEN x.miles IS NULL THEN 'over_5'
         WHEN x.miles <= 1 THEN 'le_1'
         WHEN x.miles <= 3 THEN '1_3'
         ELSE '3_5' END AS band,
    count(*) AS n
FROM (
    SELECT
        (SELECT l.layer FROM layers l
          WHERE ST_Intersects(l.g, h.geom)
          ORDER BY array_position(%(precedence)s::text[], l.layer)
          LIMIT 1) AS layer,
        (SELECT min(ST_Distance(h.geom::geography, r.geom::geography)) / %(mile_m)s
          FROM reports r
         WHERE ST_DWithin(h.geom::geography, r.geom::geography, 5 * %(mile_m)s)) AS miles
    FROM houses h
    WHERE h.geom IS NOT NULL
      AND ST_Intersects(h.geom, ST_MakeEnvelope(%(lon_min)s, %(lat_min)s, %(lon_max)s, %(lat_max)s, 4326))
) x
GROUP BY 1, 2
"""

def listing_matrix(conn, day, report_text="HAIL", tz=DISPLAY_TZ):
    """Held houses by the radar layer at the address (rows) against the distance to the nearest
    actionable report (columns).

    Returns None when the day has no loaded radar (missing, suspect, never fetched): there is 
    nothing to compare against.  Otherwise {'cells', 'total', 'outside_box', 'no_location'}.  'cells' has every row of
    MATRIX_ROWS and every band of BANDS, 0 where empty.  'total' is the houses placed.  'outside_box' counts
    held houses outside the crop box the day was loaded with and 'no_location' held houses with no coordinates;
    neither is in the matrix.  The status of a listing is as of its last pull, not as of the storm day.
    """
    box = conn.execute(_BOX_SQL, {"day": day}).fetchone()
    if box is None:
        return None
    day_start, day_end = denver_day_bounds(day)
    unplaced = conn.execute(_UNPLACED_SQL, box).fetchone()
    rows = conn.execute(_MATRIX_SQL, dict(
        box, day=day, set_id=in_use_set(conn)["set_id"], day_start=day_start, day_end=day_end,
        report_text=report_text, precedence=list(PRECEDENCE), mile_m=_METRES_PER_MILE)).fetchall()
    cells = {name: {band: 0 for band in BANDS} for name in MATRIX_ROWS}
    for r in rows:
        cells[r["layer"]][r["band"]] += r["n"]
    return {"cells": cells, "total": sum(r["n"] for r in rows),
            "outside_box": unplaced["outside_box"], "no_location": unplaced["no_location"]}

# The DRAFT tier ladder:  Points for report proximity plus points for the radar layer at the address.
# 5 - 6 is A 3-4 is B, 1 to 2 is C, 0 has no letter.  Shown as an annotation on the listing matrix
# and used for nothing else.  Radar with no report within 5 miles is not capped.
_BAND_POINTS = {"le_1": 3, "1_3": 2, "3_5": 1, "over_5": 0}
_LAYER_POINTS = {"core": 3, "margin": 2, "radar_indicated": 1, "outer": 0, "none": 0}


def _letter(points):
    if points >= 5:
        return "A"
    if points >= 3:
        return "B"
    if points >= 1:
        return "C"
    return ""

def draft_tiers():
    """Draft tier letter for each cell of the listing matrix: {layer: {band: 'A' | 'B' | 'C' | ''}}."""
    return {name: {band: _letter(_LAYER_POINTS[name] + _BAND_POINTS[band]) for band in BANDS}
            for name in MATRIX_ROWS}
