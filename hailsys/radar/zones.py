"""Builds the radar zones for the one local day from the cells the loader stored.

core            patches of hail at or above the threshold that have an actionable report
                within report_radius_km
margin          margin_km around the core, outside it
radar_indicated patches no report is near, outside core and margin

All geometry is PostGIS, a cell is its 0.01 degree square, patches are 8-neighbor groups,
distances and the margin buffer use geography, so there is no projection or zone constant
here.  The time zone is an argument.  Runs on the connection it is given and leaves the
commit to the caller.  Nothing here reads or writes email, listings, or matches.
"""


from datetime import datetime, time, timedelta

from hailsys.radar.mesh import MM_PER_INCH, STEP

# Two cells touch, diagonals included, when their centres are within 0.0142 degrees.  A
# diagonal is 0.01414 and the next cell out is 0.02.  Same patches as the 8-neighbor
# rule the original analysis used.
TOUCH_DEGREES = 0.0142


class ZoneError(Exception):
    """Nothing was built, message explains."""


_PARAMS_SQL = """
SELECT set_id, hail_threshold_in, margin_km, report_radius_km
FROM radar_parameter_sets
WHERE %(set_id)s::bigint IS NULL OR set_id = %(set_id)s
ORDER BY set_id DESC
LIMIT 1
"""


_DAY_SQL = "SELECT status from radar_days WHERE day = %(day)s"

_DELETE_ZONES_SQL = "DELETE FROM radar_zones WHERE day = %(day)s AND set_id = %(set_id)s"
_DELETE_BUILD_SQL = "DELETE FROM radar_zone_builds WHERE day = %(day)s AND set_id = %(set_id)s"

# Parameters cannot be bound in CREATE TABLE ... AS, so the scratch tables are created
# empty and filled by INSERT ... SELECT.
_SCRATCH_SQL = """
CREATE TEMP TABLE radar_anchor_reports (geom geometry(Point, 4326)) ON COMMIT DROP;
CREATE TEMP TABLE radar_work (patch INTEGER, cell geometry, anchored BOOLEAN) ON COMMIT DROP
"""

# Same test as the matcher and the storm screens.  Roof Relevant, and at or above the type's
# minimum magnitude if it has one.  a NULL magnitude fails a minimum.
_REPORTS_SQL = """
INSERT INTO radar_anchor_reports (geom)
SELECT i.geom
FROM iem_data i
JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
WHERE i.utc_datetime >= %(start)s AND i.utc_datetime < %(end)s
    AND t.roof_relevant
    AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
"""

# radar_cells.mm is a 4-byte REAL, so the threshold is cast to REAL too.  25.4 as a double is
# just above 25.4 as REAL, and a cell of exactly 1.00 in would be left out.  Cell corners are
# snapped to the 0.01 degree grid so neighboring squares share edges exactly and the union
# has no hairline gaps.
_WORK_SQL = """
INSERT INTO radar_work (patch, cell, anchored)
WITH hail AS (
    SELECT geom,
        ST_ClusterDBSCAN(geom, eps := %(touch)s, minpoints := 1) OVER () AS patch
    FROM radar_cells
    WHERE day = %(day)s AND mm >= %(threshold_mm)s::real
), near AS (
    SELECT h.patch, h.geom,
        EXISTS (SELECT 1 FROM radar_anchor_reports r
                WHERE ST_DWithin(h.geom::geography, r.geom::geography, %(radius_m)s)) AS near_report
    FROM hail h
)
SELECT patch,
    ST_SnaptoGrid(ST_MakeEnvelope(ST_X(geom) - %(half)s, ST_Y(geom) - %(half)s,
                                  ST_X(geom) + %(half)s, ST_Y(geom) + %(half)s, 4326),
                 %(step)s),
    bool_or(near_report) OVER (PARTITION by patch)
FROM near
"""


