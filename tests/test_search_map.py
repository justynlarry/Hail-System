"""The Address Search map (item 313):  hailsys/queries/address_map.py and the map on /search.

    docker compose --profile test run --rm -T test python -m unittest tests.test_search_map

The size bands and the default day need no database.  The rest run on one rolled-back connection, as
tests/test_search_jurisdiction.py does, with TEMPORARY tables that shadow municipal_boundaries,
county_boundaries, radar_zones and likelihood_zones on it, so nothing real is read or touched.

What they pin:  the band edges (1, 1.75 and 2.5 in go up a band; wind, no size and NaN are 'other');  the default
day is the newest with Core or Agree at the address, else the newest;  the lines are clipped to the box and named
by kind;  a day is listed when a zone touches the circle, with what is at the point, and not when it is outside
the circle, outside the dates or under another set;  a matched search draws the map with the point, the radius
and the picker opened on the right day;  no zones says so;  a search with no match draws no map;  the address
cannot close the data's script element.
"""

import json
import os
import re
import sys
import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hailsys.formatting import magnitude
from hailsys.queries import address_map

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.queries import likelihood_map, radar_map
    from hailsys.settings import fetch_settings
    from hailsys.tuning import miles_to_metres
    from hailsys.web import create_app, auth, search as search_web
    from tests.test_jurisdiction import TESTA, TESTB, XVILLE
    from tests.test_search_jurisdiction import TestConn
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

POINT = (-150.07, 10.07)                                   # lon, lat:  in Xville and Testa
AROUND = "MULTIPOLYGON(((-150.075 10.065, -150.065 10.065, -150.065 10.075, -150.075 10.075, -150.075 10.065)))"
NEAR = "MULTIPOLYGON(((-150.066 10.069, -150.064 10.069, -150.064 10.071, -150.066 10.071, -150.066 10.069)))"
FAR = "MULTIPOLYGON(((-150.04 10.06, -150.03 10.06, -150.03 10.08, -150.04 10.08, -150.04 10.06)))"
# NEAR is about 440 m east of POINT and does not contain it;  FAR is about 3.3 km east.


class BandTest(unittest.TestCase):
    def test_the_edges_go_up_a_band(self):
        for value, band in (("0.75", "h1"), ("0.99", "h1"), ("1.00", "h2"), ("1.74", "h2"),
                            ("1.75", "h3"), ("2.49", "h3"), ("2.50", "h4"), ("4.50", "h4")):
            with self.subTest(value=value):
                self.assertEqual(address_map.hail_band("HAIL", Decimal(value), "inches"), band)

    def test_anything_but_sized_hail_is_other(self):
        self.assertEqual(address_map.hail_band("HAIL", None, "inches"), "other")
        self.assertEqual(address_map.hail_band("HAIL", Decimal("NaN"), "inches"), "other")
        self.assertEqual(address_map.hail_band("TSTM WND GST", Decimal("70"), "mph"), "other")

    def test_the_bands_are_the_four_the_css_colours(self):
        self.assertEqual([b["key"] for b in address_map.HAIL_BANDS], ["h1", "h2", "h3", "h4"])

    def test_a_report_row_is_formatted_for_the_script(self):
        row = {"latitude": 39.7, "longitude": -104.8, "local_time": datetime(2026, 6, 1, 15, 4),
               "report_text": "HAIL", "magnitude": Decimal("1.75"), "mag_unit": "inches",
               "distance_miles": 2.345, "report_source": "Trained Spotter"}
        self.assertEqual(address_map.report_point(row), {
            "lat": 39.7, "lon": -104.8, "local_time": "2026-06-01 15:04", "report_text": "HAIL",
            "magnitude_display": magnitude(Decimal("1.75"), "inches"), "distance": "2.3 mi",
            "report_source": "Trained Spotter", "band": "h3"})


