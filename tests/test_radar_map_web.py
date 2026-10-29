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
                             r"likelihood_parameter_sets|likelihood_level_rules|likelihood_runs|"
                             r"zonepull|zone_zips|zone_estimate|zonematch|match_zones|zone_match|"
                             r"zone_pull_start|pull_zips|zone_set_id")

    def files(self, *globs):
        out = []
        for pattern in globs:
            # Not __pycache__:  a stray file there failed an unrelated guard (parking-lot item 263).
            out += sorted(p for p in ROOT.glob(pattern) if "__pycache__" not in p.parts)
        self.assertTrue(out, globs)
        return out

    def test_email_matching_rentcast_and_constantcontact_code_never_mentions_the_radar(self):
        # zonematch.py is the one file here that reads the zones:  the 2026-10-26 waiver (steps 2b and 3) puts zone
        # matching in the matching package.  The report matcher and everything else stay clear.
        for path in self.files("hailsys/email/**/*.py", "hailsys/matching/**/*.py", "hailsys/rentcast/**/*.py",
                               "hailsys/constantcontact/**/*.py", "hailsys/email/templates/**/*"):
            if path.is_file() and path.name != "zonematch.py":
                rel = str(path.relative_to(ROOT))
                with self.subTest(path=rel):
                    found = {m.group(0) for m in self.RADAR_NAMES.finditer(path.read_text())}
                    # The send engine writes the send_log snapshot column (step 4 piece 4a), and nothing else.
                    self.assertEqual(found, {"zone_set_id"} if rel == "hailsys/email/send.py" else set())

    def test_the_other_query_modules_never_mention_the_radar(self):
        # findings.py is the slice 6 panel, staff only;  likelihoodadmin.py edits the parameter sets (slice 7, admin only)
        radar = {"radar.py", "radar_map.py", "findings.py", "likelihood_map.py", "likelihoodadmin.py", "zonepull.py", "zonesend.py"}
        for path in self.files("hailsys/queries/*.py"):
            if path.name not in radar:
                with self.subTest(path=path.name):
                    found = {m.group(0) for m in self.RADAR_NAMES.finditer(path.read_text())}
                    # The by-level report (step 4 piece 5) reads send_log's snapshot column, and nothing else.
                    self.assertEqual(found, {"zone_set_id"} if path.name == "levelreport.py" else set())

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
            # zone_match is the one named exception:  the 2026-10-26 waiver's staff button for zone matching.  Its own test
            # (test_the_zone_match_route_is_post_only_and_calls_only_the_zone_matcher) pins what it may touch.
            if isinstance(node, ast.FunctionDef) and re.search(r"pull|match|send", node.name) and node.name not in ("zone_match", "zone_pull_start"):
                seen += 1
                with self.subTest(route=node.name):
                    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                    names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                    self.assertFalse(names & {"radar", "radar_map", "zones_geojson", "picker_days", "findings", "likelihood_map",
                                                 "zonepull", "zone_zips", "zone_estimate", "zonematch", "match_zones",
                                                 "zone_match", "zone_pull_start", "pull_zips"}, names)
        self.assertGreaterEqual(seen, 3)

    def zone_estimate_node(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "zone_estimate"]
        self.assertEqual(len(found), 1)
        return tree, found[0]

    def zone_match_node(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "zone_match"]
        self.assertEqual(len(found), 1)
        return tree, found[0]

    def test_the_zone_pull_route_is_post_only_and_goes_through_start_pull(self):
        tree = ast.parse((ROOT / "hailsys/web/views.py").read_text())
        found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "zone_pull_start"]
        self.assertEqual(len(found), 1)
        node = found[0]
        methods = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "route":
                for k in dec.keywords:
                    if k.arg == "methods":
                        methods = [e.value for e in k.value.elts]
        self.assertEqual(methods, ["POST"])
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        # It reaches RentCast only through start_pull (run_pull is the one place that calls it), and never the matchers.
        self.assertTrue({"start_pull", "zone_zips", "estimate_zips"} <= names, names)
        self.assertFalse(names & {"run_pull", "create_pull", "search_sale_listings", "match_storm", "zonematch",
                                  "match_zones", "estimate_pull", "exports", "sendlist", "send_log"}, names)

    def test_only_the_send_code_may_import_the_zone_send_list(self):
        # The zone send list feeds the send screen and the send engine (step 4) and nothing else:  not the exports, the activity
        # feed, the drips, the report send list, the storm browser or the pull code.
        # sendzone.py is the zone mode of the send page (piece 4b-3, decision log 2026-10-28).
        allowed = {"hailsys/web/send.py", "hailsys/web/sendjobs.py", "hailsys/email/send.py",
                   "hailsys/web/sendzone.py"}
        users = set()
        for path in self.files("hailsys/**/*.py"):
            if path.name != "zonesend.py" and "zonesend" in path.read_text():
                users.add(str(path.relative_to(ROOT)))
        self.assertTrue(users <= allowed, users - allowed)

    def test_only_start_pull_and_the_job_module_write_the_pull_zips(self):
        files = set()
        for path in self.files("hailsys/**/*.py"):
            text = path.read_text()
            rel = str(path.relative_to(ROOT))
            if rel in ("hailsys/email/send.py", "hailsys/queries/levelreport.py"):
                # Their zone_set_id is send_log's snapshot column (step 4 pieces 4a and 5), not the pull's.
                self.assertNotIn("pull_zips", text)
                self.assertNotIn("api_pulls", text)
                continue
            if "pull_zips" in text or "zone_set_id" in text:
                files.add(rel)
        self.assertEqual(files, {"hailsys/web/jobs.py"})

    def test_only_the_zone_match_route_uses_the_zone_matcher(self):
        tree, _ = self.zone_match_node()
        users = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and n.id == "zonematch":
                        users.add(node.name)
        self.assertEqual(users, {"zone_match"})
        files = set()
        for path in self.files("hailsys/**/*.py"):
            if path.name != "zonematch.py" and "zonematch" in path.read_text():
                files.add(str(path.relative_to(ROOT)))
        self.assertEqual(files, {"hailsys/web/views.py"})

    def test_the_zone_match_route_is_post_only_and_calls_only_the_zone_matcher(self):
        _, node = self.zone_match_node()
        methods = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "route":
                for k in dec.keywords:
                    if k.arg == "methods":
                        methods = [e.value for e in k.value.elts]
        self.assertEqual(methods, ["POST"])
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        # No pull, no report matcher, no estimate, no zone reads, no export or send code.
        self.assertFalse(names & {"start_pull", "create_pull", "run_pull", "match_storm", "PullInProgress", "estimate_pull",
                                  "estimate_zips", "zonepull", "zone_zips", "exports", "sendlist", "send_log"}, names)
        self.assertIn("match_zones", names)

    def test_only_the_zone_estimate_route_uses_the_zone_reads(self):
        tree, _ = self.zone_estimate_node()
        users = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for n in ast.walk(node):
                    if isinstance(n, ast.Name) and n.id == "zonepull":
                        users.add(node.name)
        self.assertEqual(users, {"zone_estimate", "zone_pull_start"})

    def test_only_views_imports_the_zone_reads(self):
        users = set()
        for path in self.files("hailsys/**/*.py"):
            if path.name != "zonepull.py" and "zonepull" in path.read_text():
                users.add(str(path.relative_to(ROOT)))
        self.assertEqual(users, {"hailsys/web/views.py"})

    def test_the_zone_estimate_route_is_get_only_and_starts_nothing(self):
        _, node = self.zone_estimate_node()
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "route":
                self.assertEqual([k.arg for k in dec.keywords if k.arg == "methods"], [])
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertFalse(names & {"start_pull", "create_pull", "run_pull", "match_storm", "PullInProgress",
                                  "commit", "session"}, names)

    def test_only_the_estimate_and_zone_pages_mention_the_zone_route_and_the_zone_page_has_no_post_form(self):
        users = set()
        for path in self.files("hailsys/web/templates/*.html"):
            if "zone_estimate" in path.read_text():
                users.add(path.name)
        self.assertEqual(users, {"pull_estimate.html", "pull_zones.html"})
        page = (ROOT / "hailsys/web/templates/pull_zones.html").read_text()
        # Two POST forms:  the Pull button and the zone Match button.  Neither is the report pull or the report matcher.
        self.assertEqual(page.count('method="post"'), 2)
        self.assertEqual(page.count('name="csrf_token"'), 2)
        self.assertIn("url_for('main.zone_match')", page)
        self.assertIn("url_for('main.zone_pull_start')", page)
        for bad in ("url_for('main.pull_start'", "url_for('main.match_start'"):
            self.assertNotIn(bad, page)
        # Every route the page links to or posts to is on this list:  the estimate page (GET), itself, the match button,
        # and the home page.
        routes = set(re.findall(r"url_for\('main\.(\w+)'", page))
        self.assertEqual(routes, {"pull_estimate", "zone_estimate", "zone_match", "zone_pull_start", "index"})

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
