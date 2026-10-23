"""Tests for hailsys/queries/findings.py -- the reads behind the findings panel under the Territory map (slice 6).

Rows are built in a transaction that is always rolled back, on 1999 dates, so this needs a role that can insert
radar rows (hail_admin), the same as test_radar_map.py:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_findings

Piece 1, reports_by_layer: each actionable report of the chosen type on one local day is counted once, under the
highest layer it falls in (core, then margin, then radar_indicated, then outer, else none).  What they pin: the
precedence when shapes overlap; the zones are the set in use's only; a day without a stored outer layer uses the raw
warning polygons and one with its warnings counted does not (as the map does); the local day's edges in summer;
reports below the type's minimum magnitude and reports of another type are left out; and the answer for a day
with no reports.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.queries import findings, radar_map
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
D1, D2 = date(1999, 6, 8), date(1999, 6, 9)
ENV = "ST_MakeEnvelope(%s, %s, %s, %s, 4326)"

# One 0.1 degree box per layer, side by side along latitude 40.0, so a point is in exactly one unless a
# test overlaps them on purpose.
CORE = (-105.0, 40.0, -104.9, 40.1)
MARGIN = (-104.9, 40.0, -104.8, 40.1)
INDICATED = (-104.8, 40.0, -104.7, 40.1)
OUTER = (-104.7, 40.0, -104.6, 40.1)
NOWHERE = (-104.55, 40.05)


def centre(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class FindingsBase(unittest.TestCase):
    """The rolled-back transaction and the row builders; the tests are in the classes below."""

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

    def add_day(self, day, status="loaded", max_mm=None, box=(1, 1, 2, 2)):
        """box is (lon_min, lat_min, lon_max, lat_max), the crop box the day was loaded with."""
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, box_lon_min, "
            "box_lon_max, floor_mm, cells_in_box, max_mm) VALUES (%s, %s, %s, now(), 'k', %s, %s, %s, %s, 12.7, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (day, self.run_id, status, box[3], box[1], box[0], box[2],
             None if status == "missing" else 5, max_mm))                  # a missing day holds nothing

    def add_build(self, day, set_id=None, warnings_used=0, patches=1, anchored=1, max_mm=None, box=(1, 1, 2, 2)):
        self.add_day(day, max_mm=max_mm, box=box)
        self.conn.execute(
            "INSERT INTO radar_zone_builds (day, set_id, window_start, window_end, cells_over_threshold, patches, "
            "patches_anchored, anchor_reports, warnings_used) VALUES (%s, %s, '1999-06-08 06:00+00', "
            "'1999-06-09 06:00+00', 1, %s, %s, 1, %s)", (day, set_id or self.set_id, patches, anchored, warnings_used))

    def add_build_row_only(self, day, set_id):
        self.conn.execute(
            "INSERT INTO radar_zone_builds (day, set_id, window_start, window_end, cells_over_threshold, patches, "
            "patches_anchored, anchor_reports, warnings_used) VALUES (%s, %s, '1999-06-08 06:00+00', "
            "'1999-06-09 06:00+00', 1, 1, 1, 1, 0)", (day, set_id))

    def add_zone(self, day, layer, box, set_id=None):
        self.conn.execute(
            "INSERT INTO radar_zones (day, set_id, layer, geom, area_km2) "
            f"SELECT %s, %s, %s, g, ST_Area(g::geography) / 1e6 FROM (SELECT ST_Multi({ENV}) AS g) q",
            (day, set_id or self.set_id, layer, *box))

    def add_warning(self, begin, end, box=OUTER):
        self.serial += 1
        self.conn.execute(
            "INSERT INTO warning_polygons (wfo, phenomena, significance, eventid, vtec_year, poly_begin, poly_end, "
            "load_id, geom) VALUES ('BOU', 'SV', 'W', %s, 1999, %s, %s, %s, ST_Multi(" + ENV + "))",
            (self.serial, begin, end, self.load_id, *box))

    def add_report(self, when, lonlat, size=1.5, report_type="H", report_text="HAIL", stagger=True):
        self.serial += 1
        if stagger:
            when = when + timedelta(minutes=self.serial)    # the table's natural key refuses two identical reports
        self.conn.execute(
            "INSERT INTO iem_data (utc_datetime, latitude, longitude, magnitude, nws_issuer, report_type, report_text, "
            "ingested_at) VALUES (%s, %s, %s, %s, 'BOU', %s, %s, now())",
            (when, lonlat[1], lonlat[0], size, report_type, report_text))

    def noon(self, day):
        return datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC)   # 14:00 in Denver

    def built_day(self, day=D1):
        """All four layers stored, side by side."""
        self.add_build(day)
        for layer, box in (("core", CORE), ("margin", MARGIN), ("radar_indicated", INDICATED), ("outer", OUTER)):
            self.add_zone(day, layer, box)

    def counts(self, day=D1, **kw):
        return findings.reports_by_layer(self.conn, day, **kw)

    def non_roof_type(self):
        """A report type that is not roof relevant (dust, cold), for the guard that the matcher also applies."""
        row = self.conn.execute("SELECT report_type, report_text FROM report_types WHERE NOT roof_relevant "
                                "AND min_magnitude IS NULL ORDER BY 1, 2 LIMIT 1").fetchone()
        if row is None:
            self.skipTest("every report type is roof relevant")
        return row["report_type"], row["report_text"]



class ReportsByLayerTest(FindingsBase):
    def test_each_report_is_counted_once_under_the_layer_it_is_in(self):
        self.built_day()
        for box in (CORE, CORE, MARGIN, INDICATED, OUTER):
            self.add_report(self.noon(D1), centre(box))
        self.add_report(self.noon(D1), NOWHERE)
        got = self.counts()
        self.assertEqual(got["by_layer"], {"core": 2, "margin": 1, "radar_indicated": 1, "outer": 1, "none": 1})
        self.assertEqual(got["total"], 6)

    def test_the_highest_layer_wins_where_shapes_overlap(self):
        self.add_build(D1)
        for layer in ("outer", "radar_indicated", "margin", "core"):
            self.add_zone(D1, layer, CORE)
        self.add_report(self.noon(D1), centre(CORE))
        self.assertEqual(self.counts()["by_layer"], {"core": 1, "margin": 0, "radar_indicated": 0, "outer": 0, "none": 0})

    def test_margin_beats_the_two_below_it(self):
        self.add_build(D1)
        for layer in ("outer", "radar_indicated", "margin"):
            self.add_zone(D1, layer, CORE)
        self.add_report(self.noon(D1), centre(CORE))
        self.assertEqual(self.counts()["by_layer"]["margin"], 1)

    def test_radar_indicated_beats_the_warnings(self):
        self.add_build(D1)
        for layer in ("outer", "radar_indicated"):
            self.add_zone(D1, layer, CORE)
        self.add_report(self.noon(D1), centre(CORE))
        self.assertEqual(self.counts()["by_layer"]["radar_indicated"], 1)

    def test_zones_built_with_another_set_are_not_used(self):
        old = self.set_id
        new = self.conn.execute(
            "INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
            "VALUES ('newer', 2.0, 3.0, 5.0) RETURNING set_id").fetchone()["set_id"]
        self.add_build(D1, set_id=old)
        self.add_zone(D1, "core", CORE, set_id=old)
        self.add_report(self.noon(D1), centre(CORE))
        self.assertEqual(self.counts()["by_layer"]["none"], 1)
        self.assertNotEqual(old, new)

    def test_a_day_with_no_stored_outer_layer_uses_the_raw_warning_polygons(self):
        self.add_build(D1, warnings_used=None)          # warnings were not fetched when it was built
        self.add_zone(D1, "core", CORE)
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        self.add_report(self.noon(D1), centre(OUTER))
        self.assertEqual(self.counts()["by_layer"]["outer"], 1)

    def test_a_day_whose_warnings_were_counted_ignores_raw_polygons(self):
        self.add_build(D1, warnings_used=0)
        self.add_zone(D1, "core", CORE)
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        self.add_report(self.noon(D1), centre(OUTER))
        self.assertEqual(self.counts()["by_layer"]["none"], 1)

    def test_a_day_with_no_build_still_sees_the_raw_warnings(self):
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        self.add_report(self.noon(D1), centre(OUTER))
        self.assertEqual(self.counts()["by_layer"]["outer"], 1)

    def test_the_local_day_runs_from_denver_midnight_to_midnight(self):
        self.built_day(D1)
        self.built_day(D2)
        c = centre(CORE)
        self.add_report(datetime(1999, 6, 8, 5, 59, tzinfo=UTC), c, stagger=False)    # 23:59 on the 7th in Denver
        self.add_report(datetime(1999, 6, 8, 6, 0, tzinfo=UTC), c, stagger=False)     # 00:00 on the 8th
        self.add_report(datetime(1999, 6, 9, 5, 59, tzinfo=UTC), c, stagger=False)     # 23:59 on the 8th
        self.add_report(datetime(1999, 6, 9, 6, 0, tzinfo=UTC), c, stagger=False)     # 00:00 on the 9th
        self.assertEqual(self.counts(D1)["total"], 2)
        self.assertEqual(self.counts(D2)["total"], 1)

    def test_a_report_below_the_types_minimum_is_left_out(self):
        self.built_day()
        self.add_report(self.noon(D1), centre(CORE), size=0.75)
        self.add_report(self.noon(D1), centre(CORE), size=1.0)
        got = self.counts()
        self.assertEqual((got["total"], got["by_layer"]["core"]), (1, 1))

    def test_a_type_that_is_not_roof_relevant_is_left_out_even_when_asked_for(self):
        rtype, rtext = self.non_roof_type()
        self.built_day()
        self.add_report(self.noon(D1), centre(CORE), size=1.5, report_type=rtype, report_text=rtext)
        self.assertEqual(self.counts(report_text=rtext)["total"], 0)

    def test_a_report_of_another_type_is_left_out(self):
        self.built_day()
        self.add_report(self.noon(D1), centre(CORE), size=60, report_type="G", report_text="TSTM WND GST")
        self.assertEqual(self.counts()["total"], 0)

    def test_the_largest_is_the_largest_counted_report_as_a_float(self):
        self.built_day()
        self.add_report(self.noon(D1), centre(CORE), size=1.25)
        self.add_report(self.noon(D1), NOWHERE, size=2.5)
        self.add_report(self.noon(D1), centre(CORE), size=0.5)          # too small to count
        got = self.counts()
        self.assertEqual(got["largest_in"], 2.5)
        self.assertIsInstance(got["largest_in"], float)

    def test_a_day_with_no_reports_says_so(self):
        self.built_day()
        got = self.counts()
        self.assertEqual(got["total"], 0)
        self.assertIsNone(got["largest_in"])
        self.assertEqual(got["by_layer"], {"core": 0, "margin": 0, "radar_indicated": 0, "outer": 0, "none": 0})


class DayOverviewTest(FindingsBase):
    """day_overview: what the radar job stored for one day, and why a day may have nothing to show."""

    def overview(self, day=D1):
        return findings.day_overview(self.conn, day)

    def test_a_built_day_reports_its_build_its_areas_and_the_largest_radar_value_in_inches(self):
        self.add_build(D1, patches=7, anchored=4, max_mm=63.5)
        self.add_zone(D1, "core", CORE)
        self.add_zone(D1, "margin", MARGIN)
        got = self.overview()
        self.assertEqual((got["status"], got["built"], got["built_other_set"]), ("loaded", True, False))
        self.assertEqual((got["patches"], got["patches_anchored"]), (7, 4))
        self.assertEqual(got["max_in"], 2.5)
        self.assertIsInstance(got["max_in"], float)
        self.assertAlmostEqual(got["areas_km2"]["core"], self.area("core"))
        self.assertAlmostEqual(got["areas_km2"]["margin"], self.area("margin"))
        self.assertEqual((got["areas_km2"]["radar_indicated"], got["areas_km2"]["outer"]), (0.0, 0.0))

    def area(self, layer, day=D1):
        return float(self.conn.execute("SELECT area_km2 FROM radar_zones WHERE day = %s AND layer = %s AND set_id = %s",
                                       (day, layer, self.set_id)).fetchone()["area_km2"])

    def test_a_day_with_no_row_has_no_status(self):
        got = self.overview()
        self.assertEqual((got["status"], got["built"], got["built_other_set"]), (None, False, False))
        self.assertEqual((got["patches"], got["max_in"]), (None, None))

    def test_a_missing_day_and_a_suspect_day_say_so(self):
        self.add_day(D1, status="missing")
        self.add_day(D2, status="suspect", max_mm=1007.0)
        self.assertEqual(self.overview(D1)["status"], "missing")
        got = self.overview(D2)
        self.assertEqual((got["status"], got["built"]), ("suspect", False))

    def test_a_day_built_only_with_another_set_is_named_not_shown(self):
        old = self.set_id
        self.conn.execute("INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
                          "VALUES ('newer', 2.0, 3.0, 5.0)")
        self.add_build(D1, set_id=old)
        self.add_zone(D1, "core", CORE, set_id=old)
        got = self.overview()
        self.assertEqual((got["built"], got["built_other_set"]), (False, True))
        self.assertEqual(got["areas_km2"]["core"], 0.0)

    def test_a_day_built_with_both_the_old_and_the_new_set_is_not_called_older(self):
        old = self.set_id
        new = self.conn.execute("INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
                                "VALUES ('newer', 2.0, 3.0, 5.0) RETURNING set_id").fetchone()["set_id"]
        self.add_build(D1, set_id=old)
        self.add_build_row_only(D1, new)
        got = self.overview()
        self.assertEqual((got["built"], got["built_other_set"]), (True, False))

    def test_the_warnings_valid_during_the_local_day_are_counted(self):
        self.add_warning(datetime(1999, 6, 8, 20, tzinfo=UTC), datetime(1999, 6, 8, 21, tzinfo=UTC))
        self.add_warning(datetime(1999, 6, 12, 20, tzinfo=UTC), datetime(1999, 6, 12, 21, tzinfo=UTC))
        self.assertEqual(self.overview()["warnings"], 1)


class CircleCoverageTest(FindingsBase):
    """circle_coverage: the match-radius circles around the day's reports against the radar's core and margin."""

    AT = (-104.95, 40.05)
    BIG = (-105.1, 39.9, -104.8, 40.2)           # holds a 5 km circle at AT with room to spare
    EAST_HALF = (-104.95, 39.9, -104.8, 40.2)    # from the circle's centre eastward

    def cover(self, radius_m=5000, day=D1):
        return findings.circle_coverage(self.conn, day, radius_m)

    def box_km2(self, box):
        return float(self.conn.execute(
            f"SELECT ST_Area({ENV}::geography) / 1e6 AS a", box).fetchone()["a"])

    def test_a_circle_wholly_inside_the_core_is_confirmed_and_the_rest_of_the_core_is_beyond_it(self):
        self.add_build(D1)
        self.add_zone(D1, "core", self.BIG)
        self.add_report(self.noon(D1), self.AT)
        got = self.cover()
        self.assertAlmostEqual(got["circle_km2"], 3.14159 * 25, delta=0.8)
        self.assertGreater(got["confirmed_share"], 0.999)
        self.assertAlmostEqual(got["beyond_km2"], self.box_km2(self.BIG) - got["circle_km2"], delta=0.5)

    def test_half_a_circle_in_the_core_is_half_confirmed(self):
        self.add_build(D1)
        self.add_zone(D1, "core", self.EAST_HALF)
        self.add_report(self.noon(D1), self.AT)
        self.assertAlmostEqual(self.cover()["confirmed_share"], 0.5, delta=0.01)

    def test_the_margin_confirms_too(self):
        self.add_build(D1)
        self.add_zone(D1, "margin", self.BIG)
        self.add_report(self.noon(D1), self.AT)
        self.assertGreater(self.cover()["confirmed_share"], 0.999)

    def test_radar_indicated_and_the_warnings_do_not_confirm(self):
        self.add_build(D1)
        self.add_zone(D1, "radar_indicated", self.BIG)
        self.add_zone(D1, "outer", self.BIG)
        self.add_report(self.noon(D1), self.AT)
        got = self.cover()
        self.assertEqual((got["confirmed_share"], got["beyond_km2"]), (0.0, 0.0))

    def test_overlapping_circles_are_counted_once(self):
        self.add_build(D1)
        self.add_zone(D1, "core", self.BIG)
        self.add_report(self.noon(D1), self.AT)
        self.add_report(self.noon(D1), self.AT)
        self.assertAlmostEqual(self.cover()["circle_km2"], 3.14159 * 25, delta=0.8)

    def test_the_radius_is_the_one_asked_for(self):
        self.add_build(D1)
        self.add_zone(D1, "core", self.BIG)
        self.add_report(self.noon(D1), self.AT)
        self.assertAlmostEqual(self.cover(1000)["circle_km2"], 3.14159, delta=0.05)

    def test_a_report_exactly_at_the_minimum_draws_a_circle(self):
        self.add_build(D1)
        self.add_zone(D1, "core", self.BIG)
        self.add_report(self.noon(D1), self.AT, size=1.0)
        self.assertGreater(self.cover()["circle_km2"], 70)

    def test_only_reports_inside_the_local_day_draw_circles(self):
        self.add_build(D1)
        self.add_zone(D1, "core", self.BIG)
        self.add_report(datetime(1999, 6, 8, 5, 59, tzinfo=UTC), self.AT, stagger=False)   # 23:59 on the 7th
        self.add_report(datetime(1999, 6, 9, 6, 0, tzinfo=UTC), self.AT, stagger=False)    # 00:00 on the 9th
        self.assertEqual(self.cover()["circle_km2"], 0.0)
        self.add_report(datetime(1999, 6, 8, 6, 0, tzinfo=UTC), self.AT, stagger=False)    # 00:00 on the 8th, inside
        self.assertGreater(self.cover()["circle_km2"], 70)

    def test_a_type_that_is_not_roof_relevant_draws_no_circle(self):
        rtype, rtext = self.non_roof_type()
        self.add_build(D1)
        self.add_zone(D1, "core", self.BIG)
        self.add_report(self.noon(D1), self.AT, report_type=rtype, report_text=rtext)
        self.assertEqual(findings.circle_coverage(self.conn, D1, 5000, report_text=rtext)["circle_km2"], 0.0)

    def test_a_report_below_the_minimum_draws_no_circle(self):
        self.add_build(D1)
        self.add_zone(D1, "core", self.BIG)
        self.add_report(self.noon(D1), self.AT, size=0.5)
        got = self.cover()
        self.assertEqual((got["circle_km2"], got["confirmed_share"]), (0.0, None))

    def test_no_reports_means_no_share_and_all_the_core_is_beyond(self):
        self.add_build(D1)
        self.add_zone(D1, "core", CORE)
        self.add_zone(D1, "margin", MARGIN)
        got = self.cover()
        self.assertIsNone(got["confirmed_share"])
        self.assertAlmostEqual(got["beyond_km2"], self.box_km2(CORE) + self.box_km2(MARGIN), delta=1.0)

    def test_a_day_with_circles_and_no_zones_confirms_nothing(self):
        self.add_report(self.noon(D1), self.AT)
        got = self.cover()
        self.assertEqual((got["confirmed_share"], got["beyond_km2"]), (0.0, 0.0))


class ListingMatrixTest(FindingsBase):
    """listing_matrix: held Active listings by the radar layer at the address and the distance to the nearest report.

    Built in the mid-Pacific, where the database holds no real listings, so only what a test inserts lands in
    the matrix.  The two counts that are global by nature (outside the crop box, no coordinates) are compared
    before and after instead.
    """

    BOX = (-151.0, 19.0, -149.0, 21.0)                 # lon_min, lat_min, lon_max, lat_max
    P_CORE = (-150.5, 20.0, -150.4, 20.1)
    P_MARGIN = (-150.4, 20.0, -150.3, 20.1)
    P_INDICATED = (-150.3, 20.0, -150.2, 20.1)
    P_OUTER = (-150.2, 20.0, -150.1, 20.1)
    P_NOWHERE = (-150.05, 20.05)
    REPORT = (-150.8, 20.05)                           # no zone near it, so distance tests land in the 'none' row
    MILE = 1609.344
    BANDS = ("le_1", "1_3", "3_5", "over_5")

    def setUp(self):
        super().setUp()
        self.add_build(D1, box=self.BOX)

    def east_of(self, lonlat, metres):
        r = self.conn.execute("SELECT ST_X(g) AS x, ST_Y(g) AS y FROM (SELECT ST_Project(ST_MakePoint(%s, %s)::geography, "
                              "%s, radians(90))::geometry AS g) q", (lonlat[0], lonlat[1], metres)).fetchone()
        return (r["x"], r["y"])

    def add_property(self, lonlat, property_type="Single Family"):
        self.serial += 1
        rid = f"test-{self.serial}"
        self.conn.execute(
            "INSERT INTO properties (rentcast_id, property_address, list_longitude, list_latitude, property_type) "
            "VALUES (%s, %s, %s, %s, %s)",
            (rid, f"{self.serial} Test St", None if lonlat is None else lonlat[0],
             None if lonlat is None else lonlat[1], property_type))
        return rid

    def add_list(self, rid, status="Active", list_type="Standard", list_date=None):
        self.serial += 1
        self.conn.execute(
            "INSERT INTO listings (rentcast_id, list_status, list_type, list_date) VALUES (%s, %s, %s, %s)",
            (rid, status, list_type, list_date or datetime(1999, 5, 1, tzinfo=UTC) + timedelta(days=self.serial)))

    def add_house(self, lonlat, **kw):
        prop = {k: kw.pop(k) for k in ("property_type",) if k in kw}
        rid = self.add_property(lonlat, **prop)
        self.add_list(rid, **kw)
        return rid

    def matrix(self):
        return findings.listing_matrix(self.conn, D1)

    def cell(self, got, layer, band):
        return got["cells"][layer][band]

    def test_a_listing_is_placed_in_the_row_of_the_layer_it_is_in(self):
        for layer, box in (("core", self.P_CORE), ("margin", self.P_MARGIN),
                           ("radar_indicated", self.P_INDICATED), ("outer", self.P_OUTER)):
            self.add_zone(D1, layer, box)
            self.add_house(centre(box))
        self.add_house(self.P_NOWHERE)
        got = self.matrix()
        for layer in ("core", "margin", "radar_indicated", "outer", "none"):
            self.assertEqual(self.cell(got, layer, "over_5"), 1, layer)
        self.assertEqual(got["total"], 5)

    def test_the_highest_layer_wins_where_shapes_overlap(self):
        for layer in ("outer", "radar_indicated", "margin", "core"):
            self.add_zone(D1, layer, self.P_CORE)
        self.add_house(centre(self.P_CORE))
        got = self.matrix()
        self.assertEqual((self.cell(got, "core", "over_5"), got["total"]), (1, 1))

    def test_a_day_with_no_stored_outer_layer_uses_the_raw_warning_polygons(self):
        self.add_build(D2, warnings_used=None, box=self.BOX)
        self.add_warning(datetime(1999, 6, 9, 20, tzinfo=UTC), datetime(1999, 6, 9, 21, tzinfo=UTC), box=self.P_OUTER)
        self.add_house(centre(self.P_OUTER))
        self.assertEqual(self.cell(findings.listing_matrix(self.conn, D2), "outer", "over_5"), 1)

    def test_the_distance_bands_break_at_one_three_and_five_miles(self):
        self.add_report(self.noon(D1), self.REPORT)
        for miles, metres_off in ((1, -1), (1, +2), (3, -1), (3, +2), (5, -1), (5, +2)):
            self.add_house(self.east_of(self.REPORT, miles * self.MILE + metres_off))
        got = self.matrix()
        self.assertEqual([self.cell(got, "none", b) for b in self.BANDS], [1, 2, 2, 1])

    def test_the_nearest_report_decides(self):
        self.add_report(self.noon(D1), self.east_of(self.REPORT, 4 * self.MILE))
        self.add_report(self.noon(D1), self.east_of(self.REPORT, 4 * self.MILE + 500))
        self.add_house(self.east_of(self.REPORT, 4 * self.MILE + 1000))
        got = self.matrix()
        self.assertEqual([self.cell(got, "none", b) for b in self.BANDS], [1, 0, 0, 0])

    def test_with_no_report_everything_is_over_five(self):
        self.add_house(self.REPORT)
        self.assertEqual(self.cell(self.matrix(), "none", "over_5"), 1)

    def test_reports_that_are_too_small_the_wrong_type_or_another_day_are_not_counted(self):
        spot = self.east_of(self.REPORT, 100)
        self.add_report(self.noon(D1), self.REPORT, size=0.5)
        self.add_report(self.noon(D1), self.REPORT, size=60, report_type="G", report_text="TSTM WND GST")
        self.add_report(self.noon(D2), self.REPORT)
        self.add_house(spot)
        self.assertEqual(self.cell(self.matrix(), "none", "over_5"), 1)

    def test_a_report_exactly_at_the_minimum_counts(self):
        self.add_report(self.noon(D1), self.REPORT, size=1.0)
        self.add_house(self.east_of(self.REPORT, 100))
        self.assertEqual(self.cell(self.matrix(), "none", "le_1"), 1)

    def test_only_reports_inside_the_local_day_count(self):
        near = self.east_of(self.REPORT, 100)
        self.add_report(datetime(1999, 6, 8, 5, 59, tzinfo=UTC), self.REPORT, stagger=False)    # the 7th
        self.add_report(datetime(1999, 6, 9, 6, 0, tzinfo=UTC), self.REPORT, stagger=False)     # the 9th
        self.add_house(near)
        self.assertEqual(self.cell(self.matrix(), "none", "over_5"), 1)
        self.add_report(datetime(1999, 6, 8, 6, 0, tzinfo=UTC), self.REPORT, stagger=False)     # first instant of the 8th
        self.assertEqual(self.cell(self.matrix(), "none", "le_1"), 1)

    def test_a_type_that_is_not_roof_relevant_does_not_count(self):
        rtype, rtext = self.non_roof_type()
        self.add_report(self.noon(D1), self.REPORT, report_type=rtype, report_text=rtext)
        self.add_house(self.east_of(self.REPORT, 100))
        got = findings.listing_matrix(self.conn, D1, report_text=rtext)
        self.assertEqual(self.cell(got, "none", "over_5"), 1)

    def test_only_active_listings_that_are_not_new_construction_or_land_are_held(self):
        self.add_house(self.REPORT, status="Inactive")
        self.add_house(self.REPORT, list_type="New Construction")
        self.add_house(self.REPORT, property_type="Land")
        self.assertEqual(self.matrix()["total"], 0)

    def test_a_listing_with_no_recorded_type_or_property_type_is_still_held(self):
        self.add_house(self.REPORT, list_type=None, property_type=None)
        self.assertEqual(self.matrix()["total"], 1)

    def test_a_house_listed_twice_is_counted_once(self):
        rid = self.add_house(self.REPORT)
        self.add_list(rid)
        self.assertEqual(self.matrix()["total"], 1)

    def test_a_house_with_one_held_listing_and_one_not_is_counted_once(self):
        rid = self.add_house(self.REPORT)
        self.add_list(rid, status="Inactive")
        self.assertEqual(self.matrix()["total"], 1)

    def test_a_house_outside_the_crop_box_is_counted_apart_not_in_the_matrix(self):
        before = self.matrix()["outside_box"]
        self.add_house((-148.0, 20.0))
        got = self.matrix()
        self.assertEqual((got["outside_box"] - before, got["total"]), (1, 0))

    def test_a_house_with_no_coordinates_is_counted_apart_not_in_the_matrix(self):
        before = self.matrix()["no_location"]
        self.add_house(None)
        got = self.matrix()
        self.assertEqual((got["no_location"] - before, got["total"]), (1, 0))

    def test_a_day_with_no_loaded_radar_has_no_matrix(self):
        self.add_house(self.REPORT)
        self.add_day(D2, status="missing")
        self.add_day(date(1999, 6, 10), status="suspect", max_mm=1007.0)
        self.assertIsNone(findings.listing_matrix(self.conn, D2))
        self.assertIsNone(findings.listing_matrix(self.conn, date(1999, 6, 10)))
        self.assertIsNone(findings.listing_matrix(self.conn, date(1999, 6, 20)))

    def test_every_row_and_band_is_present_even_when_empty(self):
        got = self.matrix()
        self.assertEqual(list(got["cells"]), ["core", "margin", "radar_indicated", "outer", "none"])
        for row in got["cells"].values():
            self.assertEqual(list(row), list(self.BANDS))
        self.assertEqual(got["total"], 0)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class DayFlagsTest(unittest.TestCase):
    """day_flags is pure: an overview and a reports answer in, the names of what to warn about out."""

    def overview(self, **kw):
        base = {"status": "loaded", "built": True, "built_other_set": False,
                "areas_km2": {"core": 5.0, "margin": 3.0, "radar_indicated": 0.0, "outer": 0.0}}
        base.update(kw)
        return base

    def reports(self, total=3):
        return {"total": total}

    def flags(self, overview=None, reports=None, day=date(2024, 6, 8)):
        return findings.day_flags(overview or self.overview(), reports or self.reports(), day)

    def test_a_normal_day_has_none(self):
        self.assertEqual(self.flags(), [])

    def test_a_suspect_day_is_named_and_is_not_also_called_unbuilt(self):
        self.assertEqual(self.flags(self.overview(status="suspect", built=False)), ["suspect_day"])

    def test_a_day_built_with_another_set_is_named_and_is_not_also_called_unbuilt(self):
        self.assertEqual(self.flags(self.overview(built=False, built_other_set=True)), ["older_set"])

    def test_a_missing_day_and_a_day_with_no_row_have_no_radar(self):
        self.assertEqual(self.flags(self.overview(status="missing", built=False)), ["no_radar"])
        self.assertEqual(self.flags(self.overview(status=None, built=False)), ["no_radar"])

    def test_no_actionable_reports_is_named(self):
        self.assertEqual(self.flags(reports=self.reports(0)), ["no_reports"])

    def test_a_single_report_is_not_no_reports(self):
        self.assertEqual(self.flags(reports=self.reports(1)), [])

    def test_cold_season_radar_hail_with_no_reports_is_named(self):
        ov = self.overview(areas_km2={"core": 0.0, "margin": 0.0, "radar_indicated": 9.0, "outer": 0.0})
        self.assertEqual(self.flags(ov, self.reports(0), date(2024, 1, 15)), ["no_reports", "cold_season_unreported"])

    def test_the_cold_season_is_october_through_march(self):
        ov = self.overview(areas_km2={"core": 0.0, "margin": 0.0, "radar_indicated": 9.0, "outer": 0.0})
        cold = {d: "cold_season_unreported" in self.flags(ov, self.reports(0), d)
                for d in (date(2024, 3, 31), date(2024, 4, 1), date(2024, 9, 30), date(2024, 10, 1))}
        self.assertEqual(cold, {date(2024, 3, 31): True, date(2024, 4, 1): False,
                                date(2024, 9, 30): False, date(2024, 10, 1): True})

    def test_cold_season_needs_radar_indicated_area_and_no_reports(self):
        ov = self.overview(areas_km2={"core": 0.0, "margin": 0.0, "radar_indicated": 9.0, "outer": 0.0})
        self.assertEqual(self.flags(ov, self.reports(2), date(2024, 1, 15)), [])
        self.assertEqual(self.flags(self.overview(), self.reports(0), date(2024, 1, 15)), ["no_reports"])


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class DraftTiersTest(unittest.TestCase):
    """draft_tiers: the DRAFT ladder from the 2026-10-22 direction, shown as an annotation on the matrix only.

    Report proximity (<= 1 mi 3, <= 3 mi 2, <= 5 mi 1, none 0) plus radar layer (core 3, margin 2, radar-indicated 1,
    warnings or none 0).  5 to 6 is A, 3 to 4 is B, 1 to 2 is C, 0 is no letter.  Whether radar with no report
    within 5 miles is capped at C is undecided (it is not capped here: core over 5 miles is a 3, a B).
    """

    def setUp(self):
        self.tiers = findings.draft_tiers()

    def test_it_has_every_row_and_band(self):
        self.assertEqual(list(self.tiers), ["core", "margin", "radar_indicated", "outer", "none"])
        for row in self.tiers.values():
            self.assertEqual(list(row), ["le_1", "1_3", "3_5", "over_5"])

    def test_the_corners(self):
        t = self.tiers
        self.assertEqual(t["core"]["le_1"], "A")             # 6
        self.assertEqual(t["margin"]["le_1"], "A")           # 5
        self.assertEqual(t["core"]["1_3"], "A")              # 5
        self.assertEqual(t["radar_indicated"]["le_1"], "B")  # 4
        self.assertEqual(t["core"]["over_5"], "B")           # 3, not capped
        self.assertEqual(t["none"]["le_1"], "B")             # 3
        self.assertEqual(t["margin"]["3_5"], "B")            # 3
        self.assertEqual(t["margin"]["over_5"], "C")         # 2
        self.assertEqual(t["none"]["1_3"], "C")              # 2
        self.assertEqual(t["radar_indicated"]["over_5"], "C")  # 1
        self.assertEqual(t["outer"]["3_5"], "C")             # 1
        self.assertEqual(t["none"]["over_5"], "")            # 0
        self.assertEqual(t["outer"]["over_5"], "")           # 0

    def test_a_letter_never_falls_as_either_input_rises(self):
        rank = {"": 0, "C": 1, "B": 2, "A": 3}
        rows = ["none", "radar_indicated", "margin", "core"]          # rising radar points (outer = none = 0)
        bands = ["over_5", "3_5", "1_3", "le_1"]                      # rising report points
        for r in range(len(rows)):
            for b in range(len(bands)):
                here = rank[self.tiers[rows[r]][bands[b]]]
                if r + 1 < len(rows):
                    self.assertLessEqual(here, rank[self.tiers[rows[r + 1]][bands[b]]])
                if b + 1 < len(bands):
                    self.assertLessEqual(here, rank[self.tiers[rows[r]][bands[b + 1]]])


if __name__ == "__main__":
    unittest.main()
