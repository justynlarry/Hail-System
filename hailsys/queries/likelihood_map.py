"""Reads for the hail-likelihood layers on the Territory map (Phase 5c, step 1).

Read only, and nothing likelihood-derived goes anywhere but the map:  not an email, an export, a pull
or the matcher (the isolation tests pin that).  Only the parameter set in use is drawn, the newest
row of likelihood_parameter_sets.  A day built with an older set is reported in 'built_with_other_set'
instead of being drawn, so a changed rule cannot leave a map that mixes sets.  The levels rank where to
look first;  they are never a probability.
"""

# Draw order, bottom first:  the map puts each level in a pane of its own in this order.
LEVELS = ("context", "one_source", "agree")

# Degrees.  Same smoothing as the radar layers (a tenth of a 0.01 degree cell).
SIMPLIFY_DEGREES = 0.001

_SET_SQL = """
SELECT s.set_id, s.name, s.cocorahs_min_in, s.band_1_mi, s.band_2_mi, s.band_3_mi
FROM likelihood_parameter_sets s
ORDER BY s.set_id DESC
LIMIT 1
"""

_LEVELS_SQL = """
WITH merged AS (
    SELECT level, ST_Union(geom) AS g, count(*) AS parts
    FROM likelihood_zones
    WHERE day = ANY(%(days)s::date[]) AND set_id = %(set_id)s
    GROUP BY level
)
SELECT level, parts,
    ST_Area(g::geography) / 1000000.0 AS area_km2,
    ST_AsGeoJSON(ST_SimplifyPreserveTopology(g, %(tolerance)s), 4)::json AS geometry
FROM merged
WHERE NOT ST_IsEmpty(g)
"""

_OTHER_SET_SQL = """
SELECT DISTINCT b.day
FROM likelihood_builds b
WHERE b.day = ANY(%(days)s::date[]) AND b.set_id <> %(set_id)s
  AND NOT EXISTS (SELECT 1 FROM likelihood_builds c WHERE c.day = b.day AND c.set_id = %(set_id)s)
ORDER BY b.day
"""


def in_use_set(conn):
    """The parameter set the map draws:  the newest, with its numbers as floats.  None if there is none."""
    row = conn.execute(_SET_SQL).fetchone()
    if row is None:
        return None
    return {"set_id": row["set_id"], "name": row["name"], "cocorahs_min_in": float(row["cocorahs_min_in"]),
            "band_1_mi": float(row["band_1_mi"]), "band_2_mi": float(row["band_2_mi"]),
            "band_3_mi": float(row["band_3_mi"])}


def zones_geojson(conn, days, simplify=SIMPLIFY_DEGREES):
    """A GeoJSON FeatureCollection with one feature per level that has anything, for these local dates.

    Each feature's geometry is the union of the days' shapes for that level, with 'level', 'area_km2' (of the
    union) and 'parts' (how many stored shapes went into it).  Extra keys on the collection:  'set' (the
    parameter set drawn), 'days' (what was asked for, ISO dates) and 'built_with_other_set' (asked-for days
    built with another set, not drawn).
    """
    days = sorted(set(days))
    chosen = in_use_set(conn)
    out = {"type": "FeatureCollection", "features": [], "set": chosen,
           "days": [d.isoformat() for d in days], "built_with_other_set": []}
    if not days or chosen is None:
        return out
    args = {"days": days, "set_id": chosen["set_id"], "tolerance": simplify}
    rows = {r["level"]: r for r in conn.execute(_LEVELS_SQL, args).fetchall()}
    for level in LEVELS:
        if level in rows:
            r = rows[level]
            out["features"].append({
                "type": "Feature",
                "properties": {"level": level, "area_km2": round(float(r["area_km2"]), 1), "parts": r["parts"]},
                "geometry": r["geometry"]})
    out["built_with_other_set"] = [
        r["day"].isoformat() for r in conn.execute(_OTHER_SET_SQL, args).fetchall()]
    return out
