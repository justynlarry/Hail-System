"""Reads for the radar layers on the Territory map.

Read only, nothing radar-derived goes anywhere but the map.  Only the parameter
set in use is drawn, the newest row of radar_parameter_sets.  A day built with an older set
is reported in 'built_with_other_set' instead of being drawn, so a changed threshold cannot
leave a map that mixes sets.  Days with no stored outer layer (before the radar archive,
or built before their warnings were fetched) show the raw NWS polygons that overlap
them.  The time zone is an argument.
"""

from hailsys.tuning import DISPLAY_TZ

# Draw order, bottom first:  the map puts each layer in a pane of its own in this order
LAYERS = ("outer", "radar_indicated", "margin", "core")

# Degrees.  A cell is 0.01, so this smooths the staircase edge by a tenth of a cell
# (about 100 m) and keeps a 30 day union under a megabyte.
SIMPLIFY_DEGREES = 0.001

_SET_SQL = """
SELECT set_id, name, hail_threshold_in, margin_km, report_radius_km
FROM radar_parameter_sets
ORDER BY set_id DESC
LIMIT 1
"""

_DAYS_SQL = """
SELECT * FROM (
    SELECT days.day,
        b.set_id IS NOT NULL AS built,
        b.patches, b.patches_anchored, b.warnings_used,
        coalesce(z.core, 0) AS core_km2,
        coalesce(z.margin, 0) AS margin_km2,
        coalesce(z.radar_indicated, 0) AS radar_indicated_km2,
        coalesce(z.outer, 0) AS outer_km2,
        (SELECT count(*) FROM warning_polygons p
          WHERE p.poly_begin < ((days.day + 1)::timestamp AT TIME ZONE %(tz)s::text)
            AND p.poly_end > (days.day::timestamp AT TIME ZONE %(tz)s::text)) AS warnings
    FROM (SELECT d::date AS day
          FROM generate_series(%(first)s::date, %(last)s::date, interval '1 day') AS d) days
    LEFT JOIN radar_zone_builds b ON b.day = days.day AND b.set_id = %(set_id)s
    LEFT JOIN LATERAL (
        SELECT max(area_km2) FILTER (WHERE layer = 'core') AS core,
               max(area_km2) FILTER (WHERE layer = 'margin') AS margin,
               max(area_km2) FILTER (WHERE layer = 'radar_indicated') AS radar_indicated,
               max(area_km2) FILTER (WHERE layer = 'outer') AS outer
        FROM radar_zones WHERE day = days.day AND set_id = %(set_id)s
    ) z ON true
) x
WHERE core_km2 + margin_km2 + radar_indicated_km2 + outer_km2 > 0 OR warnings > 0
ORDER BY day DESC
"""

# Stored layers for the chosen days, and for a day without a stored outer layer the raw warning
# polygons that overlap it.  A day with zones built but warnings_used NULL (warnings were not fetched
# when it was built) counts as having none stored.
_LAYERS_SQL = """
WITH picked AS (
    SELECT z.layer, z.geom
    FROM radar_zones z
    WHERE z.day = ANY(%(days)s::date[]) AND z.set_id = %(set_id)s
  UNION ALL
    SELECT 'outer', p.geom
    FROM unnest(%(days)s::date[]) AS d(day)
    JOIN warning_polygons p
      ON p.poly_begin < ((d.day +1)::timestamp AT TIME ZONE %(tz)s::text)
     AND p.poly_end > (d.day::timestamp AT TIME ZONE %(tz)s::text)
    WHERE NOT EXISTS (SELECT 1 FROM radar_zone_builds b
                      WHERE b.day = d.day AND b.set_id = %(set_id)s AND b.warnings_used IS NOT NULL)
), merged AS (
    SELECT layer, ST_Union(geom) AS g, count(*) AS parts
    FROM picked
    GROUP BY layer
)
SELECT layer, parts,
    ST_Area(g::geographY) / 1000000.0 AS area_km2,
    ST_AsGeoJSON(ST_SimplifyPreservetopology(g, %(tolerance)s), 4)::json AS geometry
FROM merged
WHERE NOT ST_IsEmpty(g)
"""

_OTHER_SET_SQL = """
SELECT DISTINCT b.day
FROM radar_zone_builds b
WHERE b.day = ANY(%(days)s::date[]) AND b.set_id <> %(set_id)s
  AND NOT EXISTS (SELECT 1 FROM radar_zone_builds c WHERE c.day = b.day AND c.set_id = %(set_id)s)
ORDER BY b.day
"""


def in_use_set(conn):
    """The parameter set the map draws:  the newest, with its numbers as floats."""
    row = conn.execute(_SET_SQL).fetchone()
    return {"set_id": row["set_id"], "name": row["name"],
           "hail_threshold_in": float(row["hail_threshold_in"]),
           "margin_km": float(row["margin_km"]),
            "report_radius_km": float(row["report_radius_km"])}


def picker_days(conn, first, last, tz=DISPLAY_TZ):
    """The local dates first..last (inclusive) worth offering, newest first.
    A day is offered if it has any zone built with the set in use, or if any warning polygon overlapped it;
    a day built with no hail and no warnings has nothing to draw and is left out. 'built' says whether
    zones exist.  Areas are 0 for a layer a day does not have.
    """
    chosen = in_use_set(conn)
    rows = conn.execute(_DAYS_SQL, {"first": first, "last": last, "tz": tz.key,
                                    "set_id": chosen["set_id"]}).fetchall()
    return [dict(r, core_km2=float(r["core_km2"]), margin_km2=float(r["margin_km2"]),
                 radar_indicated_km2=float(r["radar_indicated_km2"]), outer_km2=float(r["outer_km2"]))
                for r in rows]


def zones_geojson(conn, days, tz=DISPLAY_TZ, simplify=SIMPLIFY_DEGREES):
    """A GeoJSON FeatureCollection with one feature per layer that has anything, for these local dates.
    
    Each feature's geometry is the union of the days' shapes for that layer, with 'layer', 'area_km2'
    (of the union) and 'parts' (how many stored shapes went into it). Extra keys on the collection:
    'set' (parameter set drawn), 'days' (what was asked for, ISO dates) and 'built_with_other_set'
    (asked-for days whose zones were built with another set, not drawn).
    """
    days = sorted(set(days))
    chosen = in_use_set(conn)
    out = {"type": "FeatureCollection", "features": [], "set": chosen,
           "days": [d.isoformat() for d in days], "built_with_other_set": []}
    if not days:
        return out
    args = {"days": days, "set_id": chosen["set_id"], "tz": tz.key, "tolerance": simplify}
    rows = {r["layer"]: r for r in conn.execute(_LAYERS_SQL, args).fetchall()}
    for layer in LAYERS:
        if layer in rows:
            r = rows[layer]
            out["features"].append({
                "type": "Feature",
                "properties": {"layer": layer, "area_km2": round(float(r["area_km2"]), 1), "parts": r["parts"]},
                "geometry": r["geometry"]})
    out["built_with_other_set"] = [r["day"].isoformat() for r in
                                  conn.execute(_OTHER_SET_SQL, {"days": days, "set_id":
chosen["set_id"]}).fetchall()]
    return out


