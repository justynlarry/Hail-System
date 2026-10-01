"""Ingest health for Storm Days Page.

"When did a nightly run last succeed" is the only
question that holds across a failed/crashed/never started
run.

These are scripts/status.sh's queries, if one changes, they 
both need to change or the page and operator script won't
agree.
"""

_NIGHTLY_SQL = """
    SELECT max(finished_at) AS last_ok
      FROM ingest_runs
    WHERE run_mode = 'nightly'
      AND run_status = 'complete'
"""

_RUNS_SQL = """
    SELECT run_id, run_mode, run_status, window_start,
            rows_seen, rows_inserted, rows_skipped, finished_at
    FROM ingest_runs
    ORDER BY run_id DESC
    LIMIT 5
"""

_DATA_SQL = """
    SELECT count(*)                                     AS report_rows,
        max(utc_datetime) AT TIME ZONE 'America/Denver' AS newest_denver
    FROM iem_data
"""


_REJECTS_SQL = """
    SELECT reason, count(*) AS n
      FROM iem_ingest_rejects
    WHERE run_id IN (SELECT run_id FROM ingest_runs
                     ORDER BY run_id DESC LIMIT 10)
    GROUP BY reason
    ORDER BY n DESC
"""


def fetch_health(conn, *, now, stale_after):
    with conn.cursor() as cur:
        cur.execute(_NIGHTLY_SQL)
        last_ok = cur.fetchone()["last_ok"]

        cur.execute(_RUNS_SQL)
        runs = cur.fetchall()

        cur.execute(_DATA_SQL)
        data = cur.fetchone()

        cur.execute(_REJECTS_SQL)
        rejects = cur.fetchall()

    age = (now - last_ok) if last_ok is not None else None
    healthy = age is not None and age < stale_after

    return {
        "healthy": healthy,
        "last_ok": last_ok,
        "age": age,
        "stale_after": stale_after,
        "runs": runs,
        "report_rows": data["report_rows"],
        "newest_denver": data["newest_denver"],
        "rejects": rejects,
    }