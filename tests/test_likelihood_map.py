"""Tests for hailsys/queries/likelihood_map.py and the /map/likelihood.geojson route (Phase 5c, step 1).

Needs a role that can insert the radar and likelihood tables (hail_admin) for the query tests;  the route tests
need the app's environment and read the dev database:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-); FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_likelihood_map

What they pin:  one feature per level that has anything, bottom first, with the area and the number of parts;  several
days are one union;  a day built with an older set is named and not drawn, and one with no build draws nothing;  no
days or no set is an empty collection;  the page and the route: signed out goes to login, every signed-in role may
read it, a day outside the window or not a date is a 400, the day is passed on, no day means the picker's days;  the
page carries the url, the legend wording (levels rank, no probability, attribution) and the set in use.
"""

import os
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.likelihood import build
    from hailsys.queries import likelihood_map
    from hailsys.radar import mesh
    from hailsys.web import create_app, views
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
DENVER = ZoneInfo("America/Denver")
D1, D2 = date(1999, 6, 8), date(1999, 6, 9)
ROW, COL = 1525, 2500


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class QueryTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('radar_parameter_sets', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert parameter sets (hail_admin)")
        run = self.conn.execute("INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        radar_set = self.conn.execute(
            "INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
            "VALUES ('t', 1.0, 3.0, 5.0) RETURNING set_id").fetchone()["set_id"]
        self.set_id = self.new_set(radar_set)
        for day in (D1, D2):
            self.conn.execute(
                "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
                "box_lon_min, box_lon_max, floor_mm, cells_in_box, max_mm) VALUES (%s, %s, 'loaded', now(), 'k', "
                "41.5, 36.5, -109.7, -101.4, 12.7, 1000, NULL)", (day, run))
        self.radar_set = radar_set

    def new_set(self, radar_set):
        new = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "VALUES ('map test', %s, 1.0, 1, 3, 5) RETURNING set_id", (radar_set,)).fetchone()["set_id"]
        self.conn.execute(
            "INSERT INTO likelihood_level_rules (set_id, layer, band, level) "
            "SELECT %s, layer, band, level FROM likelihood_level_rules WHERE set_id = "
            "(SELECT min(set_id) FROM likelihood_parameter_sets)", (new,))
        return new

    def storm(self, day):
        """A line of radar cells with a report at its west end, so Agree and One source both exist."""
        for c in range(20):
            self.conn.execute(
                "INSERT INTO radar_cells (day, grid_row, grid_col, mm, geom) VALUES (%s, %s, %s, 30, "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (day, ROW, COL + c, mesh.LO1 + (COL + c) * mesh.STEP, mesh.LA1 - ROW * mesh.STEP))
        self.conn.execute(
            "INSERT INTO iem_data (utc_datetime, latitude, longitude, magnitude, nws_issuer, report_type, report_text, "
            "ingested_at) VALUES (%s, %s, %s, 1.5, 'TEST', 'H', 'HAIL', now())",
            (datetime(day.year, day.month, day.day, 18, tzinfo=UTC), round(mesh.LA1 - ROW * mesh.STEP, 6),
             round(mesh.LO1 + COL * mesh.STEP, 6)))
        build.build_day(self.conn, day, DENVER, set_id=self.set_id)

    def test_one_feature_per_level_bottom_first_with_area_and_parts(self):
        self.storm(D1)
        got = likelihood_map.zones_geojson(self.conn, [D1])
        levels = [f["properties"]["level"] for f in got["features"]]
        self.assertEqual(levels, [lv for lv in likelihood_map.LEVELS if lv in levels])
        self.assertTrue({"agree", "one_source"} <= set(levels))
        for f in got["features"]:
            self.assertGreater(f["properties"]["area_km2"], 0)
            self.assertEqual(f["properties"]["parts"], 1)
            self.assertIn(f["geometry"]["type"], ("Polygon", "MultiPolygon"))
        self.assertEqual(got["days"], ["1999-06-08"])
        self.assertEqual(got["set"]["set_id"], self.set_id)
        self.assertEqual(got["built_with_other_set"], [])

    def test_several_days_are_one_union_with_the_parts_counted(self):
        self.storm(D1)
        self.storm(D2)
        got = likelihood_map.zones_geojson(self.conn, [D2, D1, D1])
        self.assertEqual(got["days"], ["1999-06-08", "1999-06-09"])
        self.assertTrue(all(f["properties"]["parts"] == 2 for f in got["features"]))
        one = likelihood_map.zones_geojson(self.conn, [D1])
        agree = lambda g: next(f["properties"]["area_km2"] for f in g["features"] if f["properties"]["level"] == "agree")
        self.assertAlmostEqual(agree(got), agree(one) * 1.0, delta=agree(one) * 0.01)       # the same place twice

    def test_a_day_built_with_an_older_set_is_named_and_not_drawn(self):
        self.storm(D1)
        newer = self.new_set(self.radar_set)
        got = likelihood_map.zones_geojson(self.conn, [D1])
        self.assertEqual(got["set"]["set_id"], newer)
        self.assertEqual(got["features"], [])
        self.assertEqual(got["built_with_other_set"], ["1999-06-08"])

    def test_a_day_with_no_build_draws_nothing_and_is_not_named(self):
        got = likelihood_map.zones_geojson(self.conn, [D2])
        self.assertEqual((got["features"], got["built_with_other_set"]), ([], []))

    def test_no_days_is_an_empty_collection(self):
        got = likelihood_map.zones_geojson(self.conn, [])
        self.assertEqual((got["type"], got["features"], got["days"]), ("FeatureCollection", [], []))

    def test_the_set_in_use_is_the_newest_with_float_numbers(self):
        got = likelihood_map.in_use_set(self.conn)
        self.assertEqual(got["set_id"], self.set_id)
        self.assertEqual((got["cocorahs_min_in"], got["band_1_mi"], got["band_3_mi"]), (1.0, 1.0, 5.0))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            cls.app = create_app()

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(row_factory=dict_row) as conn:
            self.users = {}
            for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active ORDER BY emp_id"):
                self.users.setdefault(r["role"], r["emp_id"])
        picker = [{"day": date(2026, 6, 8), "built": True, "core_km2": 100.0, "warnings": 1},
                  {"day": date(2026, 6, 9), "built": True, "core_km2": 0.0, "warnings": 0}]
        self.picker = self.patch(views.radar_map, "picker_days", return_value=picker)
        self.zones = self.patch(views.likelihood_map, "zones_geojson",
                                return_value={"type": "FeatureCollection", "features": [], "days": [],
                                              "set": None, "built_with_other_set": []})

    def patch(self, target, name, **kw):
        p = mock.patch.object(target, name, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def client(self, role=None):
        role = role or next(iter(self.users))
        if role not in self.users:
            self.skipTest(f"no active {role} user")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.users[role], role, "test"
            s["issued_at"] = datetime.now(UTC).isoformat()
        return client

    def test_signed_out_goes_to_login(self):
        resp = self.app.test_client().get("/map/likelihood.geojson")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.zones.assert_not_called()

    def test_every_signed_in_role_may_read_it(self):
        for role in ("viewer", "sender", "admin"):
            if role in self.users:
                with self.subTest(role=role):
                    resp = self.client(role).get("/map/likelihood.geojson")
                    self.assertEqual((resp.status_code, resp.mimetype), (200, "application/json"))

    def test_without_a_day_it_draws_the_days_the_radar_picker_offers(self):
        self.client().get("/map/likelihood.geojson?start=2026-06-01&end=2026-06-30")
        self.assertEqual(self.picker.call_args[0][1:3], (date(2026, 6, 1), date(2026, 6, 30)))
        self.assertEqual(self.zones.call_args[0][1], [date(2026, 6, 8), date(2026, 6, 9)])

    def test_one_day_inside_the_window_is_passed_on(self):
        self.client().get("/map/likelihood.geojson?start=2026-06-01&end=2026-06-30&day=2026-06-08")
        self.assertEqual(self.zones.call_args[0][1], [date(2026, 6, 8)])

    def test_a_day_outside_the_window_or_not_a_date_is_a_400(self):
        for day in ("2026-07-01", "2026-05-31", "yesterday", "2026-13-01", ""):
            with self.subTest(day=day):
                resp = self.client().get(f"/map/likelihood.geojson?start=2026-06-01&end=2026-06-30&day={day}")
                self.assertEqual(resp.status_code, 400)
        self.zones.assert_not_called()

    def test_the_page_carries_the_url_the_legend_and_the_set_in_use(self):
        self.patch(views.radar_map, "in_use_set", return_value={
            "set_id": 1, "name": "Radar set", "hail_threshold_in": 1.0, "margin_km": 3.0, "report_radius_km": 5.0})
        self.patch(views.likelihood_map, "in_use_set", return_value={
            "set_id": 7, "name": "Map set", "cocorahs_min_in": 1.0, "band_1_mi": 1.0, "band_2_mi": 3.0, "band_3_mi": 5.0})
        text = self.client().get("/territory?start=2026-06-01&end=2026-06-30").get_data(as_text=True)
        self.assertIn('data-likelihood-url="/map/likelihood.geojson"', text)
        self.assertIn("Hail likelihood", text)
        for level in ("Agree", "One source", "Context"):
            self.assertIn(f"<strong>{level}</strong>", text)
        self.assertIn("not probabilities", text)
        self.assertIn("CC BY 4.0", text)
        self.assertIn("Likelihood set 7 (Map set)", text)
        self.assertIn('id="likelihood-status"', text)
        self.assertNotRegex(text, r"\d+ ?% (chance|likely|likelihood)")        # no percentage is ever shown as a likelihood


if __name__ == "__main__":
    unittest.main()
