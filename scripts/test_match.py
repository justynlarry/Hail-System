""" One-off manual check for storm matcher
Usage: python3 scripts/test_match.py 2026-06-24 HAIL --emp-id 2

--emp-id should be a real operator, not 1 (the system account): the run is
attributed to it in match_runs and storm_listing_matches.
"""

import argparse
from datetime import datetime

from hailsys.db import get_connection
from hailsys.matching.matcher import match_storm
from hailsys.tuning import DEFAULT_MATCH_RADIUS_MILES, denver_day_bounds

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storm_date", help="YYYY-MM-DD, local Denver day")
    # Required: a match_runs row names one storm day and one type, so there
    # is no all-types run any more (decision log 2026-09-23).
    parser.add_argument("report_text", help='e.g. "HAIL"')
    parser.add_argument("--emp-id", type=int, required=True)
    parser.add_argument("--radius", type=float, default=DEFAULT_MATCH_RADIUS_MILES)
    args = parser.parse_args()

    day = datetime.strptime(args.storm_date, "%Y-%m-%d").date()
    window_start, window_end = denver_day_bounds(day)

    with get_connection() as conn:
        new_matches = match_storm(
            conn, emp_id=args.emp_id, storm_date=day,
            window_start=window_start, window_end=window_end,
            report_text=args.report_text, radius_miles=args.radius,
        )
    print(f"new matches: {new_matches}")


if __name__ == "__main__":
    main()
