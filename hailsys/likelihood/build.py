"""Builds the hail-likelihood zones for one local day (Phase 5c, step 1b).

The radar layers are found the way hailsys/radar/zones.py finds them (it supplies the patch
SQL), but a patch is anchored by actionable HAIL reports from both IEM and CoCoRaHS.  Then each
piece of a layer, cut by its distance to the nearest of those reports, gets its level from
the parameter set's rules:

    layer (core, margin, radar_indicated, outer, none)  x  band (le_1, 1_3, 3_5, over_5) -> level

'none' is the crop box outside every radar layer, so a report with no radar behind it still
makes a level.  Levels rank where to look first;  they are never a probability.

An IEM report whose source says CoCoRaHS is the same observation relayed, so it is left out
and counted.  A CoCoRaHS filing counts at the set's size floor, by its largest stone or else
its average.  Only HAIL reports are used, unlike the radar zones, which anchor on any
roof-relevant report.

Runs on the connection it is given and leaves the commit to the caller.  Nothing here reads
or writes email, listings or matches.
"""

from hailsys.radar import warnings
# The patch SQL and its temp tables come from the radar zones so the two cannot drift;  the
# golden test builds both and compares the layers.
from hailsys.radar.fetch import day_window
from hailsys.radar.mesh import MM_PER_INCH, STEP
from hailsys.radar.zones import TOUCH_DEGREES, _SCRATCH_SQL, _WORK_SQL

METRES_PER_MILE = 1609.344
DEFAULT_REPORT_TEXT = "HAIL"
RULES_PER_SET = 20          # 5 layers x 4 bands


class LikelihoodError(Exception):
    """Nothing was built, message explains."""


_SET_SQL = """
SELECT s.set_id, s.cocorahs_min_in, s.band_1_mi, s.band_2_mi, s.band_3_mi,
       r.hail_threshold_in, r.margin_km, r.report_radius_km,
       (SELECT count(*) FROM likelihood_level_rules l WHERE l.set_id = s.set_id) AS rules
FROM likelihood_parameter_sets s
JOIN radar_parameter_sets r ON r.set_id = s.radar_set_id
WHERE %(set_id)s::bigint IS NULL OR s.set_id = %(set_id)s
ORDER BY s.set_id DESC
LIMIT 1
"""

_DAY_SQL = "SELECT status FROM radar_days WHERE day = %(day)s"

_DELETE_ZONES_SQL = "DELETE FROM likelihood_zones WHERE day = %(day)s AND set_id = %(set_id)s"
_DELETE_BUILD_SQL = "DELETE FROM likelihood_builds WHERE day = %(day)s AND set_id = %(set_id)s"

_SCRATCH_LK_SQL = """
CREATE TEMP TABLE lk_anchors (geom geometry(Point, 4326), source TEXT) ON COMMIT DROP;
CREATE TEMP TABLE lk_layers (layer TEXT, geom geometry) ON COMMIT DROP;
CREATE TEMP TABLE lk_bands (band TEXT, geom geometry) ON COMMIT DROP
"""

# Same test as the matcher and the radar zones (roof relevant, at or above the type's minimum,
# a NULL magnitude fails a minimum), for one report text.
_IEM_SQL = """
INSERT INTO lk_anchors (geom, source)
SELECT i.geom, 'iem'
FROM iem_data i
JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
WHERE i.utc_datetime >= %(start)s AND i.utc_datetime < %(end)s
    AND i.report_text = %(report_text)s
    AND t.roof_relevant
    AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
    AND i.report_source_norm IS DISTINCT FROM 'COCORAHS'
"""

_DUPLICATES_SQL = """
SELECT count(*) AS n
FROM iem_data i
JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
WHERE i.utc_datetime >= %(start)s AND i.utc_datetime < %(end)s
    AND i.report_text = %(report_text)s
    AND t.roof_relevant
    AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
    AND i.report_source_norm = 'COCORAHS'
"""

# The largest stone, else the average (what the analyses used).  A NULL size fails the floor.
_COCORAHS_SQL = """
INSERT INTO lk_anchors (geom, source)
SELECT c.geom, 'cocorahs'
FROM cocorahs_hail_reports c
WHERE c.observed_at >= %(start)s AND c.observed_at < %(end)s
    AND coalesce(c.largest_in, c.average_in) >= %(cocorahs_min)s
"""

_FILL_ANCHORS_SQL = "INSERT INTO radar_anchor_reports (geom) SELECT geom FROM lk_anchors"

# Same layers as zones._ZONES_SQL, into a temp table.
_LAYERS_SQL = """
INSERT INTO lk_layers (layer, geom)
WITH parts AS (
    SELECT COALESCE(ST_Union(cell) FILTER (WHERE anchored),
                    ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)) AS core,
           COALESCE(ST_Union(cell) FILTER (WHERE NOT anchored),
                    ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)) AS loose
    FROM radar_work
), ringed AS (
    SELECT core, loose,
           CASE WHEN ST_IsEmpty(core) THEN core
                ELSE ST_Difference(ST_Buffer(core::geography, %(margin_m)s)::geometry, core)
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
SELECT layer, g FROM shaped
WHERE NOT ST_IsEmpty(g) AND ST_Area(g::geography) > 0
"""

