"""Runs the radar job over a set of local days:  load, warnings, zones, one transaction a day.

The days are worked out here (finished days only, and not before NOAA's archive begins).  load.py,
warnings.py, and zones.py do the work for one day.  A day that fails is rolled back and recorded
and the run goes on, so one bad file doesn't stop a two thousand day backfill.  The run then ends
as failed with the list.  Commits after each day, the way a job does.

Not handled:  a day loaded with an older crop box keeps it.  If the coverage zips grow, delete
the days (load.delete_day) to fetch them again.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone

from hailsys.radar import fetch, load, warnings, zones

log = logging.getLogger(__name__)

# NOAA's MRMS archive on S3 starts on this date.  A fact about the source, not the system.
FIRST_RADAR_DAY = date(2020, 10, 14)

# Warnings are fetched this many local days at a time, one request each.
WARNING_CHUNK_DAYS = 91

# How many failed days error_detail names.
FAILURES_SHOWN = 20

_STATUS_SQL = "SELECT status FROM radar_days WHERE day = %(day)s"


def latest_finished_day(now, tz):
    """The newest local date whose warnings are all in:  it ended at least warnings.LAG ago."""
    return (now - warnings.LAG).astimezone(tz).date() - timedelta(days=1)


def days_back(count, now, tz):
    """The last 'count' finished local dates, newest first."""
    last = latest_finished_day(now, tz)
    return [last - timedelta(days=i) for i in range(count)]


def days_between(first, last, floor=FIRST_RADAR_DAY):
    """first..last inclusive, newest first, nothing before `floor`."""
    first = max(first, floor)
    return [last - timedelta(days=i) for i in range((last - first).days + 1)]


def day_status(conn, day):
    """'loaded', 'missing', or None if the day was never fetched."""
    row = conn.execute(_STATUS_SQL, {"day": day}).fetchone()
    return row["status"] if row else None


def preload_warnings(conn, first, last, tz, directory, *, run_id=None, now=None, states=None, **fetch_kw):
    """Fetch the warnings for local dates first..last a chunk at a time, skipping chunks already
    held.  Commits after each chunk.  Returns the number of chunks fetched.
    
    Each chunk's window is padded a day each side, so every day in it is covered by one fetch
    and a chunk can be fetched again without storing anything twice.
    """
    states = warnings.normalize_states(states) if states else warnings.current_states(conn)
    now = now or datetime.now(timezone.utc)
    fetched = 0
    cursor = first
    while cursor <= last:
        chunk_last = min(cursor + timedelta(days=WARNING_CHUNK_DAYS - 1), last)
        start = fetch.day_window(cursor, tz)[0]
        end = fetch.day_window(chunk_last, tz)[1]
        if not warnings.covered(conn, start, end, states):
            got = warnings.load_window(conn, start - warnings.PAD, min(end + warnings.PAD, now),
                                       states, directory, run_id=run_id, **fetch_kw)
            conn.commit()
            fetched += 1
            log.info("warnings %s to %s: %d polygons, %d new", cursor, chunk_last, got["seen"], got["new"])
        cursor = chunk_last + timedelta(days=1)
    return fetched


def _one_day(conn, day, tz, directory, *, box, run_id, set_id, fetch_new, now, fetch_kw):
    """Returns (result, built): result is 'loaded', 'missing', 'held' or 'absent'."""
    held = day_status(conn, day)
    if held is None:
        if not fetch_new:
            return "absent", False
        result = load.load_day(conn, day, tz, directory, box=box, run_id=run_id, now=now, **fetch_kw)
    else:
        result = "held"
    if day_status(conn, day) != "loaded":
        return result, False
    warnings.ensure_day(conn, day, tz, directory, run_id=run_id, now=now, **fetch_kw)
    zones.build_day(conn, day, tz, set_id=set_id)
    return result, True


def run_days(conn, days, tz, directory, *, run_id, fetch_new=True, set_id=None, box=None,
             pause=0.0, sleep=time.sleep, now=None, **fetch_kw):
    """Do each local date in 'days', in the order given, a transaction each.
    
    fetch_new False builds zones only for days already held ('absent' otherwise).  Returns
    {'loaded', 'missing', 'held', 'absent', 'built': counts, 'failed': [(day, message)]}.
    'pause' seconds are waited after each day that used the network.
    """
    out = {"loaded": 0, "missing": 0, "held": 0, "absent": 0, "built": 0, "failed": []}
    if fetch_new and box is None:
        box = load.crop_box(conn)
    for n, day in enumerate(days, 1):
        try:
            result, built = _one_day(conn, day, tz, directory, box=box, run_id=run_id, set_id=set_id,
                                     fetch_new=fetch_new, now=now, fetch_kw=fetch_kw)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            out["failed"].append((day, f"{type(exc).__name__}: {exc}"))
            log.error("%s failed (%d of %d): %s: %s", day, n, len(days), type(exc).__name__, exc)
            continue
        out[result] += 1
        out["built"] += built
        log.info("%s %s%s (%d of %d)", day, result, ", built" if built else "", n, len(days))
        if pause and result in ("loaded", "missing"):
            sleep(pause)
    return out


def run(conn, mode, days, tz, directory, *, fetch_new=True, preload=True, set_id=None, box=None,
        pause=0.0, sleep=time.sleep, now=None, **network):
    """One recorded run:  start, warnings, the days, finish.  Returns run_days' result and 'run_id'.
    
    The run row is committed first, so a crash still leaves it (as 'running').  An error outside
    a single day (warnings, the box) marks the run failed and is raised.  Days that failed are
    in the result and in error_detail, and make the run 'failed' too.  `set_id`, `box`, `pause` and
    `sleep` are for the days only, `network` (attempts, backoff, timeout) goes to every download.
    """
    now = now or datetime.now(timezone.utc)
    run_id = load.start_run(conn, mode)
    conn.commit()
    try:
        if preload and days:
            preload_warnings(conn, min(days), max(days), tz, directory, run_id=run_id, now=now, **network)
        out = run_days(conn, days, tz, directory, run_id=run_id, fetch_new=fetch_new, set_id=set_id, box=box,
                       pause=pause, sleep=sleep, now=now, **network)
    except Exception as exc:
        conn.rollback()
        load.finish_run(conn, run_id, loaded=0, missing=0, error=f"{type(exc).__name__}: {exc}")
        conn.commit()
        raise
    error = None
    if out["failed"]:
        shown = "; ".join(f"{day} {message}" for day, message in out["failed"][:FAILURES_SHOWN])
        error = f"{len(out['failed'])} of {len(days)} days failed: {shown}"
    load.finish_run(conn, run_id, loaded=out["loaded"], missing=out["missing"], built=out["built"],
                    failed=len(out["failed"]), error=error)
    conn.commit()
    return {**out, "run_id": run_id}
