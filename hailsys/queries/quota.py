"""Rentcast usage against the monthly plan

Usage is two logs added together: api_call_log, written per zip as a pull runs (it
counts calls a pull that died partway had already made), and drip_listing_checks, one row per
by-id lookup made for a drip campaign.  The pull estimate reads api_call_log only.  It
needs a zip and a pull_id on each row, and a by-id check has neither.
"""

from datetime import timedelta

_USAGE_SQL = """
    SELECT (SELECT COALESCE(sum(calls_made), 0)
              FROM api_call_log
             WHERE called_at >= %(period_start)s
               AND called_at < %(period_end)s)
         + (SELECT COALESCE(sum(calls_made), 0)
              FROM drip_listing_checks
             WHERE checked_at >= %(period_start)s
               AND checked_at < %(period_end)s) AS used
"""


def period_bounds(today, billing_day):
    if today.day >= billing_day:
        start = today.replace(day=billing_day)
    else:
        start = (today.replace(day=1) - timedelta(days=1)).replace(day=billing_day)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)

    return start, end


def fetch_usage(conn, *, today, billing_day, quota, day_bounds):
    """Usage for the current billing period.
    
    day_bounds is tuning.denver_day_bounds, passed in from tuning.py
    """
    period_start, period_end = period_bounds(today, billing_day)
    start_ts, _ = day_bounds(period_start)
    end_ts, _ = day_bounds(period_end)

    with conn.cursor() as cur:
        cur.execute(_USAGE_SQL, {"period_start": start_ts, "period_end": end_ts})
        used = cur.fetchone()["used"]

    return {
        "period_start": period_start,
        "period_end": period_end,
        "used": used,
        "quota": quota,
        "remaining": max(quota - used, 0),
        "over": max(used - quota, 0),
    }