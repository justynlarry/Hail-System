"""Tests for the radar layers on the Territory page: the /map/radar.geojson route and the page around the map.

    docker compose run --rm -T --no-deps -e PGHOST -e FLASK_SECRET_KEY -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_radar_map_web

The real app renders the real templates against the real database; the radar and storm reads are stubbed where a
test needs known data, and a few tests read the dev database's real storm days (and skip if they are not there).
Sign-in is by writing the session.  What they pin: the route needs a login, takes one day inside the window or all
of them, and refuses a bad or outside day; the page shows the day picker, opens on the newest real storm, carries
the radius setting to the map (item 216) and the numbers of the parameter set in use; with nothing to show it says
so; and nothing radar-derived can reach an email, an export, a pull or the matcher.
"""

import ast
import os
import re
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.web import create_app, views
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
SET = {"set_id": 1, "name": "Initial placeholders", "hail_threshold_in": 1.0, "margin_km": 3.0, "report_radius_km": 5.0}


def day_row(day, built=True, core=0.0, warnings=0, outer=0.0):
    return {"day": day, "built": built, "patches": 1, "patches_anchored": 1, "warnings_used": warnings,
            "core_km2": core, "margin_km2": 0.0, "radar_indicated_km2": 0.0, "outer_km2": outer, "warnings": warnings}


STORM = day_row(date(2026, 6, 8), core=1802.5, warnings=49, outer=14894.5)
QUIET = day_row(date(2026, 6, 9), core=0.0, warnings=19, outer=6670.0)
OLD = day_row(date(2006, 5, 31), built=False, warnings=28)
CITY = {"area_name": "Denver", "report_text": "HAIL", "zip_count": 3, "day_count": 2, "report_count": 5,
        "max_magnitude": 2.75, "mag_unit": "inches", "first_day": date(2026, 6, 1), "last_day": date(2026, 6, 8)}
ZIP = {"zcta5": "80202", "area_name": "Denver", "report_text": "HAIL", "report_count": 5, "nearest_miles": 1.2,
       "max_magnitude": 2.75, "mag_unit": "inches", "first_report": datetime(2026, 6, 1, tzinfo=UTC),
       "last_report": datetime(2026, 6, 8, tzinfo=UTC)}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class Base(unittest.TestCase):
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

    def client(self, role=None):
        role = role or next(iter(self.users))
        if role not in self.users:
            self.skipTest(f"no active {role} user")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.users[role], role, "test"
            s["issued_at"] = datetime.now(UTC).isoformat()
        return client

    def patch(self, target, name, **kw):
        p = mock.patch.object(target, name, **kw)
        self.addCleanup(p.stop)
        return p.start()


