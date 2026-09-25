"""Work state per storm day, shows if it's been pulled, matched, sent
against.

Similar to storms.py whose _FROM_WHERE query outputs what reports 
covered which coverage zips (iem_data, report_types, zcta_boundaries,
coverage_zips).  This query outputs how users have interacted with the
system (api_pulls, storm_listing_matches, send_log).

State is DERIVED, not stored.  
"""

from datetime import timedelta

# As of 9/2026 most insurance carriers require 1 year or less to file a
# claim from the date of loss, the queue reflects this by reading as
# history.

CLAIM_WINDOW_DAYS = 365
PULLING = "Pulling..."

_LOCAL_DAY = "(i.utc_datetime AT TIME ZONE 'America/Denver')::date"

_WORKSTATE_SQL = f"""
WITH activity AS (
    -- Pulled: api_pulls records what was clicked
    -- a storm_date IS NULL = a pull is not tied to browsed storm
    -- (manual zip test) -> not evidence about any storm day
    SELECT storm_date, report_text, 'pulled' AS kind, started_at AS at
    FROM api_pulls
    WHERE storm_date IS NOT NULL
        AND storm_date >= %(start_date)s
        AND storm_date < %(end_date)s
        AND api_status NOT IN ('failed', 'cancelled')
    
    UNION ALL

    -- Matched: any storm_listing_matches row whose report falls in
    -- the window.  Radius-agnostic.
    SELECT {_LOCAL_DAY}, i.report_text, 'matched', NULL
    FROM storm_listing_matches m
    JOIN iem_data i ON i.iem_id = m.iem_id
    WHERE i.utc_datetime >= %(window_start)s
        AND i.utc_datetime < %(window_end)s

    UNION ALL
    --  A pull still in flight, kept separate from 'pulled,' not
    -- excluded from it.  The row should read 'Pulling...' while the
    -- thread works, but a pull that doesn't finish shouldn't read
    -- 'Not Pulled.'
    SELECT storm_date, report_text, 'running', started_at
    FROM api_pulls
    WHERE storm_date is NOT NULL
        AND storm_date >= %(start_date)s
        AND storm_date < %(end_date)s
        AND api_status = 'running'

    UNION ALL
    -- Match attempted: completed run, even an empty run.
    -- This is what distinguishes "ran, nothing in range from "never ran"

    SELECT storm_date, report_text, 'match_ran', NULL
    FROM match_runs
    WHERE storm_date >= %(start_date)s
        AND storm_date < %(end_date)s
        AND run_status = 'complete'
    
    UNION ALL

    -- Sent: At least one email went out against a match from this storm.
    SELECT {_LOCAL_DAY}, i.report_text, 'sent', NULL
    FROM send_log s
    JOIN storm_listing_matches m ON m.match_id = s.match_id
    JOIN iem_data i ON i.iem_id = m.iem_id
    WHERE i.utc_datetime >= %(window_start)s
        AND i.utc_datetime < %(window_end)s
        AND s.sent_at IS NOT NULL

)
SELECT
    storm_date,
    report_text,
    bool_or(kind = 'pulled')    AS pulled,
    bool_or(kind = 'matched')   AS matched,
    bool_or(kind = 'sent')      AS sent,
    bool_or(kind = 'match_ran') AS match_ran,
    bool_or(kind = 'running')   AS running,
    max(at) FILTER (WHERE kind = 'running') AS running_since,
    max(at) FILTER (WHERE kind = 'pulled') AS last_pulled_at
FROM activity
GROUP BY storm_date, report_text 
"""


NOT_PULLED = "Not pulled"
PULLED = "Pulled, not matched"
MATCHED = "Matched, not sent"
SENT = "Sent"
MATCHED_NONE = "Matched, none in range"


def _label(row):
    if row["sent"]:
        return SENT
    if row["matched"]:
        return MATCHED
    if row["match_ran"] and row["pulled"]:
        return MATCHED_NONE
    if row["running"]:
        return PULLING
    if row["pulled"]:
        return PULLED
    return NOT_PULLED

def fetch_work_state(conn, *, window_start, window_end, today):
    """Work state for each storm day in a window that has activity
    
    Returns {(storm_date, report_text): {"state": str, "is_stale": bool}}

    A Storm Day with NO activity is ABSENT from the result.  Caller holds
    authoritative list of storm days (from storms.fetch_recent_days) and
    treats a missing key as NOT_PULLED.

    'today' is passed in instead of computed, container clock is UTC, and
    "what day is it" is a DISPLAY_TZ question already answered in the web
    layer.
    """
    stale_before = today - timedelta(days=CLAIM_WINDOW_DAYS)

    with conn.cursor() as cur:
        cur.execute(_WORKSTATE_SQL, {
            "start_date": window_start.date(),
            "end_date": window_end.date(),
            "window_start": window_start,
            "window_end": window_end,
        })
        rows = cur.fetchall()

    return {
        (row["storm_date"], row["report_text"]): {
            "state": _label(row),
            "is_stale": row["storm_date"] < stale_before,
            "last_pulled_at": row["last_pulled_at"],
        }
        for row in rows
    }


def state_for(work_state, storm_date, report_text, today):
    """One storm day, defaults to NOT_PULLED when absent
    """
    found = work_state.get((storm_date, report_text))
    if found is not None:
        return found
    return {
        "state": NOT_PULLED,
        "is_stale": storm_date < today - timedelta(days=CLAIM_WINDOW_DAYS),
        "last_pulled_at": None,
    }