class DefaultDayTest(unittest.TestCase):
    def day(self, d, radar=None, level=None):
        return {"day": date(2026, 6, d), "radar": radar, "level": level}

    def test_core_or_agree_at_the_address_beats_a_newer_day(self):
        days = [self.day(9, "outer", "context"), self.day(5, "margin", "agree"), self.day(1, "core")]
        self.assertEqual(address_map.default_day(days), date(2026, 6, 5))

    def test_without_either_it_is_the_newest(self):
        self.assertEqual(address_map.default_day([self.day(9), self.day(5, "margin")]), date(2026, 6, 9))

    def test_no_days_is_none(self):
        self.assertIsNone(address_map.default_day([]))


def coordinates(geometry):
    """Every [lon, lat] in a GeoJSON geometry, however deeply nested."""
    out = []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            out.append(c)
        else:
            for x in c:
                walk(x)
    walk(geometry["coordinates"])
    return out


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class SearchMapTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.raw.close)
        self.addCleanup(self.raw.rollback)
        row = self.raw.execute("SELECT emp_id FROM users WHERE role = 'admin' AND is_active "
                               "ORDER BY emp_id LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no active admin")
        self.emp_id = row["emp_id"]
        lk = likelihood_map.in_use_set(self.raw)
        if lk is None:
            self.skipTest("no likelihood parameter set")
        self.likelihood_set = lk["set_id"]
        self.radar_set = radar_map.in_use_set(self.raw)["set_id"]
        self.raw.execute("CREATE TEMPORARY TABLE municipal_boundaries ("
                         " place_fips CHAR(5), name TEXT, geom geometry(MultiPolygon, 4326),"
                         " loaded_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        self.raw.execute("CREATE TEMPORARY TABLE county_boundaries ("
                         " county_fips CHAR(5), name TEXT, geom geometry(MultiPolygon, 4326))")
        self.raw.execute("CREATE TEMPORARY TABLE radar_zones ("
                         " day DATE, set_id BIGINT, layer TEXT, geom geometry(MultiPolygon, 4326))")
        self.raw.execute("CREATE TEMPORARY TABLE likelihood_zones ("
                         " day DATE, set_id BIGINT, level TEXT, geom geometry(MultiPolygon, 4326))")
        for row in (TESTA, TESTB):
            self.raw.execute("INSERT INTO county_boundaries VALUES (%s, %s, ST_GeomFromText(%s, 4326))", row)
        self.raw.execute("INSERT INTO municipal_boundaries (place_fips, name, geom) "
                         "VALUES (%s, %s, ST_GeomFromText(%s, 4326))", XVILLE)
        self.conn = TestConn(self.raw)
        for module in (search_web, auth):
            patcher = mock.patch.object(module, "get_connection", return_value=self.conn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tag = uuid.uuid4().hex[:8]

    # ---- helpers ----

    def radar(self, day, layer, wkt, set_id=None):
        self.raw.execute("INSERT INTO radar_zones VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326))",
                         (day, set_id or self.radar_set, layer, wkt))

    def likelihood(self, day, level, wkt):
        self.raw.execute("INSERT INTO likelihood_zones VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326))",
                         (day, self.likelihood_set, level, wkt))

    def page(self, found=True, matched=None):
        address = f"101 Map{self.tag} St, Denver, CO 80202"
        lon, lat = POINT
        result = ([{"matched_address": matched or address.upper(), "latitude": lat, "longitude": lon,
                    "tiger_line_id": None}] if found else [])
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.emp_id, "viewer", "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        with mock.patch.object(search_web.geocode, "geocode", return_value=result):
            resp = client.get("/search/", query_string={"address": address,
                                                        "start": "2026-06-01", "end": "2026-06-30"})
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def map_data(self, page):
        m = re.search(r'<script type="application/json" id="search-map-data">(.*?)</script>', page, re.S)
        self.assertIsNotNone(m, "no map data in the page")
        return json.loads(m.group(1))

    # ---- the queries ----

    def test_days_touching_the_circle_are_listed_with_what_is_at_the_point(self):
        self.radar(date(2026, 6, 1), "margin", AROUND)
        self.radar(date(2026, 6, 1), "core", AROUND)
        self.likelihood(date(2026, 6, 1), "agree", AROUND)
        self.radar(date(2026, 6, 2), "radar_indicated", NEAR)           # inside the circle, not at the point
        self.radar(date(2026, 6, 3), "core", FAR)                       # outside the circle
        self.radar(date(2026, 5, 1), "core", AROUND)                    # outside the dates
        self.radar(date(2026, 6, 4), "core", AROUND, set_id=self.radar_set + 1000)   # another set
        self.likelihood(date(2026, 6, 5), "one_source", NEAR)           # a likelihood-only day
        lon, lat = POINT
        days = address_map.radar_days(self.raw, lat, lon, 1000, date(2026, 6, 1), date(2026, 6, 30),
                                      self.radar_set, self.likelihood_set)
        self.assertEqual([(d["day"], d["radar"], d["level"]) for d in days],
                         [(date(2026, 6, 5), None, None), (date(2026, 6, 2), None, None),
                          (date(2026, 6, 1), "core", "agree")])

    def test_the_lines_are_clipped_to_the_box_and_named_by_kind(self):
        lon, lat = POINT
        lines = address_map.boundaries_geojson(self.raw, lat, lon, 1000)      # a box about 2 km each way
        self.assertEqual([(f["properties"]["kind"], f["properties"]["name"]) for f in lines["features"]],
                         [("county", "Testa"), ("municipal", "Xville")])     # Testb starts 7.7 km east
        for f in lines["features"]:
            for x, y in coordinates(f["geometry"]):
                self.assertTrue(-150.09 < x < -150.05 and 10.05 < y < 10.09, (x, y))

    # ---- the page ----

    def test_a_matched_search_draws_the_map_opened_on_the_core_day(self):
        self.radar(date(2026, 6, 1), "core", AROUND)
        self.likelihood(date(2026, 6, 1), "agree", AROUND)
        self.radar(date(2026, 6, 20), "outer", AROUND)                  # newer, but only a warning
        page = self.page()
        flat = " ".join(page.split())
        self.assertIn('<option value="2026-06-01" selected>2026-06-01 — Core, Agree at the address</option>', flat)
        self.assertIn('<option value="2026-06-20" >2026-06-20 — NWS warning at the address</option>', flat)
        self.assertIn("<summary>Hail likelihood</summary>", page)
        self.assertNotIn("Circles show the match radius", page)          # Territory's note, not this map's
        data = self.map_data(page)
        radius = fetch_settings(self.raw)["match_radius_miles"]
        self.assertEqual((data["lon"], data["lat"]), POINT)
        self.assertEqual(data["radius_m"], round(float(miles_to_metres(radius)), 1))
        self.assertEqual(data["reports"], [])                           # no IEM reports in the Pacific
        self.assertIn(("municipal", "Xville"),
                      [(f["properties"]["kind"], f["properties"]["name"]) for f in data["boundaries"]["features"]])

    def test_no_zones_says_so_and_has_no_picker(self):
        page = self.page()
        self.assertIn("No radar or hail-likelihood zones within", page)
        self.assertNotIn('id="radar-day"', page)
        self.assertIn('id="map"', page)

    def test_a_search_with_no_match_draws_no_map(self):
        page = self.page(found=False)
        self.assertNotIn('id="map"', page)
        self.assertNotIn("search-map-data", page)

    def test_the_address_cannot_close_the_script_element(self):
        hostile = "1 A ST </script><img src=x onerror=alert(1)>"
        page = self.page(matched=hostile)
        self.assertNotIn("</script><img", page)
        self.assertEqual(self.map_data(page)["address"], hostile)
