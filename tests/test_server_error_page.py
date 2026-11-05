"""The page for an unhandled error (item 301).

    docker compose --profile test run --rm -T test python -m unittest tests.test_server_error_page

No database:  create_app()'s startup sweep is patched out, and the request is a signed-out GET of
/login, which reads nothing.  The login page's render is made to raise, standing in for any view that
fails.  The app is NOT put in testing mode:  TESTING makes Flask re-raise instead of calling the handler.
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
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ServerErrorPageTest(unittest.TestCase):

    def setUp(self):
        for patcher in (mock.patch.dict(os.environ, {"FLASK_SECRET_KEY": "test-only"}),
                        mock.patch("hailsys.web.jobs.sweep_stale_pulls")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = create_app().test_client()

    def _fail(self):
        broken = mock.patch("hailsys.web.views.render_template", side_effect=RuntimeError("secret detail"))
        with broken, self.assertLogs("hailsys.web", level="ERROR") as logs:
            resp = self.client.get("/login")
        return resp, logs

    def test_an_unhandled_error_is_a_500_with_the_page(self):
        resp, _ = self._fail()
        self.assertEqual(resp.status_code, 500)
        self.assertIn("text/html", resp.content_type)
        body = resp.get_data(as_text=True)
        self.assertIn("Something went wrong", body)
        self.assertIn('href="/"', body)

    def test_the_page_does_not_show_the_error_text(self):
        resp, _ = self._fail()
        self.assertNotIn("secret detail", resp.get_data(as_text=True))

    def test_the_traceback_still_reaches_the_log(self):
        _, logs = self._fail()
        self.assertTrue(any(r.exc_info and "secret detail" in str(r.exc_info[1]) for r in logs.records))