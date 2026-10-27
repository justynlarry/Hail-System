"""Zone matching: a listing inside the day's Agree or One source zone gets a match row.

Phase 5c step 3.  Run only when staff click the button on /pull/zones,
not after a pull.  A row has basis 'radar', 'observer', or 'both' and no iem_id.  A house
that already has a HAIL report match for the storm day is skipped, so a house keeps one match row.
Rows are insert-only, a re-run adds houses newly inside a zone and changes nothing that exists.

The level is the stored zone's.  The layer is the one the house sits in when the day is built again
in temporary tables (hailsys.likelihood.build, the same pipeline the zones came from).  Radar
present means the layer is core, margin, or radar-indicated.  Observer present means a qualifying
CoCoRaHS filing within the set's third distance edge.  Neither means no row.

Caller Commits.
"""

from hailsys.likelihood import build


class ZoneMatchError(Exception):
    """Nothing was written, message explains."""


_BUILT_SQL = """
SELECT built_at FROM likelihood_builds WHERE day = %(day)s AND set_id = %(set_id)s
"""

_SCRATCH_SQL = """
CREATE TEMP TABLE zm_candidates (
    listing_id BIGINT, level TEXT, layer TEXT,
    has_report BOOLEAN, radar BOOLEAN, observer BOOLEAN
) ON COMMIT DROP
"""

# One row per listing: a house on the edge of two zones takes the better level.
_CANDIDATES_SQL = """
INSERT INTO zm_candidates (listing_id, level, layer, has_report, radar, observer)
WITH houses AS (
    SELECT l.listing_id, p.geom
    FROM listings l
    JOIN properties p ON p.rentcast_id = l.rentcast_id
    WHERE l.list_status = 'Active'
        AND l.list_type IS DISTINCT FROM 'New Construction'
        AND p.property_type IS DISTINCT FROM 'Land'
        AND p.geom IS NOT NULL
), inside AS (
    SELECT DISTINCT ON (h.listing_id) h.listing_id, h.geom, z.level
    FROM houses h
    JOIN likelihood_zones z
        ON z.day = %(day)s AND z.set_id = %(set_id)s
        AND z.level IN ('agree', 'one_source')
        AND ST_Intersects(z.geom, h.geom)
    ORDER BY h.listing_id, CASE z.level WHEN 'agree' THEN 0 ELSE 1 END
)
SELECT i.listing_id, i.level, lay.layer,
    EXISTS (SELECT 1 FROM storm_listing_matches m
        JOIN iem_data r ON r.iem_id = m.iem_id
        WHERE m.listing_id = i.listing_id AND m.storm_date = %(day)s
            AND r.report_text = 'HAIL') AS has_report,
    COALESCE(lay.layer IN ('core', 'margin', 'radar_indicated'), false) AS radar,
    EXISTS (SELECT 1 FROM lk_anchors a
        WHERE a.source = 'cocorahs'
            AND ST_DWithin(a.geom::geography, i.geom::geography, %(b3)s)) AS observer
FROM inside i
LEFT JOIN LATERAL (
    SELECT l2.layer FROM lk_layers l2
    WHERE ST_Intersects(l2.geom, i.geom)
    ORDER BY CASE l2.layer WHEN 'core' THEN 0 WHEN 'margin' THEN 1
                           WHEN 'radar_indicated' THEN 2 WHEN 'outer' THEN 3 ELSE 4 END
    LIMIT 1
) lay ON true
"""

# Outside the day's radar crop box a house has no layer, and no row.
_COUNTS_SQL = """
SELECT count(*) AS in_zones,
    count(*) FILTER (WHERE has_report) AS has_report,
    count(*) FILTER (WHERE NOT has_report AND layer IS NULL) AS outside_crop,
    count(*) FILTER (WHERE NOT has_report AND layer IS NOT NULL
                    AND NOT radar AND NOT observer) AS no_evidence,
    count(*) FILTER (WHERE NOT has_report AND layer IS NOT NULL
                     AND (radar or observer)) AS to_write
FROM zm_candidates
"""

_INSERT_SQL = """
INSERT INTO storm_listing_matches
    (listing_id, storm_date, basis, level, layer, set_id, matched_at, emp_id)
SELECT c.listing_id, %(day)s,
    CASE WHEN c.radar AND c.observer THEN 'both'
         WHEN c.radar THEN 'radar' ELSE 'observer' END,
    c.level, c.layer, %(set_id)s, now(), %(user_id)s
FROM zm_candidates c
where not c.has_report AND c.layer IS NOT NULL AND (c.radar OR c.observer)
ON CONFLICT (storm_date, listing_id, set_id) WHERE iem_id IS NULL DO NOTHING
RETURNING basis, level
"""


def match_zones(conn, day, tz, *, emp_id, today, max_age_days):
    """Write the zone rows for local date 'day' and return the result.

    Refuses a day in the future, one older than max_age_days (a zone row for it could never
    be sent, and the app can't delete it), and one with no build for the set in use.
    Returns {'day', 'set_id', 'built_at', 'in_zones', 'has_report', 'outside_crop',
    'no_evidence', 'already', 'written', 'by_basis', 'by_level'}.
    """
    age = (today - day).days
    if age < 0:
        raise ZoneMatchError(f"{day} has not happened yet.")
    if age > max_age_days:
        raise ZoneMatchError(
            f"{day} is {age} days ago, past the storm age limit of {max_age_days} days: "
            "a match for it could never be sent.")
    try:
        args, held = build.prepare(conn, day, tz, user_id=emp_id)
    except build.LikelihoodError as exc:
        raise ZoneMatchError(str(exc)) from None
    built = conn.execute(_BUILT_SQL, args).fetchone()
    if built is None:
        raise ZoneMatchError(
            f"{day} has not been built with the hail-zone set now in use "
            f"(set {args['set_id']}), so there are no zones to match against.")
    build.fill_tables(conn, args, held)
    conn.execute(_SCRATCH_SQL)
    conn.execute(_CANDIDATES_SQL, args)
    counts = conn.execute(_COUNTS_SQL).fetchone()
    written = conn.execute(_INSERT_SQL, args).fetchall()
    conn.execute("DROP TABLE zm_candidates")
    build.drop_tables(conn)

    by_basis, by_level = {}, {}
    for row in written:
        by_basis[row["basis"]] = by_basis.get(row["basis"], 0) + 1
        by_level[row["level"]] = by_level.get(row["level"], 0) + 1
    return {"day": day, "set_id": args["set_id"], "built_at": built["built_at"],
            "in_zones": counts["in_zones"], "has_report": counts["has_report"],
            "outside_crop": counts["outside_crop"], "no_evidence": counts["no_evidence"],
            "already": counts["to_write"] - len(written), "written": len(written),
            "by_basis": by_basis, "by_level": by_level}
