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
DISTANCE_EXPR = "(d.distance_m / 1609.344)"

# Records are stored in UTC; this is the only place that's converted, to a
# named zone (not a fixed offset) so DST (MDT/MST) is handled automatically.
LOCAL_TIME_EXPR = "(i.utc_datetime AT TIME ZONE 'America/Denver')"

_ACTIONABLE = """
    AND (NOT %(actionable_only)s
        OR (t.roof_relevant
            AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)))
"""

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
-- Precomputed distances replace the ST_DWithin join. Filtering on
-- distance_m is an integer-keyed lookup plus a comparison; the spatial
-- math already happened once, at insert.
JOIN report_zip_distances d
    ON d.iem_id = i.iem_id
    AND d.distance_m <= %(radius_m)s
JOIN coverage_zips c
    ON c.zcta5 = d.zcta5
    AND c.removed_at IS NULL
WHERE i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
    -- Raises if radius_m exceeds the stored ceiling. In the shared core so
    -- all six projections get the guard from one line.
    AND hail_assert_radius_within_ceiling(%(radius_m)s)
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
# touches it. Grouped by report_text and mag_unit too, so a zip that saw
# both Hail and Wind on the same day is returned as two rows, not blurred
# into one -- intentional, do not collapse this back to
# `GROUP BY c.zcta5, c.area_name`.
ZIPS_SQL = f"""
SELECT
    c.zcta5,
    c.area_name,
    i.report_text,
    t.mag_unit,
    count(DISTINCT i.iem_id) AS report_count,
    round(min({DISTANCE_EXPR})::numeric, 2) AS nearest_miles,
    round(max({DISTANCE_EXPR})::numeric, 2) AS farthest_miles,
    min({LOCAL_TIME_EXPR}) AS first_report,
    max({LOCAL_TIME_EXPR}) AS last_report,
    max(i.magnitude) AS max_magnitude,
    min(i.magnitude) AS min_magnitude,
    string_agg(DISTINCT i.report_source, ', ') AS sources
{_FROM_WHERE}
{_ACTIONABLE}
GROUP BY c.zcta5, c.area_name, i.report_text, t.mag_unit
ORDER BY c.zcta5
"""

# One row per (local storm day, report type). Browse List's Unit:
# A day that had Hail, Wind, or Both, each as a separate item to view

RECENT_DAYS_SQL = f"""
WITH days AS (
    SELECT ({LOCAL_TIME_EXPR})::date AS storm_date
    {_FROM_WHERE}
    {_ACTIONABLE}
    GROUP BY storm_date
    ORDER BY storm_date DESC
    LIMIT %(limit)s
      OFFSET %(offset)s
)
SELECT
    ({LOCAL_TIME_EXPR})::date AS storm_date,
    i.report_text,
    t.mag_unit,
    count(DISTINCT i.iem_id) AS report_count,
    count(DISTINCT c.zcta5) AS zip_count,
    max(i.magnitude) AS max_magnitude,
    string_agg(DISTINCT i.report_source, ', ' ORDER BY i.report_source) AS sources
{_FROM_WHERE}
{_ACTIONABLE}
    AND ({LOCAL_TIME_EXPR})::date IN (SELECT storm_date FROM days)
GROUP BY storm_date, i.report_text, t.mag_unit
ORDER BY storm_date DESC, report_count DESC
"""

CITIES_SQL = f"""
SELECT
    c.area_name,
    i.report_text,
    t.mag_unit,
    count(DISTINCT c.zcta5)                     AS zip_count,
    count(DISTINCT ({LOCAL_TIME_EXPR})::date)   AS day_count,
    count(DISTINCT i.iem_id)                    AS report_count,
    max(i.magnitude)                            AS max_magnitude,
    min({LOCAL_TIME_EXPR})::date                AS first_day,
    max({LOCAL_TIME_EXPR})::date                AS last_day
{_FROM_WHERE}
{_ACTIONABLE}
GROUP BY c.area_name, i.report_text, t.mag_unit
ORDER BY report_count DESC, c.area_name
"""

RECENT_DAYS_COUNT_SQL = f"""
SELECT count(*) AS total_days
FROM (
    SELECT ({LOCAL_TIME_EXPR})::date AS storm_date
    {_FROM_WHERE}
    {_ACTIONABLE}
    GROUP BY storm_date
) d
"""


CITIES_COLUMNS = [
    "area_name", "report_text", "mag_unit", "zip_count", "day_count",
    "report_count", "max_magnitude", "first_day", "last_day"
]

