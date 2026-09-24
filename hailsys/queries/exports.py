"""CSV export projections: matched listings and the realtor list.

Shares LOCAL_TIME_EXPR with storms.py so the storm_date in a download is the
same local day the storm browser shows.  A separate copy of that expression
would drift when DST handling changed.

One row per (listing, local storm day, report type).  Match page's query
groups by listing alone, which collapses on a house matched on two storms.

Actionability is not re-applied here, a row in storm_listing_matches already
passed the roof_relevant and magnitude floor when matched through matcher.py.
"""

from hailsys.queries.storms import LOCAL_TIME_EXPR

# removed_at is NULL belongs in the ON clause, not WHERE.

_DNC_JOINS = """
LEFT JOIN dnc_list da
    ON da.email_norm = r.email_norm
    AND da.removed_at IS NULL
LEFT JOIN dnc_list do_
    ON do_.email_norm = r.office_email_norm
    AND do_.removed_at IS NULL
"""

# Suppression is keyed on the agent's email address, office flag is reported
# but doesn't filter.
_DNC_FILTER = "AND (NOT %(dnc_exclude)s OR da.dnc_id IS NULL)"

MATCHES_SQL = f"""
SELECT
    ({LOCAL_TIME_EXPR})::date       AS storm_date,
    i.report_text,
    t.mag_unit,
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
    (da.dnc_id IS NOT NULL)     AS agent_dnc,
    (do_.dnc_id IS NOT NULL)    AS office_dnc,
    min(m.distance_miles)       AS nearest_miles,
    max(i.magnitude)            AS max_magnitude,
    count(DISTINCT m.iem_id)    AS report_count
FROM storm_listing_matches m
JOIN iem_data i ON i.iem_id = m.iem_id
JOIN report_types t
    ON t.report_type = i.report_type
    AND t.report_text = i.report_text
JOIN listings l ON l.listing_id = m.listing_id
JOIN properties p ON p.rentcast_id = l.rentcast_id
LEFT JOIN realtors r ON r.realtor_id = l.realtor_id
{_DNC_JOINS}
WHERE i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
    AND m.radius_used = %(radius_miles)s
    {_DNC_FILTER}
GROUP BY
    storm_date, i.report_text, t.mag_unit, l.listing_id,
    p.property_address, p.city, p.zip_code, p.property_type, p.year_built,
    l.list_price, l.list_date, l.list_mls_number,
    r.realtor_id, r.agent_name, r.agent_email, r.agent_phone,
    r.agent_office_name, da.dnc_id, do_.dnc_id
ORDER BY storm_date DESC, i.report_text,
    r.agent_name NULLS LAST, r.realtor_id NULLS LAST,
    min(m.distance_miles)
"""

# Counted from the projection itself rather than a hand-written twin, so the
# number beside Apply can't disagree with the rows in the file.
MATCHES_COUNT_SQL = f"SELECT count(*) AS total FROM ({MATCHES_SQL}) e"

MATCHES_COLUMNS = [
    "storm_date", "report_text", "property_address", "city", "zip_code",
    "property_type", "year_built", "list_price", "list_date",
    "list_mls_number", "nearest_miles", "max_magnitude", "mag_unit",
    "report_count", "realtor_id", "agent_name", "agent_email", "agent_phone",
    "agent_office_name", "agent_dnc", "office_dnc",
]

# Not deduplicated, realtors holds one row per address by design, and DNC is
# keyed on email address (database-schema.md, realtors)
REALTORS_SQL = f"""
SELECT
    r.realtor_id,
    r.agent_name,
    r.agent_email,
    r.agent_phone,
    r.agent_office_name,
    r.agent_office_phone,
    r.agent_office_email,
    (r.first_seen_at AT TIME ZONE 'America/Denver')::date   AS first_seen,
    (r.last_seen_at AT TIME ZONE 'America/Denver')::date    AS last_seen,
    (da.dnc_id IS NOT NULL)                                 AS agent_dnc,
    (do_.dnc_id IS NOT NULL)                                AS office_dnc
FROM realtors r
{_DNC_JOINS}
WHERE TRUE
    {_DNC_FILTER}
ORDER BY r.agent_name NULLS LAST, r.realtor_id
"""

REALTORS_COUNT_SQL = f"SELECT count(*) AS total FROM ({REALTORS_SQL}) e"

REALTORS_COLUMNS = [
    "realtor_id", "agent_name", "agent_email", "agent_phone",
    "agent_office_name", "agent_office_phone", "agent_office_email",
    "first_seen", "last_seen", "agent_dnc", "office_dnc",
]


def fetch_matches(conn, *, window_start, window_end, report_text,
                  radius_miles, dnc_exclude):
    with conn.cursor() as cur:
        cur.execute(MATCHES_SQL, {
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "radius_miles": radius_miles,
            "dnc_exclude": dnc_exclude,
        })
        return cur.fetchall()


def count_matches(conn, *, window_start, window_end, report_text,
                  radius_miles, dnc_exclude):
    with conn.cursor() as cur:
        cur.execute(MATCHES_COUNT_SQL, {
            "window_start": window_start,
            "window_end": window_end,
            "report_text": report_text,
            "radius_miles": radius_miles,
            "dnc_exclude": dnc_exclude,
        })
        return cur.fetchone()["total"]

def fetch_realtors(conn, *, dnc_exclude):
    with conn.cursor() as cur:
        cur.execute(REALTORS_SQL, {"dnc_exclude": dnc_exclude})
        return cur.fetchall()


def count_realtors(conn, *, dnc_exclude):
    with conn.cursor() as cur:
        cur.execute(REALTORS_COUNT_SQL, {"dnc_exclude": dnc_exclude})
        return cur.fetchone()["total"]
