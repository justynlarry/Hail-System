"""Tests for the pull path in hailsys/web/jobs.py and hailsys/rentcast/pull.py (Phase 5c step 2b; item 261).

Before this file nothing tested start_pull, run_pull or the pull thread, the code that spends RentCast money.  Every
test runs against the real tables on one connection that is always rolled back, with RentCast replaced by a recorder
(no request leaves the machine) and the thread run inline.  Needs a role that can insert api_pulls rows (hail_admin):

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_pull_jobs

What they pin:  a pull from the hail zones writes its api_pulls row (source 'zones', the parameter set) and its pull_zips
rows in one transaction, requests each zip exactly once and finishes 'complete';  a pull from reports is unchanged (source
'reports', no zone set, no pull_zips);  a zone list that is not exactly the zips to pull, a duplicate zip, a pull already
running for the storm, and a bad zone row each raise before anything is requested and leave no api_pulls row;  a RentCast
failure or auth error marks the pull failed and stops;  the report matcher runs after a pull and the zone matcher never
does.
"""

import os
import unittest
from datetime import date, timedelta
from unittest import mock

from hailsys.rentcast.client import RentCastAuthError, RentCastServerError
from hailsys.tuning import denver_day_bounds

DAY = date(1999, 6, 9)
ZIPS = ["80106", "80808", "80999"]


class FakeThread:
    """Runs the thread body when started, on the caller's side, so a test sees the finished pull."""
    started = []

    def __init__(self, target=None, kwargs=None, daemon=None):
        self.target, self.kwargs = target, kwargs or {}

    def start(self):
        FakeThread.started.append(self)
        self.target(**self.kwargs)