class RouteTest(Base):
    def stub(self, picker=(STORM, QUIET)):
        self.picker = self.patch(views.radar_map, "picker_days", return_value=list(picker))
        self.zones = self.patch(views.radar_map, "zones_geojson",
                                return_value={"type": "FeatureCollection", "features": [], "days": [], "set": SET,
                                              "built_with_other_set": []})

    def test_signed_out_goes_to_login(self):
        self.stub()
        resp = self.app.test_client().get("/map/radar.geojson")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.zones.assert_not_called()

    def test_every_signed_in_role_may_read_it(self):
        self.stub()
        for role in ("viewer", "sender", "admin"):
            if role in self.users:
                with self.subTest(role=role):
                    resp = self.client(role).get("/map/radar.geojson")
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp.mimetype, "application/json")
                    self.assertEqual(resp.get_json()["type"], "FeatureCollection")

    def test_without_a_day_it_draws_every_day_in_the_window_that_has_anything(self):
        self.stub()
        self.client().get("/map/radar.geojson?start=2026-06-01&end=2026-06-30")
        self.assertEqual(self.picker.call_args[0][1:3], (date(2026, 6, 1), date(2026, 6, 30)))
        self.assertEqual(self.zones.call_args[0][1], [date(2026, 6, 8), date(2026, 6, 9)])

    def test_with_a_day_it_draws_that_day_only(self):
        self.stub()
        resp = self.client().get("/map/radar.geojson?start=2026-06-01&end=2026-06-30&day=2026-06-08")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.zones.call_args[0][1], [date(2026, 6, 8)])
        self.picker.assert_not_called()

    def test_the_window_edges_are_inside(self):
        self.stub()
        for day in ("2026-06-01", "2026-06-30"):
            with self.subTest(day=day):
                self.assertEqual(self.client().get(f"/map/radar.geojson?start=2026-06-01&end=2026-06-30&day={day}").status_code, 200)

    def test_a_day_outside_the_window_or_a_bad_one_is_refused(self):
        self.stub()
        for day in ("2026-05-31", "2026-07-01", "not-a-date", "2026-13-01", "", "06/08/2026"):
            with self.subTest(day=day):
                resp = self.client().get(f"/map/radar.geojson?start=2026-06-01&end=2026-06-30&day={day}")
                self.assertEqual(resp.status_code, 400)
        self.zones.assert_not_called()

    def test_the_report_type_filter_does_not_change_the_radar(self):
        self.stub()
        self.client().get("/map/radar.geojson?start=2026-06-01&end=2026-06-30&type=TORNADO&submitted=1")
        self.assertEqual(self.zones.call_args[0][1], [date(2026, 6, 8), date(2026, 6, 9)])

    def test_a_huge_window_is_clamped_not_an_error(self):
        self.stub()
        self.assertEqual(self.client().get("/map/radar.geojson?start=1990-01-01&end=2026-06-30").status_code, 200)

    def test_it_only_reads(self):
        self.stub()
        with mock.patch.object(views, "get_connection") as conn:
            conn.return_value.__enter__.return_value.commit = mock.Mock()
            self.client().get("/map/radar.geojson")
            conn.return_value.__enter__.return_value.commit.assert_not_called()

    def test_the_real_storm_day_comes_back_with_its_four_layers(self):
        resp = self.client().get("/map/radar.geojson?start=2026-06-01&end=2026-06-30&day=2026-06-08")
        data = resp.get_json()
        if not data["features"]:
            self.skipTest("2026-06-08 has not been loaded into this database")
        self.assertEqual([f["properties"]["layer"] for f in data["features"]], ["outer", "radar_indicated", "margin", "core"])
        self.assertEqual(data["days"], ["2026-06-08"])
        self.assertEqual(data["built_with_other_set"], [])
        core = data["features"][-1]
        self.assertAlmostEqual(core["properties"]["area_km2"], 1802.5, delta=1)

    def test_the_real_window_union_has_the_same_layers_and_more_days(self):
        resp = self.client().get("/map/radar.geojson?start=2026-06-01&end=2026-06-30")
        data = resp.get_json()
        if len(data["days"]) < 2:
            self.skipTest("June 2026 has not been loaded into this database")
        self.assertIn("core", [f["properties"]["layer"] for f in data["features"]])
        self.assertGreater(len(data["days"]), 1)


