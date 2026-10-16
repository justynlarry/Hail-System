"""Tests for hailsys/rentcast/dripplan.py -- which way a drip step re-checks its listings.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_plan

No network, no database: the estimate runs against a fake connection.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.rentcast import dripplan
    from hailsys.rentcast.estimate import DEFAULT_CALLS_PER_ZIP
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.asked.append(params["zip_codes"])

    def fetchall(self):
        return [{"zip_code": z, "calls_made": n, "called_at": None} for z, n in self.rows.items()]


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ChooseMethodTest(unittest.TestCase):
    def choose(self, listings, zip_calls):
        return dripplan.choose_method(listings=listings, zip_calls=zip_calls)

    def test_a_zip_pull_cheaper_than_the_lookups_is_chosen(self):
        got = self.choose(1415, 20)
        self.assertEqual(got["method"], "zip_pull")
        self.assertEqual((got["calls"], got["saves"]), (20, 1395))

    def test_lookups_cheaper_than_the_pull_are_chosen(self):
        got = self.choose(12, 20)
        self.assertEqual(got["method"], "by_id")
        self.assertEqual((got["calls"], got["saves"]), (12, 8))

    def test_a_tie_goes_to_by_id_and_saves_nothing(self):
        got = self.choose(20, 20)
        self.assertEqual((got["method"], got["calls"], got["saves"]), ("by_id", 20, 0))

    def test_one_call_either_side_of_the_tie(self):
        self.assertEqual(self.choose(21, 20)["method"], "zip_pull")
        self.assertEqual(self.choose(19, 20)["method"], "by_id")

    def test_both_costs_are_always_returned(self):
        for listings, zip_calls in ((1415, 20), (12, 20), (20, 20), (0, 0), (0, 5), (5, 0)):
            got = self.choose(listings, zip_calls)
            self.assertEqual((got["by_id_calls"], got["zip_pull_calls"]), (listings, zip_calls))
            self.assertEqual(got["calls"], min(listings, zip_calls))
            self.assertEqual(set(got), {"method", "calls", "by_id_calls", "zip_pull_calls",
                                        "saves"})

    def test_nothing_to_check_costs_nothing_by_id(self):
        got = self.choose(0, 5)
        self.assertEqual((got["method"], got["calls"]), ("by_id", 0))
        self.assertEqual(self.choose(0, 0)["method"], "by_id")

    def test_the_names_are_the_constants(self):
        self.assertEqual((dripplan.BY_ID, dripplan.ZIP_PULL), ("by_id", "zip_pull"))

    def test_bad_counts_are_refused(self):
        for bad in (-1, 1.5, "3", None, True, [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.choose(bad, 5)
                with self.assertRaises(ValueError):
                    self.choose(5, bad)

    def test_the_arguments_are_keyword_only(self):
        with self.assertRaises(TypeError):
            dripplan.choose_method(5, 5)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ZipCallsEstimateTest(unittest.TestCase):
    def test_history_is_used_and_new_zips_get_the_default(self):
        conn = FakeConn({"80911": 3, "80004": 1})
        self.assertEqual(dripplan.zip_calls_estimate(conn, ["80911", "80004", "80000"]),
                         3 + 1 + DEFAULT_CALLS_PER_ZIP)

    def test_a_repeated_zip_counts_once(self):
        conn = FakeConn({"80911": 3})
        self.assertEqual(dripplan.zip_calls_estimate(conn, ["80911", "80911", "80911"]), 3)
        self.assertEqual(conn.asked, [["80911"]])

    def test_the_zips_are_asked_for_sorted(self):
        conn = FakeConn({})
        dripplan.zip_calls_estimate(conn, ["80911", "80004", "80500"])
        self.assertEqual(conn.asked, [["80004", "80500", "80911"]])

    def test_no_zips_cost_nothing_and_ask_nothing(self):
        conn = FakeConn({})
        self.assertEqual(dripplan.zip_calls_estimate(conn, []), 0)
        self.assertEqual(conn.asked, [])

    def test_unpulled_zips_cost_the_default_each(self):
        conn = FakeConn({})
        self.assertEqual(dripplan.zip_calls_estimate(conn, ["1", "2", "3"]),
                         3 * DEFAULT_CALLS_PER_ZIP)


if __name__ == "__main__":
    unittest.main()
