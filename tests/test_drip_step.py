"""Tests for hailsys/rentcast/dripstep.py -- spending RentCast calls on one drip step.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_step

What they pin: the by-id loop stops after 10 failures in a row (and not on 10 scattered ones),
stops at once on an auth or malformed-request failure, and carries the calls spent; listings with a
recent answer are not paid for again; the zip pull reads each listing off the pull (returned,
not returned, zip failed, no zip), links the pull to the step, and closes the pull as failed
when it cannot go on; no text from a response reaches a reason.  Everything is faked:  no
database, no network.
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.rentcast import client, dripcheck, dripstep
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

TODAY = date(2026, 10, 16)
FRESH = 7
SECRET = "SECRET-BODY-TEXT"
AGENT = "agent@example.com"


def item(n, email=AGENT):
    return {"listing_id": n, "rentcast_id": f"rc{n}", "email": email}


def verdict(outcome="active", calls=1):
    return {"outcome": outcome, "rc_status": "Active", "last_seen": None, "removed_at": None,
            "http_status": 200, "calls_made": calls, "detail": None}


def raw(rc_id, **extra):
    base = {"id": rc_id, "status": "Active", "lastSeenDate": "2026-10-16T10:51:59.131Z",
            "removedDate": None, "listingAgent": {"name": "A", "email": AGENT}}
    base.update(extra)
    return base


class FakeConn:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchall(self):
        return self.rows

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class HelpersTest(unittest.TestCase):
    def test_recent_definite_returns_listing_ids_and_passes_the_window(self):
        conn = FakeConn([{"listing_id": 4}, {"listing_id": 9}])
        self.assertEqual(dripstep.recent_definite(conn, 7, 2), {4, 9})
        self.assertEqual(conn.executed[0][1], {"campaign_id": 7, "step_no": 2, "hours": 24})

    def test_recent_definite_counts_only_definite_outcomes(self):
        self.assertIn("'active', 'inactive', 'not_found'", dripstep._RECENT_SQL)
        self.assertNotIn("'error'", dripstep._RECENT_SQL)

    def test_listing_zips_maps_listing_to_zip(self):
        conn = FakeConn([{"listing_id": 1, "zip_code": "80202"},
                         {"listing_id": 2, "zip_code": None}])
        self.assertEqual(dripstep.listing_zips(conn, {1, 2}), {1: "80202", 2: None})
        self.assertEqual(sorted(conn.executed[0][1]["listing_ids"]), [1, 2])


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class CheckByIdTest(unittest.TestCase):
    def run_loop(self, listings, verdicts, skip=frozenset()):
        conn = FakeConn()
        with mock.patch.object(dripcheck, "check_listing", side_effect=verdicts) as chk:
            result = dripstep.check_by_id(conn, campaign_id=5, step_no=2, listings=listings,
                                          skip=skip, today=TODAY, freshness_days=FRESH)
        return result, chk

    def test_checks_every_listing_and_sums_calls(self):
        result, chk = self.run_loop([item(1), item(2), item(3)],
                                    [verdict(calls=1), verdict("inactive", 1), verdict(calls=2)])
        self.assertEqual(result, {"method": "by_id", "calls": 4, "checked": 3, "reused": 0})
        self.assertEqual(chk.call_count, 3)

    def test_passes_the_step_the_id_and_the_address_emailed(self):
        _, chk = self.run_loop([item(1, email="a@x.com")], [verdict()])
        self.assertEqual(chk.call_args.kwargs, {
            "campaign_id": 5, "step_no": 2, "listing_id": 1, "rentcast_id": "rc1",
            "today": TODAY, "freshness_days": FRESH, "agent_email": "a@x.com"})

    def test_skipped_listings_are_not_looked_up_and_are_counted_as_reused(self):
        result, chk = self.run_loop([item(1), item(2), item(3)], [verdict()], skip={1, 3})
        self.assertEqual(chk.call_count, 1)
        self.assertEqual(chk.call_args.kwargs["listing_id"], 2)
        self.assertEqual((result["checked"], result["reused"]), (1, 2))

    def test_ten_failures_in_a_row_stop_the_step_with_the_calls_spent(self):
        listings = [item(n) for n in range(1, 21)]
        with self.assertRaises(dripstep.StepStopped) as cm:
            self.run_loop(listings, [verdict("error", 3)] * 20)
        self.assertEqual(cm.exception.calls, 30)
        self.assertEqual(cm.exception.reason, "10 lookups in a row failed")

    def test_the_loop_stops_at_the_tenth_failure_not_later(self):
        listings = [item(n) for n in range(1, 21)]
        conn = FakeConn()
        with mock.patch.object(dripcheck, "check_listing",
                               return_value=verdict("error", 1)) as chk:
            with self.assertRaises(dripstep.StepStopped):
                dripstep.check_by_id(conn, campaign_id=5, step_no=2, listings=listings,
                                     skip=set(), today=TODAY, freshness_days=FRESH)
        self.assertEqual(chk.call_count, 10)

    def test_nine_failures_do_not_stop_it(self):
        listings = [item(n) for n in range(1, 11)]
        result, _ = self.run_loop(listings, [verdict("error")] * 9 + [verdict()])
        self.assertEqual(result["checked"], 10)

    def test_a_success_resets_the_count(self):
        listings = [item(n) for n in range(1, 21)]
        pattern = ([verdict("error")] * 9 + [verdict()]) * 2
        result, _ = self.run_loop(listings, pattern)
        self.assertEqual(result["checked"], 20)

    def test_not_found_and_inactive_are_not_failures(self):
        listings = [item(n) for n in range(1, 16)]
        result, _ = self.run_loop(listings, [verdict("not_found", 0)] * 12 +
                                  [verdict("inactive")] * 3)
        self.assertEqual(result["checked"], 15)

    def test_an_auth_failure_stops_at_once_and_adds_the_failed_attempts(self):
        err = client.RentCastAuthError(SECRET, attempts=1, status=401)
        with self.assertRaises(dripstep.StepStopped) as cm:
            self.run_loop([item(1), item(2), item(3)], [verdict(calls=1), err])
        self.assertEqual(cm.exception.calls, 2)
        self.assertEqual(cm.exception.reason, "RentCast refused the request (RentCastAuthError)")

    def test_a_malformed_request_failure_stops_at_once(self):
        err = client.RentCastValidationError(SECRET, attempts=1, status=400)
        with self.assertRaises(dripstep.StepStopped) as cm:
            self.run_loop([item(1), item(2)], [err])
        self.assertIn("RentCastValidationError", cm.exception.reason)

    def test_no_response_text_reaches_the_reason(self):
        err = client.RentCastAuthError(SECRET, attempts=1, status=401)
        with self.assertRaises(dripstep.StepStopped) as cm:
            self.run_loop([item(1)], [err])
        self.assertNotIn(SECRET, cm.exception.reason)
        self.assertNotIn(SECRET, str(cm.exception))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class CheckByZipPullTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConn()
        self.records = []
        patches = [
            mock.patch.object(dripcheck, "record_check",
                              side_effect=lambda conn, **kw: self.records.append(kw)),
            mock.patch.object(dripstep.pull, "create_pull", return_value=77),
            mock.patch.object(dripstep.pull, "_log_zip"),
            mock.patch.object(dripstep.pull, "_finish_pull"),
            mock.patch.object(dripstep.upsert, "upsert_listings"),
        ]
        (self.record_check, self.create, self.log_zip, self.finish,
         self.upsert) = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def run_pull(self, listings, zips, answers, skip=frozenset()):
        """answers: zip -> (list, calls) or an exception, in the order searched."""
        def search(zip_code, status="Active", days_old=None):
            result = answers[zip_code]
            if isinstance(result, Exception):
                raise result
            return result
        with mock.patch.object(client, "search_sale_listings", side_effect=search) as s:
            self.search = s
            return dripstep.check_by_zip_pull(
                self.conn, campaign_id=5, step_no=2, listings=listings, skip=skip, zips=zips,
                emp_id=3, estimated_calls=4, today=TODAY, freshness_days=FRESH)

    def by_listing(self):
        return {r["listing_id"]: r["verdict"] for r in self.records}

    def test_a_returned_active_listing_is_recorded_active_for_no_calls(self):
        result = self.run_pull([item(1)], {1: "80202"}, {"80202": ([raw("rc1")], 1)})
        v = self.by_listing()[1]
        self.assertEqual((v["outcome"], v["calls_made"], v["http_status"]), ("active", 0, 200))
        self.assertEqual(result["calls"], 1)

    def test_a_listing_missing_from_the_pull_is_not_found_for_no_calls(self):
        self.run_pull([item(1)], {1: "80202"}, {"80202": ([raw("other")], 1)})
        v = self.by_listing()[1]
        self.assertEqual((v["outcome"], v["calls_made"]), ("not_found", 0))

    def test_a_returned_listing_is_judged_with_the_address_emailed(self):
        self.run_pull([item(1, email="old@x.com")], {1: "80202"}, {"80202": ([raw("rc1")], 1)})
        self.assertEqual(self.by_listing()[1]["outcome"], "inactive")

    def test_a_returned_stale_listing_is_inactive(self):
        stale = raw("rc1", lastSeenDate="2026-09-01T00:00:00.000Z")
        self.run_pull([item(1)], {1: "80202"}, {"80202": ([stale], 1)})
        self.assertEqual(self.by_listing()[1]["outcome"], "inactive")

    def test_a_listing_with_no_zip_is_an_error_and_is_not_pulled(self):
        self.run_pull([item(1), item(2)], {1: None, 2: "80202"}, {"80202": ([raw("rc2")], 1)})
        self.assertEqual(self.by_listing()[1]["outcome"], "error")
        self.assertEqual(self.search.call_count, 1)
        self.assertEqual(self.create.call_args.kwargs["zip_count"], 1)

    def test_each_zip_is_pulled_once_for_active_listings_in_order(self):
        self.run_pull([item(1), item(2), item(3)], {1: "80301", 2: "80202", 3: "80202"},
                      {"80301": ([], 1), "80202": ([], 1)})
        self.assertEqual([c.args[0] for c in self.search.call_args_list], ["80202", "80301"])
        self.assertEqual(self.search.call_args_list[0].kwargs["status"], "Active")

    def test_skipped_listings_are_not_pulled_for_or_recorded(self):
        result = self.run_pull([item(1), item(2)], {1: "80202", 2: "80301"},
                               {"80301": ([raw("rc2")], 1)}, skip={1})
        self.assertEqual(set(self.by_listing()), {2})
        self.assertEqual([c.args[0] for c in self.search.call_args_list], ["80301"])
        self.assertEqual((result["checked"], result["reused"]), (1, 1))

    def test_the_pull_is_created_for_no_storm_and_linked_to_the_step(self):
        self.run_pull([item(1)], {1: "80202"}, {"80202": ([], 1)})
        kw = self.create.call_args.kwargs
        self.assertEqual((kw["emp_id"], kw["storm_date"], kw["report_text"],
                          kw["estimated_api_calls"]), (3, None, None, 4))
        sql, params = self.conn.executed[0]
        self.assertIn("drip_step_pulls", sql)
        self.assertEqual(params, {"pull_id": 77, "campaign_id": 5, "step_no": 2})

    def test_each_zip_is_logged_and_the_pull_is_finished_complete(self):
        result = self.run_pull([item(1)], {1: "80202"}, {"80202": ([raw("rc1"), raw("z")], 2)})
        self.log_zip.assert_called_once_with(self.conn, 77, "80202", 2, 2, 200)
        self.finish.assert_called_once_with(self.conn, 77, 2, 2, "complete")
        self.assertEqual((result["method"], result["calls"], result["pull_id"]),
                         ("zip_pull", 2, 77))

    def test_returned_listings_are_stored_like_any_pull(self):
        found = [raw("rc1")]
        self.run_pull([item(1)], {1: "80202"}, {"80202": (found, 1)})
        self.upsert.assert_called_once_with(self.conn, found)

    def test_an_empty_zip_stores_nothing(self):
        self.run_pull([item(1)], {1: "80202"}, {"80202": ([], 1)})
        self.upsert.assert_not_called()

    def test_a_failed_zip_marks_only_its_listings_as_errors_and_the_step_goes_on(self):
        err = client.RentCastServerError(SECRET, attempts=3, status=503)
        result = self.run_pull([item(1), item(2)], {1: "80202", 2: "80301"},
                               {"80202": err, "80301": ([raw("rc2")], 1)})
        v = self.by_listing()
        self.assertEqual((v[1]["outcome"], v[1]["http_status"]), ("error", 503))
        self.assertEqual(v[2]["outcome"], "active")
        self.assertEqual(result["calls"], 4)
        self.finish.assert_called_once_with(self.conn, 77, 4, 1, "complete")
        self.log_zip.assert_any_call(self.conn, 77, "80202", 3, 0, 503)

    def test_a_failed_zip_records_no_response_text(self):
        err = client.RentCastServerError(SECRET, attempts=3, status=503)
        self.run_pull([item(1)], {1: "80202"}, {"80202": err})
        self.assertNotIn(SECRET, str(self.by_listing()[1]))

    def test_an_auth_failure_ends_the_step_and_fails_the_pull(self):
        err = client.RentCastAuthError(SECRET, attempts=1, status=401)
        with self.assertRaises(dripstep.StepStopped) as cm:
            self.run_pull([item(1), item(2)], {1: "80202", 2: "80301"},
                          {"80202": ([raw("rc1")], 1), "80301": err})
        self.assertEqual(cm.exception.calls, 2)
        self.assertNotIn(SECRET, cm.exception.reason)
        self.finish.assert_called_once_with(self.conn, 77, 2, 1, "failed")
        self.assertEqual(set(self.by_listing()), {1})

    def test_a_malformed_request_ends_the_step(self):
        err = client.RentCastValidationError(SECRET, attempts=1, status=400)
        with self.assertRaises(dripstep.StepStopped):
            self.run_pull([item(1)], {1: "80202"}, {"80202": err})
        self.finish.assert_called_once_with(self.conn, 77, 1, 0, "failed")

    def test_a_failed_save_rolls_back_fails_the_pull_and_raises(self):
        self.upsert.side_effect = RuntimeError("db down")
        with self.assertRaises(RuntimeError):
            self.run_pull([item(1)], {1: "80202"}, {"80202": ([raw("rc1")], 1)})
        self.assertEqual(self.conn.rollbacks, 1)
        self.finish.assert_called_once_with(self.conn, 77, 1, 1, "failed")

    def test_an_unclassified_error_is_counted_as_a_call_fails_the_pull_and_raises(self):
        with self.assertRaises(ValueError):
            self.run_pull([item(1)], {1: "80202"}, {"80202": ValueError("odd")})
        self.log_zip.assert_called_once_with(self.conn, 77, "80202", 1, 0, None)
        self.finish.assert_called_once_with(self.conn, 77, 1, 0, "failed")

    def test_every_record_names_the_campaign_and_step(self):
        self.run_pull([item(1), item(2)], {1: "80202", 2: None}, {"80202": ([raw("rc1")], 1)})
        for r in self.records:
            self.assertEqual((r["campaign_id"], r["step_no"]), (5, 2))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class PlanMethodTest(unittest.TestCase):
    def plan(self, todo, zips, estimate):
        with mock.patch.object(dripstep, "listing_zips", return_value=zips) as lz, \
                mock.patch.object(dripstep.dripplan, "zip_calls_estimate",
                                  return_value=estimate) as est:
            result = dripstep.plan_method(FakeConn(), todo)
        return result, lz, est

    def test_nothing_to_check_reads_nothing(self):
        (zips, plan), lz, est = self.plan([], {}, 0)
        lz.assert_not_called()
        self.assertEqual((zips, plan["method"], plan["calls"]), ({}, "by_id", 0))

    def test_the_cheaper_way_is_chosen(self):
        todo = [item(n) for n in range(1, 6)]
        zips = {n: "80202" for n in range(1, 6)}
        (_, plan), _, _ = self.plan(todo, zips, 1)
        self.assertEqual((plan["method"], plan["calls"]), ("zip_pull", 1))

    def test_a_tie_is_by_id(self):
        todo = [item(1), item(2)]
        (_, plan), _, _ = self.plan(todo, {1: "80202", 2: "80202"}, 2)
        self.assertEqual((plan["method"], plan["calls"]), ("by_id", 2))

    def test_a_listing_with_no_zip_forces_by_id_and_its_cost(self):
        todo = [item(n) for n in range(1, 6)]
        zips = {1: "80202", 2: None, 3: "80202", 4: "80202", 5: "80202"}
        (_, plan), _, _ = self.plan(todo, zips, 1)
        self.assertEqual((plan["method"], plan["calls"]), ("by_id", 5))
        self.assertEqual(plan["zip_pull_calls"], 1)

    def test_a_listing_missing_from_the_lookup_forces_by_id(self):
        todo = [item(n) for n in range(1, 6)]
        zips = {n: "80202" for n in range(1, 5)}
        (_, plan), _, _ = self.plan(todo, zips, 1)
        self.assertEqual((plan["method"], plan["calls"]), ("by_id", 5))

    def test_the_estimate_covers_only_the_zips_of_listings_still_to_check(self):
        (_, _), lz, est = self.plan([item(2)], {2: "80203"}, 1)
        self.assertEqual(lz.call_args.args[1], [2])
        self.assertEqual(est.call_args.args[1], {"80203"})

    def test_no_zip_is_not_asked_about_in_the_estimate(self):
        (_, _), _, est = self.plan([item(1), item(2)], {1: "80202", 2: None}, 1)
        self.assertEqual(est.call_args.args[1], {"80202"})


if __name__ == "__main__":
    unittest.main()
