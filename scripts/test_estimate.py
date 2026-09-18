"""One-off manual check for estimate_pull's recently-pulled split (item 4).
Usage: python3 scripts/test_estimate.py 2026-06-24 HAIL
"""

import argparse
from datetime import datetime, timedelta, timezone

from hailsys.db import get_connection
from hailsys.rentcast.estimate import estimate_pull
from hailsys.tuning import DEFAULT_ZIP_RADIUS_MILES, miles_to_metres

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storm_date", help="YYYY-MM-DD, local Denver day")
    parser.add_argument("report_text", help='e.g. "HAIL"')
    args = parser.parse_args()

    day = datetime.strptime(args.storm_date, "%Y-%m-%d").date()
    window_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=1)

    with get_connection() as conn:
        result = estimate_pull(
            conn, radius_m=miles_to_metres(DEFAULT_ZIP_RADIUS_MILES),
            window_start=window_start, window_end=window_end,
            report_text=args.report_text,
        )

    print(f"zip_count={result['zip_count']} "
         f"estimated_api_calls={result['estimated_api_calls']}")
    print(f"pulled_recently ({len(result['pulled_recently'])}):")
    for r in result["pulled_recently"]:
        print(f"  {r['zip_code']}  last pulled {r['last_pulled']}")
    print(f"not_pulled_recently ({len(result['not_pulled_recently'])}):")
    for z in result["not_pulled_recently"]:
        print(f"  {z}")

if __name__ == "__main__":
    main()