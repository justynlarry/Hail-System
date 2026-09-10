#!/usr/bin/env python3

"""Nightly ingest of IEM Local Storm Reports

Pulls a rolling/overlapping time window, the natural key allows
discard of records already in the database.  Computes its own
window from the clock, and runs from systemd timer.

    python3 scripts/iem_ingest.py
    python3 scripts/iem_ingest.py --hours 72

Shares iem_common.py with iem_backfill.py.

rows_seen will likely exceed rows_inserted.

"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from iem_common import configure_logging, perform_run, recent_url

# 30 Hour window, buffer of 6 hours overlap from previous run

DEFAULT_HOURS = 30

SECONDS_PER_HOUR = 3600


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Nightly ingest of recent IEM Local Storm Reports.",
    )
    parser.add_argument(
        "--hours", type=int, default=DEFAULT_HOURS,
        help=f"how far back to look, in hours (default: {DEFAULT_HOURS}). "
             f"Widen this to cover a gap after an outage; the natural key "
             f"makes the overlap free.",
    )
    args = parser.parse_args(argv)

    if args.hours < 1:
        parser.error("--hours must be at least 1")

    return args


def main(argv=None):
    configure_logging()
    args = parse_args(argv)

    seconds = args.hours * SECONDS_PER_HOUR

    # IEM computes 'recent' from its clock not the system, so these two
    # timestamps are the best account of the window, but not exact

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=args.hours)

    windows = [(window_start, window_end, recent_url(seconds))]

    return perform_run("nightly", window_start, window_end, windows)


if __name__ == "__main__":
    sys.exit(main())