class PageTest(Base):
    def stub(self, picker=(STORM, QUIET), rows=(CITY,), setting=None):
        self.patch(views.radar_map, "picker_days", return_value=list(picker))
        self.patch(views.radar_map, "in_use_set", return_value=dict(SET))
        self.patch(views.storms, "fetch_cities", return_value=list(rows))
        self.patch(views.storms, "fetch_zips", return_value=[ZIP])
        self.patch(views.storms, "fetch_report_types", return_value=["HAIL", "TORNADO"])
        if setting:
            from hailsys.web import auth
            original = auth.fetch_settings
            self.patch(auth, "fetch_settings", side_effect=lambda conn: {**original(conn), **setting})

    def page(self, path="/territory?start=2026-06-01&end=2026-06-30"):
        resp = self.client().get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp.get_data(as_text=True)

    def options(self, text):
        return re.findall(r'<option value="([^"]*)"\s*(selected)?\s*>([^<]*)</option>',
                          re.search(r'<select id="radar-day">(.*?)</select>', text, re.S).group(1))

    def test_the_picker_offers_all_days_then_each_day_with_its_numbers(self):
        self.stub()
        opts = self.options(self.page())
        self.assertEqual([o[0] for o in opts], ["all", "2026-06-08", "2026-06-09"])
        self.assertEqual(opts[0][2], "All days in window (2)")
        self.assertEqual(opts[1][2], "2026-06-08 — core 1,802 km², 49 warnings")
        self.assertEqual(opts[2][2], "2026-06-09 — core 0 km², 19 warnings")

    def test_a_day_without_radar_says_so_and_one_warning_is_singular(self):
        one = dict(OLD, warnings=1)
        self.stub(picker=(one,))
        self.assertEqual(self.options(self.page())[1][2], "2006-05-31 — 1 warning, no radar")

    def test_it_opens_on_the_newest_real_storm_not_the_newest_quiet_day(self):
        self.stub(picker=(QUIET, STORM))                       # QUIET is newer but has no core
        opts = self.options(self.page())
        self.assertEqual([o[0] for o in opts if o[1]], ["2026-06-08"])

    def test_with_no_core_anywhere_it_opens_on_the_newest_built_day(self):
        self.stub(picker=(OLD, QUIET))
        self.assertEqual([o[0] for o in self.options(self.page()) if o[1]], ["2026-06-09"])

    def test_with_only_warning_days_it_opens_on_the_newest(self):
        self.stub(picker=(OLD, dict(OLD, day=date(2006, 5, 30))))
        self.assertEqual([o[0] for o in self.options(self.page()) if o[1]], ["2006-05-31"])

    def test_nothing_in_the_window_says_so_and_has_no_picker_or_legend(self):
        self.stub(picker=())
        text = self.page()
        self.assertIn("No radar or NWS warnings in this window.", text)
        self.assertNotIn('id="radar-day"', text)
        self.assertNotIn("radar-legend", text)
        self.assertIn('id="map"', text)                       # the map and the circles still work

    def test_the_map_carries_the_radius_setting_and_the_radar_url(self):
        self.stub()
        text = self.page()
        self.assertIn('data-match-radius-m="8046.7"', text)
        self.assertIn('data-radar-url="/map/radar.geojson"', text)

    def test_a_changed_radius_setting_changes_the_circle_radius(self):
        self.stub(setting={"match_radius_miles": 3.0, "zip_radius_miles": 5.0})
        self.assertIn('data-match-radius-m="4828.0"', self.page())

    def test_the_legend_states_the_numbers_of_the_set_in_use(self):
        self.stub()
        text = self.page()
        self.assertIn("1.00 in or more with a report within 5 km", text)
        self.assertIn("3 km around the core", text)
        self.assertIn("parameter set 1", text)
        self.assertIn("Staff only: not used for emails, exports, pulls or matching.", text)

    def test_the_legend_follows_whatever_set_is_in_use(self):
        self.stub()
        self.patch(views.radar_map, "in_use_set", return_value={"set_id": 7, "name": "Calibrated", "hail_threshold_in": 1.5,
                                                                "margin_km": 2.5, "report_radius_km": 4.0})
        text = self.page()
        self.assertIn("1.50 in or more with a report within 4 km", text)
        self.assertIn("2.5 km around the core", text)
        self.assertIn("parameter set 7", text)
        self.assertIn("(Calibrated)", text)

    def test_the_legend_has_the_four_layers_and_the_circles_note(self):
        self.stub()
        text = self.page()
        for name in ("Core", "Margin", "Radar-indicated", "NWS warnings"):
            self.assertIn(f"<strong>{name}</strong>", text)
        self.assertIn("Circles show the match radius", text)

    def test_both_groupings_have_the_map_exactly_once(self):
        self.stub()
        for path in ("/territory?start=2026-06-01&end=2026-06-30",
                     "/territory?start=2026-06-01&end=2026-06-30&group_by=zip"):
            with self.subTest(path=path):
                text = self.page(path)
                self.assertEqual(text.count('id="map"'), 1)
                self.assertEqual(text.count('id="radar-day"'), 1)

    def test_a_window_with_no_storm_rows_has_no_map_and_no_picker(self):
        self.stub(rows=())
        text = self.page()
        self.assertIn("Nothing Matches These Filters", text)
        self.assertNotIn('id="map"', text)

    def test_the_script_is_loaded_and_the_storm_days_page_has_no_map(self):
        self.stub()
        self.assertIn("map.js", self.page())
        self.assertNotIn('id="radar-day"', self.client().get("/").get_data(as_text=True))


