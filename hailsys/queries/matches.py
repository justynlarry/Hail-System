"""Match Detail, match listings to storm day hits.

Interacts with:  storm_listing_matches, listings, properties, realtors tables.
"""

# One row per listing, not per match, which keeps a history of how many times
# each property has been hit by actionable weather.

__MATCH_DETAIL_SQL = """
SELECT
    l.listing_id,
    p.property_address,
    p.city,
    p.zip_code,
    p.property_type,
    p.year_built,
    l.list_price,
    l.list_date,
    l.list_mls_number,
    r.realtor_id,
    r.agent_name,
    r.agent_email,
    r.agent_phone,
    r.agent_office_name,
    min(m.distance_miles)       AS nearest_miles,
    max(i.magnitude)            AS max_magnitude,
    max(t.mag_unit)             AS mag_unit,
    count(DISTINCT m.iem_id)    AS report_count
FROM storm_listing_matches m
JOIN iem_data i ON i.iem_id = m.iem_id
JOIN report_types t
    ON t.report_type = i.report_type
    AND t.report_text = i.report_text
JOIN listings l ON l.listing_id = m.listing_id
JOIN properties p ON p.rentcast_id = l.rentcast_id
LEFT JOIN realtors r ON r.realtor_id = l.realtor_id
WHERE i.utc_datetime >= %(window_start)s
   AND i.utc_datetime < %(window_end)s
   AND (%(report_text)s:: text is NULL OR i.report_text = %(report_text)s)
   AND m.radius_used = %(radius_miles)s
GROUP BY
    l.listing_id, p.property_address, p.city, p.zip_code, p.property_type,
    p.year_built, l.list_price, l.list_date, l.list_mls_number,
    r.realtor_id, r.agent_name, r.agent_email, r.agent_phone,
    r.agent_office_name
-- Sorted by Agent first because the view groups on realtor_id with
-- itertools.groupby, which will only collapse ADJACENT rows (it does not
-- sort -- Jinja's groupby filter does, but would crash comparing the NULL
-- and int realtor_ids this LEFT JOIN produces, so grouping happens here).
-- If unsorted, would produce same agent several times
-- NULLS LAST puts uncontactable listings at bottom
ORDER BY r.agent_name  NULLS LAST, r.realtor_id NULLS LAST,
    min(m.distance_miles)
"""


_PULL_COVERAGE_SQL = """
SELECT DISTINCT ON (zip_code)
    zip_code, called_at
FROM api_call_log
WHERE zip_code = ANY(%(zip_codes)s)
    AND http_status = 200
ORDER BY zip_code, called_at DESC
"""


MATCH_DETAIL_COLUMNS = [
    "listing_id", "property_address", "city", "zip_code", "property_type",
    "year_built", "list_price", "list_date", "list_mls_number",
    "realtor_id", "agent_name", "agent_email", "agent_phone",
    "agent_office_name", "nearest_miles", "max_magnitude", "mag_unit",
    "report_count"
]


def fetch_match_detail(conn, *, window_start, window_end, report_text,
                       radius_miles):
    with conn.cursor() as cur:
        cur.execute(__MATCH_DETAIL_SQL , {
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "radius_miles": radius_miles,
        })
        return cur.fetchall()


def fetch_pull_coverage(conn, zip_codes):
    if not zip_codes:
        return {}
    with conn.cursor() as cur:
        cur.execute(_PULL_COVERAGE_SQL, {"zip_codes": list(zip_codes)})
        return {row["zip_code"]: row["called_at"] for row in cur.fetchall()}