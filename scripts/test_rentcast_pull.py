""" One-off manual test for the RentCast pull orchestrator

usage:
    python3 scripts/test_rentcast_pull.py 80014 --emp-id 1
"""

import argparse
import logging

from hailsys.db import get_connection
from hailsys.rentcast.pull import run_pull

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_code", help="Single zip code to pull, e.g. 80014")
    parser.add_argument("--emp-id", type=int, required=True,
                        help="emp_id to record who ran this pull.")
    parser.add_argument("--days-old", type=int, default=None,
                        help="RentCast daysOld filter (omit for no filter)")
    args = parser.parse_args()

    with get_connection() as conn:
        pull_id = run_pull(
            conn, emp_id=args.emp_id, storm_date=None, report_text = None,
            zip_codes=[args.zip_code], estimated_api_calls=1,
            status="Active", days_old=args.days_old,
        )
    print(f"pull_id={pull_id} --check api_pulls/api_call_log/properties/"
          f"listings/realtors by hand")

if __name__ == "__main__":
    main()
                    