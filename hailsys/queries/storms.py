"""Storm-day query: which coverage zips saw which reports, and how.

Two projections over one join/filter core, so a schema or business-rule
change (radius handling, the coverage rule, the local-day window) is made in
exactly one place -- `_FROM_WHERE` -- and both projections pick it up.

pairs: one row per (report, coverage zip) pair. This is the shape the system
was built around -- a busy storm day has many reports, and each report
typically falls within the radius of several coverage zips, so a pair row
keeps magnitude, source, distance, and time attached to the specific report
that produced them, instead of blurring them into one zip-level summary.

zips: the same rows collapsed to one per coverage zip, for the natural
next step once summarizing (rather than inspecting) a storm day is what's
needed. Aggregates are computed over the same DISTANCE_EXPR / LOCAL_TIME_EXPR
the pairs projection displays, so the two views can never quietly disagree
about what "distance" or "local time" mean.
"""

# Distance is the report point to the NEAREST EDGE of the zip polygon --
# ST_DWithin tests against this. A report that falls inside the zip reads
# 0.00. It is not distance to the centroid: a report just outside a large
# zip's boundary is genuinely close to that zip, and centroid distance would
# say otherwise.
DISTANCE_EXPR = "(ST_Distance(i.geom::geography, z.geom::geography) / 1609.344)"

# Records are stored in UTC; this is the only place that's converted, to a
# named zone (not a fixed offset) so DST (MDT/MST) is handled automatically.
LOCAL_TIME_EXPR = "(i.utc_datetime AT TIME ZONE 'America/Denver')"

_FROM_WHERE = """
FROM iem_data i
JOIN report_types t
    ON t.report_type = i.report_type
    AND t.report_text = i.report_text
-- LEFT JOIN, not JOIN.  report_sources has no Foreign Key from iem_data
-- and should not get one.  SOURCE is free text typed at NWS offices, a
-- new value would break nightly ingest.  Source with no lookup row
-- yields a NULL tier here instead of dropping the report.
LEFT JOIN report_sources s
    ON s.source = i.report_source_norm
JOIN zcta_boundaries z
    ON ST_DWithin(i.geom::geography, z.geom::geography, %(radius_m)s)
JOIN coverage_zips c
    ON c.zcta5 = z.zcta5
    AND c.removed_at IS NULL
WHERE i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
"""

# One row per report-zip pair. Column order is the export's contract --
# do not reorder.
PAIRS_SQL = f"""
SELECT
    c.zcta5,
    c.area_name,
    i.iem_id,
    {LOCAL_TIME_EXPR} AS local_time,
    i.utc_datetime,
    i.report_type,
    i.report_text,
    i.magnitude,
    t.mag_unit,
    round({DISTANCE_EXPR}::numeric, 2) AS distance_miles,
    i.report_source,
    s.confidence_tier,
    i.report_qualifier,
    i.county,
    i.nws_issuer,
    i.latitude,
    i.longitude,
    i.remark
{_FROM_WHERE}
ORDER BY c.zcta5, i.utc_datetime, distance_miles
"""

# One row per coverage zip, aggregated across every report-zip pair that
# touches it.
ZIPS_SQL = f"""
SELECT
    c.zcta5,
    c.area_name,
    count(DISTINCT i.iem_id) AS report_count,
    round(min({DISTANCE_EXPR})::numeric, 2) AS nearest_miles,
    round(max({DISTANCE_EXPR})::numeric, 2) AS farthest_miles,
    min({LOCAL_TIME_EXPR}) AS first_report,
    max({LOCAL_TIME_EXPR}) AS last_report,
    max(i.magnitude) AS max_magnitude,
    min(i.magnitude) AS min_magnitude,
    string_agg(DISTINCT i.report_source, ', ') AS sources
{_FROM_WHERE}
GROUP BY c.zcta5, c.area_name
ORDER BY c.zcta5
"""

PAIRS_COLUMNS = [
    "zcta5", "area_name", "iem_id", "local_time", "utc_datetime",
    "report_type", "report_text", "magnitude", "mag_unit", "distance_miles",
    "report_source", "confidence_tier", "report_qualifier", "county",
    "nws_issuer", "latitude", "longitude", "remark",
]

ZIPS_COLUMNS = [
    "zcta5", "area_name", "report_count", "nearest_miles", "farthest_miles",
    "first_report", "last_report", "max_magnitude", "min_magnitude",
    "sources",
]


def fetch_pairs(conn, *, radius_m, window_start, window_end, report_text):
    with conn.cursor() as cur:
        cur.execute(PAIRS_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
        })
        return cur.fetchall()


def fetch_zips(conn, *, radius_m, window_start, window_end, report_text):
    with conn.cursor() as cur:
        cur.execute(ZIPS_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
        })
        return cur.fetchall()
