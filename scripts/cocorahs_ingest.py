#!/usr/bin/env python3

"""The CoCoRaHS hail jobs:  nightly, weekly and backfill.

    python3 scripts/cocorahs_ingest.py nightly              # last 3 days by report timestamp, from the timer
    python3 scripts/cocorahs_ingest.py weekly               # last 30 days by report date, from the timer
    python3 scripts/cocorahs_ingest.py backfill             # monthly chunks from 2020-10-15
    python3 scripts/cocorahs_ingest.py backfill --from 2024-01-01 --to 2024-06-30

CoCoRaHS is public data with no key and no cost.  The request names settings.contact_email in its
User-Agent.  Nothing here sends anything, calls RentCast or touches pulls and matching.  A window
that fails is rolled back and named at the end, and the exit status is 1 then.
"""

import argparse
import sys
from datetime import date, datetime, timezone
from functools import partial

from hailsys.cocorahs import fetch, ingest
from hailsys.db import get_connection
from hailsys.logconfig import configure_logging

_CONTACT_SQL = "SELECT contact_email FROM settings"


def _iso(text):
    return date.fromisoformat(text)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="CoCoRaHS hail reports into the database.")
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("nightly", help="the last 3 days by report timestamp")
    sub.add_parser("weekly", help="the last 30 days by report date")
    backfill = sub.add_parser("backfill", help="monthly chunks from the first CoCoRaHS day")
    backfill.add_argument("--from", type=_iso, dest="first", help="default 2020-10-15")
    backfill.add_argument("--to", type=_iso, dest="last", help="default today (UTC)")
    return parser.parse_args(argv)


def main(argv=None):
    configure_logging()
    args = parse_args(argv)
    today = datetime.now(timezone.utc).date()
    with get_connection() as conn:
        contact = conn.execute(_CONTACT_SQL).fetchone()["contact_email"]
        codes = ingest.states(conn)
        if not codes:
            print("settings.storm_states is empty, so there is nothing to read.", file=sys.stderr)
            return 2
        last = min(getattr(args, "last", None) or today, today)
        wins = ingest.windows(args.mode, last, first=getattr(args, "first", None))
        fetcher = partial(fetch.fetch_hail, contact=contact)
        out = ingest.run(conn, args.mode, codes, wins, fetcher)
    print(f"run {out['run_id']}: {out['loads']} loads, seen {out['seen']}, new {out['new']}, "
          f"changed {out['changed']}, rejected {out['rejected']}, failed {len(out['failed'])}")
    for what, message in out["failed"]:
        print(f"  failed {what}: {message}", file=sys.stderr)
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())