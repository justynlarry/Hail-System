#!/usr/bin/env python3
"""Backfill iem_data from the IEM Local Storm Report Archive

One-time historical load -> replay path for a window that needs re-fetching.
Takes an explicit date range, this is separate from the nightly job.

    python3 scripts/iem_backfill.py --start 2021-01-01 --end 2021-02-01

Re-running this script is safe for iem_data: the natural key plus ON CONFLICT
DO NOTHING means a window that has already been loaded inserts zero rows, so
re-running after a failure cannot duplicate storm reports.

iem_ingest_rejects is deliberately NOT deduplicated.  A re-run writes its
rejects again under the new run_id, because that table answers "what did run
47 drop" -- collapsing rejects across runs would destroy the question it
exists to answer.  Expect reject counts to grow with each re-run; that is the
design, not a leak.

Shares iem_common.py with iem_ingest.py

"""

import argparse
import sys
from datetime import datetime, timezone

from iem_common import archive_url, configure_logging, log_event, perform_run

import logging

# Archive floor is a fixed date, not a rolling window.  Set to the practical
# start of the IEM LSR archive for Colorado -- the earliest report is
# 2004-01-26.  Only drives the below_archive_floor WARNING; an earlier --start
# is still honoured.  See decision-log 2026-09-10 (supersedes 2026-09-04).

ARCHIVE_FLOOR = datetime(2004, 1, 1, tzinfo=timezone.utc)


def iso_date(text):
    """argparse type: validate YYY-MM-DD and return an aware UTC datetime
    """

    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date in YYYY-MM-DD form"
        )
    return parsed.replace(tzinfo=timezone.utc)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Backfill iem_data from the IEM LSR archive.",
    )
    parser.add_argument(
        "--start", required=True, type=iso_date,
        help="window start, YYYY-MM-DD (UTC, inclusive)",
    )
    parser.add_argument(
        "--end", required=True, type=iso_date,
        help="window end, YYYY-MM-DD (UTC, EXCLUSIVE -- the date given is "
             "midnight UTC, so its reports are not fetched)",
    )
    parser.add_argument(
        "--mode", default="backfill", choices=("backfill", "replay"),
        help="value recorded in ingest_runs.run_mode (default: backfill)",
    )
    args = parser.parse_args(argv)
    if args.end <= args.start:
        parser.error("--end must be after --start")

    return args


def month_windows(start, end):
    """Yield (start, end, url) sub-windows covering [start, end] - one/month
    """

    cursor = start
    while cursor < end:
        if cursor.month == 12:
            following = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            following = cursor.replace(month=cursor.month + 1, day=1)
        following = min(following, end)
        yield cursor, following, archive_url(cursor, following)
        cursor = following


def main(argv=None):
    configure_logging()
    args = parse_args(argv)

    if args.start < ARCHIVE_FLOOR:
        log_event(
            "below_archive_floor", level=logging.WARNING,
            start=args.start.date(), floor=ARCHIVE_FLOOR.date(),
        )

    return perform_run(
        args.mode, args.start, args.end,
        month_windows(args.start, args.end),
    )


if __name__ == "__main__":
    sys.exit(main())



