"""What's covered in a pull from hail-likelihood zones for the estimate comparison.

Read-Only.  Lists the coverage zips that overlap the day's zones, with how much of each zip,
and sets them beside the zips today's report-based pull would take.  The levels rank where to look
first.  They are not a probability, only the set in use is read, and a day not built with it
is reported as not built, with no zip list.
"""

from hailsys.queries import storms

ALWAYS = ("agree", "one_source")
OPT_IN = "context"
RANK = ("agree", "one_source", "context")

_SET_SQL = "SELECT set_id, name FROM likelihood_parameter_sets ORDER BY set_id DESC LIMIT 1"

_BUILT_SQL = "SELECT 1 FROM likelihood_builds WHERE day = %(day)s AND set_id = %(set_id)s"

# Any overlap at all counts.  A zip that only touches a zone along an edge has no area and is left out.
_ZIPS_SQL = """
SELECT c.zcta5, c.area_name,
    max(ST_Area(b.geom::geography)) / 1000000.0 AS zip_km2,
    coalesce(sum(a.km2) FILTER (WHERE a.level = 'agree'), 0) AS agree_km2,
    coalesce(sum(a.km2) FILTER (WHERE a.level = 'one_source'), 0) AS one_source_km2,
    coalesce(sum(a.km2) FILTER (WHERE a.level = 'context'), 0) AS context_km2
FROM coverage_zips c
JOIN zcta_boundaries b ON b.zcta5 = c.zcta5
JOIN LATERAL (
    SELECT z.level, ST_Area(ST_Intersection(z.geom, b.geom)::geography) / 1000000.0 AS km2
    FROM  likelihood_zones z
    WHERE z.day = %(day)s AND z.set_id = %(set_id)s AND z.level = ANY(%(levels)s)
        AND ST_Intersects(z.geom, b.geom)
) a ON true
WHERE c.removed_at IS NULL
GROUP BY c.zcta5, c.area_name
HAVING coalesce(sum(a.km2), 0) > 0
ORDER BY c.zcta5
"""


def zone_zips(conn, *, day, include_context, radius_m, window_start, window_end,
              report_text, actionable_only):
    """Zones view of one storm day.

    Returns {'day', 'set', 'built', 'include_context', 'rows', 'zips', 'counts'}, 'rows' has one
    dict per zip in the union, best level first:  zcta5, area_name, basis ('both', 'zones' or 'reports'),
    best_level (None for a reports-only zip), the km2 overlapped at each level, zip_km2 and share
    (the included levels' overlap as a fraction of the zip).  'zips' is the sorted union for the call estimate.
    A day not built with the set in use has built False and no rows.
    """
    chosen = conn.execute(_SET_SQL).fetchone()
    out = {"day": day, "set": chosen, "built": False, "include_context": include_context,
           "rows": [], "zips": [], "counts": {}}
    if chosen is None:
        return out
    built = conn.execute(_BUILT_SQL, {"day": day, "set_id": chosen["set_id"]}).fetchone()
    if built is None:
        return out
    out["built"] = True

    reports = storms.fetch_zips(conn, radius_m=radius_m, window_start=window_start,
                                window_end=window_end, report_text=report_text,
                                actionable_only=actionable_only,)
    report_names = {row["zcta5"]: row["area_name"] for row in reports}
    levels = list(ALWAYS) + ([OPT_IN] if include_context else [])
    found = conn.execute(_ZIPS_SQL, {"day": day, "set_id": chosen["set_id"],
                                     "levels": levels}).fetchall()

    rows = {}
    for r in found:
        overlap = {level: r[f"{level}_km2"] for level in RANK}
        best = next(level for level in RANK if overlap[level] > 0)
        rows[r["zcta5"]] = {
            "zcta5": r["zcta5"], "area_name": r["area_name"],
            "basis": "both" if r["zcta5"] in report_names else "zones",
            "best_level": best, "zip_km2": r["zip_km2"],
            "agree_km2": overlap["agree"], "one_source_km2": overlap["one_source"],
            "context_km2": overlap["context"],
            "share": sum(overlap.values()) / r["zip_km2"] if r["zip_km2"] else 0.0}
    for zcta5, name in report_names.items():
        if zcta5 not in rows:
            rows[zcta5] = {"zcta5": zcta5, "area_name": name, "basis": "reports",
                           "best_level": None, "zip_km2": None, "agree_km2": 0.0,
                           "one_source_km2": 0.0, "context_km2": 0.0, "share": 0.0}

    order = {level: n for n, level in enumerate(RANK)}
    out["rows"] = sorted(rows.values(),
                         key=lambda x: (order.get(x["best_level"], len(RANK)), x["zcta5"]))
    out["zips"] = sorted(rows)
    basis = [x["basis"] for x in out["rows"]]
    out["counts"] = {"union": len(rows), "zones": len(found), "reports": len(report_names),
                     "both": basis.count("both"), "zones_only": basis.count("zones"),
                     "reports_only": basis.count("reports"),
                     **{level: sum(1 for x in out["rows"] if x["best_level"] == level)
                        for level in RANK}}
    return out
