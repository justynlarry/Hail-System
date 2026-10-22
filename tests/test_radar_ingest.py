"""Tests for hailsys/radar/ingest.py:  the radar job over a set of local days.

The day arithmetic needs nothing.  The rest runs whole days through the real database with the network
stubbed (a synthetic MRMS file, a synthetic warning export), and needs a role that can write the radar tables
and update settings, see test_radar_zones.py:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_radar_ingest

The job commits after each day, so these tests COMMIT.  They use 1999 dates that no real row uses and delete
what they made, before and after, on a separate connection.

DO NOT RUN THEM WHILE A RADAR JOB IS RUNNING (backfill, nightly).  The cleanup deletes every radar_runs row
newer than the one that was newest when the test started, which includes the live job's own run row (the
delete fails on the days that reference it, and the test errors).

What they pin: a finished day is one that ended LAG ago; days run in the order given, one transaction each; a
day that fails is rolled back whole and the run goes on; the run is recorded with its counts and the days that
failed; warnings are fetched a quarter at a time and not again; a held day is not fetched but is rebuilt; a
rebuild fetches nothing; a day NOAA does not have is recorded missing and not built; and it all works as
hail_ingest.
"""

import logging
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.radar import fetch, ingest, load, warnings
    from tests import radar_fixtures as fx
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

def setUpModule():
    logging.disable(logging.CRITICAL)           # the failure tests log errors on purpose


def tearDownModule():
    logging.disable(logging.NOTSET)


DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc
NOW = datetime(2000, 1, 1, tzinfo=UTC)               # long after every 1999 day
D1, D2, D3 = date(1999, 6, 8), date(1999, 6, 7), date(1999, 6, 6)     # newest first, as a job runs them
CELLS = {(12, 105): 300, (15, 110): 137, (18, 120): 200}


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class DayArithmeticTest(unittest.TestCase):
    def test_a_day_is_finished_two_hours_after_it_ends(self):
        # Denver midnight ending 2026-06-08 is 06:00 UTC on the 9th
        self.assertEqual(ingest.latest_finished_day(datetime(2026, 6, 9, 8, 0, tzinfo=UTC), DENVER), date(2026, 6, 8))
        self.assertEqual(ingest.latest_finished_day(datetime(2026, 6, 9, 7, 59, tzinfo=UTC), DENVER), date(2026, 6, 7))

    def test_in_winter_the_edge_is_an_hour_later_in_utc(self):
        self.assertEqual(ingest.latest_finished_day(datetime(2026, 1, 16, 9, 0, tzinfo=UTC), DENVER), date(2026, 1, 15))
        self.assertEqual(ingest.latest_finished_day(datetime(2026, 1, 16, 8, 59, tzinfo=UTC), DENVER), date(2026, 1, 14))

    def test_the_zone_is_an_argument(self):
        self.assertEqual(ingest.latest_finished_day(datetime(2026, 6, 9, 2, 0, tzinfo=UTC), UTC), date(2026, 6, 8))

    def test_days_back_are_the_last_finished_days_newest_first(self):
        self.assertEqual(ingest.days_back(3, datetime(2026, 6, 9, 10, 30, tzinfo=UTC), DENVER),
                         [date(2026, 6, 8), date(2026, 6, 7), date(2026, 6, 6)])

    def test_days_back_of_none_is_empty(self):
        self.assertEqual(ingest.days_back(0, NOW, DENVER), [])

    def test_days_between_is_inclusive_and_newest_first(self):
        self.assertEqual(ingest.days_between(date(2026, 6, 6), date(2026, 6, 8)),
                         [date(2026, 6, 8), date(2026, 6, 7), date(2026, 6, 6)])
        self.assertEqual(ingest.days_between(date(2026, 6, 8), date(2026, 6, 8)), [date(2026, 6, 8)])

    def test_days_between_stops_at_the_start_of_the_archive(self):
        days = ingest.days_between(date(2000, 1, 1), date(2020, 10, 16))
        self.assertEqual(days, [date(2020, 10, 16), date(2020, 10, 15), date(2020, 10, 14)])
        self.assertEqual(ingest.FIRST_RADAR_DAY, date(2020, 10, 14))

    def test_days_between_a_floor_can_be_given(self):
        self.assertEqual(ingest.days_between(date(2000, 1, 1), date(2000, 1, 3), floor=date(1999, 12, 31)),
                         [date(2000, 1, 3), date(2000, 1, 2), date(2000, 1, 1)])

    def test_days_between_with_nothing_in_range_is_empty(self):
        self.assertEqual(ingest.days_between(date(2026, 6, 9), date(2026, 6, 8)), [])
        self.assertEqual(ingest.days_between(date(2019, 1, 1), date(2019, 1, 5)), [])


