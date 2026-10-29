"""Tests for hailsys/radar/load.py:  one local day of radar into the database.

Needs a role that can insert into the radar tables (not hail_app), see test_radar_zones.py:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_radar_load

Everything runs in a transaction that is always rolled back.  The network is stubbed: fetch_day
writes a small synthetic MRMS file (tests/radar_fixtures.py) where the real one would be.

What they pin: the crop box follows the active coverage zips, sits on the grid and inside it;
a loaded day stores the cells at or above the floor with the file's own time, box and counts;
a day already held is skipped; a 404 long after the stamp is a missing day and a recent one is
an error; a file for the wrong time is refused; the download is deleted whatever happens; and
a day can be deleted with everything built from it, its likelihood builds included (item 276).
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.radar import fetch, load, mesh, zones
    from tests import radar_fixtures as fx
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc
DAY = date(1999, 6, 8)
WHEN = (1999, 6, 9, 6, 0, 0)
STAMP = datetime(1999, 6, 9, 6, 0, tzinfo=UTC)
LATER = STAMP + timedelta(days=2)
CELLS = {(12, 105): 300, (15, 110): 137, (16, 111): 136, (18, 120): 200}   # 29.0, 12.7, 12.6, 19.0 mm


def _fake_fetch(data=None, when=WHEN, seen=None):
    """A fetch_day that writes a synthetic file into the directory it is given."""
    def fake(day, tz, directory, **kw):
        if seen is not None:
            seen.append((day, tz, directory, kw))
        stamp = fetch.day_stamp(day, tz)
        key = fetch.source_key(stamp)
        path = os.path.join(directory, os.path.basename(key))
        fx.write(path, data if data is not None else fx.grib(CELLS, 16, when=when), gz=True)
        return path, stamp, key
    return fake


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class LoadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # building the file takes a moment, do it once
        cls.good = fx.grib(CELLS, 16, when=WHEN)
        # mm = (x - 10) / 10, so x = 2550 is exactly 254.0 mm (the ceiling) and 2551 is 254.1
        cls.at_ceiling = fx.grib({**CELLS, (13, 106): 2550}, 16, when=WHEN)
        cls.over = fx.grib({**CELLS, (13, 106): 2551}, 16, when=WHEN)
        cls.impossible = fx.grib({(12, 105): 300, (13, 106): 10176}, 16, when=WHEN)        # 1,016.6 mm

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        # radar_runs, not radar_parameter_sets: sql/056 lets hail_app add parameter sets (item 241).
        if not self.conn.execute(
                "SELECT has_table_privilege('radar_runs', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert radar_runs rows (hail_admin)")
        self.user = self.conn.execute("SELECT emp_id FROM users ORDER BY emp_id LIMIT 1").fetchone()["emp_id"]
        self.run_id = load.start_run(self.conn, "manual")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def load(self, fetcher=None, now=LATER, **kw):
        patcher = mock.patch.object(load.fetch, "fetch_day", fetcher or _fake_fetch(self.good))
        patcher.start()
        self.addCleanup(patcher.stop)
        return load.load_day(self.conn, DAY, DENVER, self.dir.name, box=fx.BOX, run_id=self.run_id,
                             now=now, **kw)

    def day_row(self):
        return self.conn.execute("SELECT * FROM radar_days WHERE day = %s", (DAY,)).fetchone()

    def cells(self):
        return self.conn.execute(
            "SELECT grid_row, grid_col, mm, ST_X(geom) AS lon, ST_Y(geom) AS lat FROM radar_cells "
            "WHERE day = %s ORDER BY grid_row, grid_col", (DAY,)).fetchall()

    # -- the crop box ---------------------------------------------------------------------

    def test_box_covers_the_active_coverage_extent_with_the_margin(self):
        box = load.crop_box(self.conn)
        e = self.conn.execute(
            "SELECT ST_YMax(e) AS la, ST_YMin(e) AS lb, ST_XMin(e) AS lo, ST_XMax(e) AS lp FROM "
            "(SELECT ST_Extent(z.geom) AS e FROM coverage_zips c JOIN zcta_boundaries z ON z.zcta5 = c.zcta5 "
            "WHERE c.removed_at IS NULL) x").fetchone()
        m = load.BOX_MARGIN_DEGREES
        # a parameter set may use a margin of up to 50 km (sql/048), about 0.45 degrees of latitude
        self.assertGreaterEqual(m, 0.45)
        self.assertGreaterEqual(box[0], e["la"] + m)
        self.assertLessEqual(box[0], e["la"] + m + mesh.STEP)
        self.assertLessEqual(box[1], e["lb"] - m)
        self.assertGreaterEqual(box[1], e["lb"] - m - mesh.STEP)
        self.assertLessEqual(box[2], e["lo"] - m)
        self.assertGreaterEqual(box[2], e["lo"] - m - mesh.STEP)
        self.assertGreaterEqual(box[3], e["lp"] + m)
        self.assertLessEqual(box[3], e["lp"] + m + mesh.STEP)

    def test_box_edges_are_cell_centres_of_the_grid(self):
        lat_max, lat_min, lon_min, lon_max = load.crop_box(self.conn)
        for lat in (lat_max, lat_min):
            self.assertAlmostEqual((mesh.LA1 - lat) / mesh.STEP, round((mesh.LA1 - lat) / mesh.STEP), places=4)
        for lon in (lon_min, lon_max):
            self.assertAlmostEqual((lon - mesh.LO1) / mesh.STEP, round((lon - mesh.LO1) / mesh.STEP), places=4)
        self.assertGreater(lat_max, lat_min)
        self.assertGreater(lon_max, lon_min)

    def test_the_box_follows_the_coverage_zips(self):
        before = load.crop_box(self.conn)
        self.conn.execute(
            "UPDATE coverage_zips SET removed_at = now(), removed_by = %s "
            "WHERE zcta5 <> (SELECT min(zcta5) FROM coverage_zips WHERE removed_at IS NULL) "
            "AND removed_at IS NULL", (self.user,))
        after = load.crop_box(self.conn)
        self.assertLess(after[0] - after[1], before[0] - before[1])
        self.assertLess(after[3] - after[2], before[3] - before[2])
        # one zip plus half a degree each side is a little over 1 degree
        self.assertLess(after[0] - after[1], 1.5)

    def test_no_coverage_zips_is_an_error(self):
        self.conn.execute("UPDATE coverage_zips SET removed_at = now(), removed_by = %s "
                          "WHERE removed_at IS NULL", (self.user,))
        with self.assertRaisesRegex(load.LoadError, "no active coverage zips"):
            load.crop_box(self.conn)

    # -- loading a day --------------------------------------------------------------------

    def test_a_loaded_day_stores_the_cells_at_or_above_the_floor(self):
        self.assertEqual(self.load(), "loaded")
        cells = self.cells()
        self.assertEqual([(c["grid_row"], c["grid_col"]) for c in cells], [(12, 105), (15, 110), (18, 120)])
        self.assertAlmostEqual(cells[0]["mm"], 29.0, places=3)
        self.assertAlmostEqual(cells[0]["lon"], mesh.LO1 + 105 * mesh.STEP, places=5)
        self.assertAlmostEqual(cells[0]["lat"], mesh.LA1 - 12 * mesh.STEP, places=5)

    def test_the_day_row_records_the_file_the_box_and_the_counts(self):
        self.load()
        row = self.day_row()
        self.assertEqual((row["status"], row["run_id"], row["stamp_utc"]), ("loaded", self.run_id, STAMP))
        self.assertEqual(row["source_key"], fetch.source_key(STAMP))
        self.assertEqual((row["box_lat_max"], row["box_lat_min"], row["box_lon_min"], row["box_lon_max"]),
                         (fx.BOX[0], fx.BOX[1], fx.BOX[2], fx.BOX[3]))
        self.assertAlmostEqual(row["floor_mm"], 12.7, places=3)
        self.assertEqual((row["cells_in_box"], row["cells_stored"]), (11 * 31, 3))
        self.assertAlmostEqual(row["max_mm"], 29.0, places=3)

    def test_a_day_with_no_hail_is_loaded_with_no_cells_and_no_max(self):
        quiet = _fake_fetch(fx.grib({(12, 105): 100}, 16, when=WHEN))
        self.assertEqual(self.load(quiet), "loaded")
        row = self.day_row()
        self.assertEqual((row["cells_stored"], row["max_mm"]), (0, None))
        self.assertEqual(self.cells(), [])

    def test_fetch_gets_the_day_the_zone_and_the_directory(self):
        seen = []
        self.load(_fake_fetch(self.good, seen=seen), attempts=2)
        self.assertEqual(seen, [(DAY, DENVER, self.dir.name, {"attempts": 2})])

    def test_the_download_is_deleted_after_loading(self):
        self.load()
        self.assertEqual(os.listdir(self.dir.name), [])

    def test_a_day_already_held_is_skipped_and_not_fetched(self):
        self.load()
        seen = []
        self.assertEqual(self.load(_fake_fetch(self.good, seen=seen)), "skipped")
        self.assertEqual(seen, [])

    def test_a_missing_day_is_skipped_too(self):
        def gone(day, tz, directory, **kw):
            raise fetch.NotAvailable("x")
        self.assertEqual(self.load(gone), "missing")
        self.assertEqual(self.load(_fake_fetch(self.good)), "skipped")

    # -- not available --------------------------------------------------------------------

    def test_a_404_long_after_the_stamp_records_a_missing_day(self):
        def gone(day, tz, directory, **kw):
            raise fetch.NotAvailable("x")
        self.assertEqual(self.load(gone, now=STAMP + timedelta(hours=7)), "missing")
        row = self.day_row()
        self.assertEqual((row["status"], row["stamp_utc"], row["cells_in_box"], row["cells_stored"], row["max_mm"]),
                         ("missing", STAMP, None, 0, None))
        self.assertEqual(row["source_key"], fetch.source_key(STAMP))

    def test_a_404_exactly_at_the_limit_is_a_missing_day(self):
        def gone(day, tz, directory, **kw):
            raise fetch.NotAvailable("x")
        self.assertEqual(self.load(gone, now=STAMP + load.MISSING_AFTER), "missing")

    def test_a_404_soon_after_the_stamp_is_an_error_and_records_nothing(self):
        def gone(day, tz, directory, **kw):
            raise fetch.NotAvailable("x")
        with self.assertRaisesRegex(load.LoadError, "Try again later"):
            self.load(gone, now=STAMP + timedelta(hours=5, minutes=59))
        self.assertIsNone(self.day_row())

    # -- refusals -------------------------------------------------------------------------

    def test_a_file_for_another_time_is_refused_and_nothing_is_stored(self):
        wrong = _fake_fetch(fx.grib(CELLS, 16, when=(1999, 6, 10, 6, 0, 0)))
        with self.assertRaisesRegex(load.LoadError, "says it is valid 1999-06-10 06:00Z, not 1999-06-09 06:00Z"):
            self.load(wrong)
        self.assertIsNone(self.day_row())
        self.assertEqual(os.listdir(self.dir.name), [])

    def test_an_unreadable_file_propagates_and_is_still_deleted(self):
        bad = _fake_fetch(b"this is not a GRIB file at all")
        with self.assertRaises(mesh.MeshError):
            self.load(bad)
        self.assertIsNone(self.day_row())
        self.assertEqual(os.listdir(self.dir.name), [])

    # -- the roles that run it ------------------------------------------------------------

    def test_the_ingest_role_can_make_the_box_and_load_a_day(self):
        self.conn.execute("SET LOCAL ROLE hail_ingest")
        try:
            self.assertGreater(load.crop_box(self.conn)[0], load.crop_box(self.conn)[1])
            self.assertEqual(self.load(), "loaded")
            self.assertEqual(load.delete_day(self.conn, DAY), None)
        finally:
            self.conn.execute("RESET ROLE")

    # -- the plausibility ceiling (item 247) ----------------------------------------------------

    def test_the_ceiling_is_ten_inches_and_above_the_floor(self):
        self.assertEqual((load.CEILING_IN, load.CEILING_MM), (10.0, 254.0))
        self.assertGreater(load.CEILING_MM, load.FLOOR_MM)

    def test_a_value_exactly_at_the_ceiling_is_hail(self):
        self.assertEqual(self.load(_fake_fetch(self.at_ceiling)), "loaded")
        self.assertIn((13, 106), [(c["grid_row"], c["grid_col"]) for c in self.cells()])

    def test_a_value_just_over_the_ceiling_makes_the_day_suspect(self):
        self.assertEqual(self.load(_fake_fetch(self.over)), "suspect")

    def test_a_suspect_day_keeps_its_evidence_and_stores_no_cells(self):
        self.assertEqual(self.load(_fake_fetch(self.impossible)), "suspect")
        row = self.day_row()
        self.assertEqual((row["status"], row["cells_stored"], row["cells_in_box"], row["stamp_utc"]),
                         ("suspect", 0, 11 * 31, STAMP))
        self.assertAlmostEqual(row["max_mm"], 1016.6, places=1)
        self.assertEqual(row["source_key"], fetch.source_key(STAMP))
        self.assertEqual(self.cells(), [])

    def test_the_good_cells_of_a_suspect_day_are_not_stored_either(self):
        self.load(_fake_fetch(self.over))
        self.assertEqual(self.cells(), [])

    def test_the_download_of_a_suspect_day_is_deleted(self):
        self.load(_fake_fetch(self.over))
        self.assertEqual(os.listdir(self.dir.name), [])

    def test_a_suspect_day_is_held_and_not_fetched_again(self):
        self.load(_fake_fetch(self.over))
        seen = []
        self.assertEqual(self.load(_fake_fetch(self.good, seen=seen)), "skipped")
        self.assertEqual(seen, [])

    def test_a_suspect_day_can_be_fetched_again_once_deleted(self):
        self.load(_fake_fetch(self.over))
        load.delete_day(self.conn, DAY)
        self.assertEqual(self.load(_fake_fetch(self.good)), "loaded")
        self.assertEqual(self.day_row()["status"], "loaded")

    def test_the_ingest_role_can_record_a_suspect_day(self):
        self.conn.execute("SET LOCAL ROLE hail_ingest")
        try:
            self.assertEqual(self.load(_fake_fetch(self.over)), "suspect")
        finally:
            self.conn.execute("RESET ROLE")

    def test_a_run_records_its_suspect_days(self):
        load.finish_run(self.conn, self.run_id, loaded=1, missing=2, suspect=3)
        run = self.conn.execute("SELECT days_loaded, days_missing, days_suspect FROM radar_runs WHERE run_id = %s",
                                (self.run_id,)).fetchone()
        self.assertEqual((run["days_loaded"], run["days_missing"], run["days_suspect"]), (1, 2, 3))

    # -- runs and deleting ----------------------------------------------------------------

    def test_a_run_is_started_and_finished(self):
        run = self.conn.execute("SELECT * FROM radar_runs WHERE run_id = %s", (self.run_id,)).fetchone()
        self.assertEqual((run["run_mode"], run["run_status"], run["finished_at"]), ("manual", "running", None))
        load.finish_run(self.conn, self.run_id, loaded=3, missing=1)
        run = self.conn.execute("SELECT * FROM radar_runs WHERE run_id = %s", (self.run_id,)).fetchone()
        self.assertEqual((run["run_status"], run["days_loaded"], run["days_missing"], run["error_detail"]),
                         ("complete", 3, 1, None))
        self.assertIsNotNone(run["finished_at"])

    def test_a_failed_run_keeps_the_error(self):
        load.finish_run(self.conn, self.run_id, loaded=0, missing=0, error="FetchError: boom")
        run = self.conn.execute("SELECT * FROM radar_runs WHERE run_id = %s", (self.run_id,)).fetchone()
        self.assertEqual((run["run_status"], run["error_detail"]), ("failed", "FetchError: boom"))

    def test_delete_day_removes_the_cells_and_everything_built_from_them(self):
        self.load()
        zones.build_day(self.conn, DAY, DENVER)
        count = lambda t: self.conn.execute(f"SELECT count(*) AS n FROM {t} WHERE day = %s", (DAY,)).fetchone()["n"]
        self.assertGreater(count("radar_zones"), 0)
        load.delete_day(self.conn, DAY)
        for table in ("radar_zones", "radar_zone_builds", "radar_cells", "radar_days"):
            self.assertEqual(count(table), 0, table)

    def test_a_deleted_day_can_be_loaded_again(self):
        self.load()
        load.delete_day(self.conn, DAY)
        self.assertEqual(self.load(), "loaded")

    def test_delete_day_leaves_other_days_alone(self):
        self.load()
        other = date(1999, 6, 9)
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box) VALUES (%s, %s, 'loaded', now(), 'k', "
            "2, 1, 1, 2, 12.7, 5)", (other, self.run_id))
        load.delete_day(self.conn, DAY)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM radar_days WHERE day = %s",
                                           (other,)).fetchone()["n"], 1)

    def test_delete_day_removes_the_likelihood_builds_too(self):
        # likelihood_builds.day references radar_days (sql/054): before item 276 this delete was refused.
        self.load()
        other = date(1999, 6, 9)
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box) VALUES (%s, %s, 'loaded', now(), 'k', "
            "2, 1, 1, 2, 12.7, 5)", (other, self.run_id))
        for day in (DAY, other):
            self.conn.execute(
                "INSERT INTO likelihood_builds (day, set_id, window_start, window_end, iem_reports, "
                "cocorahs_reports, duplicates_dropped, patches, patches_anchored) "
                "SELECT %s, max(set_id), now() - interval '1 day', now(), 0, 0, 0, 0, 0 "
                "FROM likelihood_parameter_sets", (day,))
            self.conn.execute(
                "INSERT INTO likelihood_zones (day, set_id, level, geom, area_km2) "
                "SELECT %s, max(set_id), 'agree', ST_Multi(ST_MakeEnvelope(1, 1, 2, 2, 4326)), 1 "
                "FROM likelihood_parameter_sets", (day,))
        load.delete_day(self.conn, DAY)
        count = lambda t, d: self.conn.execute(f"SELECT count(*) AS n FROM {t} WHERE day = %s", (d,)).fetchone()["n"]
        for table in ("likelihood_zones", "likelihood_builds", "radar_days"):
            self.assertEqual(count(table, DAY), 0, table)
            self.assertEqual(count(table, other), 1, table)


if __name__ == "__main__":
    unittest.main()