class TestConn:
    """The test's one connection, handed to every get_connection() call.

    A commit moves a savepoint forward and a rollback goes back to it, so a refusal can undo its own work while the work
    before it stays, and the whole test is rolled back at the end.
    """
    def __init__(self, conn):
        self._c, self._n = conn, 0
        self._savepoint()

    def _savepoint(self):
        self._n += 1
        self._c.execute(f"SAVEPOINT t{self._n}")

    def commit(self):
        self._savepoint()

    def rollback(self):
        self._c.execute(f"ROLLBACK TO SAVEPOINT t{self._n}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        return False

    def __getattr__(self, name):
        return getattr(self._c, name)


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class PullJobsTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        from hailsys.web import jobs
        self.jobs = jobs
        self.raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.raw.close)
        self.addCleanup(self.raw.rollback)
        if not self.raw.execute("SELECT has_table_privilege('api_pulls', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert api_pulls rows (hail_admin)")
        if self.raw.execute("SELECT to_regclass('pull_zips') AS t").fetchone()["t"] is None:
            self.fail("sql/059_pull_zips.sql is not applied")
        self.conn = TestConn(self.raw)
        self.emp = self.raw.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]
        self.set_id = self.raw.execute("SELECT min(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        FakeThread.started = []
        self.search = mock.Mock(return_value=([], 1))
        patches = [
            mock.patch.object(jobs, "get_connection", return_value=self.conn),
            mock.patch.object(jobs.threading, "Thread", FakeThread),
            mock.patch("hailsys.rentcast.pull.search_sale_listings", self.search),
            mock.patch("hailsys.rentcast.pull.upsert_listings"),
            mock.patch.object(jobs, "match_storm", return_value=0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.match_storm = jobs.match_storm
        self.upsert = __import__("hailsys.rentcast.pull", fromlist=["upsert_listings"]).upsert_listings
        self.start_end = denver_day_bounds(DAY)

    def rows(self, zips=ZIPS, level="one_source"):
        return [{"zcta5": z, "basis": "zones", "best_level": level} for z in zips]

    def start(self, zips=ZIPS, zone="default", **kw):
        if zone == "default":
            zone = {"set_id": self.set_id, "rows": self.rows(zips)}
        return self.jobs.start_pull(
            emp_id=self.emp, storm_date=DAY, report_text="HAIL", zip_codes=list(zips),
            estimated_api_calls=len(zips), window_start=self.start_end[0], window_end=self.start_end[1],
            zone=zone, **kw)

    def pulls(self):
        return self.raw.execute("SELECT * FROM api_pulls WHERE storm_date = %s ORDER BY pull_id", (DAY,)).fetchall()

    def pull_zips(self, pull_id):
        return self.raw.execute("SELECT zcta5, basis, best_level FROM pull_zips WHERE pull_id = %s ORDER BY zcta5",
                                (pull_id,)).fetchall()

    def requested(self):
        return [c.args[0] for c in self.search.call_args_list]

    def nothing_happened(self):
        self.assertEqual(self.requested(), [])
        self.assertEqual(self.pulls(), [])
        self.assertEqual(FakeThread.started, [])
        self.assertEqual(self.match_storm.call_count, 0)

    # -- a pull from the zones ---------------------------------------------------------------------

    def test_a_zones_pull_writes_its_row_and_its_zips_and_finishes_complete(self):
        self.start()
        (pull,) = self.pulls()
        self.assertEqual((pull["pull_source"], pull["zone_set_id"]), ("zones", self.set_id))
        self.assertEqual((pull["storm_date"], pull["report_text"], pull["emp_id"]), (DAY, "HAIL", self.emp))
        self.assertEqual((pull["zip_count"], pull["estimated_api_calls"]), (3, 3))
        self.assertEqual((pull["api_status"], pull["actual_api_calls"], pull["listings_returned"]), ("complete", 3, 0))
        self.assertIsNotNone(pull["finished_at"])
        self.assertEqual([(r["zcta5"], r["basis"], r["best_level"]) for r in self.pull_zips(pull["pull_id"])],
                         [(z, "zones", "one_source") for z in sorted(ZIPS)])

    def test_each_zip_is_requested_exactly_once_for_active_listings(self):
        self.start()
        self.assertEqual(sorted(self.requested()), sorted(ZIPS))
        self.assertEqual(len(self.requested()), len(set(self.requested())))
        for call in self.search.call_args_list:
            self.assertEqual(call.kwargs, {"status": "Active", "days_old": None})

    def test_the_call_log_has_one_row_per_zip(self):
        self.start()
        (pull,) = self.pulls()
        logged = self.raw.execute("SELECT zip_code, calls_made, http_status FROM api_call_log WHERE pull_id = %s "
                                  "ORDER BY zip_code", (pull["pull_id"],)).fetchall()
        self.assertEqual([(r["zip_code"], r["calls_made"], r["http_status"]) for r in logged],
                         [(z, 1, 200) for z in sorted(ZIPS)])

    def test_the_basis_and_level_of_each_zip_are_kept_as_given(self):
        rows = [{"zcta5": "80106", "basis": "both", "best_level": "agree"},
                {"zcta5": "80808", "basis": "zones", "best_level": "context"},
                {"zcta5": "80999", "basis": "reports", "best_level": None}]
        self.start(zone={"set_id": self.set_id, "rows": rows})
        (pull,) = self.pulls()
        self.assertEqual([(r["zcta5"], r["basis"], r["best_level"]) for r in self.pull_zips(pull["pull_id"])],
                         [(r["zcta5"], r["basis"], r["best_level"]) for r in rows])

    def test_listings_returned_are_handed_to_the_upsert_and_counted(self):
        self.search.return_value = ([{"id": "a"}, {"id": "b"}], 2)
        self.start(zips=["80106"])
        (pull,) = self.pulls()
        self.assertEqual((pull["listings_returned"], pull["actual_api_calls"]), (2, 2))
        self.assertEqual(self.upsert.call_count, 1)

    def test_the_report_matcher_runs_after_a_zones_pull_and_the_zone_matcher_never_does(self):
        with mock.patch("hailsys.matching.zonematch.match_zones") as zone_match:
            self.start()
        self.assertEqual(self.match_storm.call_count, 1)
        self.assertEqual(self.match_storm.call_args.kwargs["report_text"], "HAIL")
        self.assertEqual(self.match_storm.call_args.kwargs["storm_date"], DAY)
        self.assertEqual(zone_match.call_count, 0)

    # -- a pull from reports, unchanged -------------------------------------------------------------

    def test_a_pull_from_reports_has_no_zone_set_and_no_pull_zips(self):
        self.start(zone=None)
        (pull,) = self.pulls()
        self.assertEqual((pull["pull_source"], pull["zone_set_id"], pull["api_status"]), ("reports", None, "complete"))
        self.assertEqual(self.pull_zips(pull["pull_id"]), [])
        self.assertEqual(sorted(self.requested()), sorted(ZIPS))
        self.assertEqual(self.match_storm.call_count, 1)

    # -- refusals spend nothing ---------------------------------------------------------------------

    def test_a_zone_list_that_is_not_exactly_the_zips_is_refused_before_anything(self):
        cases = {"a zip missing from the rows": self.rows(ZIPS[:2]),
                 "an extra row": self.rows(ZIPS + ["80111"]),
                 "a different zip": self.rows(["80106", "80808", "80111"]),
                 "a repeated row": self.rows(ZIPS) + self.rows(ZIPS[:1])}
        for label, rows in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    self.start(zone={"set_id": self.set_id, "rows": rows})
                self.nothing_happened()

    def test_a_repeated_zip_in_the_list_is_refused_before_anything(self):
        with self.assertRaises(ValueError):
            self.start(zips=["80106", "80106", "80808"], zone={"set_id": self.set_id, "rows": self.rows(["80106", "80808"])})
        self.nothing_happened()

    def test_a_bad_zone_row_leaves_no_pull_and_requests_nothing(self):
        import psycopg
        bad = self.rows(ZIPS)
        bad[1] = {"zcta5": "80808", "basis": "zones", "best_level": None}      # a zones zip needs a level
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.start(zone={"set_id": self.set_id, "rows": bad})
        self.nothing_happened()

    def test_a_malformed_zip_leaves_no_pull_and_requests_nothing(self):
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.start(zips=["8080", "80808"], zone={"set_id": self.set_id, "rows": self.rows(["8080", "80808"])})
        self.nothing_happened()

    def test_an_unknown_parameter_set_leaves_no_pull_and_requests_nothing(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.start(zone={"set_id": 987654321, "rows": self.rows()})
        self.nothing_happened()

    def test_a_pull_already_running_for_the_storm_is_refused_and_nothing_is_requested(self):
        self.raw.execute(
            "INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count, estimated_api_calls, api_status) "
            "VALUES (%s, %s, 'HAIL', 1, 1, 'running')", (self.emp, DAY))
        self.conn.commit()
        with self.assertRaises(self.jobs.PullInProgress):
            self.start()
        self.assertEqual(self.requested(), [])
        self.assertEqual(len(self.pulls()), 1)
        self.assertEqual(self.raw.execute("SELECT count(*) AS n FROM pull_zips").fetchone()["n"],
                         self.raw.execute("SELECT count(*) AS n FROM pull_zips WHERE pull_id IN "
                                          "(SELECT pull_id FROM api_pulls WHERE storm_date <> %s)", (DAY,)).fetchone()["n"])
        self.assertEqual(FakeThread.started, [])

    def test_a_stale_running_pull_does_not_block_a_new_one(self):
        self.raw.execute(
            "INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count, estimated_api_calls, api_status, started_at) "
            "VALUES (%s, %s, 'HAIL', 1, 1, 'running', now() - interval '2 hours')", (self.emp, DAY))
        self.conn.commit()
        self.start()
        statuses = [p["api_status"] for p in self.pulls()]
        self.assertEqual(sorted(statuses), ["cancelled", "complete"])
        self.assertEqual(sorted(self.requested()), sorted(ZIPS))

    # -- failures stop and mark the pull -------------------------------------------------------------

    def test_an_auth_error_aborts_the_pull_after_the_first_zip_and_marks_it_failed(self):
        self.search.side_effect = RentCastAuthError("rejected", attempts=1, status=401)
        self.start()
        (pull,) = self.pulls()
        self.assertEqual(pull["api_status"], "failed")
        self.assertEqual(len(self.requested()), 1)
        self.assertEqual(self.match_storm.call_count, 0)

    def test_a_server_error_on_one_zip_is_logged_and_the_rest_are_still_requested(self):
        self.search.side_effect = [RentCastServerError("down", attempts=3, status=503), ([], 1), ([], 1)]
        self.start()
        (pull,) = self.pulls()
        self.assertEqual(pull["api_status"], "complete")
        self.assertEqual(len(self.requested()), 3)
        statuses = {r["zip_code"]: r["http_status"] for r in self.raw.execute(
            "SELECT zip_code, http_status FROM api_call_log WHERE pull_id = %s", (pull["pull_id"],)).fetchall()}
        self.assertEqual(sorted(statuses.values()), [200, 200, 503])
        self.assertEqual(pull["actual_api_calls"], 3 + 1 + 1)

    def test_an_unexpected_error_marks_the_pull_failed_and_stops(self):
        self.search.side_effect = RuntimeError("boom")
        self.start()
        (pull,) = self.pulls()
        self.assertEqual(pull["api_status"], "failed")
        self.assertEqual(len(self.requested()), 1)
        self.assertEqual(self.match_storm.call_count, 0)

    def test_the_sweep_leaves_a_fresh_running_pull_alone(self):
        self.raw.execute(
            "INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count, estimated_api_calls, api_status) "
            "VALUES (%s, %s, 'HAIL', 1, 1, 'running')", (self.emp, DAY))
        self.jobs.sweep_stale_pulls()
        self.assertEqual([p["api_status"] for p in self.pulls()], ["running"])


if __name__ == "__main__":
    unittest.main()