@lru_cache(maxsize=None)
def _grib(when):
    return fx.grib(CELLS, 16, when=when)


def _fake_fetch_day(calls, fail=None, gone=()):
    """A fetch_day writing a synthetic file stamped for the day asked.  fail: {day: exception}."""
    def fake(day, tz, directory, **kw):
        calls.append(day)
        if day in (fail or {}):
            raise fail[day]
        if day in gone:
            raise fetch.NotAvailable("x")
        stamp = fetch.day_stamp(day, tz)
        key = fetch.source_key(stamp)
        path = os.path.join(directory, os.path.basename(key))
        fx.write(path, _grib((stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, 0)), gz=True)
        return path, stamp, key
    return fake


def _fake_download(urls, data=None, fail=None):
    def download(url, dest, **kw):
        urls.append(url)
        if fail:
            raise fail
        with open(dest, "wb") as f:
            f.write(data if data is not None else fx.shapefile_zip([]))
        return 1
    return download


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class RunTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.psycopg, self.dict_row = psycopg, dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        if not self.conn.execute("SELECT has_table_privilege('warning_loads', 'DELETE') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can write the warning tables (hail_admin)")
        self.first_run = self.conn.execute("SELECT coalesce(max(run_id), 0) AS m FROM radar_runs").fetchone()["m"]
        self.conn.rollback()
        self.scrub()
        self.addCleanup(self.scrub)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.calls, self.urls = [], []

    def scrub(self):
        """Delete what the tests committed, on a connection of its own."""
        with self.psycopg.connect(autocommit=True) as c:
            for table in ("radar_zones", "radar_zone_builds", "radar_cells", "radar_days"):
                c.execute(f"DELETE FROM {table} WHERE day < '2000-01-01'")
            c.execute("DELETE FROM warning_polygons WHERE vtec_year = 1999")
            c.execute("DELETE FROM warning_loads WHERE window_start < '2000-01-01'")
            c.execute("DELETE FROM radar_runs WHERE run_id > %s", (self.first_run,))

    # -- helpers --------------------------------------------------------------------------

    def patches(self, fail=None, gone=(), data=None, download_fail=None):
        p1 = mock.patch.object(load.fetch, "fetch_day", _fake_fetch_day(self.calls, fail, gone))
        p2 = mock.patch.object(warnings.fetch, "download", _fake_download(self.urls, data, download_fail))
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def run_days(self, days, **kw):
        run_id = load.start_run(self.conn, "manual")
        self.conn.commit()
        return ingest.run_days(self.conn, days, DENVER, self.dir.name, run_id=run_id, box=fx.BOX, now=NOW, **kw)

    def table(self, sql, *params):
        with self.psycopg.connect(row_factory=self.dict_row) as c:
            return c.execute(sql, params).fetchall()

    def count(self, table):
        return self.table(f"SELECT count(*) AS n FROM {table} WHERE day < '2000-01-01'")[0]["n"]

    # -- days -----------------------------------------------------------------------------

    def test_each_day_is_loaded_and_built_in_the_order_given(self):
        self.patches()
        got = self.run_days([D1, D2, D3])
        self.assertEqual((got["loaded"], got["built"], got["failed"]), (3, 3, []))
        self.assertEqual(self.calls, [D1, D2, D3])
        self.assertEqual(self.count("radar_days"), 3)
        self.assertEqual(self.count("radar_zone_builds"), 3)
        self.assertEqual(self.count("radar_cells"), 3 * 3)

    def test_without_a_box_the_coverage_zips_make_one(self):
        self.patches()
        run_id = load.start_run(self.conn, "manual")
        self.conn.commit()
        ingest.run_days(self.conn, [D1], DENVER, self.dir.name, run_id=run_id, now=NOW)
        row = self.table("SELECT box_lat_max, box_lat_min, box_lon_min, box_lon_max FROM radar_days WHERE day = %s", D1)[0]
        want = load.crop_box(self.conn)
        self.assertEqual((row["box_lat_max"], row["box_lat_min"], row["box_lon_min"], row["box_lon_max"]), want)

    def test_a_held_day_is_not_fetched_again_but_is_rebuilt(self):
        self.patches()
        self.run_days([D1, D2])
        self.calls.clear()
        got = self.run_days([D1, D2])
        self.assertEqual((got["loaded"], got["held"], got["built"]), (0, 2, 2))
        self.assertEqual(self.calls, [])

    def test_a_rebuild_fetches_nothing_and_skips_days_never_loaded(self):
        self.patches()
        self.run_days([D2])
        self.calls.clear()
        got = self.run_days([D1, D2, D3], fetch_new=False)
        self.assertEqual((got["held"], got["absent"], got["loaded"], got["built"]), (1, 2, 0, 1))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.count("radar_days"), 1)

    def test_a_day_noaa_does_not_have_is_missing_and_not_built(self):
        self.patches(gone=(D2,))
        got = self.run_days([D1, D2])
        self.assertEqual((got["loaded"], got["missing"], got["built"]), (1, 1, 1))
        self.assertEqual(self.table("SELECT status FROM radar_days WHERE day = %s", D2)[0]["status"], "missing")
        self.assertEqual(self.count("radar_zone_builds"), 1)

    def test_a_missing_day_already_held_is_not_asked_for_again_or_counted(self):
        self.patches(gone=(D2,))
        self.run_days([D2])
        self.calls.clear()
        got = self.run_days([D2])
        self.assertEqual((got["missing"], got["held"], got["built"]), (0, 1, 0))
        self.assertEqual(self.calls, [])

    def test_a_failed_day_is_rolled_back_whole_and_the_run_goes_on(self):
        self.patches(fail={D2: fetch.FetchError("boom")})
        got = self.run_days([D1, D2, D3])
        self.assertEqual((got["loaded"], got["built"]), (2, 2))
        self.assertEqual([(d, m) for d, m in got["failed"]], [(D2, "FetchError: boom")])
        self.assertEqual(self.count("radar_days"), 2)
        self.assertEqual(self.calls, [D1, D2, D3])

    def test_a_build_that_fails_takes_the_days_load_with_it(self):
        self.patches()
        real = ingest.zones.build_day

        def build(conn, day, *a, **kw):
            if day == D2:
                raise RuntimeError("no build")
            return real(conn, day, *a, **kw)
        with mock.patch.object(ingest.zones, "build_day", build):
            got = self.run_days([D1, D2, D3])
        self.assertEqual([d for d, _ in got["failed"]], [D2])
        self.assertEqual(got["failed"][0][1], "RuntimeError: no build")
        self.assertEqual(self.count("radar_days"), 2)
        self.assertEqual(self.count("radar_cells"), 2 * 3)
        self.assertEqual(self.table("SELECT count(*) AS n FROM radar_days WHERE day = %s", D2)[0]["n"], 0)

    def test_pause_is_taken_after_a_day_that_used_the_network_only(self):
        self.patches()
        self.run_days([D2])
        slept = []
        self.run_days([D1, D2], pause=0.5, sleep=slept.append)
        self.assertEqual(slept, [0.5])          # D1 was fetched, D2 was held

    def test_the_network_options_reach_the_downloads(self):
        self.patches()
        seen = []
        original = load.fetch.fetch_day

        def spy(day, tz, directory, **kw):
            seen.append(kw)
            return original(day, tz, directory, **kw)
        with mock.patch.object(load.fetch, "fetch_day", spy):
            self.run_days([D1], attempts=2)
        self.assertEqual(seen, [{"attempts": 2}])

    # -- warnings -------------------------------------------------------------------------

    def preload(self, first, last, **kw):
        return ingest.preload_warnings(self.conn, first, last, DENVER, self.dir.name, now=NOW, states="CO", **kw)

    def test_warnings_are_fetched_a_quarter_at_a_time(self):
        self.patches()
        chunks = self.preload(date(1999, 1, 1), date(1999, 7, 19))        # 200 days
        self.assertEqual(chunks, 3)
        self.assertEqual(len(self.urls), 3)
        want = [(date(1999, 1, 1), date(1999, 4, 1)), (date(1999, 4, 2), date(1999, 7, 1)),
                (date(1999, 7, 2), date(1999, 7, 19))]
        for url, (first, last) in zip(self.urls, want):
            start = fetch.day_window(first, DENVER)[0] - warnings.PAD
            end = fetch.day_window(last, DENVER)[1] + warnings.PAD
            self.assertEqual(url, warnings.request_url("CO", start, end))

    def test_the_last_chunk_never_reaches_past_now(self):
        self.patches()
        end = fetch.day_window(D1, DENVER)[1]
        now = end + timedelta(hours=5)                    # padding would run a day past this
        ingest.preload_warnings(self.conn, D1, D1, DENVER, self.dir.name, now=now, states="CO")
        self.assertEqual(self.urls, [warnings.request_url("CO", fetch.day_window(D1, DENVER)[0] - warnings.PAD, now)])

    def test_each_chunk_is_committed_as_it_is_fetched(self):
        self.patches()
        self.preload(date(1999, 1, 1), date(1999, 4, 1))
        self.assertEqual(len(self.table("SELECT 1 FROM warning_loads WHERE window_start < '2000-01-01'")), 1)

    def test_warnings_already_held_are_not_fetched_again(self):
        self.patches()
        self.preload(date(1999, 1, 1), date(1999, 7, 19))
        self.urls.clear()
        self.assertEqual(self.preload(date(1999, 1, 1), date(1999, 7, 19)), 0)
        self.assertEqual(self.urls, [])

    def test_one_chunk_held_the_others_are_fetched(self):
        self.patches()
        self.preload(date(1999, 1, 1), date(1999, 4, 1))
        self.urls.clear()
        self.assertEqual(self.preload(date(1999, 1, 1), date(1999, 7, 19)), 2)

    def test_every_day_of_the_range_is_then_covered(self):
        self.patches()
        self.preload(date(1999, 1, 1), date(1999, 7, 19))
        day = date(1999, 1, 1)
        while day <= date(1999, 7, 19):
            start, end = fetch.day_window(day, DENVER)
            self.assertTrue(warnings.covered(self.conn, start, end, "CO"), day)
            day += timedelta(days=1)

    def test_a_day_needs_no_fetch_of_its_own_after_the_preload(self):
        self.patches()
        self.preload(D3, D1)
        self.urls.clear()
        self.run_days([D1, D2, D3])
        self.assertEqual(self.urls, [])

    # -- the recorded run -----------------------------------------------------------------

    def run_job(self, mode="nightly", days=(D1, D2, D3), **kw):
        out = ingest.run(self.conn, mode, list(days), DENVER, self.dir.name, box=fx.BOX, now=NOW, **kw)
        run = self.table("SELECT * FROM radar_runs WHERE run_id = %s", out["run_id"])[0]
        return out, run

    def test_a_run_is_recorded_with_its_counts(self):
        self.patches()
        out, run = self.run_job()
        self.assertEqual((run["run_mode"], run["run_status"], run["days_loaded"], run["days_missing"],
                          run["days_built"], run["days_failed"], run["error_detail"]),
                         ("nightly", "complete", 3, 0, 3, 0, None))
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(out["built"], 3)

    def test_a_day_with_no_preload_fetches_its_own_warnings_and_gets_an_outer_layer_count(self):
        self.patches()
        self.run_days([D1])
        self.assertEqual(len(self.urls), 1)
        row = self.table("SELECT warnings_used FROM radar_zone_builds WHERE day = %s", D1)[0]
        self.assertEqual(row["warnings_used"], 0)

    def test_preload_can_be_turned_off(self):
        self.patches()
        self.run_job(preload=False)
        start = fetch.day_window(D1, DENVER)[0] - warnings.PAD
        end = fetch.day_window(D1, DENVER)[1] + warnings.PAD
        self.assertEqual(self.urls[0], warnings.request_url("CO", start, end))

    def test_the_warnings_are_fetched_before_the_days(self):
        self.patches()
        out, run = self.run_job()
        self.assertEqual(len(self.urls), 1)
        self.assertEqual(self.table("SELECT run_id FROM warning_loads WHERE window_start < '2000-01-01'")[0]["run_id"],
                         out["run_id"])

    def test_failed_days_make_the_run_failed_and_are_named(self):
        self.patches(fail={D2: fetch.FetchError("boom")})
        out, run = self.run_job()
        self.assertEqual((run["run_status"], run["days_loaded"], run["days_built"], run["days_failed"]),
                         ("failed", 2, 2, 1))
        self.assertEqual(run["error_detail"], "1 of 3 days failed: 1999-06-07 FetchError: boom")
        self.assertEqual(out["failed"], [(D2, "FetchError: boom")])

    def test_only_the_first_failed_days_are_named(self):
        days = [date(1999, 6, 30) - timedelta(days=i) for i in range(25)]
        self.patches(fail={d: fetch.FetchError("x") for d in days})
        _, run = self.run_job(days=days)
        self.assertEqual(run["days_failed"], 25)
        self.assertEqual(run["error_detail"].count("FetchError"), ingest.FAILURES_SHOWN)
        self.assertTrue(run["error_detail"].startswith("25 of 25 days failed: 1999-06-30 "))

    def test_the_pause_goes_to_the_day_loop_not_to_the_downloads(self):
        self.patches()
        slept = []
        out, run = self.run_job(pause=0.25, sleep=slept.append)
        self.assertEqual(run["run_status"], "complete")
        self.assertEqual(slept, [0.25, 0.25, 0.25])         # three days fetched, one pause after each

    def test_network_options_reach_the_warnings_and_the_days_but_pause_does_not(self):
        self.patches()
        seen = []
        original = load.fetch.fetch_day

        def spy(day, tz, directory, **kw):
            seen.append(kw)
            return original(day, tz, directory, **kw)
        downloads = []

        def download(url, dest, **kw):
            downloads.append(kw)
            with open(dest, "wb") as f:
                f.write(fx.shapefile_zip([]))
        with mock.patch.object(load.fetch, "fetch_day", spy), mock.patch.object(warnings.fetch, "download", download):
            self.run_job(days=(D1,), attempts=2, pause=0.1, sleep=lambda s: None)
        self.assertEqual(seen, [{"attempts": 2}])
        self.assertTrue(downloads and all(kw == {"attempts": 2} for kw in downloads))

    def test_a_warnings_failure_fails_the_run_and_is_raised(self):
        self.patches(download_fail=fetch.FetchError("iem down"))
        with self.assertRaises(fetch.FetchError):
            ingest.run(self.conn, "nightly", [D1], DENVER, self.dir.name, box=fx.BOX, now=NOW)
        run = self.table("SELECT * FROM radar_runs WHERE run_id > %s ORDER BY run_id DESC LIMIT 1", self.first_run)[0]
        self.assertEqual((run["run_status"], run["error_detail"]), ("failed", "FetchError: iem down"))
        self.assertEqual(self.calls, [])

    def test_a_rebuild_run_can_skip_the_download_of_days(self):
        self.patches()
        self.run_job()
        self.calls.clear()
        out, run = self.run_job("rebuild", fetch_new=False)
        self.assertEqual((run["run_mode"], run["days_loaded"], run["days_built"]), ("rebuild", 0, 3))
        self.assertEqual(self.calls, [])

    def test_a_run_with_no_days_does_nothing_and_is_complete(self):
        self.patches()
        out, run = self.run_job(days=())
        self.assertEqual((run["run_status"], run["days_built"]), ("complete", 0))
        self.assertEqual(self.urls, [])

    # -- the role -------------------------------------------------------------------------

    def test_it_all_works_as_the_ingest_role(self):
        self.patches()
        self.conn.execute("SET ROLE hail_ingest")
        self.addCleanup(lambda: self.conn.execute("RESET ROLE") if not self.conn.closed else None)
        out, run = self.run_job(days=(D1, D2))
        self.assertEqual((run["run_status"], run["days_built"], run["days_failed"]), ("complete", 2, 0))
        self.assertEqual(out["failed"], [])


if __name__ == "__main__":
    unittest.main()
