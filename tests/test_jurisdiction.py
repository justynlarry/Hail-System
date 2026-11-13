"""The jurisdiction lookup (hailsys/queries/jurisdiction.py, item 190 step 1).

    docker compose --profile test run --rm -T test python -m unittest tests.test_jurisdiction

Runs as hail_app, which can only read the boundary tables.  So the fixture tests make TEMPORARY
tables named municipal_boundaries and county_boundaries:  Postgres looks in the session's
pg_temp schema before public for an unqualified name, so the module reads the fixtures, and
nothing real is touched.  Everything is rolled back.  The fixtures sit in the Pacific (about 10 N,
150 W), where at this latitude 0.001 degree is about 110 m of longitude.

    Testa county   lon -150.10 .. -150.00, lat 10.00 .. 10.10
    Testb county   lon -150.00 .. -149.90, same latitudes (shares Testa's east line)
    Xville city    lon -150.08 .. -150.02, lat 10.02 .. 10.08, inside Testa,
                   with an unincorporated hole lon -150.055 .. -150.045, lat 10.045 .. 10.055

What they pin:  inside a city, far from lines;  unincorporated, nearest a county line;  just outside
and just inside a city line;  a shared county line names the county the point is not in;  the hole
is unincorporated and its edge is a line;  two overlapping cities are both listed and near;  no
line within the reach is None;  empty municipal_boundaries is "not loaded", not unincorporated;
the distance has one decimal place, and the flag agrees with it at the setting's edge.

The known-answer tests read the real tables, and skip when they are empty:  Civic Center Park
is Denver;  Highlands Ranch is unincorporated Douglas County (decision log 2026-09-23).
"""

import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.queries import jurisdiction
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


def square(w, s, e, n, hole=None):
    ring = f"({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s})"
    if hole:
        hw, hs, he, hn = hole
        ring += f", ({hw} {hs}, {hw} {hn}, {he} {hn}, {he} {hs}, {hw} {hs})"
    return f"MULTIPOLYGON(({ring}))"