class IsolationTest(unittest.TestCase):
    """Nothing radar-derived reaches an email, an export, a pull or the matcher."""

    RADAR_NAMES = re.compile(r"hailsys\.radar|radar_map|queries import[^\n]*\bradar\b|radar_zones|radar_cells|radar_days|"
                             r"radar_zone_builds|warning_polygons|warning_loads|radar_parameter_sets|"
                             r"queries\.findings|queries import[^\n]*\bfindings\b|"
                             r"hailsys\.likelihood|likelihood_map|likelihood_zones|likelihood_builds|"
                             r"likelihood_parameter_sets|likelihood_level_rules|likelihood_runs")

    def files(self, *globs):
        out = []
        for pattern in globs:
            out += sorted(ROOT.glob(pattern))
        self.assertTrue(out, globs)
        return out

    def test_email_matching_rentcast_and_constantcontact_code_never_mentions_the_radar(self):
        for path in self.files("hailsys/email/**/*.py", "hailsys/matching/**/*.py", "hailsys/rentcast/**/*.py",
                               "hailsys/constantcontact/**/*.py", "hailsys/email/templates/**/*"):
            if path.is_file():
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertIsNone(self.RADAR_NAMES.search(path.read_text()))

    def test_the_other_query_modules_never_mention_the_radar(self):
        # findings.py is the slice 6 panel, staff only;  likelihoodadmin.py edits the parameter sets (slice 7, admin only)
        radar = {"radar.py", "radar_map.py", "findings.py", "likelihood_map.py", "likelihoodadmin.py"}
        for path in self.files("hailsys/queries/*.py"):
            if path.name not in radar:
                with self.subTest(path=path.name):
                    self.assertIsNone(self.RADAR_NAMES.search(path.read_text()))

    def test_only_the_admin_blueprint_uses_the_parameter_set_editor(self):
        users = set()
        for path in self.files("hailsys/**/*.py"):
            if path.name != "likelihoodadmin.py" and "likelihoodadmin" in path.read_text():
                users.add(str(path.relative_to(ROOT)))
        self.assertEqual(users, {"hailsys/web/admin.py"} if users else set())

    def test_no_csv_or_export_route_touches_the_radar(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        seen = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and re.search(r"csv|export", node.name):
                seen += 1
                with self.subTest(route=node.name):
                    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                    names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                    self.assertFalse(names & {"radar", "radar_map", "zones_geojson", "picker_days", "findings", "likelihood_map"}, names)
        self.assertGreaterEqual(seen, 4)

    def test_the_pull_and_match_routes_do_not_touch_the_radar(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        seen = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and re.search(r"pull|match|send", node.name):
                seen += 1
                with self.subTest(route=node.name):
                    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                    names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                    self.assertFalse(names & {"radar", "radar_map", "zones_geojson", "picker_days", "findings", "likelihood_map"}, names)
        self.assertGreaterEqual(seen, 3)

    def test_only_the_findings_route_uses_the_findings_reads(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        users = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and n.id == "findings":
                        users.add(node.name)
        self.assertEqual(users, {"territory_findings"})

    def test_only_the_territory_view_and_the_radar_routes_use_the_map_reads(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        users = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and n.id == "radar_map":
                        users.add(node.name)
        self.assertEqual(users, {"territory", "map_radar", "map_likelihood", "territory_findings"})

    def test_only_the_territory_view_and_the_likelihood_route_use_the_likelihood_reads(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        users = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and n.id == "likelihood_map":
                        users.add(node.name)
        self.assertEqual(users, {"territory", "map_likelihood"})


if __name__ == "__main__":
    unittest.main()
