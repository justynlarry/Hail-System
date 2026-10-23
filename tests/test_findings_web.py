"""Tests for the findings panel's route and its place on the Territory page (Phase 5b, slice 6).

    docker compose run --rm -T --no-deps -e PGHOST=postgis -e FLASK_SECRET_KEY=x -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_findings_web

The real app renders the real templates; the findings reads are stubbed where a test needs known data, and one test
reads the dev database's real storm day (and skips if it is not there).  Sign-in is by writing the session.  What they
pin: the route needs a login and a day inside the window, refuses a missing, bad or outside day, and only reads; a built
day shows its numbers, the layers, the circles and the matrix with the tier letters as a labelled draft, only on cells
that hold houses; a day without radar zones, or a suspect one, says why and runs neither the circles nor the matrix (and
never prints the impossible value); a day with no reports says so; the circles use the match radius setting; and the
Territory page carries the findings box and its url exactly when it carries the picker.
"""

import os
import re
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.web import views
    from tests import test_radar_map_web as rmw
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)
    rmw = None

DAY = date(2026, 6, 8)
QS = "start=2026-06-01&end=2026-06-30&day=2026-06-08"
AREAS = {"core": 909.0, "margin": 1285.0, "radar_indicated": 716.0, "outer": 12008.0}
BUILT = {"day": DAY, "status": "loaded", "built": True, "built_other_set": False, "patches": 46, "patches_anchored": 9,
         "max_in": 3.4, "areas_km2": AREAS, "warnings": 41}
UNBUILT = {"day": DAY, "status": None, "built": False, "built_other_set": False, "patches": None,
           "patches_anchored": None, "max_in": None, "areas_km2": {k: 0.0 for k in AREAS}, "warnings": 0}
SUSPECT = dict(UNBUILT, status="suspect", max_in=39.67)
REPORTS = {"total": 85, "largest_in": 2.75, "by_layer": {"core": 55, "margin": 25, "radar_indicated": 0, "outer": 2, "none": 3}}
NO_REPORTS = {"total": 0, "largest_in": None, "by_layer": {k: 0 for k in REPORTS["by_layer"]}}
CIRCLES = {"circle_km2": 3025.0, "confirmed_km2": 1626.0, "confirmed_share": 0.5375, "beyond_km2": 568.0}
NO_CIRCLES = {"circle_km2": 0.0, "confirmed_km2": 0.0, "confirmed_share": None, "beyond_km2": 568.0}


