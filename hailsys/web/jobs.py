"""Background execution for RentCast pulls.

At this scale, a worker container, new Dockerfile, etc.
are not justified.

A thread dies with its process.  A restart mid-pull leaves api_pulls
stuck at 'running' with API requests already used; api_call_log shows how
far the pull got.  A match run cut off the same way is left at 'running'
in match_runs.  Two things deal with both (parking-lot item 47):
sweep_stale_pulls() below marks such rows 'cancelled' (pulls) or 'failed'
(match runs) at startup, and workstate.py stops reading a 'running' row as
"Pulling..." once it is older than PULL_STALE_AFTER, so it reads right before
any sweep has run.
"""

import logging
import threading
import psycopg

from hailsys.db import get_connection
from hailsys.matching.matcher import match_storm
from hailsys.queries.workstate import PULL_STALE_AFTER
from hailsys.rentcast.pull import run_pull, create_pull

logger = logging.getLogger(__name__)


# The age comes from workstate.PULL_STALE_AFTER, so the sweep and the label
# can't disagree about when a running pull is dead.
_SWEEP_SQL = """
UPDATE api_pulls
SET api_status = 'cancelled', finished_at = now()
WHERE api_status = 'running'
    AND started_at < now() - %(age)s
RETURNING pull_id, storm_date, report_text, started_at
"""

_SWEEP_MATCH_SQL = """
UPDATE match_runs
SET run_status = 'failed', finished_at = now(),
    error_detail = 'process ended before the run finished (swept at startup)'
WHERE run_status = 'running'
    AND started_at < now() - %(age)s
RETURNING match_run_id, storm_date, report_text, started_at
"""

_INDEX_NAME = "api_pulls_one_running_per_storm"

_CANCEL_STALE_FOR_STORM_SQL = """
UPDATE api_pulls
SET api_status = 'cancelled', finished_at = now()
WHERE api_status = 'running'
    AND storm_date = %(storm_date)s AND report_text = %(report_text)s
    AND started_at < now() - %(age)s
"""

_RUNNING_FOR_STORM_SQL = """
SELECT p.started_at, u.emp_fname
FROM api_pulls p JOIN users u ON u.emp_id = p.emp_id
WHERE p.api_status = 'running'
    AND p.storm_date = %(storm_date)s AND p.report_text = %(report_text)s
"""

_FAIL_PULL_SQL = """
UPDATE api_pulls SET api_status = 'failed', finished_at = now()
WHERE pull_id = %s AND api_status = 'running'
"""

class PullInProgress(Exception):
    """A pull for this storm is already running (sql/033).

    Carries who started it and when, so the view can say so.
    """

    def __init__(self, started_by, started_at):
        super().__init__("a pull for this storm is already running")
        self.started_by = started_by
        self.started_at = started_at




def _pull_and_match(*, pull_id, emp_id, storm_date, report_text, zip_codes,
                    estimated_api_calls, window_start, window_end):
    """Thread body: run the pull whose row start_pull created, then match.

    On any failure the row is marked 'failed' if run_pull had not already
    given it a final status, so it does not sit 'running' and block the storm.
    """
    try:
        with get_connection() as conn:
            run_pull(
                conn, emp_id=emp_id, storm_date=storm_date,
                report_text=report_text, zip_codes=zip_codes,
                estimated_api_calls=estimated_api_calls,
                pull_id=pull_id,
            )
            new_matches = match_storm(
                conn, emp_id=emp_id, storm_date=storm_date, 
                window_start=window_start, window_end=window_end,
                report_text=report_text,
            )
        logger.info("event=pull_job_complete pull_id=%s new_matches=%d",
                    pull_id, new_matches)
    except Exception:
        logger.exception("event=pull_job_failed storm_date=%s report_text=%r",
                         storm_date, report_text)
        try:
            with get_connection() as conn:
                conn.execute(_FAIL_PULL_SQL, (pull_id,))
        except Exception:
            logger.exception("event=pull_fail_mark_failed pull_id=%s", pull_id)

def start_pull(*, emp_id, storm_date, report_text, zip_codes,
               estimated_api_calls, window_start, window_end):
    """Create the pull's api_pulls row, then run the pull in a thread.

    The row is created here, in the request, so a second pull for the same
    storm fails now with PullInProgress and nothing is spent.  The unique
    index (sql/033) decides the race; this code only turns its error into
    something the view can show.  Stale 'running' rows for the storm are
    cancelled first, in the same transaction as the insert, so a pull whose
    thread died cannot block its storm forever.
    """
    storm = {"storm_date": storm_date, "report_text": report_text}
    with get_connection() as conn:
        try:
            conn.execute(_CANCEL_STALE_FOR_STORM_SQL,
                         {**storm, "age": PULL_STALE_AFTER})
            pull_id = create_pull(
                conn, emp_id=emp_id, storm_date=storm_date,
                report_text=report_text, zip_count=len(zip_codes),
                estimated_api_calls=estimated_api_calls)
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name != _INDEX_NAME:
                raise
            conn.rollback()
            row = conn.execute(_RUNNING_FOR_STORM_SQL, storm).fetchone()
            raise PullInProgress(
                row["emp_fname"] if row else None,
                row["started_at"] if row else None)
    thread = threading.Thread(
        target=_pull_and_match,
        kwargs=dict(pull_id=pull_id, emp_id=emp_id, storm_date=storm_date,
                   report_text=report_text, zip_codes=zip_codes,
                   estimated_api_calls=estimated_api_calls,
                   window_start=window_start, window_end=window_end),
        daemon=True)
    thread.start()


def sweep_stale_pulls():
    """Mark pulls and match runs left 'running' by a dead process.

    daemon=True - a pull thread dies with its process, so a restart
    mid-pull leaves api_pulls at 'running' forever:  workstate reads 
    that as pulled, Pull link stays hidden, and storm appears to be done
    when it isn't

    The signal is Startup, nothing this process started is running when
    it starts.  10-minute floor covers the case gunicorn replaces one
    crashed worker while another worker's pull is in flight.  Both call
    create_app().  A bare 'running' sweep would kill a live pull.

    api_call_log still records what was spent, this closes the bookkeeping row.
    A match run has no 'cancelled' status, so a swept one is marked 'failed'.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SWEEP_SQL, {"age": PULL_STALE_AFTER})
                rows = cur.fetchall()
                cur.execute(_SWEEP_MATCH_SQL, {"age": PULL_STALE_AFTER})
                match_rows = cur.fetchall()
    except Exception:
        logger.exception("event=sweep_failed")
        return
    for row in rows:
        logger.warning(
            "event=stale_pull_swept pull_id=%s storm_date=%s "
            "report_text=%r started_at=%s",
            row["pull_id"], row["storm_date"], row["report_text"],
            row["started_at"].isoformat())
    for row in match_rows:
        logger.warning(
            "event=stale_match_run_swept match_run_id=%s storm_date=%s "
            "report_text=%r started_at=%s",
            row["match_run_id"], row["storm_date"], row["report_text"],
            row["started_at"].isoformat())
