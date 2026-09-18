"""Pre-pull estimate, the zip count and projected RentCast call count, shown
to the user before a pull is confirmed.

This calls hailsys.queries.storms.fetch_zips for the zip list, and only adds
RentCast-side bookkeeping, how many calls a zip will LIKELY cost based on 
pull history.
"""

from datetime import datetime, timedelta, timezone

from hailsys.queries.storms import fetch_zips
from hailsys.tuning import RECENT_PULL_WINDOW_DAYS

DEFAULT_CALLS_PER_ZIP = 1

_LATEST_CALL_SQL = """
SELECT DISTINCT ON (zip_code)
    zip_code, calls_made, called_at
FROM api_call_log
WHERE zip_code = ANY(%(zip_codes)s)
ORDER BY zip_code, called_at DESC
"""


def _latest_call_per_zip(conn, zip_codes):
    """Most recent api_call_log row per zip, for whichever of zip_codes
    have been pulled before. Feeds both the call-count estimate and the
    recency warning -- one query, since they're the same underlying fact."""
    if not zip_codes:
        return {}
    with conn.cursor() as cur:
        cur.execute(_LATEST_CALL_SQL, {"zip_codes": list(zip_codes)})
        return {row["zip_code"]: row for row in cur.fetchall()}


def estimate_pull(conn, *, radius_m, window_start, window_end, report_text,
                  actionable_only=True):
    rows = fetch_zips(conn, radius_m=radius_m, window_start=window_start,
                      window_end=window_end, report_text=report_text,
                      actionable_only=actionable_only)
    zip_codes = sorted({row["zcta5"] for row in rows})

    history = _latest_call_per_zip(conn, zip_codes)
    estimated_calls = sum(
        history[z]["calls_made"] if z in history else DEFAULT_CALLS_PER_ZIP
        for z in zip_codes
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_PULL_WINDOW_DAYS)
    pulled_recently = sorted(
        ({"zip_code": z, "last_pulled": history[z]["called_at"]}
         for z in zip_codes if z in history and history[z]["called_at"] >= cutoff),
        key=lambda r: r["last_pulled"], reverse=True,
    )
    not_pulled_recently = [z for z in zip_codes
                           if z not in {r["zip_code"] for r in pulled_recently}]

    return {
        "zip_count": len(zip_codes),
        "estimated_api_calls": estimated_calls,
        "pulled_recently": pulled_recently,        # [{"zip_code", "last_pulled"}, ...], newest first
        "not_pulled_recently": not_pulled_recently, # [zip_code, ...]
    }