def matrix(**cells):
    """An empty matrix with the given (layer, band) cells set;  core/le_1 is an A, none/over_5 has no letter."""
    out = {name: {b: 0 for b in ("le_1", "1_3", "3_5", "over_5")}
           for name in ("core", "margin", "radar_indicated", "outer", "none")}
    for key, n in cells.items():
        layer, band = key.split("__")
        out[layer][band] = n
    return {"cells": out, "total": sum(sum(r.values()) for r in out.values()), "outside_box": 1, "no_location": 0}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class FindingsRouteTest(rmw.Base if rmw else unittest.TestCase):
    def stub(self, overview=BUILT, reports=REPORTS, circles=CIRCLES, mx=None):
        f = views.findings
        self.overview = self.patch(f, "day_overview", return_value=overview)
        self.reports = self.patch(f, "reports_by_layer", return_value=reports)
        self.circles = self.patch(f, "circle_coverage", return_value=circles)
        self.matrix = self.patch(f, "listing_matrix", return_value=mx or matrix(core__le_1=7, none__over_5=9))
        self.patch(views.radar_map, "in_use_set", return_value=dict(rmw.SET))

    def get(self, qs=QS, role=None):
        return self.client(role).get("/territory/findings?" + qs)

    def text(self, qs=QS):
        resp = self.get(qs)
        self.assertEqual(resp.status_code, 200, qs)
        return resp.get_data(as_text=True)

    # -- access ------------------------------------------------------------------------------

    def test_signed_out_goes_to_login(self):
        self.stub()
        resp = self.app.test_client().get("/territory/findings?" + QS)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.overview.assert_not_called()

    def test_every_signed_in_role_may_read_it(self):
        self.stub()
        for role in ("viewer", "sender", "admin"):
            if role in self.users:
                with self.subTest(role=role):
                    self.assertEqual(self.get(role=role).status_code, 200)

    def test_a_missing_bad_or_outside_day_is_refused(self):
        self.stub()
        for qs in ("start=2026-06-01&end=2026-06-30",
                   "start=2026-06-01&end=2026-06-30&day=",
                   "start=2026-06-01&end=2026-06-30&day=nope",
                   "start=2026-06-01&end=2026-06-30&day=2026-05-31",
                   "start=2026-06-01&end=2026-06-30&day=2026-07-01"):
            with self.subTest(qs=qs):
                self.assertEqual(self.get(qs).status_code, 400)
        self.overview.assert_not_called()

    def test_the_window_edges_are_inside(self):
        self.stub()
        for day in ("2026-06-01", "2026-06-30"):
            with self.subTest(day=day):
                self.assertEqual(self.get(f"start=2026-06-01&end=2026-06-30&day={day}").status_code, 200)

    def test_it_only_reads(self):
        self.stub()
        with mock.patch.object(views, "get_connection") as conn:
            conn.return_value.__enter__.return_value.commit = mock.Mock()
            self.get()
            conn.return_value.__enter__.return_value.commit.assert_not_called()

    # -- what it shows -----------------------------------------------------------------------

    def test_a_built_day_shows_its_numbers_layers_circles_and_matrix(self):
        self.stub()
        text = self.text()
        self.assertIn("Findings for 2026-06-08", text)
        for expected in ("85", "2.75 in", "3.4 in", "46, 9", "909 km²", "12,008 km²", "41",
                         "54% of the circle area", "3,025 km²", "568 km² of core and margin lies outside every circle",
                         "Held listings by radar layer", "16 houses placed"):
            self.assertIn(expected, text)
        self.assertIn("It does not say any roof is damaged", text)
        self.assertEqual(self.overview.call_args.args[1], DAY)

    def test_the_circles_use_the_match_radius_setting(self):
        self.stub()
        self.text()
        self.assertAlmostEqual(self.circles.call_args.args[2], 8046.7, delta=0.1)
        self.assertIn("5 mi around each report", self.text())

    def test_a_changed_radius_setting_changes_the_circles(self):
        from hailsys.web import auth
        original = auth.fetch_settings
        self.patch(auth, "fetch_settings", side_effect=lambda conn: {**original(conn), "match_radius_miles": 3.0})
        self.stub()
        text = self.text()
        self.assertAlmostEqual(self.circles.call_args.args[2], 4828.0, delta=0.1)
        self.assertIn("3 mi around each report", text)

    def test_the_tier_letters_are_a_labelled_draft_and_only_on_cells_with_houses(self):
        self.stub()
        text = self.text()
        self.assertEqual(len(re.findall(r'class="tier"', text)), 1)      # core / 1 mi has houses and a letter;  none / over 5 has no letter
        self.assertIn("<strong>draft</strong>", text)
        self.assertIn('title="DRAFT tier"', text)

    def test_a_day_without_radar_zones_says_why_and_runs_neither_circles_nor_matrix(self):
        self.stub(overview=UNBUILT, reports=NO_REPORTS)
        text = self.text()
        self.assertIn("No radar zones for this day", text)
        self.assertNotIn("Report circles against the radar", text)
        self.assertNotIn("Held listings by radar layer", text)
        self.circles.assert_not_called()
        self.matrix.assert_not_called()

    def test_a_suspect_day_never_prints_the_impossible_value(self):
        self.stub(overview=SUSPECT, reports=NO_REPORTS)
        text = self.text()
        self.assertIn("suspect: out of range", text)
        self.assertIn("Suspect day", text)
        self.assertNotIn("39.67", text)
        self.circles.assert_not_called()
        self.matrix.assert_not_called()

    def test_a_day_built_with_an_older_set_says_so(self):
        self.stub(overview=dict(UNBUILT, built_other_set=True), reports=REPORTS)
        self.assertIn("older parameter set", self.text())
        self.matrix.assert_not_called()

    def test_a_day_with_no_reports_says_there_are_no_circles(self):
        self.stub(reports=NO_REPORTS, circles=NO_CIRCLES)
        text = self.text()
        self.assertIn("No actionable reports, so no circles.", text)
        self.assertIn("No actionable hail reports this day.", text)
        self.assertIn("568 km² of core and margin lies outside every circle", text)

    def test_the_cold_season_flag_shows(self):
        ov = dict(BUILT, areas_km2=dict(AREAS, radar_indicated=9.0))
        self.stub(overview=ov, reports=NO_REPORTS, circles=NO_CIRCLES)
        winter = "start=2026-01-01&end=2026-01-31&day=2026-01-15"
        self.assertIn("more likely a radar artefact than hail", self.text(winter))

    def test_houses_not_placed_are_counted_apart_and_singular_reads_right(self):
        mx = matrix(core__le_1=2)
        mx.update(outside_box=1, no_location=3)
        self.stub(mx=mx)
        self.assertIn("Not placed: 1 outside this day's radar area,", self.text())
        self.assertIn("3 with no coordinates.", self.text())

    def test_no_address_agent_or_email_is_ever_in_the_fragment(self):
        self.stub()
        text = self.text().lower()
        for word in ("@", "agent", "address", "mailto", "phone"):
            self.assertNotIn(word, text.replace("the address.", "").replace("at the address", ""))

    def test_the_fragment_says_the_zones_count_wind_reports_too(self):
        self.stub()
        self.assertIn("counted as near a report if any\n   wind report is also within 5 km", self.text())

    def test_the_real_storm_day_renders_with_the_matrix(self):
        import psycopg
        with psycopg.connect() as conn:
            row = conn.execute("SELECT 1 FROM radar_zone_builds WHERE day = '2024-05-30'").fetchone()
        if not row:
            self.skipTest("the dev database has no radar build for 2024-05-30")
        resp = self.get("start=2024-05-01&end=2024-06-30&day=2024-05-30")
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        self.assertIn("Findings for 2024-05-30", text)
        self.assertIn("Held listings by radar layer", text)
        self.assertRegex(text, r"\d[\d,]* houses placed")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class FindingsPageTest(rmw.Base if rmw else unittest.TestCase):
    """The Territory page carries the findings box and its url exactly when it carries the radar picker."""

    def stub(self, picker):
        self.patch(views.radar_map, "picker_days", return_value=list(picker))
        self.patch(views.radar_map, "in_use_set", return_value=dict(rmw.SET))
        self.patch(views.storms, "fetch_cities", return_value=[rmw.CITY])
        self.patch(views.storms, "fetch_zips", return_value=[rmw.ZIP])
        self.patch(views.storms, "fetch_report_types", return_value=["HAIL", "TORNADO"])

    def page(self, path="/territory?start=2026-06-01&end=2026-06-30"):
        resp = self.client().get(path)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_with_radar_days_the_page_has_the_findings_box_and_url_once(self):
        self.stub([rmw.STORM, rmw.QUIET])
        text = self.page()
        self.assertEqual(text.count('id="findings"'), 1)
        self.assertIn('data-findings-url="/territory/findings"', text)

    def test_with_no_radar_days_there_is_no_findings_box(self):
        self.stub([])
        text = self.page()
        self.assertNotIn('id="findings"', text)

    def test_both_groupings_have_the_box_once(self):
        self.stub([rmw.STORM])
        for group in ("city", "zip"):
            with self.subTest(group=group):
                self.assertEqual(self.page(f"/territory?start=2026-06-01&end=2026-06-30&group_by={group}").count('id="findings"'), 1)


if __name__ == "__main__":
    unittest.main()
