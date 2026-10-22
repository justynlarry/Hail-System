"""Tests for the default report type on the Storm Days and Territory pages and the map.

    docker compose run --rm -T --no-deps -e PGHOST -e FLASK_SECRET_KEY -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_default_type

Until a type is chosen the pages show HAIL (developer, 2026-10-22).  What they pin: the default applies only
when neither `type` nor `submitted` is in the URL, so "All" (a submitted form with an empty type) and a named type
both still work; the Download CSV link carries the type the page is showing, since the default is not in the URL;
the map's points follow the same default; and links that name a type (the day expanders, the pull and match
pages) are left alone.
"""

import os
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.web import create_app, views
    from hailsys.queries import storms
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class DefaultTypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            cls.app = create_app()

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(row_factory=dict_row) as conn:
            self.user = conn.execute("SELECT emp_id, role FROM users WHERE is_active ORDER BY emp_id LIMIT 1").fetchone()
        if not self.user:
            self.skipTest("no active user")

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.user["emp_id"], self.user["role"], "test"
            s["issued_at"] = datetime.now(UTC).isoformat()
        return client

    def type_of(self, query):
        with self.app.test_request_context("/?" + query):
            return views._report_type_from_args()

    # -- the rule ---------------------------------------------------------------------------

    def test_no_choice_yet_is_hail(self):
        self.assertEqual(self.type_of(""), "HAIL")
        self.assertEqual(self.type_of("days=90"), "HAIL")
        self.assertEqual(self.type_of("start=2026-06-01&end=2026-06-30"), "HAIL")
        self.assertEqual(views.DEFAULT_REPORT_TEXT, "HAIL")

    def test_all_is_still_a_choice(self):
        self.assertIsNone(self.type_of("submitted=1&type="))
        self.assertIsNone(self.type_of("submitted=1&type=&actionable=1"))
        self.assertIsNone(self.type_of("type="))
        self.assertIsNone(self.type_of("submitted=1"))

    def test_a_named_type_is_used(self):
        self.assertEqual(self.type_of("type=TORNADO"), "TORNADO")
        self.assertEqual(self.type_of("submitted=1&type=TSTM+WND+GST"), "TSTM WND GST")
        self.assertEqual(self.type_of("type=HAIL"), "HAIL")

    # -- the download link ------------------------------------------------------------------

    def export_query(self, query, report_text):
        with self.app.test_request_context("/?" + query):
            return views._export_query(report_text)

    def test_the_download_link_gets_the_default_type(self):
        self.assertEqual(self.export_query("", "HAIL"), "type=HAIL")
        self.assertEqual(self.export_query("days=90", "HAIL"), "days=90&type=HAIL")

    def test_the_type_is_url_encoded(self):
        self.assertEqual(self.export_query("", "TSTM WND GST"), "type=TSTM+WND+GST")

    def test_a_type_already_in_the_url_is_left_alone(self):
        self.assertEqual(self.export_query("submitted=1&type=TORNADO", "TORNADO"), "submitted=1&type=TORNADO")
        self.assertEqual(self.export_query("submitted=1&type=", None), "submitted=1&type=")

    def test_all_types_adds_nothing(self):
        self.assertEqual(self.export_query("days=90", None), "days=90")

    # -- the pages --------------------------------------------------------------------------

    def get(self, path):
        resp = self.client().get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp.get_data(as_text=True)

    def selected_option(self, text):
        """The value of the selected option of the type dropdown, or '' for All."""
        block = re.search(r'<select id="type" name="type">(.*?)</select>', text, re.S).group(1)
        chosen = re.findall(r'<option value="([^"]*)"\s*selected', block)
        return chosen[0] if chosen else ""

    def download_href(self, text):
        return re.search(r'<a href="(/export\.csv\?[^"]*)">Download CSV</a>', text).group(1)

    def test_both_pages_open_on_hail(self):
        for path in ("/", "/territory", "/territory?group_by=zip"):
            with self.subTest(path=path):
                self.assertEqual(self.selected_option(self.get(path)), "HAIL")

    def test_both_pages_can_still_show_all_types(self):
        for path in ("/?submitted=1&type=", "/territory?submitted=1&type="):
            with self.subTest(path=path):
                self.assertEqual(self.selected_option(self.get(path)), "")

    def test_a_named_type_is_shown_selected(self):
        for path in ("/?type=TORNADO&submitted=1", "/territory?type=TORNADO&submitted=1"):
            with self.subTest(path=path):
                self.assertEqual(self.selected_option(self.get(path)), "TORNADO")

    def test_the_download_link_on_each_page_carries_the_shown_type(self):
        self.assertIn("type=HAIL", self.download_href(self.get("/")))
        self.assertIn("type=HAIL", self.download_href(self.get("/territory")))
        self.assertIn("type=HAIL", self.download_href(self.get("/?days=90")))
        self.assertNotIn("HAIL", self.download_href(self.get("/?submitted=1&type=")))
        self.assertIn("type=TORNADO", self.download_href(self.get("/?submitted=1&type=TORNADO")))

    def test_the_lists_are_asked_for_hail_by_default(self):
        seen = {}

        def spy(name):
            real = getattr(storms, name)

            def wrapper(*a, **kw):
                seen.setdefault(name, []).append(kw.get("report_text"))
                return real(*a, **kw)
            return mock.patch.object(storms, name, wrapper)

        names = ("fetch_day_count", "fetch_recent_days", "fetch_cities", "fetch_zips", "fetch_report_points")
        patchers = [spy(n) for n in names]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.get("/")
        self.get("/territory")
        self.get("/territory?group_by=zip")
        self.get("/map/points.geojson")
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(seen.get(name), f"{name} was not called")
                self.assertEqual(set(seen[name]), {"HAIL"})

    def test_the_lists_are_asked_for_everything_when_all_is_chosen(self):
        seen = []
        real = storms.fetch_recent_days

        def wrapper(*a, **kw):
            seen.append(kw.get("report_text"))
            return real(*a, **kw)
        with mock.patch.object(storms, "fetch_recent_days", wrapper):
            self.get("/?submitted=1&type=")
        self.assertEqual(seen, [None])

    def test_the_map_points_follow_the_page_query_string(self):
        seen = []
        real = storms.fetch_report_points

        def wrapper(*a, **kw):
            seen.append(kw.get("report_text"))
            return real(*a, **kw)
        with mock.patch.object(storms, "fetch_report_points", wrapper):
            self.get("/map/points.geojson?submitted=1&type=")
            self.get("/map/points.geojson?type=TORNADO")
            self.get("/map/points.geojson")
        self.assertEqual(seen, [None, "TORNADO", "HAIL"])

    def test_the_day_expander_keeps_the_type_it_names(self):
        seen = []
        real = storms.fetch_city_days

        def wrapper(*a, **kw):
            seen.append(kw.get("report_text"))
            return real(*a, **kw)
        with mock.patch.object(storms, "fetch_city_days", wrapper):
            self.get("/territory/days?area_name=Denver&type=TORNADO")
            self.get("/territory/days?area_name=Denver&type=")
        self.assertEqual(seen, ["TORNADO", None])


if __name__ == "__main__":
    unittest.main()
