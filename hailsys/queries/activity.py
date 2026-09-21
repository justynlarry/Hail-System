"""Activity since a point in time: per-user awareness layer.

Informational, nothing here changes work state, workstate.py
derives the queue from the data.
"""

from datetime import timedelta

from hailsys.queries import storms
from hailsys.queries.workstate import CLAIM_WINDOW_DAYS
from hailsys.tuning import denver_day_bounds

_LOCAL_DAY = "(i.utc_datetime AT TIME ZONE 'America/Denver')::date"

# ingested_at == when report arrived, not storm date
# storm_floor guards against backfill

_NEW_STORM_KEYS_SQL = f"""
SELECT
    {_LOCAL_DAY} AS storm_date,
    i.report_text,
    count(*) AS new_reports
FROM iem_data i
WHERE i.ingested_at >= %(since)s
    AND i.utc_datetime >= %(storm_floor)s
GROUP BY storm_date, i.report_text
"""

_PULLS_SQL = """
SELECT
    p.pull_id, p.started_at, p.storm_date, p.report_text,
    p.zip_count, p.actual_api_calls, p.listings_returned, p.api_status,
    u.emp_fname, u.emp_lname
FROM api_pulls p
JOIN users u ON u.emp_id = p.emp_id
WHERE p.started_at >= %(since)s
ORDER BY p.started_at DESC
"""

_MATCH_RUNS_SQL = f"""
SELECT
    m.matched_at,
    min({_LOCAL_DAY}) AS storm_date,
    string_agg(DISTINCT i.report_text, ', ') AS report_texts,
    count(DISTINCT m.listing_id) AS listings,
    u.emp_fname, u.emp_lname
FROM storm_listing_matches m
JOIN iem_data i ON i.iem_id = m.iem_id
LEFT JOIN users u ON u.emp_id = m.emp_id
WHERE m.matched_at >= %(since)s
GROUP BY m.matched_at, m.emp_id, u.emp_fname, u.emp_lname
ORDER BY m.matched_at DESC
"""


def build_feed(conn, *, since, today, radius_m):
    """Returns {"new_storms": [...], "pulls": [..], "match_runs": [...]}.
    new_storms rows are fetch_recent_days rows (already coverage- and
    actionable-filtered) with a "new_reports" count attached.
    """
    storm_floor, _ = denver_day_bounds(today - timedelta(days=CLAIM_WINDOW_DAYS))

    with conn.cursor() as cur:
        cur.execute(_NEW_STORM_KEYS_SQL, {"since": since, "storm_floor":storm_floor})
        new_keys = {(r["storm_date"], r["report_text"]): r["new_reports"]
                    for r in cur.fetchall()}

        cur.execute(_PULLS_SQL, {"since": since})
        pulls = cur.fetchall()

        cur.execute(_MATCH_RUNS_SQL, {"since": since})
        match_runs = cur.fetchall()

    new_storms = []
    if new_keys:
        oldest_day = min(day for day, _ in new_keys)
        window_start, _ =denver_day_bounds(oldest_day)
        _, window_end = denver_day_bounds(today)
        candidates = storms.fetch_recent_days(
            conn,
            radius_m=radius_m,
            window_start=window_start,
            window_end=window_end,
            report_text=None,
            actionable_only=True,
            limit=1000,
        )
        for row in candidates:
            key = (row["storm_date"], row["report_text"])
            if key in new_keys:
                row["new_reports"] = new_keys[key]
                new_storms.append(row)
    return {"new_storms": new_storms, "pulls": pulls, "match_runs": match_runs}