TESTA = ("99001", "Testa", square(-150.10, 10.00, -150.00, 10.10))
TESTB = ("99002", "Testb", square(-150.00, 10.00, -149.90, 10.10))
XVILLE = ("99901", "Xville",
          square(-150.08, 10.02, -150.02, 10.08, hole=(-150.055, 10.045, -150.045, 10.055)))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class FixtureLookupTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.conn.execute("CREATE TEMPORARY TABLE municipal_boundaries ("
                          " place_fips CHAR(5), name TEXT, geom geometry(MultiPolygon, 4326),"
                          " loaded_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        self.conn.execute("CREATE TEMPORARY TABLE county_boundaries ("
                          " county_fips CHAR(5), name TEXT, geom geometry(MultiPolygon, 4326))")

    def city(self, row):
        self.conn.execute("INSERT INTO municipal_boundaries (place_fips, name, geom) "
                          "VALUES (%s, %s, ST_GeomFromText(%s, 4326))", row)

    def county(self, row):
        self.conn.execute("INSERT INTO county_boundaries (county_fips, name, geom) "
                          "VALUES (%s, %s, ST_GeomFromText(%s, 4326))", row)

    def standard(self):
        self.county(TESTA)
        self.county(TESTB)
        self.city(XVILLE)

    def look(self, lon, lat, near_m=150):
        return jurisdiction.lookup(self.conn, lat, lon, near_m)

    def assertLine(self, got, kind, name, low, high):
        line = got["nearest_line"]
        self.assertIsNotNone(line)
        self.assertEqual((line["kind"], line["name"]), (kind, name))
        self.assertTrue(low <= line["distance_m"] <= high, line["distance_m"])

    # ---- the answers ----

    def test_inside_a_city_far_from_any_line(self):
        self.standard()
        got = self.look(-150.07, 10.07)
        self.assertTrue(got["loaded"])
        self.assertEqual(got["places"], [{"place_fips": "99901", "name": "Xville"}])
        self.assertEqual(got["county"], {"county_fips": "99001", "name": "Testa"})
        self.assertLine(got, "municipal", "Xville", 1085, 1105)     # the west edge, 0.01 deg
        self.assertFalse(got["near_boundary"])
        self.assertEqual(got["near_boundary_m"], 150)
        self.assertIsNotNone(got["boundaries_loaded_at"])

    def test_unincorporated_nearest_a_county_line(self):
        self.standard()
        got = self.look(-150.09, 10.01)
        self.assertEqual(got["places"], [])
        self.assertEqual(got["county"]["name"], "Testa")
        self.assertLine(got, "county", "Testa", 1085, 1105)
        self.assertFalse(got["near_boundary"])

    def test_just_outside_a_city_line_is_near(self):
        self.standard()
        got = self.look(-150.0805, 10.05)
        self.assertEqual(got["places"], [])
        self.assertLine(got, "municipal", "Xville", 50, 60)
        self.assertTrue(got["near_boundary"])

    def test_just_inside_a_city_line_is_near(self):
        self.standard()
        got = self.look(-150.0795, 10.05)
        self.assertEqual([p["name"] for p in got["places"]], ["Xville"])
        self.assertLine(got, "municipal", "Xville", 50, 60)
        self.assertTrue(got["near_boundary"])

    def test_a_shared_county_line_names_the_county_the_point_is_not_in(self):
        self.standard()
        got = self.look(-149.999, 10.01)
        self.assertEqual(got["county"]["name"], "Testb")
        self.assertLine(got, "county", "Testa", 105, 115)
        self.assertTrue(got["near_boundary"])

    def test_a_hole_in_a_city_is_unincorporated(self):
        self.standard()
        got = self.look(-150.05, 10.05)
        self.assertEqual(got["places"], [])
        self.assertEqual(got["county"]["name"], "Testa")
        self.assertLine(got, "municipal", "Xville", 540, 556)
        self.assertFalse(got["near_boundary"])

    def test_the_edge_of_a_hole_is_a_line(self):
        self.standard()
        got = self.look(-150.0455, 10.05)
        self.assertEqual(got["places"], [])
        self.assertLine(got, "municipal", "Xville", 50, 60)
        self.assertTrue(got["near_boundary"])

    def test_two_overlapping_cities_are_both_listed_and_near(self):
        self.standard()
        self.city(("99902", "Yburg", square(-150.075, 10.065, -150.065, 10.075)))
        got = self.look(-150.07, 10.07)
        self.assertEqual([p["name"] for p in got["places"]], ["Xville", "Yburg"])
        self.assertGreater(got["nearest_line"]["distance_m"], 150)   # near because of the overlap
        self.assertTrue(got["near_boundary"])

    def test_no_line_within_the_reach_is_none(self):
        self.city(XVILLE)                                            # far away, so "loaded"
        self.county(("99003", "Testbig", square(-140.5, 10.0, -139.5, 11.0)))
        got = self.look(-140.0, 10.5)
        self.assertEqual(got["county"]["name"], "Testbig")
        self.assertIsNone(got["nearest_line"])
        self.assertFalse(got["near_boundary"])

    def test_outside_every_county_has_no_county(self):
        self.standard()
        got = self.look(-150.20, 10.05)
        self.assertEqual(got["places"], [])
        self.assertIsNone(got["county"])

    def test_empty_municipal_boundaries_is_not_loaded(self):
        self.county(TESTA)
        got = self.look(-150.09, 10.01)
        self.assertEqual(got, {"loaded": False, "boundaries_loaded_at": None, "places": [],
                               "county": None, "nearest_line": None, "near_boundary": False,
                               "near_boundary_m": 150})

    # ---- the number the flag is made from ----

    def test_the_distance_is_a_decimal_to_one_place(self):
        self.standard()
        d = self.look(-150.0805, 10.05)["nearest_line"]["distance_m"]
        self.assertIsInstance(d, Decimal)
        self.assertEqual(d.as_tuple().exponent, -1)

    def test_the_flag_agrees_with_the_rounded_distance_at_the_setting(self):
        # jurisdiction_checks re-derives the flag from the stored, rounded number, so the
        # module must decide on that same number:  equal is near, one metre under is not.
        self.standard()
        d = self.look(-150.0805, 10.05)["nearest_line"]["distance_m"]
        edge = int(d.to_integral_value(rounding="ROUND_CEILING"))
        self.assertTrue(self.look(-150.0805, 10.05, near_m=edge)["near_boundary"])
        self.assertFalse(self.look(-150.0805, 10.05, near_m=edge - 1)["near_boundary"])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class KnownAnswerTest(unittest.TestCase):
    """The real boundary tables.  These catch what synthetic squares cannot:  a load in the wrong
    SRID (TIGER ships NAD83, CLAUDE.md), or DOLA's place codes not being what the code expects."""

    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if self.conn.execute("SELECT count(*) AS n FROM municipal_boundaries").fetchone()["n"] == 0:
            self.skipTest("municipal_boundaries is not loaded on this database")

    def test_civic_center_park_is_denver(self):
        got = jurisdiction.lookup(self.conn, 39.7372, -104.9879, 150)
        self.assertEqual([p["place_fips"] for p in got["places"]], ["20000"])
        self.assertEqual(got["county"]["county_fips"], "08031")

    def test_highlands_ranch_is_unincorporated_douglas(self):
        got = jurisdiction.lookup(self.conn, 39.5530, -104.9690, 150)
        self.assertEqual(got["places"], [])
        self.assertEqual(got["county"]["county_fips"], "08035")


if __name__ == "__main__":
    unittest.main()
