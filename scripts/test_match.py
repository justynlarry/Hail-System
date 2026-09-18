""" One-off manual check for storm matcher
Usage: python3 scripts/test_match.py 2026-06-24 HAIL --emp-id 1
"""

import argparse
from datetime import datetime

from hailsys.db import get_connection
from hailsys.matching.matcher import match_storm
from hailsys.tuning import DEFAULT_MATCH_RADIUS_MILES, denver_day_bounds

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storm_date", help="YYYY-MM-DD, local Denver day")
    parser.add_argument("report_text", nargs="?", default=None,
                        help='e.g. "HAIL"; omit to match every actionable type')
    parser.add_argument("--emp-id", type=int, required=True)
    parser.add_argument("--radius", type=float, default=DEFAULT_MATCH_RADIUS_MILES)
    args = parser.parse_args()

    day = datetime.strptime(args.storm_date, "%Y-%m-%d").date()
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        new_matches = match_storm(
            conn, emp_id=args.emp_id, window_start=window_start,
            window_end=window_end, report_text=args.report_text,
            radius_miles=args.radius,
        )
    print(f"new matches: {new_matches}")


if __name__ == "__main__":
    main()