def fetch_cities(conn, *, radius_m, window_start, window_end, report_text,
                actionable_only):
    with conn.cursor() as cur:
        cur.execute(CITIES_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "actionable_only": actionable_only,
        })
        return cur.fetchall()

RECENT_DAYS_COLUMNS = [
    "storm_date", "report_text", "mag_unit",
    "report_count", "zip_count", "max_magnitude", "sources",
]


def fetch_recent_days(conn, *, radius_m, window_start, window_end,
                      report_text, actionable_only, limit, offset=0):
    # offset defaults to 0 so activity.build_feed, which wants every day
    # in its window rather than a page, doesn't have to pass one.
    with conn.cursor() as cur:
        cur.execute(RECENT_DAYS_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "actionable_only": actionable_only,
            "limit": limit,
            "offset": offset,
        })
        return cur.fetchall()


PAIRS_COLUMNS = [
    "zcta5", "area_name", "iem_id", "local_time", "utc_datetime",
    "report_type", "report_text", "magnitude", "mag_unit", "distance_miles",
    "report_source", "confidence_tier", "report_qualifier", "county",
    "nws_issuer", "latitude", "longitude", "remark",
]

ZIPS_COLUMNS = [
    "zcta5", "area_name", "report_text", "mag_unit", "report_count",
    "nearest_miles", "farthest_miles", "first_report", "last_report",
    "max_magnitude", "min_magnitude", "sources", 
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


def fetch_zips(conn, *, radius_m, window_start, window_end, report_text,
               actionable_only):
    with conn.cursor() as cur:
        cur.execute(ZIPS_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "actionable_only": actionable_only,
        })
        return cur.fetchall()

REPORT_TYPES_SQL = """
SELECT DISTINCT report_text
FROM report_types
WHERE roof_relevant
ORDER BY report_text
"""

def fetch_report_types(conn):
    with conn.cursor() as cur:
        cur.execute(REPORT_TYPES_SQL)
        return [row["report_text"] for row in cur.fetchall()]


CITY_DAYS_SQL = f"""
SELECT
    ({LOCAL_TIME_EXPR})::date AS storm_date,
    i.report_text,
    t.mag_unit,
    count(DISTINCT i.iem_id)    AS report_count,
    count(DISTINCT c.zcta5)     AS zip_count,
    max(i.magnitude)            AS max_magnitude
{_FROM_WHERE}
{_ACTIONABLE}
    AND c.area_name = %(area_name)s
GROUP BY storm_date, i.report_text, t.mag_unit
ORDER BY storm_date DESC
"""

CITY_DAYS_COLUMNS = [
    "storm_date", "report_text", "mag_unit",
    "report_count", "zip_count", "max_magnitude",
]

def fetch_city_days(conn, *, radius_m, window_start, window_end, report_text,
                    actionable_only, area_name):
    with conn.cursor() as cur:
        cur.execute(CITY_DAYS_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end":window_end,
            "report_text": report_text,
            "actionable_only": actionable_only,
            "area_name": area_name,
        })
        return cur.fetchall()

REPORT_POINTS_SQL = f"""
SELECT DISTINCT ON (i.iem_id)
    i.iem_id,
    i.latitude,
    i.longitude,
    {LOCAL_TIME_EXPR} AS local_time,
    i.report_text,
    i.magnitude,
    t.mag_unit,
    i.report_source,
    i.report_source_norm
{_FROM_WHERE}
{_ACTIONABLE}
ORDER BY i.iem_id, i.utc_datetime DESC
LIMIT %(limit)s
"""

REPORT_POINTS_COLUMNS = [
    "iem_id", "latitude", "longitude", "local_time",
    "report_text", "magnitude", "mag_unit", "report_source",
    "report_source_norm",
]

def fetch_report_points(conn, *, radius_m, window_start, window_end,
                        report_text, actionable_only, limit):
    with conn.cursor() as cur:
        cur.execute(REPORT_POINTS_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "actionable_only": actionable_only,
            "limit": limit,
        })
        return cur.fetchall()


def fetch_day_count(conn, *, radius_m, window_start, window_end, report_text,
                    actionable_only):
    with conn.cursor() as cur:
        cur.execute(RECENT_DAYS_COUNT_SQL, {
            "radius_m": radius_m,
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "actionable_only": actionable_only,
        })
        return cur.fetchone()["total_days"]