# The day's warning polygons clipped to the crop box, outside the layers already found.
_OUTER_SQL = """
INSERT INTO lk_layers (layer, geom)
WITH warn AS (
    SELECT ST_Union(geom) AS g FROM warning_polygons
    WHERE poly_begin < %(end)s AND poly_end > %(start)s
), box AS (
    SELECT ST_MakeEnvelope(box_lon_min - %(half)s, box_lat_min - %(half)s,
                           box_lon_max + %(half)s, box_lat_max + %(half)s, 4326) AS g
    FROM radar_days WHERE day = %(day)s
), taken AS (
    SELECT COALESCE(ST_Union(geom), ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)) AS g
    FROM lk_layers
), shaped AS (
    SELECT ST_Multi(ST_CollectionExtract(
               ST_Difference(ST_Intersection(warn.g, box.g), taken.g), 3)) AS g
    FROM warn, box, taken
    WHERE warn.g IS NOT NULL
)
SELECT 'outer', g FROM shaped
WHERE NOT ST_IsEmpty(g) AND ST_Area(g::geography) > 0
"""

# 'none' is the crop box outside every layer above, so a report with no radar behind it can
# still make a level.
_NONE_SQL = """
INSERT INTO lk_layers (layer, geom)
WITH box AS (
    SELECT ST_MakeEnvelope(box_lon_min - %(half)s, box_lat_min - %(half)s,
                           box_lon_max + %(half)s, box_lat_max + %(half)s, 4326) AS g
    FROM radar_days WHERE day = %(day)s
), taken AS (
    SELECT COALESCE(ST_Union(geom), ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326)) AS g
    FROM lk_layers
), shaped AS (
    SELECT ST_Multi(ST_CollectionExtract(ST_Difference(box.g, taken.g), 3)) AS g
    FROM box, taken
)
SELECT 'none', g FROM shaped
WHERE NOT ST_IsEmpty(g) AND ST_Area(g::geography) > 0
"""

# Distance bands round the reports, in metres on the spheroid.  No reports, no buffers:  the
# whole box is over_5.
_BANDS_SQL = """
INSERT INTO lk_bands (band, geom)
WITH pts AS (
    SELECT ST_Collect(geom) AS g FROM lk_anchors
), ring AS (
    SELECT COALESCE(ST_Buffer(pts.g::geography, %(b1)s)::geometry, e.g) AS b1,
           COALESCE(ST_Buffer(pts.g::geography, %(b2)s)::geometry, e.g) AS b2,
           COALESCE(ST_Buffer(pts.g::geography, %(b3)s)::geometry, e.g) AS b3
    FROM pts, (SELECT ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 4326) AS g) e
), box AS (
    SELECT ST_MakeEnvelope(box_lon_min - %(half)s, box_lat_min - %(half)s,
                           box_lon_max + %(half)s, box_lat_max + %(half)s, 4326) AS g
    FROM radar_days WHERE day = %(day)s
)
SELECT 'le_1', b1 FROM ring
UNION ALL SELECT '1_3', ST_Difference(b2, b1) FROM ring
UNION ALL SELECT '3_5', ST_Difference(b3, b2) FROM ring
UNION ALL SELECT 'over_5', ST_Difference(box.g, ring.b3) FROM box, ring
"""

_BUILD_SQL = """
INSERT INTO likelihood_builds (day, set_id, built_by, window_start, window_end, iem_reports,
    cocorahs_reports, duplicates_dropped, patches, patches_anchored)
SELECT %(day)s, %(set_id)s, %(user_id)s, %(start)s, %(end)s,
       (SELECT count(*) FROM lk_anchors WHERE source = 'iem'),
       (SELECT count(*) FROM lk_anchors WHERE source = 'cocorahs'),
       %(duplicates)s::int,
       (SELECT count(DISTINCT patch) FROM radar_work),
       (SELECT count(DISTINCT patch) FROM radar_work WHERE anchored)
RETURNING iem_reports, cocorahs_reports, duplicates_dropped, patches, patches_anchored
"""

# Each rule cuts one layer by one band.  The layers do not overlap and neither do the bands,
# so the pieces do not, and a level is the union of its pieces.  'none' is never stored.
_ZONES_SQL = """
INSERT INTO likelihood_zones (day, set_id, level, geom, area_km2)
WITH pieces AS (
    SELECT r.level, ST_Intersection(l.geom, b.geom) AS g
    FROM likelihood_level_rules r
    JOIN lk_layers l ON l.layer = r.layer
    JOIN lk_bands b ON b.band = r.band
    WHERE r.set_id = %(set_id)s AND r.level <> 'none'
), merged AS (
    SELECT level, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Union(g)), 3)) AS g
    FROM pieces
    GROUP BY level
)
SELECT %(day)s, %(set_id)s, level, g, ST_Area(g::geography) / 1000000.0
FROM merged
WHERE NOT ST_IsEmpty(g) AND ST_Area(g::geography) > 0
"""

