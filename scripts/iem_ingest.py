#!/usr/bin/env python3

"""Nightly ingest of IEM Local Storm Reports.

Computes a rolling window from the clock and runs from a systemd timer; the
natural key discards what is already held, so the window overlaps freely.

    python3 scripts/iem_ingest.py
    python3 scripts/iem_ingest.py --hours 72     # widen after an outage

Shares iem_common.py with iem_backfill.py.

THE OVERLAP IS THE RECOVERY MECHANISM.  The default 30h against a daily timer
means one missed night self-heals on the next run; two consecutive misses leave
a hole only a replay closes, which is why the timer carries Persistent=true.
Expect rows_seen >> rows_inserted -- if they are equal the overlap is not
overlapping, and that raises no error.

KNOWN GAP: the window filters on VALID (when the storm happened), not on when
IEM received the report, so a report entered days late falls outside every
nightly window.  A weekly `iem_backfill.py --mode replay` over the last ~30 days
closes it -- and a replay that inserts rows is how we learn late entry happens.
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

