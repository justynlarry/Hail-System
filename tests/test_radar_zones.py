"""Tests for hailsys/radar/zones.py:  the radar zones built from stored cells.

The database tests need a role that can insert into the radar tables, parameter sets and
iem_data, which hail_app cannot, so run them as the admin role:

    set -a; . ./.env; set +a
    docker compose run --rm -T --no-deps -e PGUSER=hail_admin -e PGPASSWORD="$POSTGIS_ROOT_PASSWORD" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_radar_zones

Under hail_app they skip.  Everything is built in a transaction that is always rolled back,
on 1999 dates that no real row uses.

What they pin: patches join on diagonals and not across a two cell gap; a patch is anchored
by an actionable report within the radius, not just past it, not by a small or out of day
report; the local day window holds in summer and winter; core, margin and radar_indicated
never overlap; a cell exactly at the threshold counts; a different parameter set changes the
core without new cells; a rebuild replaces; an empty day is built and has no zones.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.radar import mesh, zones
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc
SUMMER = date(1999, 6, 8)
WINTER = date(1999, 1, 15)
START = datetime(1999, 6, 8, 6, tzinfo=UTC)       # SUMMER in Denver, in UTC
END = datetime(1999, 6, 9, 6, tzinfo=UTC)

# Denver, on the NOAA grid:  a cell is 0.01 degrees, about 0.86 km wide and 1.11 km tall here.
ROW, COL = 1525, 2500


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class DayWindowTest(unittest.TestCase):
    def test_summer_day_runs_0600_to_0600_utc(self):
        start, end = zones.day_window(SUMMER, DENVER)
        self.assertEqual((start.astimezone(UTC), end.astimezone(UTC)),
                         (datetime(1999, 6, 8, 6, tzinfo=UTC), datetime(1999, 6, 9, 6, tzinfo=UTC)))

    def test_winter_day_runs_0700_to_0700_utc(self):
        start, end = zones.day_window(WINTER, DENVER)
        self.assertEqual((start.astimezone(UTC), end.astimezone(UTC)),
                         (datetime(1999, 1, 15, 7, tzinfo=UTC), datetime(1999, 1, 16, 7, tzinfo=UTC)))

    def test_spring_forward_day_is_23_hours(self):
        start, end = zones.day_window(date(1999, 4, 4), DENVER)
        # aware datetimes in one zone subtract as wall clock, so compare in UTC
        self.assertEqual((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 3600, 23)

    def test_the_zone_is_an_argument(self):
        start, _ = zones.day_window(SUMMER, UTC)
        self.assertEqual(start, datetime(1999, 6, 8, tzinfo=UTC))


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class BuildTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        allowed = self.conn.execute(
            "SELECT has_table_privilege('radar_parameter_sets', 'INSERT') AS ok").fetchone()["ok"]
        if not allowed:
            self.skipTest("needs a role that can insert parameter sets (hail_admin)")
        self.user = self.conn.execute("SELECT emp_id FROM users ORDER BY emp_id LIMIT 1").fetchone()["emp_id"]
        self.run_id = self.conn.execute(
            "INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.set_id = self.add_set("test-1.00", 1.00, 3.0, 5.0)
        self.serial = 0

    # -- building blocks ------------------------------------------------------------------

    def add_set(self, name, threshold, margin, radius):
        return self.conn.execute(
            "INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
            "VALUES (%s, %s, %s, %s) RETURNING set_id", (name, threshold, margin, radius)).fetchone()["set_id"]

    def add_day(self, day, status="loaded"):
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box, max_mm) VALUES (%s, %s, %s, now(), 'k', "
            "41.5, 36.5, -109.7, -101.4, 12.7, %s, %s)",
            (day, self.run_id, status, None if status == "missing" else 1000, 1007.7 if status == "suspect" else None))

    def add_cells(self, day, cells):
        for row, col, mm in cells:
            self.conn.execute(
                "INSERT INTO radar_cells (day, grid_row, grid_col, mm, geom) "
                "VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (day, row, col, mm, mesh.LO1 + col * mesh.STEP, mesh.LA1 - row * mesh.STEP))

    def add_report(self, when, lat, lon, magnitude=1.0, report_text="HAIL"):
        self.serial += 1
        report_type = {"HAIL": "H", "HEAVY RAIN": "R"}[report_text]
        self.conn.execute(
            "INSERT INTO iem_data (utc_datetime, latitude, longitude, magnitude, nws_issuer, report_type, "
            "report_text, ingested_at) VALUES (%s, %s, %s, %s, 'TEST', %s, %s, now())",
            (when, round(lat, 6), round(lon + self.serial * 1e-6, 6), magnitude, report_type, report_text))

    def cell_lat_lon(self, row, col):
        return mesh.LA1 - row * mesh.STEP, mesh.LO1 + col * mesh.STEP

    def noon(self, day=SUMMER):
        return datetime(day.year, day.month, day.day, 18, tzinfo=UTC)

    def build(self, day=SUMMER, **kw):
        return zones.build_day(self.conn, day, DENVER, set_id=kw.pop("set_id", self.set_id), **kw)

    def layers(self, day=SUMMER, set_id=None):
        rows = self.conn.execute("SELECT layer, area_km2 FROM radar_zones WHERE day = %s AND set_id = %s",
                                 (day, set_id or self.set_id)).fetchall()
        return {r["layer"]: r["area_km2"] for r in rows}

    def overlap_km2(self, a, b, day=SUMMER):
        row = self.conn.execute(
            "SELECT ST_Area(ST_Intersection(x.geom, y.geom)::geography) / 1e6 AS km2 "
            "FROM radar_zones x, radar_zones y WHERE x.day = %s AND y.day = %s AND x.set_id = y.set_id "
            "AND x.layer = %s AND y.layer = %s", (day, day, a, b)).fetchone()
        return row["km2"]

    # -- patches and anchoring ------------------------------------------------------------

    def test_diagonal_cells_are_one_patch_and_a_two_cell_gap_is_two(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30), (ROW + 1, COL + 1, 30), (ROW, COL + 20, 30), (ROW, COL + 22, 30)])
        got = self.build()
        self.assertEqual((got["cells_over_threshold"], got["patches"]), (4, 3))

    def test_only_a_patch_near_an_actionable_report_is_core(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30), (ROW, COL + 20, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        got = self.build()
        self.assertEqual((got["patches"], got["patches_anchored"], got["anchor_reports"]), (2, 1, 1))
        layers = self.layers()
        self.assertEqual(set(layers), {"core", "margin", "radar_indicated"})
        self.assertGreater(layers["core"], 0.8)
        self.assertLess(layers["core"], 1.1)

    def test_the_radius_is_5_km_not_more(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat + 0.0440, lon)          # about 4.9 km north
        self.assertEqual(self.build()["patches_anchored"], 1)
        self.add_report(self.noon(), lat - 0.0460, lon)          # about 5.1 km south
        self.add_day(date(1999, 6, 9))
        self.add_cells(date(1999, 6, 9), [(ROW, COL, 30)])
        self.add_report(datetime(1999, 6, 9, 18, tzinfo=UTC), lat - 0.0460, lon)
        self.assertEqual(self.build(date(1999, 6, 9))["patches_anchored"], 0)

    def test_a_small_report_or_a_wrong_type_does_not_anchor(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon, magnitude=0.75)   # under the 1.00 in minimum
        self.add_report(self.noon(), lat, lon, magnitude=2.0, report_text="HEAVY RAIN")
        got = self.build()
        self.assertEqual((got["anchor_reports"], got["patches_anchored"]), (0, 0))
        self.assertEqual(set(self.layers()), {"radar_indicated"})

    def test_the_day_window_in_summer_and_winter(self):
        for day, edge in ((SUMMER, 6), (WINTER, 7)):
            with self.subTest(day=day):
                self.add_day(day)
                self.add_cells(day, [(ROW, COL, 30)])
                lat, lon = self.cell_lat_lon(ROW, COL)
                start = datetime(day.year, day.month, day.day, edge, tzinfo=UTC)
                nxt = datetime(day.year, day.month, day.day + 1, edge, tzinfo=UTC)
                self.add_report(start, lat, lon)                                 # first minute: in
                self.add_report(nxt - (nxt - start) / 1440, lat, lon)            # last minute: in
                self.add_report(nxt, lat, lon)                                   # next day: out
                self.add_report(start - (nxt - start) / 1440, lat, lon)          # day before: out
                self.assertEqual(self.build(day)["anchor_reports"], 2)

    # -- the layers -----------------------------------------------------------------------

    def anchored_with_neighbour(self):
        """A 20 cell core line with its report at one end, and a separate 16 cell line two rows
        down that starts 12 km along (beyond the 5 km radius) but runs 2 km from the core."""
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL + c, 30) for c in range(20)]
                       + [(ROW + 2, COL + c, 30) for c in range(15, 31)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)

    def test_layers_do_not_overlap(self):
        self.anchored_with_neighbour()
        self.build()
        for a, b in (("core", "margin"), ("core", "radar_indicated"), ("margin", "radar_indicated")):
            with self.subTest(a=a, b=b):
                self.assertLess(self.overlap_km2(a, b), 1e-6)

    def test_the_margin_trims_the_neighbour_it_reaches(self):
        self.anchored_with_neighbour()
        self.build()
        layers = self.layers()
        whole = 16 * 0.9556                       # 16 cells of about 0.95 km2
        self.assertLess(layers["radar_indicated"], whole - 4.0)     # the margin took the near end
        self.assertGreater(layers["radar_indicated"], 3.0)          # and left the far end

    def test_margin_of_one_cell_is_about_a_3_km_ring(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        self.build()
        layers = self.layers()
        # a 3 km buffer is 28 km2, plus 3 km along the cell's 3.9 km perimeter
        self.assertGreater(layers["margin"], 36)
        self.assertLess(layers["margin"], 44)

    def test_a_block_of_cells_is_one_polygon_with_no_gaps(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW + r, COL + c, 30) for r in range(3) for c in range(3)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        self.build()
        row = self.conn.execute(
            "SELECT ST_NumGeometries(geom) AS parts, ST_NumInteriorRings(ST_GeometryN(geom, 1)) AS holes, "
            "area_km2 FROM radar_zones WHERE day = %s AND layer = 'core'", (SUMMER,)).fetchone()
        self.assertEqual((row["parts"], row["holes"]), (1, 0))
        self.assertAlmostEqual(row["area_km2"], 9 * 0.9556, delta=0.4)

    # -- parameters -----------------------------------------------------------------------

    def test_a_cell_exactly_at_the_threshold_counts(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 25.4)])
        self.assertEqual(self.build()["cells_over_threshold"], 1)

    def test_a_cell_just_under_the_threshold_does_not(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 25.3)])
        self.assertEqual(self.build()["cells_over_threshold"], 0)

    def test_another_parameter_set_changes_the_core_with_the_same_cells(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30), (ROW, COL + 1, 60)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        big = self.add_set("test-2.00", 2.00, 3.0, 5.0)
        self.build()
        self.build(set_id=big)
        low, high = self.layers()["core"], self.layers(set_id=big)["core"]
        self.assertAlmostEqual(low, 2 * 0.9556, delta=0.3)
        self.assertAlmostEqual(high, 0.9556, delta=0.2)

    def test_no_set_given_uses_the_newest(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        newest = self.add_set("test-newest", 1.50, 2.0, 5.0)
        got = zones.build_day(self.conn, SUMMER, DENVER)
        self.assertEqual(got["set_id"], newest)

    def test_the_margin_follows_the_set(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        narrow = self.add_set("test-1km", 1.00, 1.0, 5.0)
        self.build()
        self.build(set_id=narrow)
        self.assertLess(self.layers(set_id=narrow)["margin"], self.layers()["margin"] / 2)

    def test_the_report_radius_follows_the_set(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat + 0.0440, lon)            # about 4.9 km
        tight = self.add_set("test-2km", 1.00, 3.0, 2.0)
        self.assertEqual(self.build()["patches_anchored"], 1)
        self.assertEqual(self.build(set_id=tight)["patches_anchored"], 0)

    # -- storing --------------------------------------------------------------------------

    def test_a_rebuild_replaces_and_does_not_duplicate(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        self.build()
        self.add_cells(SUMMER, [(ROW, COL + 1, 30)])
        self.build()
        builds = self.conn.execute("SELECT count(*) AS n FROM radar_zone_builds WHERE day = %s", (SUMMER,)).fetchone()
        zone_rows = self.conn.execute("SELECT count(*) AS n FROM radar_zones WHERE day = %s AND layer = 'core'",
                                      (SUMMER,)).fetchone()
        self.assertEqual((builds["n"], zone_rows["n"]), (1, 1))
        self.assertAlmostEqual(self.layers()["core"], 2 * 0.9556, delta=0.3)

    def test_an_empty_day_is_built_with_counts_and_no_zones(self):
        self.add_day(SUMMER)
        got = self.build()
        self.assertEqual((got["cells_over_threshold"], got["patches"], got["patches_anchored"]), (0, 0, 0))
        self.assertEqual(got["areas"], {})
        self.assertEqual(self.layers(), {})
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM radar_zone_builds").fetchone()["n"] > 0, True)

    def test_the_build_records_who_and_the_window(self):
        self.add_day(SUMMER)
        self.build(user_id=self.user)
        row = self.conn.execute("SELECT built_by, window_start, window_end FROM radar_zone_builds "
                                "WHERE day = %s", (SUMMER,)).fetchone()
        self.assertEqual(row["built_by"], self.user)
        self.assertEqual(row["window_start"], datetime(1999, 6, 8, 6, tzinfo=UTC))
        self.assertEqual(row["window_end"], datetime(1999, 6, 9, 6, tzinfo=UTC))

    def test_the_summary_areas_match_the_stored_layers(self):
        self.anchored_with_neighbour()
        got = self.build()
        self.assertEqual(set(got["areas"]), set(self.layers()))
        for layer, km2 in self.layers().items():
            self.assertAlmostEqual(got["areas"][layer], km2)

    # -- the outer layer: NWS warning polygons ---------------------------------------------

    def cover(self, states="CO", start=START - timedelta(days=1), end=END + timedelta(days=1)):
        """A warning_loads row, as a fetch for the day would leave."""
        return self.conn.execute(
            "INSERT INTO warning_loads (window_start, window_end, states, polygons_seen, polygons_new) "
            "VALUES (%s, %s, %s, 0, 0) RETURNING load_id", (start, end, states)).fetchone()["load_id"]

    def add_warning(self, load_id, begin=START + timedelta(hours=12), end=None, box=(-105.2, 39.6, -104.8, 39.9),
                    eventid=None):
        self.serial += 1
        end = end or begin + timedelta(minutes=30)
        self.conn.execute(
            "INSERT INTO warning_polygons (wfo, phenomena, significance, eventid, vtec_year, poly_begin, "
            "poly_end, load_id, geom) VALUES ('BOU', 'SV', 'W', %s, 1999, %s, %s, %s, "
            "ST_Multi(ST_MakeEnvelope(%s, %s, %s, %s, 4326)))",
            (eventid or self.serial, begin, end, load_id, *box))

    def anchored_day(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)

    def test_without_a_warnings_fetch_there_is_no_outer_layer_and_it_says_so(self):
        self.anchored_day()
        got = self.build()
        self.assertIsNone(got["warnings_used"])
        self.assertNotIn("outer", got["areas"])

    def test_a_fetch_for_other_states_does_not_count(self):
        self.anchored_day()
        self.add_warning(self.cover(states="WY"))
        got = self.build()
        self.assertIsNone(got["warnings_used"])
        self.assertNotIn("outer", got["areas"])

    def test_a_fetch_that_found_nothing_is_zero_warnings_and_no_outer_layer(self):
        self.anchored_day()
        self.cover()
        got = self.build()
        self.assertEqual(got["warnings_used"], 0)
        self.assertNotIn("outer", got["areas"])

    def test_a_warning_over_the_storm_becomes_the_outer_layer(self):
        self.anchored_day()
        self.add_warning(self.cover())
        got = self.build()
        self.assertEqual(got["warnings_used"], 1)
        self.assertIn("outer", got["areas"])
        self.assertEqual(set(self.layers()), {"core", "margin", "outer"})

    def test_the_outer_layer_is_outside_the_other_three(self):
        self.anchored_day()
        self.add_warning(self.cover())
        self.build()
        for other in ("core", "margin"):
            with self.subTest(other=other):
                self.assertLess(self.overlap_km2("outer", other), 1e-6)
        polygon_km2 = self.conn.execute(
            "SELECT ST_Area(ST_MakeEnvelope(-105.2, 39.6, -104.8, 39.9, 4326)::geography) / 1e6 AS a").fetchone()["a"]
        layers = self.layers()
        self.assertAlmostEqual(layers["outer"] + layers["core"] + layers["margin"], polygon_km2, delta=2.0)

    def test_the_outer_layer_is_clipped_to_the_crop_box(self):
        self.anchored_day()
        self.add_warning(self.cover(), box=(-125.0, 39.6, -104.8, 39.9))      # runs far west of the box
        self.build()
        inside = self.conn.execute(
            "SELECT ST_Within(z.geom, ST_MakeEnvelope(-109.7 - 0.0051, 36.5 - 0.0051, -101.4 + 0.0051, 41.5 + 0.0051, 4326)) AS ok "
            "FROM radar_zones z WHERE z.day = %s AND z.layer = 'outer'", (SUMMER,)).fetchone()["ok"]
        self.assertTrue(inside)

    def test_the_outer_layer_reaches_the_edge_of_the_box_cells(self):
        self.anchored_day()
        self.add_warning(self.cover(), box=(-125.0, 39.6, -104.8, 39.9))
        self.build()
        west = self.conn.execute("SELECT ST_XMin(geom) AS x FROM radar_zones WHERE day = %s AND layer = 'outer'",
                                 (SUMMER,)).fetchone()["x"]
        self.assertAlmostEqual(west, -109.7 - 0.005, places=6)       # the edge of the westmost cell

    def test_the_outer_layer_reaches_the_north_edge_of_the_box_cells_too(self):
        self.anchored_day()
        self.add_warning(self.cover(), box=(-105.2, 39.6, -104.8, 45.0))
        self.build()
        north = self.conn.execute("SELECT ST_YMax(geom) AS y FROM radar_zones WHERE day = %s AND layer = 'outer'",
                                  (SUMMER,)).fetchone()["y"]
        self.assertAlmostEqual(north, 41.5 + 0.005, places=6)

    def test_a_warning_bigger_than_the_box_fills_all_four_edges_of_the_box_cells(self):
        self.anchored_day()
        self.add_warning(self.cover(), box=(-125.0, 30.0, -95.0, 45.0))
        self.build()
        row = self.conn.execute("SELECT ST_XMin(geom) AS w, ST_XMax(geom) AS e, ST_YMin(geom) AS s, "
                                "ST_YMax(geom) AS n FROM radar_zones WHERE day = %s AND layer = 'outer'",
                                (SUMMER,)).fetchone()
        half = 0.005
        for name, want in (("w", -109.7 - half), ("e", -101.4 + half), ("s", 36.5 - half), ("n", 41.5 + half)):
            with self.subTest(edge=name):
                self.assertAlmostEqual(row[name], want, places=6)

    def test_warnings_touching_the_day_only_at_its_edges_make_no_outer_layer(self):
        self.anchored_day()
        load = self.cover()
        self.add_warning(load, begin=START - timedelta(hours=1), end=START)
        self.add_warning(load, begin=END, end=END + timedelta(hours=1))
        got = self.build()
        self.assertEqual(got["warnings_used"], 0)
        self.assertNotIn("outer", got["areas"])

    def test_a_warning_far_from_the_box_counts_but_leaves_no_outer_layer(self):
        self.anchored_day()
        self.add_warning(self.cover(), box=(-90.2, 35.0, -90.0, 35.2))
        got = self.build()
        self.assertEqual(got["warnings_used"], 1)
        self.assertNotIn("outer", got["areas"])

    def test_a_warning_belongs_to_the_day_if_valid_at_any_moment_of_it(self):
        self.anchored_day()
        load = self.cover()
        minute = timedelta(minutes=1)
        inside = [("starts in the last minute", END - minute, END + timedelta(hours=1)),
                  ("ends in the first minute", START - timedelta(hours=1), START + minute),
                  ("spans the whole day", START - timedelta(hours=1), END + timedelta(hours=1))]
        outside = [("ends exactly as the day starts", START - timedelta(hours=1), START),
                   ("begins exactly as the day ends", END, END + timedelta(hours=1)),
                   ("the day before", START - timedelta(hours=5), START - timedelta(hours=4))]
        for _, begin, end in inside + outside:
            self.add_warning(load, begin=begin, end=end)
        self.assertEqual(self.build()["warnings_used"], len(inside))

    def test_a_rebuild_after_the_warnings_arrive_adds_the_outer_layer(self):
        self.anchored_day()
        self.assertNotIn("outer", self.build()["areas"])
        self.add_warning(self.cover())
        self.assertIn("outer", self.build()["areas"])

    def test_the_states_setting_decides_which_fetch_counts(self):
        self.anchored_day()
        self.add_warning(self.cover(states="CO,WY"))
        self.assertIsNone(self.build()["warnings_used"])
        self.conn.execute("SELECT set_config('app.current_emp_id', (SELECT emp_id::text FROM users ORDER BY emp_id LIMIT 1), true)")
        self.conn.execute("UPDATE settings SET storm_states = 'WY,CO'")
        self.assertEqual(self.build()["warnings_used"], 1)

    def test_a_fetch_that_ended_too_soon_after_the_day_does_not_count(self):
        self.anchored_day()
        self.add_warning(self.cover(end=END + timedelta(hours=1)))
        self.assertIsNone(self.build()["warnings_used"])

    # -- the roles that run it ------------------------------------------------------------

    def test_the_ingest_and_app_roles_can_build_a_day_with_the_outer_layer(self):
        self.anchored_day()
        self.add_warning(self.cover())
        for role in ("hail_ingest", "hail_app"):
            with self.subTest(role=role):
                self.conn.execute(f"SET LOCAL ROLE {role}")
                try:
                    got = self.build()
                finally:
                    self.conn.execute("RESET ROLE")
                self.assertEqual(got["warnings_used"], 1)
                self.assertIn("outer", got["areas"])

    # -- refusals -------------------------------------------------------------------------

    def test_a_day_that_was_never_loaded_is_refused(self):
        with self.assertRaisesRegex(zones.ZoneError, "no radar file loaded"):
            self.build()

    def test_a_missing_day_is_refused(self):
        self.add_day(SUMMER, status="missing")
        with self.assertRaisesRegex(zones.ZoneError, "no radar file loaded"):
            self.build()

    def test_a_suspect_day_is_refused(self):
        self.add_day(SUMMER, status="suspect")
        with self.assertRaisesRegex(zones.ZoneError, "no radar file loaded"):
            self.build()

    def test_an_unknown_parameter_set_is_refused(self):
        self.add_day(SUMMER)
        with self.assertRaisesRegex(zones.ZoneError, "No such parameter set"):
            self.build(set_id=-5)

    def test_a_refused_build_leaves_the_old_zones_alone(self):
        self.add_day(SUMMER)
        self.add_cells(SUMMER, [(ROW, COL, 30)])
        lat, lon = self.cell_lat_lon(ROW, COL)
        self.add_report(self.noon(), lat, lon)
        self.build()
        with self.assertRaises(zones.ZoneError):
            self.build(set_id=-5)
        self.assertIn("core", self.layers())


if __name__ == "__main__":
    unittest.main()
