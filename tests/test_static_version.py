"""Static file links carry a version (parking-lot item 296).

A browser behind the Cloudflare tunnel kept running an old script and stylesheet after they changed.
Every url_for('static', ...) now carries the file's modification time as ?v=, so a changed file has a
new address.

    docker compose run --rm -v ./tests:/app/tests:ro web python -m unittest tests.test_static_version
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.web import create_app
    from flask import url_for
    MISSING = None
except ImportError as exc:                  # pragma: no cover
    MISSING = str(exc)


@unittest.skipIf(MISSING, f"hailsys not importable: {MISSING}")
class StaticVersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            cls.app = create_app()

    def url(self, filename, **extra):
        with self.app.test_request_context():
            return url_for("static", filename=filename, **extra)

    def test_a_static_link_carries_the_files_modification_time(self):
        mtime = int(os.stat(os.path.join(self.app.static_folder, "style.css")).st_mtime)
        self.assertEqual(self.url("style.css"), f"/static/style.css?v={mtime}")

    def test_a_changed_file_gets_a_new_address(self):
        before = self.url("tplpicker.js")
        real = os.stat
        def later(path, *a, **k):
            result = real(path, *a, **k)
            if str(path).endswith("tplpicker.js"):
                return os.stat_result((*result[:8], result.st_mtime + 60, result[9]))
            return result
        with mock.patch("hailsys.web.os.stat", side_effect=later):
            after = self.url("tplpicker.js")
        self.assertNotEqual(before, after)

    def test_a_missing_file_gets_no_version_and_still_404s(self):
        self.assertEqual(self.url("no-such-file.js"), "/static/no-such-file.js")
        self.assertEqual(self.app.test_client().get("/static/no-such-file.js").status_code, 404)

    def test_a_versioned_address_still_serves_the_file(self):
        resp = self.app.test_client().get(self.url("style.css"))
        self.assertEqual(resp.status_code, 200)
        resp.close()

    def test_an_explicit_version_is_left_alone_and_other_endpoints_get_none(self):
        self.assertEqual(self.url("style.css", v="pinned"), "/static/style.css?v=pinned")
        with self.app.test_request_context():
            self.assertNotIn("v=", url_for("main.login"))

    def test_the_pages_link_their_files_with_a_version(self):
        page = self.app.test_client().get("/login").get_data(as_text=True)
        self.assertRegex(page, r'href="/static/style\.css\?v=\d+"')
        self.assertRegex(page, r'src="/static/theme\.js\?v=\d+"')


if __name__ == "__main__":
    unittest.main()
