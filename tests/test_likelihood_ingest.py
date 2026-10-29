"""Tests for hailsys/likelihood/ingest.py -- which days the job builds, and the run around them (Phase 5c, step 1b).

The database tests need a role that can insert radar days and likelihood runs (hail_admin):

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_likelihood_ingest

The run commits after each day, so the tests put a stand-in round the connection (commit is a savepoint, rollback goes
back to the last one) and nothing is left behind.  The builder is a stub except in the last test.

What they pin:  backfill picks loaded radar days with no build for the newest set, newest first, inside the dates and
the limit;  a day built for an older set is still pending for the newest;  a run builds each loaded day and skips the
rest (missing, suspect, never fetched) without failing;  a day that raises is named and the others still run;  the run
ends 'complete' or 'failed' with its counts and the failures in error_detail;  the mode is recorded;  and the real
builder, run through the job, stores a build.
"""

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.likelihood import build, ingest
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DENVER = ZoneInfo("America/Denver")
D1, D2, D3, D4, D5 = (date(1999, 6, n) for n in (1, 2, 3, 4, 5))


class FakeConn:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("SAVEPOINT stub")

    def execute(self, *args, **kw):
        return self.conn.execute(*args, **kw)

    def commit(self):
        self.conn.execute("RELEASE SAVEPOINT stub")
        self.conn.execute("SAVEPOINT stub")

    def rollback(self):
        self.conn.execute("ROLLBACK TO SAVEPOINT stub")


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class IngestTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.raw.close)
        self.addCleanup(self.raw.rollback)
        if not self.raw.execute("SELECT to_regclass('likelihood_runs') IS NOT NULL AS ok").fetchone()["ok"]:
            self.fail("sql/055_likelihood_runs.sql is not applied")
        # radar_runs, not radar_parameter_sets: sql/056 lets hail_app add parameter sets (item 241).
        if not self.raw.execute("SELECT has_table_privilege('radar_runs', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert radar_runs rows (hail_admin)")
        self.conn = FakeConn(self.raw)
        self.run_id = self.raw.execute(
            "INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.radar_set = self.raw.execute(
            "INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
            "VALUES ('t', 1.0, 3.0, 5.0) RETURNING set_id").fetchone()["set_id"]
        self.set_id = self.add_set()
        self.built = []

    def add_set(self):
        new = self.raw.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "VALUES ('t', %s, 1.0, 1, 3, 5) RETURNING set_id", (self.radar_set,)).fetchone()["set_id"]
        self.raw.execute(
            "INSERT INTO likelihood_level_rules (set_id, layer, band, level) "
            "SELECT %s, layer, band, level FROM likelihood_level_rules WHERE set_id = "
            "(SELECT min(set_id) FROM likelihood_parameter_sets)", (new,))
        return new

    def add_day(self, day, status="loaded"):
        self.raw.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box, max_mm) VALUES (%s, %s, %s, now(), 'k', "
            "41.5, 36.5, -109.7, -101.4, 12.7, %s, %s)",
            (day, self.run_id, status, None if status == "missing" else 1000, 1007.7 if status == "suspect" else None))

    def mark_built(self, day, set_id):
        self.raw.execute(
            "INSERT INTO likelihood_builds (day, set_id, window_start, window_end, iem_reports, cocorahs_reports, "
            "duplicates_dropped, patches, patches_anchored) VALUES (%s, %s, now(), now() + interval '1 day', 0, 0, 0, 0, 0)",
            (day, set_id))

    def stub(self, fail=()):
        def builder(conn, day, tz, set_id=None):
            if day in fail:
                raise RuntimeError(f"boom {day}")
            self.built.append(day)
        return builder

    def runs_row(self, run_id):
        return self.raw.execute("SELECT * FROM likelihood_runs WHERE run_id = %s", (run_id,)).fetchone()

    # -- pending days -----------------------------------------------------------------------

    def test_pending_days_are_loaded_days_with_no_build_newest_first(self):
        for day in (D1, D2, D3):
            self.add_day(day)
        self.add_day(D4, "missing")
        self.add_day(D5, "suspect")
        self.mark_built(D2, self.set_id)
        got = ingest.pending_days(self.conn, D1, D5)
        self.assertEqual(got, [D3, D1])

    def test_pending_days_respect_the_dates_and_the_limit(self):
        for day in (D1, D2, D3, D4):
            self.add_day(day)
        self.assertEqual(ingest.pending_days(self.conn, D2, D3), [D3, D2])
        self.assertEqual(ingest.pending_days(self.conn, D1, D4, limit=2), [D4, D3])

    def test_a_day_built_for_an_older_set_is_pending_for_the_newest(self):
        self.add_day(D1)
        self.add_day(D2)
        self.mark_built(D1, self.set_id)
        newest = self.add_set()
        self.assertEqual(ingest.pending_days(self.conn, D1, D2), [D2, D1])
        self.mark_built(D1, newest)
        self.assertEqual(ingest.pending_days(self.conn, D1, D2), [D2])

    # -- the run ----------------------------------------------------------------------------

    def test_a_run_builds_loaded_days_and_skips_the_rest_without_failing(self):
        self.add_day(D1)
        self.add_day(D2, "missing")
        self.add_day(D3, "suspect")
        out = ingest.run(self.conn, "nightly", [D3, D2, D1, D4], DENVER, builder=self.stub())
        self.assertEqual((out["built"], out["skipped"], out["failed"]), (1, 3, []))
        self.assertEqual(self.built, [D1])
        row = self.runs_row(out["run_id"])
        self.assertEqual((row["run_status"], row["days_built"], row["days_skipped"], row["days_failed"]),
                         ("complete", 1, 3, 0))
        self.assertIsNotNone(row["finished_at"])
        self.assertIsNone(row["error_detail"])

    def test_the_mode_is_recorded(self):
        for mode in ("nightly", "rebuild", "backfill", "manual"):
            with self.subTest(mode=mode):
                out = ingest.run(self.conn, mode, [], DENVER, builder=self.stub())
                self.assertEqual(self.runs_row(out["run_id"])["run_mode"], mode)

    def test_a_day_that_fails_is_named_and_the_others_still_run(self):
        for day in (D1, D2, D3):
            self.add_day(day)
        out = ingest.run(self.conn, "backfill", [D3, D2, D1], DENVER, builder=self.stub(fail={D2}))
        self.assertEqual(self.built, [D3, D1])
        self.assertEqual([day for day, _ in out["failed"]], [D2])
        self.assertIn("boom", out["failed"][0][1])
        row = self.runs_row(out["run_id"])
        self.assertEqual((row["run_status"], row["days_built"], row["days_failed"]), ("failed", 2, 1))
        self.assertIn("1 of 3 days failed", row["error_detail"])
        self.assertIn(str(D2), row["error_detail"])

    def test_the_failure_list_in_the_run_is_capped(self):
        days = [date(1999, 7, n) for n in range(1, 29)]
        for day in days:
            self.add_day(day)
        out = ingest.run(self.conn, "backfill", days, DENVER, builder=self.stub(fail=set(days)))
        self.assertEqual(len(out["failed"]), 28)
        detail = self.runs_row(out["run_id"])["error_detail"]
        self.assertTrue(detail.startswith("28 of 28 days failed"))
        self.assertEqual(detail.count("boom"), ingest.FAILURES_SHOWN)

    def test_an_empty_run_is_complete(self):
        out = ingest.run(self.conn, "nightly", [], DENVER, builder=self.stub())
        self.assertEqual((out["built"], out["skipped"], out["failed"]), (0, 0, []))
        self.assertEqual(self.runs_row(out["run_id"])["run_status"], "complete")

    def test_the_set_is_passed_to_the_builder(self):
        self.add_day(D1)
        seen = []
        ingest.run(self.conn, "manual", [D1], DENVER, set_id=self.set_id,
                   builder=lambda conn, day, tz, set_id=None: seen.append(set_id))
        self.assertEqual(seen, [self.set_id])

    def test_the_real_builder_stores_a_build_through_the_job(self):
        self.add_day(D1)
        out = ingest.run(self.conn, "manual", [D1], DENVER, set_id=self.set_id)
        self.assertEqual((out["built"], out["failed"]), (1, []))
        n = self.raw.execute("SELECT count(*) AS n FROM likelihood_builds WHERE day = %s AND set_id = %s",
                             (D1, self.set_id)).fetchone()["n"]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
