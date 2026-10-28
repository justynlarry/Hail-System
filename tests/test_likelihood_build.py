"""Tests for hailsys/likelihood/build.py:  the hail-likelihood zones for one day (Phase 5c, step 1b).

The database tests need a role that can insert into the radar, IEM, CoCoRaHS and likelihood tables, which
hail_app cannot, so run them as the admin role (under hail_app they skip):

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_likelihood_build

Everything is built in a transaction that is always rolled back, on 1999 dates no real row uses.  The fixtures are
the radar zones' own (a line of radar cells, a report at its west end) so the two builds can be compared.

What they pin:  with IEM hail reports only, the radar layers equal the radar zones' (the golden test);  a level is
found by the layer and the distance to the nearest report, point by point (the design grid);  a report with no radar
behind it still makes a level, about 5 miles round;  the rules and the band edges come from the set, not the code;
a CoCoRaHS filing anchors a patch at or above the set's floor, by its largest stone or else its average, in the
day only;  an IEM report whose source says CoCoRaHS is dropped and counted;  only hail reports count (a wind report
anchors the radar zones and not these);  the warning layer becomes Context only when warnings were fetched;  levels
never overlap;  a rebuild replaces;  and a day with no radar file, a set with missing rules or no set is an error.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from math import pi
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.likelihood import build
    from hailsys.radar import mesh, zones
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc
SUMMER = date(1999, 6, 8)
START = datetime(1999, 6, 8, 6, tzinfo=UTC)       # SUMMER in Denver, in UTC
END = datetime(1999, 6, 9, 6, tzinfo=UTC)
ROW, COL = 1525, 2500                              # Denver on the NOAA grid, a cell about 0.86 by 1.11 km

LAYERS = ("core", "margin", "radar_indicated", "outer", "none")
BANDS = ("le_1", "1_3", "3_5", "over_5")
GRID = {                                           # the design grid of 2026-10-23, as seeded in sql/054
    "core": ("agree", "agree", "agree", "one_source"),
    "margin": ("agree", "agree", "one_source", "one_source"),
    "radar_indicated": ("one_source",) * 4,
    "outer": ("one_source", "one_source", "context", "context"),
    "none": ("one_source", "one_source", "one_source", "none"),
}


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class BuildTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT to_regclass('likelihood_builds') IS NOT NULL AS ok").fetchone()["ok"]:
            self.fail("sql/054_likelihood.sql is not applied")
        if not self.conn.execute("SELECT has_table_privilege('radar_parameter_sets', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert parameter sets (hail_admin)")
        self.run_id = self.conn.execute(
            "INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.radar_set = self.conn.execute(
            "INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
            "VALUES ('test-1.00', 1.00, 3.0, 5.0) RETURNING set_id").fetchone()["set_id"]
        self.lk_set = self.add_set()
        self.serial = 0
        self.cocorahs_load = None

    # -- building blocks ------------------------------------------------------------------

    def add_set(self, floor=1.00, edges=(1.0, 3.0, 5.0), changes=None, drop=0):
        """A likelihood set on the test radar set with the seeded grid, 'changes' {(layer, band): level} applied."""
        new = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, "
            "band_3_mi) VALUES ('test', %s, %s, %s, %s, %s) RETURNING set_id",
            (self.radar_set, floor, *edges)).fetchone()["set_id"]
        rules = [(layer, band, (changes or {}).get((layer, band), GRID[layer][i]))
                 for layer in LAYERS for i, band in enumerate(BANDS)]
        for layer, band, level in rules[:len(rules) - drop]:
            self.conn.execute("INSERT INTO likelihood_level_rules (set_id, layer, band, level) VALUES (%s, %s, %s, %s)",
                              (new, layer, band, level))
        return new

    def add_day(self, day=SUMMER, status="loaded"):
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box, max_mm) VALUES (%s, %s, %s, now(), 'k', "
            "41.5, 36.5, -109.7, -101.4, 12.7, %s, %s)",
            (day, self.run_id, status, None if status == "missing" else 1000, 1007.7 if status == "suspect" else None))

    def add_cells(self, cells, day=SUMMER):
        for row, col, mm in cells:
            self.conn.execute(
                "INSERT INTO radar_cells (day, grid_row, grid_col, mm, geom) "
                "VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (day, row, col, mm, mesh.LO1 + col * mesh.STEP, mesh.LA1 - row * mesh.STEP))

    def cell(self, row, col):
        return mesh.LA1 - row * mesh.STEP, mesh.LO1 + col * mesh.STEP

    def noon(self, day=SUMMER):
        return datetime(day.year, day.month, day.day, 18, tzinfo=UTC)

    def add_report(self, when, lat, lon, magnitude=1.0, report_text="HAIL", source=None):
        self.serial += 1
        report_type = {"HAIL": "H", "TSTM WND GST": "G", "TORNADO": "T"}[report_text]
        self.conn.execute(
            "INSERT INTO iem_data (utc_datetime, latitude, longitude, magnitude, nws_issuer, report_type, "
            "report_text, report_source, ingested_at) VALUES (%s, %s, %s, %s, 'TEST', %s, %s, %s, now())",
            (when, round(lat, 6), round(lon + self.serial * 1e-6, 6), magnitude, report_type, report_text, source))

    def add_filing(self, when, lat, lon, largest=1.25, average=None, station=None):
        """A CoCoRaHS hail filing."""
        self.serial += 1
        if self.cocorahs_load is None:
            run = self.conn.execute(
                "INSERT INTO cocorahs_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
            self.cocorahs_load = self.conn.execute(
                "INSERT INTO cocorahs_loads (run_id, state, date_type, window_start, window_end, rows_seen, rows_new, "
                "rows_changed, rows_rejected) VALUES (%s, 'CO', 'reportdate', '1999-06-01', '1999-06-30', 0, 0, 0, 0) "
                "RETURNING load_id", (run,)).fetchone()["load_id"]
        self.conn.execute(
            "INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, smallest_in, "
            "average_in, largest_in, load_id, raw_row) VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, 'raw')",
            (station or f"ZZ-TEST-{self.serial}", when, round(lat, 6), round(lon, 6), average, largest,
             self.cocorahs_load))

    def build(self, day=SUMMER, **kw):
        return build.build_day(self.conn, day, DENVER, set_id=kw.pop("set_id", self.lk_set), **kw)

    def level_at(self, lat, lon, day=SUMMER, set_id=None):
        row = self.conn.execute(
            "SELECT level FROM likelihood_zones WHERE day = %s AND set_id = %s "
            "AND ST_Intersects(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
            (day, set_id or self.lk_set, lon, lat)).fetchone()
        return row["level"] if row else None

    def scenario(self):
        """A 20 cell line of hail (about 16 km) with its report at the west end, and a separate patch 11 km south.

        Returns the report's lat and lon.  Both patches are 3 km or more apart from each other's margin.
        """
        self.add_day()
        self.add_cells([(ROW, COL + c, 30) for c in range(20)] + [(ROW + 10, COL, 30)])
        lat, lon = self.cell(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        return lat, lon

    # -- golden:  the radar layers are the radar zones' ---------------------------------------

    def test_with_iem_hail_reports_only_the_layers_are_the_radar_zones_layers(self):
        self.scenario()
        zones.build_day(self.conn, SUMMER, DENVER, set_id=self.radar_set)
        want = {r["layer"]: r["area_km2"] for r in self.conn.execute(
            "SELECT layer, area_km2 FROM radar_zones WHERE day = %s AND set_id = %s", (SUMMER, self.radar_set))}
        got = self.build()["layer_areas"]
        for layer in ("core", "margin", "radar_indicated"):
            with self.subTest(layer=layer):
                self.assertAlmostEqual(got[layer], want[layer], delta=want[layer] * 1e-9)
        self.assertEqual(set(want), {"core", "margin", "radar_indicated"})

    # -- levels, point by point -------------------------------------------------------------

    def test_core_at_the_report_and_within_five_miles_is_agree_and_beyond_is_one_source(self):
        lat, lon = self.scenario()
        self.build()
        self.assertEqual(self.level_at(lat, lon), "agree")                           # core, under a mile
        self.assertEqual(self.level_at(*self.cell(ROW, COL + 7)), "agree")           # core, about 3.7 miles
        self.assertEqual(self.level_at(*self.cell(ROW, COL + 17)), "one_source")     # core, about 9 miles

    def test_margin_is_agree_near_the_report_and_one_source_from_three_miles(self):
        lat, lon = self.scenario()
        self.build()
        self.assertEqual(self.level_at(lat + 0.0135, lon), "agree")                  # margin, under a mile
        self.assertEqual(self.level_at(self.cell(ROW, COL + 7)[0] + 0.0135, self.cell(ROW, COL + 7)[1]),
                         "one_source")                                               # margin, about 3.8 miles

    def test_an_unanchored_radar_patch_is_one_source(self):
        self.scenario()
        self.build()
        self.assertEqual(self.level_at(*self.cell(ROW + 10, COL)), "one_source")

    def test_a_report_with_no_radar_behind_it_makes_one_source_out_to_five_miles_and_nothing_beyond(self):
        lat, lon = self.scenario()
        self.build()
        self.assertEqual(self.level_at(lat - 0.0405, lon), "one_source")             # no radar layer, about 2.8 miles
        self.assertIsNone(self.level_at(lat + 0.2, lon))                             # no radar layer, about 14 miles

    def test_a_report_alone_makes_a_circle_of_about_five_miles(self):
        self.add_day()
        lat, lon = self.cell(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        got = self.build()
        want = pi * (5 * 1609.344 / 1000) ** 2
        self.assertEqual(set(got["areas"]), {"one_source"})
        self.assertAlmostEqual(got["areas"]["one_source"], want, delta=want * 0.01)
        self.assertEqual((got["patches"], got["patches_anchored"]), (0, 0))

    def test_levels_never_overlap(self):
        self.scenario()
        got = self.build()
        self.assertGreaterEqual(len(got["areas"]), 2)
        rows = self.conn.execute(
            "SELECT a.level AS a, b.level AS b, ST_Area(ST_Intersection(a.geom, b.geom)::geography) / 1e6 AS km2 "
            "FROM likelihood_zones a JOIN likelihood_zones b ON a.day = b.day AND a.set_id = b.set_id "
            "AND a.level < b.level WHERE a.day = %s AND a.set_id = %s", (SUMMER, self.lk_set)).fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertLess(row["km2"], 1e-6, row)

    def test_the_stored_areas_are_the_returned_areas_and_every_zone_is_a_valid_multipolygon(self):
        self.scenario()
        got = self.build()
        rows = self.conn.execute(
            "SELECT level, area_km2, ST_IsValid(geom) AS ok, GeometryType(geom) AS kind "
            "FROM likelihood_zones WHERE day = %s AND set_id = %s", (SUMMER, self.lk_set)).fetchall()
        self.assertEqual({r["level"]: r["area_km2"] for r in rows}, got["areas"])
        self.assertTrue(all(r["ok"] and r["kind"] == "MULTIPOLYGON" for r in rows))

    # -- the rules and the edges belong to the set -------------------------------------------

    def test_a_changed_rule_changes_the_level_and_context_is_stored(self):
        lat, lon = self.scenario()
        other = self.add_set(changes={("none", "1_3"): "context", ("none", "over_5"): "one_source"})
        got = self.build(set_id=other)
        self.assertIn("context", got["areas"])
        self.assertEqual(self.level_at(lat - 0.0405, lon, set_id=other), "context")
        self.assertEqual(self.level_at(lat + 0.2, lon, set_id=other), "one_source")     # the box beyond the reports

    def test_the_band_edges_come_from_the_set(self):
        self.scenario()
        wide = self.add_set(edges=(1.0, 2.0, 3.0))
        self.build()
        self.build(set_id=wide)
        spot = self.cell(ROW, COL + 7)                                                  # about 3.7 miles from the report
        self.assertEqual(self.level_at(*spot), "agree")                                 # 3_5 for the default edges
        self.assertEqual(self.level_at(*spot, set_id=wide), "one_source")               # over_5 for 1, 2 and 3

    def test_the_default_is_the_newest_set_and_an_older_one_can_be_asked_for(self):
        self.scenario()
        newest = self.add_set(changes={("core", "le_1"): "one_source"})
        newest_run = build.build_day(self.conn, SUMMER, DENVER)
        self.assertEqual(newest_run["set_id"], newest)
        lat, lon = self.cell(ROW, COL)
        self.assertEqual(self.level_at(lat, lon, set_id=newest), "one_source")
        build.build_day(self.conn, SUMMER, DENVER, set_id=self.lk_set)
        self.assertEqual(self.level_at(lat, lon, set_id=self.lk_set), "agree")

    # -- CoCoRaHS ---------------------------------------------------------------------------

    def one_patch(self):
        self.add_day()
        self.add_cells([(ROW, COL, 30)])
        return self.cell(ROW, COL)

    def test_a_cocorahs_filing_at_the_floor_anchors_a_patch(self):
        lat, lon = self.one_patch()
        self.add_filing(self.noon(), lat, lon, largest=1.00)
        got = self.build()
        self.assertEqual((got["cocorahs_reports"], got["iem_reports"], got["patches_anchored"]), (1, 0, 1))
        self.assertIn("agree", got["areas"])

    def test_a_filing_under_the_floor_does_not_count_and_the_floor_is_the_sets(self):
        lat, lon = self.one_patch()
        self.add_filing(self.noon(), lat, lon, largest=0.75)
        got = self.build()
        self.assertEqual((got["cocorahs_reports"], got["patches_anchored"]), (0, 0))
        self.assertEqual(set(got["areas"]), {"one_source"})
        low = self.add_set(floor=0.50)
        again = self.build(set_id=low)
        self.assertEqual((again["cocorahs_reports"], again["patches_anchored"]), (1, 1))

    def test_a_filing_with_no_largest_stone_counts_by_its_average_and_with_neither_does_not(self):
        lat, lon = self.one_patch()
        self.add_filing(self.noon(), lat, lon, largest=None, average=1.25)
        self.add_filing(self.noon() + timedelta(minutes=1), lat, lon, largest=None, average=None)
        self.add_filing(self.noon() + timedelta(minutes=2), lat, lon, largest=None, average=0.5)
        self.assertEqual(self.build()["cocorahs_reports"], 1)

    def test_a_filing_counts_in_the_local_day_only(self):
        lat, lon = self.one_patch()
        self.add_filing(START, lat, lon)                                  # first minute: in
        self.add_filing(END - timedelta(minutes=1), lat, lon)             # last minute: in
        self.add_filing(END, lat, lon)                                    # next day: out
        self.add_filing(START - timedelta(minutes=1), lat, lon)           # day before: out
        self.assertEqual(self.build()["cocorahs_reports"], 2)

    def test_a_cocorahs_filing_with_no_radar_makes_a_circle_too(self):
        self.add_day()
        lat, lon = self.cell(ROW, COL)
        self.add_filing(self.noon(), lat, lon)
        self.assertIn("one_source", self.build()["areas"])

    # -- IEM reports ------------------------------------------------------------------------

    def test_an_iem_report_whose_source_says_cocorahs_is_dropped_and_counted(self):
        lat, lon = self.one_patch()
        self.add_report(self.noon(), lat, lon, source="CoCoRaHS ")
        self.add_report(self.noon(), lat, lon, source="cocorahs")
        got = self.build()
        self.assertEqual((got["iem_reports"], got["duplicates_dropped"], got["patches_anchored"]), (0, 2, 0))

    def test_an_iem_report_near_a_filing_is_kept_when_its_source_does_not_say_cocorahs(self):
        lat, lon = self.one_patch()
        self.add_report(self.noon(), lat, lon, source="PUBLIC")
        self.add_filing(self.noon(), lat, lon)
        got = self.build()
        self.assertEqual((got["iem_reports"], got["cocorahs_reports"], got["duplicates_dropped"]), (1, 1, 0))

    def test_a_small_report_does_not_count(self):
        lat, lon = self.one_patch()
        self.add_report(self.noon(), lat, lon, magnitude=0.75)
        self.assertEqual(self.build()["iem_reports"], 0)

    def test_only_hail_reports_count_and_a_wind_report_anchors_neither_these_nor_the_radar_zones(self):
        # Item 254:  the radar zones used to anchor on any roof-relevant report.  Both now take HAIL only.
        lat, lon = self.one_patch()
        self.add_report(self.noon(), lat, lon, magnitude=70, report_text="TSTM WND GST")
        self.add_report(self.noon(), lat, lon, magnitude=0, report_text="TORNADO")
        got = self.build()
        self.assertEqual((got["iem_reports"], got["patches_anchored"]), (0, 0))
        self.assertEqual(zones.build_day(self.conn, SUMMER, DENVER, set_id=self.radar_set)["patches_anchored"], 0)

    def test_an_iem_report_counts_in_the_local_day_only(self):
        lat, lon = self.one_patch()
        self.add_report(START, lat, lon)
        self.add_report(END - timedelta(minutes=1), lat, lon)
        self.add_report(END, lat, lon)
        self.add_report(START - timedelta(minutes=1), lat, lon)
        self.assertEqual(self.build()["iem_reports"], 2)

    # -- warnings ---------------------------------------------------------------------------

    def cover(self, states="CO"):
        return self.conn.execute(
            "INSERT INTO warning_loads (window_start, window_end, states, polygons_seen, polygons_new) "
            "VALUES (%s, %s, %s, 0, 0) RETURNING load_id",
            (START - timedelta(days=1), END + timedelta(days=1), states)).fetchone()["load_id"]

    def add_warning(self, load_id, box):
        self.serial += 1
        self.conn.execute(
            "INSERT INTO warning_polygons (wfo, phenomena, significance, eventid, vtec_year, poly_begin, poly_end, "
            "load_id, geom) VALUES ('BOU', 'SV', 'W', %s, 1999, %s, %s, %s, ST_Multi(ST_MakeEnvelope(%s, %s, %s, %s, 4326)))",
            (self.serial, START + timedelta(hours=12), START + timedelta(hours=13), load_id, *box))

    def test_a_warning_far_from_a_report_is_context_and_only_when_warnings_were_fetched(self):
        lat, lon = self.scenario()
        far = (lon - 0.05, lat + 0.15, lon + 0.05, lat + 0.25)                      # 17 to 28 km north of the report
        self.add_warning(self.cover(), far)
        got = self.build()
        self.assertTrue(got["warnings_held"])
        self.assertIn("outer", got["layer_areas"])
        self.assertEqual(self.level_at(lat + 0.2, lon), "context")

    def test_without_a_warnings_fetch_there_is_no_outer_layer_and_no_context(self):
        lat, lon = self.scenario()
        self.add_warning(self.conn.execute(
            "INSERT INTO warning_loads (window_start, window_end, states, polygons_seen, polygons_new) "
            "VALUES (%s, %s, 'WY', 0, 0) RETURNING load_id", (START, END)).fetchone()["load_id"],
            (lon - 0.05, lat + 0.15, lon + 0.05, lat + 0.25))
        got = self.build()
        self.assertFalse(got["warnings_held"])
        self.assertNotIn("outer", got["layer_areas"])
        self.assertNotIn("context", got["areas"])

    # -- the build row, rebuilds, empties and errors -------------------------------------------

    def test_the_builds_row_holds_what_the_build_saw(self):
        lat, lon = self.scenario()
        self.add_filing(self.noon(), lat, lon)
        self.add_report(self.noon(), lat, lon, source="COCORAHS")
        user = self.conn.execute("SELECT emp_id FROM users ORDER BY emp_id LIMIT 1").fetchone()["emp_id"]
        self.build(user_id=user)
        row = self.conn.execute("SELECT * FROM likelihood_builds WHERE day = %s AND set_id = %s",
                                (SUMMER, self.lk_set)).fetchone()
        self.assertEqual((row["iem_reports"], row["cocorahs_reports"], row["duplicates_dropped"]), (1, 1, 1))
        self.assertEqual((row["patches"], row["patches_anchored"], row["built_by"]), (2, 1, user))
        self.assertEqual((row["window_start"], row["window_end"]), (START, END))

    def test_a_rebuild_replaces_the_build_and_its_zones(self):
        lat, lon = self.scenario()
        self.build()
        first = self.conn.execute("SELECT count(*) AS n FROM likelihood_zones WHERE day = %s", (SUMMER,)).fetchone()["n"]
        self.add_filing(self.noon(), lat + 0.2, lon)                                  # a new circle far north
        again = self.build()
        builds = self.conn.execute("SELECT count(*) AS n FROM likelihood_builds WHERE day = %s AND set_id = %s",
                                   (SUMMER, self.lk_set)).fetchone()["n"]
        self.assertEqual(builds, 1)
        self.assertEqual(again["cocorahs_reports"], 1)
        self.assertEqual(self.level_at(lat + 0.2, lon), "one_source")
        self.assertGreaterEqual(first, 2)

    def test_a_day_with_no_hail_and_no_reports_is_built_with_no_zones(self):
        self.add_day()
        got = self.build()
        self.assertEqual((got["areas"], got["patches"], got["iem_reports"]), ({}, 0, 0))
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM likelihood_builds WHERE day = %s",
                                           (SUMMER,)).fetchone()["n"], 1)

    def test_a_day_without_a_loaded_radar_file_is_an_error(self):
        for status in ("missing", "suspect"):
            with self.subTest(status=status):
                day = date(1999, 7, 1) if status == "missing" else date(1999, 7, 2)
                self.add_day(day, status)
                with self.assertRaisesRegex(build.LikelihoodError, "no radar file loaded"):
                    self.build(day)
        with self.assertRaisesRegex(build.LikelihoodError, "no radar file loaded"):
            self.build(date(1999, 7, 3))

    def test_a_set_with_missing_rules_or_no_such_set_is_an_error(self):
        self.add_day()
        short = self.add_set(drop=1)
        with self.assertRaisesRegex(build.LikelihoodError, "19 rules"):
            self.build(set_id=short)
        with self.assertRaisesRegex(build.LikelihoodError, "No such parameter set"):
            self.build(set_id=999999)

    def test_building_two_days_in_one_transaction_works(self):
        self.scenario()
        self.add_day(date(1999, 6, 9))
        self.build()
        self.build(date(1999, 6, 9))
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM likelihood_builds WHERE set_id = %s",
                                           (self.lk_set,)).fetchone()["n"], 2)


if __name__ == "__main__":
    unittest.main()
