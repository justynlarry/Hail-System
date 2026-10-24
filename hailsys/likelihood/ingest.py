"""The likelihood jobs' logic:  which days to build, and one run around them (Phase 5c, step 1b).

nightly rebuilds the last 3 finished days, after the radar job (10:30 UTC) and the CoCoRaHS job
(11:00) have run.  rebuild redoes the last 30, which picks up late CoCoRaHS entries and edits.
backfill builds every loaded radar day that has no build yet for the newest parameter set.

A day with no radar file loaded is skipped, not failed:  radar's own job reports that.  A day
that raises is rolled back and named, the others still run, the run ends 'failed' and the script
exits 1, so a gap is loud.  Nothing here sends anything, calls RentCast or touches pulls and
matching.
"""

import logging

from hailsys.likelihood import build
from hailsys.radar import ingest as radar_ingest

log = logging.getLogger(__name__)

NIGHTLY_DAYS = 3
REBUILD_DAYS = 30
FAILURES_SHOWN = 20

_START_SQL = "INSERT INTO likelihood_runs (run_mode) VALUES (%(mode)s) RETURNING run_id"

_FINISH_SQL = """
UPDATE likelihood_runs
SET run_status = %(status)s, finished_at = now(), days_built = %(built)s,
    days_skipped = %(skipped)s, days_failed = %(failed)s, error_detail = %(error)s
WHERE run_id = %(run_id)s
"""

# Loaded radar days with no build for the newest parameter set, newest first.
_PENDING_SQL = """
SELECT d.day
FROM radar_days d
WHERE d.status = 'loaded' AND d.day BETWEEN %(first)s AND %(last)s
    AND NOT EXISTS (SELECT 1 FROM likelihood_builds b
                    WHERE b.day = d.day
                      AND b.set_id = (SELECT max(set_id) FROM likelihood_parameter_sets))
ORDER BY d.day DESC
"""


def pending_days(conn, first, last, limit=None):
    """Loaded radar days from first to last that the newest set has not built yet, newest first."""
    days = [row["day"] for row in conn.execute(_PENDING_SQL, {"first": first, "last": last}).fetchall()]
    return days[:limit] if limit else days


def run(conn, mode, days, tz, *, set_id=None, builder=build.build_day):
    """One recorded run over 'days', a transaction each.  Returns the counts, 'failed' and 'run_id'.

    The run row is committed first, so a crash still leaves it (as 'running').  'builder' is an
    argument so the tests can make one day fail.
    """
    run_id = conn.execute(_START_SQL, {"mode": mode}).fetchone()["run_id"]
    conn.commit()
    out = {"built": 0, "skipped": 0, "failed": []}
    for n, day in enumerate(days, 1):
        if radar_ingest.day_status(conn, day) != "loaded":
            out["skipped"] += 1
            log.info("%s skipped, no radar file loaded (%d of %d)", day, n, len(days))
            continue
        try:
            builder(conn, day, tz, set_id=set_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            out["failed"].append((day, f"{type(exc).__name__}: {exc}"))
            log.error("%s failed (%d of %d): %s: %s", day, n, len(days), type(exc).__name__, exc)
            continue
        out["built"] += 1
        log.info("%s built (%d of %d)", day, n, len(days))
    error = None
    if out["failed"]:
        shown = "; ".join(f"{day} {message}" for day, message in out["failed"][:FAILURES_SHOWN])
        error = f"{len(out['failed'])} of {len(days)} days failed: {shown}"
    conn.execute(_FINISH_SQL, {"run_id": run_id, "status": "failed" if error else "complete",
                               "built": out["built"], "skipped": out["skipped"],
                               "failed": len(out["failed"]), "error": error})
    conn.commit()
    return {**out, "run_id": run_id}
