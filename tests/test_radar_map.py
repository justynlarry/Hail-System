"""Tests for hailsys/queries/radar_map.py -- the reads behind the radar layers on the Territory map.

Rows are built in a transaction that is always rolled back, on 1999 dates, so this needs a role that can
insert radar rows (hail_admin), see test_radar_zones.py:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_radar_map

What they pin: only the parameter set in use is drawn (a day built with another is named, not drawn); the
picker offers a day only if there is something to draw, newest first, bounds inclusive; a day's warnings are
the polygons valid at any moment of its local day, in summer and winter; a union of days is one shape per
layer with the area of the union; a day with no stored outer layer shows the raw polygons and one with its
warnings counted does not; geometry is simplified; layers come in draw order and an empty layer is left out;
and the module only reads.
"""

import json
import os
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.queries import radar_map
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
DENVER = ZoneInfo("America/Denver")
D1, D2, D3 = date(1999, 6, 8), date(1999, 6, 9), date(1999, 6, 10)
ENV = "ST_MakeEnvelope(%s, %s, %s, %s, 4326)"


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class RadarMapTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('radar_parameter_sets', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert radar rows (hail_admin)")
        self.set_id = radar_map.in_use_set(self.conn)["set_id"]
        self.run_id = self.conn.execute(
            "INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.load_id = self.conn.execute(
            "INSERT INTO warning_loads (window_start, window_end, states, polygons_seen, polygons_new) "
            "VALUES ('1999-06-01', '1999-07-01', 'CO', 0, 0) RETURNING load_id").fetchone()["load_id"]
        self.serial = 0

    # -- building blocks ---------------------------------------------------------------------

    def add_set(self, name="newer", threshold=2.0):
        return self.conn.execute(
            "INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
            "VALUES (%s, %s, 3.0, 5.0) RETURNING set_id", (name, threshold)).fetchone()["set_id"]

    def add_day(self, day):
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, box_lon_min, "
            "box_lon_max, floor_mm, cells_in_box) VALUES (%s, %s, 'loaded', now(), 'k', 2, 1, 1, 2, 12.7, 5) "
            "ON CONFLICT DO NOTHING", (day, self.run_id))

    def add_build(self, day, set_id=None, warnings_used=0):
        self.add_day(day)
        self.conn.execute(
            "INSERT INTO radar_zone_builds (day, set_id, window_start, window_end, cells_over_threshold, patches, "
            "patches_anchored, anchor_reports, warnings_used) VALUES (%s, %s, '1999-06-08 06:00+00', "
            "'1999-06-09 06:00+00', 1, 1, 1, 1, %s)", (day, set_id or self.set_id, warnings_used))

    def add_zone(self, day, layer, box, set_id=None, geom_sql=None):
        self.conn.execute(
            "INSERT INTO radar_zones (day, set_id, layer, geom, area_km2) "
            f"SELECT %s, %s, %s, g, ST_Area(g::geography) / 1e6 FROM (SELECT {geom_sql or 'ST_Multi(' + ENV + ')'} AS g) q",
            (day, set_id or self.set_id, layer, *([] if geom_sql else box)))

    def add_warning(self, begin, end, box=(-105.0, 40.0, -104.9, 40.1)):
        self.serial += 1
        self.conn.execute(
            "INSERT INTO warning_polygons (wfo, phenomena, significance, eventid, vtec_year, poly_begin, poly_end, "
            "load_id, geom) VALUES ('BOU', 'SV', 'W', %s, 1999, %s, %s, %s, ST_Multi(" + ENV + "))",
            (self.serial, begin, end, self.load_id, *box))

    def built_day(self, day, core=(-105.0, 40.0, -104.9, 40.1), **kw):
        self.add_build(day, **kw)
        self.add_zone(day, "core", core)

    def stored_km2(self, day, layer, set_id=None):
        return self.conn.execute("SELECT area_km2 FROM radar_zones WHERE day = %s AND layer = %s AND set_id = %s",
                                 (day, layer, set_id or self.set_id)).fetchone()["area_km2"]

    def layers(self, days, **kw):
        got = radar_map.zones_geojson(self.conn, days, **kw)
        return got, {f["properties"]["layer"]: f for f in got["features"]}

    # -- the set in use ----------------------------------------------------------------------

    def test_the_set_in_use_is_the_newest_with_numbers_as_floats(self):
        newer = self.add_set(threshold=2.5)
        got = radar_map.in_use_set(self.conn)
        self.assertEqual((got["set_id"], got["hail_threshold_in"], got["margin_km"], got["report_radius_km"]),
                         (newer, 2.5, 3.0, 5.0))
        self.assertIsInstance(got["hail_threshold_in"], float)

    # -- the picker --------------------------------------------------------------------------

    def picker(self, first=D1, last=D3, **kw):
        return radar_map.picker_days(self.conn, first, last, **kw)

    def test_a_day_with_zones_is_offered_with_its_areas(self):
        self.built_day(D1)
        self.add_zone(D1, "margin", (-105.1, 39.9, -104.8, 40.2))
        (row,) = self.picker()
        self.assertEqual((row["day"], row["built"]), (D1, True))
        self.assertAlmostEqual(row["core_km2"], self.stored_km2(D1, "core"))
        self.assertAlmostEqual(row["margin_km2"], self.stored_km2(D1, "margin"))
        self.assertEqual((row["radar_indicated_km2"], row["outer_km2"], row["warnings"]), (0.0, 0.0, 0))

    def test_a_day_built_with_nothing_to_draw_is_left_out(self):
        self.add_build(D1)
        self.assertEqual(self.picker(), [])

    def test_a_day_with_only_warnings_is_offered_as_not_built(self):
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        (row,) = self.picker()
        self.assertEqual((row["day"], row["built"], row["warnings"], row["core_km2"]), (D1, False, 1, 0.0))

    def test_days_come_newest_first_and_the_bounds_are_inclusive(self):
        for day in (D1, D2, D3):
            self.built_day(day)
        self.assertEqual([r["day"] for r in self.picker()], [D3, D2, D1])
        self.assertEqual([r["day"] for r in self.picker(D2, D2)], [D2])
        self.assertEqual([r["day"] for r in self.picker(D1, D2)], [D2, D1])

    def test_a_warning_belongs_to_the_local_day_in_summer(self):
        # Denver June 8 is 06:00 UTC June 8 to 06:00 UTC June 9
        self.add_warning(datetime(1999, 6, 9, 5, 0, tzinfo=UTC), datetime(1999, 6, 9, 5, 59, tzinfo=UTC))
        self.assertEqual([r["day"] for r in self.picker()], [D1])

    def test_a_warning_in_the_first_minute_of_the_next_local_day_belongs_to_it(self):
        self.add_warning(datetime(1999, 6, 9, 6, 0, tzinfo=UTC), datetime(1999, 6, 9, 6, 30, tzinfo=UTC))
        self.assertEqual([r["day"] for r in self.picker()], [D2])

    def test_a_warning_spanning_midnight_is_in_both_days(self):
        self.add_warning(datetime(1999, 6, 9, 5, 30, tzinfo=UTC), datetime(1999, 6, 9, 6, 30, tzinfo=UTC))
        self.assertEqual([r["day"] for r in self.picker()], [D2, D1])

    def test_a_warning_ending_exactly_as_the_day_starts_is_not_in_it(self):
        self.add_warning(datetime(1999, 6, 8, 5, 0, tzinfo=UTC), datetime(1999, 6, 8, 6, 0, tzinfo=UTC))
        self.assertEqual(self.picker(D1, D1), [])

    def test_in_winter_the_local_day_is_an_hour_later_in_utc(self):
        jan = date(1999, 1, 15)
        self.add_warning(datetime(1999, 1, 16, 6, 30, tzinfo=UTC), datetime(1999, 1, 16, 6, 59, tzinfo=UTC))
        self.assertEqual([r["day"] for r in self.picker(jan, jan)], [jan])
        self.assertEqual(self.picker(date(1999, 1, 16), date(1999, 1, 16)), [])

    def test_the_zone_is_an_argument(self):
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        self.assertEqual([r["day"] for r in self.picker(tz=ZoneInfo("Pacific/Auckland"))], [D1 + timedelta(days=1)])

    def test_a_day_built_with_another_set_is_not_offered_as_built(self):
        self.built_day(D1)
        self.add_set()
        self.assertEqual(self.picker(), [])

    def test_a_day_built_with_another_set_is_not_built_for_the_set_in_use(self):
        self.built_day(D1)
        self.add_set()
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        (row,) = self.picker()
        self.assertEqual((row["built"], row["core_km2"], row["warnings"]), (False, 0.0, 1))

    # -- the layers --------------------------------------------------------------------------

    def test_one_day_gives_one_feature_per_layer_in_draw_order(self):
        self.built_day(D1)
        self.add_zone(D1, "margin", (-105.1, 39.9, -104.8, 40.2))
        self.add_zone(D1, "radar_indicated", (-104.7, 40.0, -104.6, 40.1))
        self.add_zone(D1, "outer", (-105.5, 39.5, -104.0, 40.5))
        got, by = self.layers([D1])
        self.assertEqual([f["properties"]["layer"] for f in got["features"]],
                         ["outer", "radar_indicated", "margin", "core"])
        self.assertEqual(got["type"], "FeatureCollection")
        self.assertEqual(radar_map.LAYERS, ("outer", "radar_indicated", "margin", "core"))

    def test_a_layer_with_nothing_is_left_out(self):
        self.built_day(D1)
        got, by = self.layers([D1])
        self.assertEqual(list(by), ["core"])

    def test_a_feature_is_a_polygon_shape_with_its_area_and_part_count(self):
        self.built_day(D1)
        _, by = self.layers([D1])
        f = by["core"]
        self.assertEqual((f["type"], f["properties"]["parts"]), ("Feature", 1))
        self.assertIn(f["geometry"]["type"], ("Polygon", "MultiPolygon"))     # a union of one shape may come out as Polygon
        self.assertAlmostEqual(f["properties"]["area_km2"], self.stored_km2(D1, "core"), delta=0.1)
        json.dumps(got := self.layers([D1])[0])                 # plain JSON all the way down
        self.assertIsInstance(got["features"][0]["geometry"]["coordinates"], list)

    def test_the_collection_names_the_set_and_the_days_sorted_and_unique(self):
        self.built_day(D1)
        got, _ = self.layers([D2, D1, D1])
        self.assertEqual(got["days"], ["1999-06-08", "1999-06-09"])
        self.assertEqual(got["set"]["set_id"], self.set_id)

    def test_no_days_is_an_empty_collection_with_its_keys(self):
        got = radar_map.zones_geojson(self.conn, [])
        self.assertEqual((got["features"], got["days"], got["built_with_other_set"]), ([], [], []))
        self.assertEqual(got["set"]["set_id"], self.set_id)

    def test_a_day_with_nothing_is_an_empty_collection(self):
        self.assertEqual(self.layers([D1])[0]["features"], [])

    # -- a union of days ---------------------------------------------------------------------

    def test_two_days_are_one_shape_per_layer_with_the_area_of_the_union(self):
        self.built_day(D1, core=(-105.0, 40.0, -104.9, 40.1))
        self.built_day(D2, core=(-104.95, 40.0, -104.85, 40.1))            # overlaps the first by half
        _, by = self.layers([D1, D2])
        one, two = self.stored_km2(D1, "core"), self.stored_km2(D2, "core")
        self.assertEqual(by["core"]["properties"]["parts"], 2)
        self.assertLess(by["core"]["properties"]["area_km2"], one + two - 1)         # not the sum
        self.assertGreater(by["core"]["properties"]["area_km2"], max(one, two))      # more than either

    def test_separate_shapes_in_a_union_add_up(self):
        self.built_day(D1, core=(-105.0, 40.0, -104.9, 40.1))
        self.built_day(D2, core=(-104.0, 40.0, -103.9, 40.1))
        _, by = self.layers([D1, D2])
        self.assertAlmostEqual(by["core"]["properties"]["area_km2"],
                               self.stored_km2(D1, "core") + self.stored_km2(D2, "core"), delta=0.2)

    def test_only_the_days_asked_for_are_in_it(self):
        self.built_day(D1)
        self.built_day(D2, core=(-104.0, 40.0, -103.9, 40.1))
        _, by = self.layers([D1])
        self.assertEqual(by["core"]["properties"]["parts"], 1)
        self.assertAlmostEqual(by["core"]["properties"]["area_km2"], self.stored_km2(D1, "core"), delta=0.1)

    # -- warnings ----------------------------------------------------------------------------

    def test_a_day_without_a_build_shows_the_raw_warnings_as_outer(self):
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        got, by = self.layers([D1])
        self.assertEqual(list(by), ["outer"])
        self.assertEqual(by["outer"]["properties"]["parts"], 1)

    def test_a_day_built_before_its_warnings_were_fetched_shows_the_raw_ones_too(self):
        self.built_day(D1, warnings_used=None)
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        _, by = self.layers([D1])
        self.assertEqual(set(by), {"core", "outer"})

    def test_a_day_with_its_warnings_counted_shows_the_stored_outer_not_the_raw(self):
        self.built_day(D1, warnings_used=1)
        self.add_zone(D1, "outer", (-105.5, 39.5, -104.0, 40.5))
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC),
                         box=(-90.0, 30.0, -89.0, 31.0))                 # far away, must not appear
        _, by = self.layers([D1])
        self.assertEqual(by["outer"]["properties"]["parts"], 1)
        self.assertAlmostEqual(by["outer"]["properties"]["area_km2"], self.stored_km2(D1, "outer"), delta=0.5)

    def test_the_raw_warnings_follow_the_same_day_edges_as_the_picker(self):
        start, end = datetime(1999, 6, 8, 6, tzinfo=UTC), datetime(1999, 6, 9, 6, tzinfo=UTC)
        hour = timedelta(hours=1)
        self.add_warning(start - hour, start)                       # ends exactly as the day begins
        self.add_warning(end, end + hour)                           # begins exactly as the day ends
        self.assertEqual(self.layers([D1])[0]["features"], [])
        self.add_warning(start - hour, start + timedelta(minutes=1))           # reaches into the first minute
        self.assertEqual(self.layers([D1])[1]["outer"]["properties"]["parts"], 1)
        self.add_warning(end - timedelta(minutes=1), end + hour)               # starts in the last minute
        self.assertEqual(self.layers([D1])[1]["outer"]["properties"]["parts"], 2)

    def test_a_warning_from_another_local_day_is_not_drawn(self):
        self.add_warning(datetime(1999, 6, 9, 12, tzinfo=UTC), datetime(1999, 6, 9, 13, tzinfo=UTC))
        self.assertEqual(self.layers([D1])[0]["features"], [])

    # -- the set in use, again ---------------------------------------------------------------

    def test_a_day_built_with_another_set_is_named_and_not_drawn(self):
        self.built_day(D1)
        newer = self.add_set()
        got, by = self.layers([D1])
        self.assertEqual(got["set"]["set_id"], newer)
        self.assertEqual(got["built_with_other_set"], ["1999-06-08"])
        self.assertNotIn("core", by)

    def test_a_day_built_with_both_sets_is_drawn_from_the_newest_and_not_named(self):
        self.built_day(D1)
        newer = self.add_set()
        self.add_build(D1, set_id=newer)
        self.add_zone(D1, "core", (-105.0, 40.0, -104.99, 40.01), set_id=newer)         # a much smaller core
        got, by = self.layers([D1])
        self.assertEqual(got["built_with_other_set"], [])
        self.assertAlmostEqual(by["core"]["properties"]["area_km2"], self.stored_km2(D1, "core", set_id=newer), delta=0.1)
        self.assertLess(by["core"]["properties"]["area_km2"], self.stored_km2(D1, "core") / 5)

    def test_only_the_days_asked_for_are_checked_for_another_set(self):
        self.built_day(D1)
        self.built_day(D2)
        self.add_set()
        self.assertEqual(self.layers([D1])[0]["built_with_other_set"], ["1999-06-08"])

    # -- simplifying -------------------------------------------------------------------------

    def test_geometry_is_simplified_by_the_tolerance(self):
        self.add_build(D1)
        self.add_zone(D1, "core", None, geom_sql="ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(-105, 40), 4326), 0.05, 64))")

        def points(simplify):
            f = self.layers([D1], simplify=simplify)[1]["core"]
            return sum(len(ring) for polygon in f["geometry"]["coordinates"] for ring in polygon)

        self.assertGreater(points(0.0), 100)
        self.assertLess(points(0.01), points(0.0) / 3)
        self.assertLessEqual(points(radar_map.SIMPLIFY_DEGREES), points(0.0))
        self.assertEqual(radar_map.SIMPLIFY_DEGREES, 0.001)

    def test_simplifying_does_not_change_the_reported_area(self):
        self.add_build(D1)
        self.add_zone(D1, "core", None, geom_sql="ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(-105, 40), 4326), 0.05, 64))")
        a = self.layers([D1], simplify=0.0)[1]["core"]["properties"]["area_km2"]
        b = self.layers([D1], simplify=0.02)[1]["core"]["properties"]["area_km2"]
        self.assertEqual(a, b)

    # -- the roles and the module itself ------------------------------------------------------

    def test_the_app_role_can_read_it_all(self):
        self.built_day(D1, warnings_used=None)
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        self.conn.execute("SET LOCAL ROLE hail_app")
        self.addCleanup(self.conn.execute, "RESET ROLE")
        self.assertEqual([r["day"] for r in self.picker(D1, D1)], [D1])
        self.assertEqual(set(self.layers([D1])[1]), {"core", "outer"})

    def test_the_module_only_reads(self):
        source = (ROOT / "hailsys" / "queries" / "radar_map.py").read_text()
        for word in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "DROP", "ALTER", "CREATE", "commit"):
            self.assertIsNone(re.search(rf"\b{word}\b", source), word)


if __name__ == "__main__":
    unittest.main()
