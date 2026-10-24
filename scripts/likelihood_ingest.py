#!/usr/bin/env python3

"""The hail-likelihood jobs:  nightly, rebuild and backfill.

    python3 scripts/likelihood_ingest.py nightly                # the last 3 finished days, from the timer
    python3 scripts/likelihood_ingest.py rebuild --days 30      # the last 30, from the Sunday timer
    python3 scripts/likelihood_ingest.py backfill               # every loaded radar day with no build yet
    python3 scripts/likelihood_ingest.py backfill --from 2024-01-01 --to 2024-06-30 --limit 100

Builds the zones from data already stored (radar cells, IEM reports, CoCoRaHS filings, warnings);
it fetches nothing and costs nothing.  Nothing here sends anything, calls RentCast or touches
pulls and matching.  A day that fails is rolled back and named at the end, and the exit status is
1 then.  Days are the display time zone's dates, newest first.
"""

import argparse
import sys
from datetime import date, datetime, timezone

from hailsys.db import get_connection
from hailsys.likelihood import ingest
from hailsys.logconfig import configure_logging
from hailsys.radar import ingest as radar_ingest
from hailsys.tuning import DISPLAY_TZ


def _iso(text):
    return date.fromisoformat(text)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Hail-likelihood zones from the stored evidence.")
    sub = parser.add_subparsers(dest="mode", required=True)
    nightly = sub.add_parser("nightly", help="the last few finished days")
    nightly.add_argument("--days", type=int, default=ingest.NIGHTLY_DAYS)
    rebuild = sub.add_parser("rebuild", help="the last month, to pick up late CoCoRaHS entries")
    rebuild.add_argument("--days", type=int, default=ingest.REBUILD_DAYS)
    backfill = sub.add_parser("backfill", help="every loaded radar day with no build yet")
    backfill.add_argument("--from", type=_iso, dest="first", help="oldest day, default the first radar day")
    backfill.add_argument("--to", type=_iso, dest="last", help="newest day, default the last finished day")
    backfill.add_argument("--limit", type=int, help="stop after this many days")
    args = parser.parse_args(argv)
    if getattr(args, "days", 1) < 1:
        parser.error("--days must be at least 1")
    if getattr(args, "limit", None) is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main(argv=None):
    configure_logging()
    args = parse_args(argv)
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        latest = radar_ingest.latest_finished_day(now, DISPLAY_TZ)
        if args.mode == "backfill":
            last = min(args.last, latest) if args.last else latest
            days = ingest.pending_days(conn, args.first or radar_ingest.FIRST_RADAR_DAY, last, args.limit)
        else:
            days = radar_ingest.days_back(args.days, now, DISPLAY_TZ)
        out = ingest.run(conn, args.mode, days, DISPLAY_TZ)
    print(f"run {out['run_id']}: {len(days)} days: built {out['built']}, skipped {out['skipped']}, "
          f"failed {len(out['failed'])}")
    for day, message in out["failed"]:
        print(f"  failed {day}: {message}", file=sys.stderr)
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
