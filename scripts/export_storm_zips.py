#!/usr/bin/env python3

"""Export the coverage zips affected by one storm day, as CSV.

    python3 scripts/export_storm_zips.py --date 2026-06-24
    python3 scripts/export_storm_zips.py --date 2026-06-24 --type HAIL
    python3 scripts/export_storm_zips.py --date 2026-06-24 --radius 8

One row per REPORT-ZIP PAIR, not per zip.  A single report 
typically falls within the radius of serveral coverage zips,
and a storm day produces many reports, so a busy day will have
many rows, but not a large amount of zip codes.  Right now the 
script is set up to show the shape of the data, magnitude, source,
distance, and time individually, instead of aggregating it into 
one zip code per row, which is the natural progression once the
system as a whole is more mature.

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
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from iem_common import configure_logging, log_event
from tuning import DEFAULT_ZIP_RADIUS_MILES, miles_to_metres

# Display Timezone:  Records are stored using UTC, this is the
# only place that time is converted, and it is converted to
# a named zone instead of a fixed offset to account for
# Daylight Savings (MDT/MST) which is dependent on the time
# of year.

DISPLAY_TZ = ZoneInfo("America/Denver")

OUTPUT_DIR = Path("output")

# Distance is the report point to the Nearest Edge of the
# zip polygon, ST_DWithin tests against this.  A report 
# that falls inside the zip reads 0.00.  It is not the 
# distance to the centroid, a report that falls just
# outside a large zip's boundary is genuinely close to
# that zip and the centroid distance would say otherwise

EXPORT_SQL = """
SELECT
    c.zcta5,
    c.area_name,
    i.iem_id,
    (i.utc_datetime AT TIME ZONE 'America/Denver') AS local_time,
    i.utc_datetime,
    i.report_type,
    i.report_text,
    i.magnitude,
    t.mag_unit,
    round(
        (ST_Distance(i.geom::geography, z.geom::geography) / 1609.344)::numeric,
        2
    ) AS distance_miles,
    i.report_source,
    s.confidence_tier,
    i.report_qualifier,
    i.county,
    i.nws_issuer,
    i.latitude,
    i.longitude,
    i.remark
FROM iem_data i
JOIN report_types t
    ON t.report_type = i.report_type
    AND t.report_text = i.report_text
-- LEFT JOIN, not JOIN.  report_sources has no Foreign Key from iem_data
-- and should not get one.  SOURCE is free text typed at NWS offices, a 
-- new value would break nightly ingest.  Source with no lookup row 
-- yields a NULL tier here instead of dropping the report.
LEFT JOIN report_sources s
    ON s.source = i.report_source_norm
JOIN zcta_boundaries z
    ON ST_DWithin(i.geom::geography, z.geom::geography, %(radius_m)s)
JOIN coverage_zips c
    ON c.zcta5 = z.zcta5
    AND c.removed_at IS NULL
WHERE i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
ORDER BY c.zcta5, i.utc_datetime, distance_miles
"""

COLUMNS = [
    "zcta5", "area_name", "iem_id", "local_time", "utc_datetime",
    "report_type", "report_text", "magnitude", "mag_unit", "distance_miles",
    "report_source", "confidence_tier", "report_qualifier", "county",
    "nws_issuer", "latitude", "longitude", "remark",
]

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

    args = parser.parse_args(argv)

    if args.radius <= 0:
        parser.error("--radius must be positive")

    return args

def denver_day_bounds(day):
    """UTC half-open range covering one Calendar Day in Denver

    If a storm occurs later in the day, the UTC will record it as
    the next day, this portion counters that problem.

    The end bound is built by combining the next date with midnight,
    not by adding the timedelta(days=1) to the start.  
    """
    start = datetime.combine(day, time.min, tzinfo=DISPLAY_TZ)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=DISPLAY_TZ)
    return start, end

def output_path(directory, day, report_text):
    label = (report_text or "ALL").replace("/", "-").replace(" ","_")
    return directory / f"storm_zips_{day.isoformat()}_{label}.csv"

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
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
    )

    with psycopg.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(EXPORT_SQL, {
                "radius_m": radius_m,
                "window_start": window_start,
                "window_end": window_end,
                "report_text": args.report_text,
            })
            rows = cur.fetchall()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = output_path(args.output_dir, args.date, args.report_text)

    # lineterminator='\n'.  Python's csv.writer emits CRLF by default,
    # and Postgres \copy rejects it with "unquoted carriage return found
    # in data."

    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    distinct_zips = len({row[0] for row in rows})
    distinct_reports = len({row[2] for row in rows})

    log_event(
        "export_done",
        path=str(path), pairs=len(rows),
        zips=distinct_zips, report=distinct_reports,
    )

    if not rows:
        # Not an error, most days won't have anything to report in the
        # coverage area.
        log_event("no_matches", date=args.date.isoformat())

    return 0

if __name__ == "__main__":
    sys.exit(main())