"""Radar job health for the Storm Days page.

The same question as the IEM block (queries/ingest.py): when did a nightly run last succeed?
An absence query.  These are scripts/status.sh radar's queries, if one
changes both do.  'started' tells a system that has never run a nightly (backfill still going, timer
not installed) from one whose nightly has stopped.
"""

_NIGHTLY_SQL = """
    SELECT max(finished_at) AS last_ok
        FROM radar_runs
    WHERE run_mode = 'nightly'
        AND run_status = 'complete'
"""

_STARTED_SQL = """
    SELECT count(*) > 0 AS started
        FROM radar_runs
    WHERE run_mode = 'nightly'
"""

_RUNS_SQL = """
    SELECT run_id, run_mode, run_status, days_loaded, days_missing, days_built,
        days_failed, finished_at, error_detail
      FROM radar_runs
    ORDER BY run_id DESC
    LIMIT 5
"""

_DAYS_SQL = """
    SELECT count(*) FILTER (WHERE status = 'loaded') AS held,
           count(*) FILTER (WHERE status = 'missing') AS missing,
           min(day) FILTER (WHERE status = 'loaded') AS oldest_day,
           max(day) FILTER (WHERE status = 'loaded') AS newest_day,
           (SELECT count(DISTINCT day) FROM radar_zone_builds) AS built
    FROM radar_days
"""


def fetch_health(conn, *, now, stale_after):
    with conn.cursor() as cur:
        cur.execute(_NIGHTLY_SQL)
        last_ok = cur.fetchone()["last_ok"]

        cur.execute(_STARTED_SQL)
        started = cur.fetchone()["started"]

        cur.execute(_RUNS_SQL)
        runs = cur.fetchall()

        cur.execute(_DAYS_SQL)
        days = cur.fetchone()

    age = (now - last_ok) if last_ok is not None else None
    return {
        "healthy": age is not None and age < stale_after,
        "started": started,
        "last_ok": last_ok,
        "age": age,
        "stale_after": stale_after,
        "runs": runs,
        "held": days["held"],
        "missing": days["missing"],
        "built": days["built"],
        "oldest_day": days["oldest_day"],
        "newest_day": days["newest_day"],
    }
