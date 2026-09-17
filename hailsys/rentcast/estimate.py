"""Pre-pull estimate, the zip count and projected RentCast call count, shown
to the user before a pull is confirmed.

This calls hailsys.queries.storms.fetch_zips for the zip list, and only adds
RentCast-side bookkeeping, how many calls a zip will LIKELY cost based on 
pull history.
"""

from hailsys.queries.storms import fetch_zips

DEFAULT_CALLS_PER_ZIP = 1

_ZIP_HISTORY_SQL = """
SELECT DISTINCT ON (zip_code)
    zip_code, calls_made
FROM api_call_log
WHERE zip_code = ANY(%(zip_codes)s)
ORDER BY zip_code, called_at DESC
"""

def _latest_calls_per_zip(conn, zip_codes):
    if not zip_codes:
        return {}
    with conn.cursor() as cur:
        cur.execute(_ZIP_HISTORY_SQL, {"zip_codes": list(zip_codes)})
        return {row["zip_code"]: row["calls_made"] for row in cur.fetchall()}
    
def estimate_pull(conn, *, radius_m, window_start, window_end, report_text,
                  actionable_only=True):

    rows = fetch_zips(conn, radius_m=radius_m, window_start=window_start,
                  window_end=window_end, report_text=report_text,
                  actionable_only=actionable_only)
    zip_codes = sorted({row["zcta5"] for row in rows})

    history = _latest_calls_per_zip(conn, zip_codes)
    estimated_calls = sum(
        history.get(z, DEFAULT_CALLS_PER_ZIP) for z in zip_codes
    )

    return {
        "zip_count": len(zip_codes),
        "estimated_api_calls": estimated_calls,
        "zips": zip_codes,
    }
