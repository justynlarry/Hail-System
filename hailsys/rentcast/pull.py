""" Pull Orchestrator, wirtes api_pulls/api_call_log bookkeeping
and hands each zip's fetched listings to the properties/listings/realtors
upsert.

Auth failure (RentCast's 401,403) will abort the WHOLE pull
"""


import logging

from hailsys.rentcast.client import search_sale_listings, RentCastAuthError, RentCastError
from hailsys.rentcast.upsert import upsert_listings

logger = logging.getLogger(__name__)

_INSERT_PULL_SQL = """
INSERT INTO api_pulls
    (emp_id, storm_date, report_text, zip_count, estimated_api_calls,
     started_at, api_status)
VALUES (%(emp_id)s, %(storm_date)s, %(report_text)s, %(zip_count)s,
        %(estimated_api_calls)s, now(), 'running')
RETURNING pull_id
"""

_LOG_ZIP_SQL = """
INSERT INTO api_call_log
    (pull_id, zip_code, called_at, calls_made, listings_returned, http_status)
VALUES (%(pull_id)s, %(zip_code)s, now(), %(calls_made)s,
        %(listings_returned)s, %(http_status)s)
"""

_FINISH_PULL_SQL = """
UPDATE api_pulls
SET finished_at = now(), actual_api_calls = %(actual_api_calls)s,
    listings_returned = %(listings_returned)s, api_status = %(api_status)s
WHERE pull_id = %(pull_id)s
"""

def run_pull(conn, *, emp_id, storm_date, report_text, zip_codes,
            estimated_api_calls, status="Active", days_old=None):
    with conn.cursor() as cur:
        cur.execute(_INSERT_PULL_SQL, {
            "emp_id": emp_id,
            "storm_date": storm_date,
            "report_text": report_text,
            "zip_count": len(zip_codes),
            "estimated_api_calls": estimated_api_calls,
        })
        pull_id = cur.fetchone()["pull_id"]
    conn.commit()

    total_calls = 0
    total_listings = 0

    for zip_code in zip_codes:
        http_status = 200
        try:
            listings, calls_made = search_sale_listings(
                zip_code, status=status, days_old=days_old)
        except RentCastAuthError as exc:
            logger.error("event=pull_aborted pull_id=%s zip=%s reason=auth",
                         pull_id, zip_code)
            _finish_pull(conn, pull_id, total_calls + exc.attempts,
                        total_listings, "failed")
            raise
        except RentCastError as exc:
            listings, calls_made = [], exc.attempts
            http_status = exc.status
            logger.warning("event=zip_failed pull_id=%s zip=%s status=%s error=%s",
                           pull_id, zip_code, http_status, exc)

        with conn.cursor() as cur:
            cur.execute(_LOG_ZIP_SQL, {
                "pull_id": pull_id, "zip_code": zip_code,
                "calls_made": calls_made, "listings_returned": len(listings),
                "http_status": http_status,
            })
        conn.commit()

        total_calls += calls_made
        total_listings += len(listings)

        if listings:
            try:
                upsert_listings(conn, listings)
            except Exception:
                logger.exception("event=upsert_failed pull_id=%s zip=%s",
                                 pull_id, zip_code)
                # A failed statement leaves the transaction aborted -- every
                # command on this connection raises InFailedSqlTransaction,
                # including _finish_pull's own UPDATE, until this runs.
                conn.rollback()
                _finish_pull(conn, pull_id, total_calls, total_listings, "failed")
                raise

    _finish_pull(conn, pull_id, total_calls, total_listings, "complete")
    return pull_id

def _finish_pull(conn, pull_id, actual_api_calls, listings_returned, api_status):
    with conn.cursor() as cur:
        cur.execute(_FINISH_PULL_SQL, {
            "pull_id": pull_id, "actual_api_calls":actual_api_calls,
            "listings_returned": listings_returned, "api_status": api_status,
        })
    conn.commit()