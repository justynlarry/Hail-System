"""The CoCoRaHS jobs' logic:  which windows to read, and one run around them.

Three modes.  nightly reads the last 3 days by Report Timestamp, which is how a late entry or an
edit to an old report is found.  weekly reads the last 30 days by Report Date, a net under the
nightly.  backfill reads monthly chunks by Report Date from the first CoCoRaHS day.

A window that fails is rolled back and named, and the others still run.  The run is closed
'failed' and the script exits 1, so a gap is loud and never silent.  Nothing here sends anything,
calls RentCast or touches pulls and matching.
"""

import calendar
import logging
from datetime import date, timedelta

from hailsys.cocorahs import load, parse

logger = logging.getLogger(__name__)

FIRST_COCORAHS_DAY = date(2020, 10, 15)
NIGHTLY_DAYS = 3
WEEKLY_DAYS = 30

_STATES_SQL = "SELECT storm_states FROM settings"


def windows(mode, today, first=None):
    """[(date_type, start, end)] for a mode.  Dates are inclusive, UTC."""
    if mode == "nightly":
        return [("timestamp", today - timedelta(days=NIGHTLY_DAYS), today)]
    if mode == "weekly":
        return [("reportdate", today - timedelta(days=WEEKLY_DAYS), today)]
    if mode != "backfill":
        raise ValueError(f"unknown mode {mode!r}")
    out = []
    start = first or FIRST_COCORAHS_DAY
    while start <= today:
        last_of_month = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        end = min(last_of_month, today)
        out.append(("reportdate", start, end))
        start = end + timedelta(days=1)
    return out


def states(conn):
    """The storm states, two letter codes, sorted, from settings.storm_states."""
    text = conn.execute(_STATES_SQL).fetchone()["storm_states"]
    return sorted({part.strip().upper() for part in (text or "").split(",") if part.strip()})


def run(conn, mode, state_codes, wins, fetcher):
    """Read every window for every state, store it, commit after each.

    'fetcher' takes (state, date_type, start, end) and returns the export's text.  Returns
    {'run_id', 'loads', 'seen', 'new', 'changed', 'rejected', 'failed'}, where 'failed' is a list
    of (what, message).
    """
    run_id = load.start_run(conn, mode)
    conn.commit()
    total = {"loads": 0, "seen": 0, "new": 0, "changed": 0, "rejected": 0}
    failed = []
    for date_type, start, end in wins:
        for state in state_codes:
            what = f"{state} {date_type} {start} to {end}"
            try:
                parsed = parse.parse_hail(fetcher(state, date_type, start, end))
                stored = load.store_window(conn, run_id, state, date_type, start, end, parsed)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.error("CoCoRaHS window failed (%s): %s", what, exc)
                failed.append((what, f"{type(exc).__name__}: {exc}"))
                continue
            total["loads"] += 1
            for key in ("seen", "new", "changed", "rejected"):
                total[key] += stored[key]
    error = "; ".join(f"{what}: {message}" for what, message in failed) or None
    load.finish_run(conn, run_id, status="failed" if failed else "complete", error=error, **total)
    conn.commit()
    return {"run_id": run_id, "failed": failed, **total}