_AREAS_SQL = """
SELECT level, area_km2 FROM likelihood_zones WHERE day = %(day)s AND set_id = %(set_id)s
"""

_LAYER_AREAS_SQL = "SELECT layer, ST_Area(geom::geography) / 1000000.0 AS km2 FROM lk_layers"


def prepare(conn, day, tz, *, set_id=None, user_id=None, report_text=DEFAULT_REPORT_TEXT):
    """Check the set and the day and return (args, held):  the query arguments, and whether the
    day's warnings are all held.  Writes nothing.

    LikelihoodError if there is no such set, the set lacks rules, or the day has no radar file
    loaded.  fill_tables and build_day take what this returns.
    """
    chosen = conn.execute(_SET_SQL, {"set_id": set_id}).fetchone()
    if chosen is None:
        raise LikelihoodError(
            "No such parameter set." if set_id else "there is no parameter set.")
    if chosen["rules"] != RULES_PER_SET:
        raise LikelihoodError(f"set {chosen['set_id']} has {chosen['rules']} rules, "
                              f"it needs {RULES_PER_SET}.")
    loaded = conn.execute(_DAY_SQL, {"day": day}).fetchone()
    if loaded is None or loaded["status"] != "loaded":
        raise LikelihoodError(
            f"{day} has no radar file loaded, so there is nothing to build from.")

    start, end = day_window(day, tz)
    miles = lambda value: float(value) * METRES_PER_MILE
    # Decimal * float is a TypeError, so everything is cast here.
    args = {"day": day, "set_id": chosen["set_id"], "user_id": user_id,
            "start": start, "end": end,
            "report_text": report_text, "cocorahs_min": chosen["cocorahs_min_in"],
            "touch": TOUCH_DEGREES, "step": STEP, "half": STEP / 2,
            "threshold_mm": float(chosen["hail_threshold_in"]) * MM_PER_INCH,
            "margin_m": float(chosen["margin_km"]) * 1000.0,
            "radius_m": float(chosen["report_radius_km"]) * 1000.0,
            "b1": miles(chosen["band_1_mi"]), "b2": miles(chosen["band_2_mi"]),
            "b3": miles(chosen["band_3_mi"])}
    held = warnings.covered(conn, start, end, warnings.current_states(conn))
    return args, held


def fill_tables(conn, args, held):
    """Create and fill the day's temporary tables:  lk_anchors (the actionable reports), lk_layers
    (the radar layers and 'none'), lk_bands (the distance bands), and the radar work tables.

    They are ON COMMIT DROP;  drop_tables ends them sooner.  Adds 'duplicates' to args.  Nothing
    permanent is written.
    """
    conn.execute(_SCRATCH_SQL)
    conn.execute(_SCRATCH_LK_SQL)
    args["duplicates"] = conn.execute(_DUPLICATES_SQL, args).fetchone()["n"]
    conn.execute(_IEM_SQL, args)
    conn.execute(_COCORAHS_SQL, args)
    conn.execute(_FILL_ANCHORS_SQL)
    conn.execute(_WORK_SQL, args)
    conn.execute(_LAYERS_SQL, args)
    if held:
        conn.execute(_OUTER_SQL, args)
    conn.execute(_NONE_SQL, args)
    conn.execute(_BANDS_SQL, args)


def drop_tables(conn):
    conn.execute("DROP TABLE radar_work, radar_anchor_reports, lk_anchors, lk_layers, lk_bands")


def build_day(conn, day, tz, *, set_id=None, user_id=None, report_text=DEFAULT_REPORT_TEXT):
    """Build and store the likelihood zones for local date 'day', replacing any with the same set.

    set_id None uses the newest parameter set.  user_id is who clicked, None for a job.
    Returns the build's counts, 'layer_areas' (km2 of each radar layer before the rules) and
    'areas' (km2 of each stored level).  LikelihoodError if there is no such set, the set
    lacks rules, or the day has no radar file loaded.
    """
    args, held = prepare(conn, day, tz, set_id=set_id, user_id=user_id, report_text=report_text)

    conn.execute(_DELETE_ZONES_SQL, args)
    conn.execute(_DELETE_BUILD_SQL, args)
    fill_tables(conn, args, held)
    counts = conn.execute(_BUILD_SQL, args).fetchone()
    conn.execute(_ZONES_SQL, args)
    layer_areas = {r["layer"]: r["km2"] for r in conn.execute(_LAYER_AREAS_SQL).fetchall()}
    areas = {r["level"]: r["area_km2"] for r in conn.execute(_AREAS_SQL, args).fetchall()}
    drop_tables(conn)
    return {"set_id": args["set_id"], **counts, "warnings_held": bool(held),
            "layer_areas": layer_areas, "areas": areas}
