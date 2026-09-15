#!/usr/bin/env python3

"""Export the coverage zips affected by one storm day, as CSV.

    python3 scripts/export_storm_zips.py --date 2026-06-24
    python3 scripts/export_storm_zips.py --date 2026-06-24 --type HAIL
    python3 scripts/export_storm_zips.py --date 2026-06-24 --radius 8
    python3 scripts/export_storm_zips.py --date 2026-06-24 --format zips
    python3 scripts/export_storm_zips.py --date 2026-06-24 --actionable-only

--format pairs (default): one row per REPORT-ZIP PAIR, not per zip.  A single
report typically falls within the radius of several coverage zips, and a
storm day produces many reports, so a busy day will have many rows, but not a
large amount of zip codes.  This is set up to show the shape of the data,
magnitude, source, distance, and time individually, instead of aggregating it
into one zip code per row.

--format zips: the same reports collapsed to one row per coverage zip, the
natural progression once the system as a whole is more mature and a summary
is wanted instead of the full pair-level detail.

No Magnitude Floor:  Every report for each requested type is
exported with the magnitude shown, including those that may
have a NULL magnitude to decide in the future what triggers
outreach.

Zip codes outside of the coverage area are NOT exported, along
with retired zips.  The coverage area is edited by marking rows,
not deleting them.

"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from hailsys.db import get_connection
from hailsys.iem.common import configure_logging, log_event
from hailsys.queries.storms import (
    PAIRS_COLUMNS,
    ZIPS_COLUMNS,
    fetch_pairs,
    fetch_zips,
)
from hailsys.tuning import DEFAULT_ZIP_RADIUS_MILES, denver_day_bounds, miles_to_metres


# Display Timezone:  Records are stored using UTC, this is the
# only place that time is converted, and it is converted to
# a named zone instead of a fixed offset to account for
# Daylight Savings (MDT/MST) which is dependent on the time
# of year.

OUTPUT_DIR = Path("output")

FORMATS = {
    "pairs": (fetch_pairs, PAIRS_COLUMNS),
    "zips": (fetch_zips, ZIPS_COLUMNS),
}

def local_date(text):
    """argparse type: validate YYYY-MM-DD and return a plain date.

    This is Local Date, the day that the person on the ground
    experienced it, converted to UTC range.
    """
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date in YYYY-MM-DD form"
        )

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Export coverage zips affected by one storm day.",
    )
    parser.add_argument(
        "--date", required=True, type=local_date,
        help="storm day, YYYY-MM-DD, in America/Denver local time.",
    )
    parser.add_argument(
        "--type", dest="report_text", default=None,
        help="report_text to filter on, e.g. HAIL.  Omit for all types.",
    )
    parser.add_argument(
        "--radius", type=float, default=DEFAULT_ZIP_RADIUS_MILES,
        help=f"miles from a report to collect zips"
             f"(default: {DEFAULT_ZIP_RADIUS_MILES})",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR,
        help=f"where to write the CSV (default: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--format", choices=sorted(FORMATS), default="pairs",
        help="pairs: one row per report-zip pair (default). "
             "zips: one row per coverage zip, aggregated.",
    )
    parser.add_argument(
        "--actionable-only", action="store_true",
        help="--format zips only: count only reports that are roof-relevant "
             "and at or above the type's magnitude floor.",
    )

    args = parser.parse_args(argv)

    if args.radius <= 0:
        parser.error("--radius must be positive")

    return args

def output_path(directory, day, report_text, fmt):
    label = (report_text or "ALL").replace("/", "-").replace(" ","_")
    return directory / f"storm_zips_{day.isoformat()}_{label}_{fmt}.csv"

def main(argv=None):
    configure_logging()
    args = parse_args(argv)

    window_start, window_end = denver_day_bounds(args.date)
    radius_m = miles_to_metres(args.radius)

    log_event(
        "export_start",
        date=args.date.isoformat(),
        type=args.report_text or "ALL",
        radius_miles=args.radius,
        format=args.format,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
    )

    fetch, columns = FORMATS[args.format]

    kwargs = dict(
        radius_m=radius_m,
        window_start=window_start,
        window_end=window_end,
        report_text=args.report_text,
    )
    if args.format == "zips":
        kwargs["actionable_only"] = args.actionable_only

    with get_connection() as conn:
        rows = fetch(conn, **kwargs)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = output_path(args.output_dir, args.date, args.report_text, args.format)

    # lineterminator='\n'.  Python's csv.writer emits CRLF by default,
    # and Postgres \copy rejects it with "unquoted carriage return found
    # in data."

    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([row[col] for col in columns] for row in rows)

    distinct_zips = len({row["zcta5"] for row in rows})

    if args.format == "pairs":
        distinct_reports = len({row["iem_id"] for row in rows})
        log_event(
            "export_done",
            path=str(path), pairs=len(rows),
            zips=distinct_zips, reports=distinct_reports,
        )
    else:
        log_event(
            "export_done",
            path=str(path), zips=len(rows),
            reports=sum(row["report_count"] for row in rows),
        )

    if not rows:
        # Not an error, most days won't have anything to report in the
        # coverage area.
        log_event("no_matches", date=args.date.isoformat())

    return 0

if __name__ == "__main__":
    sys.exit(main())