_BUILD_SQL = """
INSERT INTO radar_zone_builds (day, set_id, built_by, window_start, window_end,
    cells_over_threshold, patches, patches_anchored, anchor_reports)
SELECT %(day)s, %(set_id)s, %(user_id)s, %(start)s, %(end)s,
        (SELECT count(*) FROM radar_work),
        (SELECT count(DISTINCT patch) FROM radar_work),
        (SELECT count(DISTINCT patch) FrOM radar_work WHERE anchored),
        (SELECT count(*) FROM radar_anchor_reports)
"""


_ZONES_SQL = """
INSERT INTO radar_zones (day, set_id, layer, geom, area_km2)
WITH parts AS (
    SELECT COALESCE(ST_Union(cell) FILTER (WHERE anchored),
                    ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)) AS core,
           COALESCE(ST_Union(cell) FILTER (WHERE NOT anchored),
                    ST_geomFromText('GEOMETRYCOLLECTION EMPTY', 4326)) AS LOOSE
    FROM radar_work
), ringed AS (
    SELECT core, loose,
            CASE WHEN ST_IsEmpty(core) THEN core
                  Else ST_Difference(ST_Buffer(core::geography, %(margin_m)s)::geometry, core)
            END AS margin
    FROM parts
), layers AS (
    SELECT 'core' AS layer, core AS g FROM ringed
    UNION ALL
    SELECT 'margin', margin FROM ringed
    UNION ALL
    SELECT 'radar_indicated', ST_Difference(ST_Difference(loose, core), margin) FROM ringed
), shaped AS (
    SELECT layer, ST_Multi(ST_CollectionExtract(g, 3)) AS g FROM layers
)
SELECT %(day)s, %(set_id)s, layer, g, ST_Area(g::geography) / 1000000.0
FROM shaped
WHERE NOT ST_IsEmpty(g) AND ST_Area(g::geography) > 0
"""


_SUMMARY_SQL = """
SELECT b.cells_over_threshold, b.patches, b.patches_anchored, b.anchor_reports,
        COALESCE((SELECT jsonb_object_agg(z.layer, z.area_km2)
                FROM radar_zones z WHERE z.day = b.day AND z.set_id = b.set_id), '{}'::jsonb) AS areas
FROM radar_zone_builds b
WHERE b.day = %(day)s AND b.set_id = %(set_id)s
"""


def day_window(day, tz):
    """UTC-aware start and end of local date 'day', from local midnight to the next."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def build_day(conn, day, tz, *, set_id=None, user_id=None):
    """Build and store the zones for local date 'day', replacing any built before with the same set.
    
    set_id None uses the newest parameter set.  user_id is who clicked, None for a job.
    Returns the counts and the area in km2 of each layer that is not empty.  ZoneError if
    there is no parameter set or the day has no loaded radar file.
    """
    chosen = conn.execute(_PARAMS_SQL, {"set_id": set_id}).fetchone()
    if chosen is None:
        raise ZoneError("No such parameter set." if set_id else "there is no parameter set.")
    loaded = conn.execute(_DAY_SQL, {"day": day}).fetchone()
    if loaded is None or loaded["status"] != "loaded":
        raise ZoneError(f"{day} has no radar file loaded, so there is nothing to build from.")

    start, end = day_window(day, tz)
    # Decimal * float is a TypeError, so everything is cast here.
    args = {"day": day, "set_id": chosen["set_id"], "user_id": user_id,
            "start": start, "end": end, "touch": TOUCH_DEGREES, "step": STEP, "half": STEP / 2,
            "threshold_mm": float(chosen["hail_threshold_in"]) * MM_PER_INCH,
            "margin_m": float(chosen["margin_km"]) * 1000.0,
            "radius_m": float(chosen["report_radius_km"]) * 1000.0}

    conn.execute(_DELETE_ZONES_SQL, args)
    conn.execute(_DELETE_BUILD_SQL, args)
    conn.execute(_SCRATCH_SQL)
    conn.execute(_REPORTS_SQL, args)
    conn.execute(_WORK_SQL, args)
    conn.execute(_BUILD_SQL, args)
    conn.execute(_ZONES_SQL, args)
    summary = conn.execute(_SUMMARY_SQL, args).fetchone()
    conn.execute("DROP TABLE radar_work, radar_anchor_reports")
    return {"set_id": chosen["set_id"], **summary}