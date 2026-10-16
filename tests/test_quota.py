"""Tests for hailsys/queries/quota.py -- RentCast usage is api_call_log plus drip_listing_checks.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_quota

The database test skips without PGHOST.
"""

import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys import tuning
    from hailsys.queries import quota
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None

HAVE_DB = bool(os.environ.get("PGHOST")) and psycopg is not None


class FakeConn:
    def __init__(self, used):
        self.used = used
        self.calls = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return {"used": self.used}


def day_bounds(day):
    return (f"start-of-{day}", f"end-of-{day}")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class PeriodBoundsTest(unittest.TestCase):
    def test_on_and_after_the_billing_day_the_period_starts_this_month(self):
        for day in (9, 10, 31):
            self.assertEqual(quota.period_bounds(date(2026, 10, day), 9),
                             (date(2026, 10, 9), date(2026, 11, 9)))

    def test_before_the_billing_day_it_started_last_month(self):
        self.assertEqual(quota.period_bounds(date(2026, 10, 8), 9),
                         (date(2026, 9, 9), date(2026, 10, 9)))

    def test_across_the_new_year(self):
        self.assertEqual(quota.period_bounds(date(2026, 12, 20), 9),
                         (date(2026, 12, 9), date(2027, 1, 9)))
        self.assertEqual(quota.period_bounds(date(2027, 1, 3), 9),
                         (date(2026, 12, 9), date(2027, 1, 9)))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class FetchUsageTest(unittest.TestCase):
    def fetch(self, used, quota_=1000):
        conn = FakeConn(used)
        got = quota.fetch_usage(conn, today=date(2026, 10, 16), billing_day=9, quota=quota_,
                                day_bounds=day_bounds)
        return conn, got

    def test_the_query_sums_both_logs(self):
        conn, _ = self.fetch(5)
        sql = conn.calls[0][0]
        self.assertIn("FROM api_call_log", sql)
        self.assertIn("FROM drip_listing_checks", sql)
        self.assertEqual(sql.count("sum(calls_made)"), 2)
        # Added, not subtracted: with no drip rows the database test cannot tell the difference.
        self.assertRegex(sql, r"\)\s*\+\s*\(SELECT")

    def test_each_log_is_cut_to_the_same_window_on_its_own_time_column(self):
        sql = quota._USAGE_SQL
        self.assertEqual(sql.count("%(period_start)s"), 2)
        self.assertEqual(sql.count("%(period_end)s"), 2)
        self.assertIn("called_at >= %(period_start)s", sql)
        self.assertIn("checked_at >= %(period_start)s", sql)
        self.assertIn("called_at < %(period_end)s", sql)
        self.assertIn("checked_at < %(period_end)s", sql)

    def test_the_window_is_the_billing_period_in_denver_days(self):
        conn, got = self.fetch(5)
        self.assertEqual(conn.calls[0][1], {"period_start": "start-of-2026-10-09",
                                            "period_end": "start-of-2026-11-09"})
        self.assertEqual((got["period_start"], got["period_end"]),
                         (date(2026, 10, 9), date(2026, 11, 9)))

    def test_remaining_and_over(self):
        _, got = self.fetch(400)
        self.assertEqual((got["used"], got["remaining"], got["over"]), (400, 600, 0))
        _, got = self.fetch(1030)
        self.assertEqual((got["remaining"], got["over"]), (0, 30))
        _, got = self.fetch(1000)
        self.assertEqual((got["remaining"], got["over"]), (0, 0))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(HAVE_DB, "no database (PGHOST unset)")
class FetchUsageDbTest(unittest.TestCase):
    def test_used_is_the_two_tables_added_together(self):
        today = date(2026, 10, 16)
        with psycopg.connect(row_factory=dict_row) as conn:
            got = quota.fetch_usage(conn, today=today, billing_day=9, quota=1000,
                                    day_bounds=tuning.denver_day_bounds)
            start, _ = tuning.denver_day_bounds(got["period_start"])
            end, _ = tuning.denver_day_bounds(got["period_end"])
            pulls = conn.execute(
                "SELECT COALESCE(sum(calls_made), 0) AS n FROM api_call_log "
                "WHERE called_at >= %s AND called_at < %s", (start, end)).fetchone()["n"]
            drips = conn.execute(
                "SELECT COALESCE(sum(calls_made), 0) AS n FROM drip_listing_checks "
                "WHERE checked_at >= %s AND checked_at < %s", (start, end)).fetchone()["n"]
        self.assertEqual(got["used"], pulls + drips)


if __name__ == "__main__":
    unittest.main()
