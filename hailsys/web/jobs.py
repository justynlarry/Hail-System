"""Background execution for RentCast pulls.

At this scale, a worker container, new Dockerfile, etc.
are not justified.

Known gap:  a thread dies with its process.  A restart
mid-pull leaves api_pulls stuck at 'running' with API 
requests already used, the api_call_log will show how
far the api_pull got.  Requires a stale-pull
sweep before this is load-bearing.
"""

import logging
import threading


from hailsys.db import get_connection
from hailsys.matching.matcher import match_storm
from hailsys.rentcast.pull import run_pull

logger = logging.getLogger(__name__)


_SWEEP_SQL = """
UPDATE api_pulls
SET api_status = 'cancelled', finished_at = now()
WHERE api_status = 'running'
    AND started_at < now() - interval '10 minutes'
RETURNING pull_id, storm_date, report_text, started_at
"""


def _pull_and_match(*, emp_id, storm_date, report_text, zip_codes,
                    estimated_api_calls, window_start, window_end):
    try:
        with get_connection() as conn:
            pull_id = run_pull(
                conn, emp_id=emp_id, storm_date=storm_date,
                report_text=report_text, zip_codes=zip_codes,
                estimated_api_calls=estimated_api_calls,
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


def start_pull(**kwargs):
    thread = threading.Thread(target=_pull_and_match, kwargs=kwargs, daemon=True)
    thread.start()


def sweep_stale_pulls():
    """Mark pulls left 'running' by a dead process.

    daemon=True - a pull thread dies with its process, so a restart
    mid-pull leaves api_pulls at 'running' forever:  workstate reads 
    that as pulled, Pull link stays hidden, and storm appears to be done
    when it isn't

    The signal is Startup, nothing this process started is running when
    it starts.  10-minute floor covers the case gunicorn replaces one
    crashed worker while another worker's pull is in flight.  Both call
    create_app().  A bare 'running' sweep would kill a live pull.

    api_call_log still records what was spent, this closes the bookkeeping row.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_SWEEP_SQL)
                rows = cur.fetchall()
    except Exception:
        logger.exception("event=sweep_failed")
        return
    for row in rows:
        logger.warning(
            "event=stale_pull_swept pull_id=%s storm_date=%s "
            "report_text=%r started_at=%s",
            row["pull_id"], row["storm_date"], row["report_text"],
            row["started_at"].isoformat())