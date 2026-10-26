"""Tests for hailsys/rentcast/estimate.py after estimate_zips was split out of estimate_pull (Phase 5c, step 2a).

Read only, no RentCast call:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_estimate_zips

What they pin:  estimate_pull gives the same answer as estimate_zips over the zips it found (so the live pull page
did not change with the split);  the zip list is sorted and de-duplicated;  an empty list is zero zips and zero calls;
a zip never pulled costs the default one call;  a zip pulled before costs what it cost last time and is flagged when
the pull was recent.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

from hailsys.rentcast import estimate
from hailsys.tuning import RECENT_PULL_WINDOW_DAYS, denver_day_bounds, miles_to_metres


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class EstimateZipsTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)

    def test_an_empty_list_is_zero_zips_and_zero_calls(self):
        out = estimate.estimate_zips(self.conn, [])
        self.assertEqual((out["zip_count"], out["estimated_api_calls"], out["zips"]), (0, 0, []))
        self.assertEqual((out["pulled_recently"], out["not_pulled_recently"]), ([], []))

    def test_the_list_is_sorted_and_duplicates_are_dropped(self):
        out = estimate.estimate_zips(self.conn, ["99998", "99997", "99998"])
        self.assertEqual(out["zips"], ["99997", "99998"])
        self.assertEqual(out["zip_count"], 2)

    def test_a_zip_never_pulled_costs_the_default_one_call_and_is_not_recent(self):
        out = estimate.estimate_zips(self.conn, ["99997", "99998", "99996"])
        self.assertEqual(out["estimated_api_calls"], 3 * estimate.DEFAULT_CALLS_PER_ZIP)
        self.assertEqual(out["pulled_recently"], [])
        self.assertEqual(out["not_pulled_recently"], ["99996", "99997", "99998"])

    def test_a_zip_pulled_before_costs_what_it_cost_last_time(self):
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        emp = self.conn.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]
        now = datetime.now(timezone.utc)
        for zip_code, calls, age_days in (("99991", 3, 1), ("99992", 2, RECENT_PULL_WINDOW_DAYS + 30)):
            pull = self.conn.execute(
                "INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count, estimated_api_calls, started_at, "
                "api_status) VALUES (%s, '1999-06-09', 'HAIL', 1, 1, now(), 'complete') RETURNING pull_id",
                (emp,)).fetchone()["pull_id"]
            self.conn.execute(
                "INSERT INTO api_call_log (pull_id, zip_code, called_at, calls_made, listings_returned, http_status) "
                "VALUES (%s, %s, %s, %s, 0, 200)", (pull, zip_code, now - timedelta(days=age_days), calls))
        out = estimate.estimate_zips(self.conn, ["99991", "99992", "99993"])
        self.assertEqual(out["estimated_api_calls"], 3 + 2 + estimate.DEFAULT_CALLS_PER_ZIP)
        self.assertEqual([r["zip_code"] for r in out["pulled_recently"]], ["99991"])
        self.assertEqual(out["not_pulled_recently"], ["99992", "99993"])

    def test_estimate_pull_is_the_same_as_estimate_zips_over_the_zips_it_found(self):
        from hailsys.queries import storms
        row = self.conn.execute(
            "SELECT max((utc_datetime AT TIME ZONE 'America/Denver')::date) AS d FROM iem_data "
            "WHERE report_text = 'HAIL' AND magnitude >= 1").fetchone()
        start, end = denver_day_bounds(row["d"])
        kw = dict(radius_m=miles_to_metres(5.0), window_start=start, window_end=end, report_text="HAIL",
                  actionable_only=True)
        found = {r["zcta5"] for r in storms.fetch_zips(self.conn, **kw)}
        self.assertEqual(estimate.estimate_pull(self.conn, **kw), estimate.estimate_zips(self.conn, found))

    def test_estimate_pull_keeps_its_keys(self):
        row = self.conn.execute(
            "SELECT max((utc_datetime AT TIME ZONE 'America/Denver')::date) AS d FROM iem_data "
            "WHERE report_text = 'HAIL' AND magnitude >= 1").fetchone()
        start, end = denver_day_bounds(row["d"])
        out = estimate.estimate_pull(self.conn, radius_m=miles_to_metres(5.0), window_start=start, window_end=end,
                                     report_text="HAIL", actionable_only=True)
        self.assertEqual(set(out), {"zip_count", "estimated_api_calls", "zips", "pulled_recently",
                                    "not_pulled_recently"})


if __name__ == "__main__":
    unittest.main()
