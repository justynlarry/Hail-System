#!/usr/bin/env python3

"""The radar jobs:  nightly, weekly rebuild, backfill and warnings for the years before the radar.

    python3 scripts/radar_ingest.py nightly                 # the last 3 finished days, from the timer
    python3 scripts/radar_ingest.py rebuild --days 30       # zones again from stored cells, from the timer
    python3 scripts/radar_ingest.py backfill                # every day from 2020-10-14 not yet held
    python3 scripts/radar_ingest.py backfill --limit 100 --to 2025-12-31
    python3 scripts/radar_ingest.py warnings                # NWS warnings from the first stored report

Radar files are public NOAA data and warnings are IEM data, no key, no cost.  Nothing here sends
anything, calls RentCast or touches pulls and matching.  Days are the display time zone's
dates, newest first.  A day that fails is rolled back and named at the end, the exit status is 1
then.  Backfill skips days already held, so it can be stopped and started again.
"""

import argparse
import logging
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

from hailsys.db import get_connection
from hailsys.logconfig import configure_logging
from hailsys.radar import ingest, load
from hailsys.tuning import DISPLAY_TZ

DEFAULT_NIGHTLY_DAYS = 3
DEFAULT_REBUILD_DAYS = 30
DEFAULT_PAUSE = 0.5

_HELD_SQL = "SELECT day FROM radar_days WHERE day BETWEEN %(first)s AND %(last)s"
_FIRST_REPORT_SQL = "SELECT min(utc_datetime) AS first_report FROM iem_data"
_YEARS_SQL = "SELECT vtec_year, count(*) AS n FROM warning_polygons GROUP BY vtec_year ORDER BY vtec_year"


def _iso(text):
    return date.fromisoformat(text)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Radar layers: NOAA hail radar and NWS warnings into zones.")
    sub = parser.add_subparsers(dest="mode", required=True)
    nightly = sub.add_parser("nightly", help="load and build the last few finished days")
    nightly.add_argument("--days", type=int, default=DEFAULT_NIGHTLY_DAYS)
    nightly.add_argument("--pause", type=float, default=DEFAULT_PAUSE, help="seconds between downloads")

    rebuild = sub.add_parser("rebuild", help="build zones again from the cells already stored")
    rebuild.add_argument("--days", type=int, default=DEFAULT_REBUILD_DAYS)

    backfill = sub.add_parser("backfill", help="load and build every day not yet held, newest first")
    backfill.add_argument("--from", type=_iso, dest="first", help="oldest day, default 2020-10-14")
    backfill.add_argument("--to", type=_iso, dest="last", help="newest day, default the last finished day")
    backfill.add_argument("--limit", type=int, help="stop after this many days")
    backfill.add_argument("--pause", type=float, default=DEFAULT_PAUSE, help="seconds between downloads")

    warn = sub.add_parser("warnings", help="load NWS warnings for days before the radar archive")
    warn.add_argument("--from", type=_iso, dest="first", help="default the date of the first stored report")
    warn.add_argument("--to", type=_iso, dest="last", help="default the day before the radar archive begins")

    args = parser.parse_args(argv)
    if getattr(args, "days", 1) < 1:
        parser.error("--days must be at least 1")
    if getattr(args, "limit", None) is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def pick_days(conn, args, now):
    """The local dates this run does, newest first."""
    latest = ingest.latest_finished_day(now, DISPLAY_TZ)
    if args.mode in ("nightly", "rebuild"):
        return ingest.days_back(args.days, now, DISPLAY_TZ)
    last = min(args.last, latest) if args.last else latest
    days = ingest.days_between(args.first or ingest.FIRST_RADAR_DAY, last)
    if not days:
        return []
    held = {row["day"] for row in conn.execute(_HELD_SQL, {"first": days[-1], "last": days[0]}).fetchall()}
    todo = [day for day in days if day not in held]
    return todo[:args.limit] if args.limit else todo


def run_warnings(conn, args, now, folder):
    """Warnings only, for the years before the radar.  Returns the exit status."""
    latest = ingest.latest_finished_day(now, DISPLAY_TZ)
    first = args.first
    if first is None:
        newest = conn.execute(_FIRST_REPORT_SQL).fetchone()["first_report"]
        if newest is None:
            print("There are no storm reports, so there is no first day.", file=sys.stderr)
            return 2
        first = newest.astimezone(DISPLAY_TZ).date()
    last = min(args.last or ingest.FIRST_RADAR_DAY - timedelta(days=1), latest)
    run_id = load.start_run(conn, "backfill")
    conn.commit()
    try:
        chunks = ingest.preload_warnings(conn, first, last, DISPLAY_TZ, folder, run_id=run_id, now=now)
    except Exception as exc:
        conn.rollback()
        load.finish_run(conn, run_id, loaded=0, missing=0, error=f"{type(exc).__name__}: {exc}")
        conn.commit()
        raise
    load.finish_run(conn, run_id, loaded=0, missing=0)
    conn.commit()
    print(f"run {run_id}: warnings {first} to {last}, {chunks} requests")
    for row in conn.execute(_YEARS_SQL).fetchall():
        print(f"  {row['vtec_year']}  {row['n']:6d} polygons")
    return 0


def main(argv=None):
    configure_logging()
    args = parse_args(argv)
    now = datetime.now(timezone.utc)
    with get_connection() as conn, tempfile.TemporaryDirectory() as folder:
        if args.mode == "warnings":
            return run_warnings(conn, args, now, folder)
        days = pick_days(conn, args, now)
        out = ingest.run(conn, args.mode, days, DISPLAY_TZ, folder,
                         fetch_new=args.mode != "rebuild", pause=getattr(args, "pause", 0.0), now=now)
    print(f"run {out['run_id']}: {len(days)} days: loaded {out['loaded']}, missing {out['missing']}, "
          f"held {out['held']}, absent {out['absent']}, built {out['built']}, failed {len(out['failed'])}")
    for day, message in out["failed"]:
        print(f"  failed {day}: {message}", file=sys.stderr)
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
    