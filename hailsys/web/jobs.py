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
        logger.exception("event=pull_job_failed storm_date=%s report_text=%s",
                         storm_date, report_text)


def start_pull(**kwargs):
    thread = threading.Thread(target=_pull_and_match, kwargs=kwargs, daemon=True)
    thread.start()
