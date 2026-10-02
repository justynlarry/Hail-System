"""feed_rows() flattens the activity feed into CSV rows.  No Flask/DB.
"""

import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from hailsys.queries.activity import ACTIVITY_COLUMNS, feed_rows

DENVER = ZoneInfo("America/Denver")
T = datetime(2026, 10, 2, 18, 30, tzinfo=timezone.utc)


def _feed():
    return {
        "new_storms": [{"storm_date": date(2026, 10, 1), "report_text": "HAIL",
                        "zip_count": 4, "new_reports": 2}],
        "pulls": [
            {"started_at": T, "storm_date": date(2026, 10, 1),
             "report_text": "HAIL", "zip_count": 4, "listings_returned": None,
             "api_status": "complete", "emp_fname": "Ann", "emp_lname": "Lee"},
            {"started_at": T, "storm_date": None, "report_text": None,
             "zip_count": 1, "listings_returned": 7, "api_status": "running",
             "emp_fname": "Bo", "emp_lname": None},
        ],
        "match_runs": [{"matched_at": T, "storm_date": date(2026, 10, 1),
                        "report_texts": "HAIL", "listings": 9,
                        "emp_fname": None, "emp_lname": None}],
    }

class FeedRowsTest(unittest.TestCase):
    def setUp(self):
        self.rows = feed_rows(_feed(), DENVER)
    def test_every_row_has_column(self):
        for r in self.rows:
            self.assertEqual(set(r), set(ACTIVITY_COLUMNS))
    def test_one_row_per_event_and_kinds(self):
        self.assertEqual([r["kind"] for r in self.rows],
                         ["new_storm", "pull", "pull", "match_run"])

    def test_times_are_denver_not_utc(self):
        self.assertEqual(self.rows[1]["when"], "2026-10-02 12:30")
        self.assertEqual(self.rows[3]["when"], "2026-10-02 12:30")

    def test_manual_pull_has_blank_storm_and_zero_listings_default(self):
        self.assertEqual(self.rows[1]["listings"], 0)
        self.assertEqual(self.rows[2]["storm_date"], "")
        self.assertEqual(self.rows[2]["report_text"], "")

    def test_names_handle_missing_parts(self):
        self.assertEqual(self.rows[1]["user"], "Ann Lee")
        self.assertEqual(self.rows[2]["user"], "Bo")
        self.assertEqual(self.rows[3]["user"], "Unattributed")


if __name__ == "__main__":
    unittest.main()    
    