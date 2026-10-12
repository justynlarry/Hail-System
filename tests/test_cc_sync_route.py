"""Tests for the "Sync unsubscribes now" button (hailsys/web/cc.py, POST /cc/sync).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_sync_route

unsubs.run_sync is replaced in every test, GET and refused requests included, so nothing here can
reach Constant Contact or write cc_sync_runs / dnc_list.  The page the route redirects to is the
real /cc/ page, which only reads.  sweep_stale_pulls is mocked so building the app does not touch
api_pulls.
"""

import contextlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.constantcontact.api import ApiError
    from hailsys.constantcontact.oauth import OAuthError
    from hailsys.web import create_app, cc as cc_web
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

RESULT = {"fetched": 12, "inserted": 3, "already_present": 9, "conflicts": 1}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class CcSyncRouteTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        # Every test starts with the sync replaced, so no path can reach the real one.
        self.sync = self.stub()

    def stub(self, **kw):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        return stack.enter_context(mock.patch.object(cc_web.unsubs, "run_sync", **kw))

    def client_as(self, role):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.users[role]
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def post(self, role="admin"):
        self.app.config["WTF_CSRF_ENABLED"] = False
        return self.client_as(role).post("/cc/sync", follow_redirects=True)

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in ("cc_sync_runs", "dnc_list")}

    def test_the_sync_needs_a_token(self):
        self.assertEqual(self.client_as("admin").post("/cc/sync").status_code, 400)
        self.sync.assert_not_called()

    def test_the_sync_is_post_only(self):
        self.assertEqual(self.client_as("admin").get("/cc/sync").status_code, 405)
        self.sync.assert_not_called()

    def test_signed_out_goes_to_login_and_syncs_nothing(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        resp = self.app.test_client().post("/cc/sync")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.sync.assert_not_called()

    def test_only_admin_may_sync(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post(role).status_code, 403, role)
        self.sync.assert_not_called()

    def test_an_admin_sync_runs_once_as_that_admin_and_reports_the_counts(self):
        self.sync.return_value = RESULT
        resp = self.post()
        self.assertEqual(resp.status_code, 200)
        self.sync.assert_called_once_with(triggered_by=self.users["admin"])
        text = resp.get_data(as_text=True)
        self.assertIn("Sync finished: 12 unsubscribed contacts checked", text)
        self.assertIn("3 new to the DNC list", text)
        self.assertIn("9 already there", text)
        self.assertIn("1 conflicts", text)

    def test_a_sync_that_is_already_running_says_so(self):
        self.sync.side_effect = cc_web.unsubs.SyncBusy("Another sync is running.")
        text = self.post().get_data(as_text=True)
        self.assertIn("Another sync is running.", text)
        self.assertNotIn("Sync finished", text)

    def test_an_api_error_is_shown_and_not_reported_as_success(self):
        self.sync.side_effect = ApiError("Constant Contact refused the request", status=403)
        text = self.post().get_data(as_text=True)
        self.assertIn("The sync failed: Constant Contact refused the request", text)
        self.assertNotIn("Sync finished", text)

    def test_an_oauth_error_is_shown_and_not_reported_as_success(self):
        self.sync.side_effect = OAuthError("The Constant Contact grant has lapsed")
        text = self.post().get_data(as_text=True)
        self.assertIn("The sync failed: The Constant Contact grant has lapsed", text)
        self.assertNotIn("Sync finished", text)

    def test_an_unexpected_error_is_said_so_logged_and_hides_the_detail(self):
        self.sync.side_effect = RuntimeError("secret-detail")
        with self.assertLogs("hailsys.web.cc", level="ERROR"):
            text = self.post().get_data(as_text=True)
        self.assertIn("failed unexpectedly", text)
        self.assertNotIn("secret-detail", text)
        self.assertNotIn("Sync finished", text)

    def test_the_connection_page_offers_the_button_and_writes_nothing(self):
        before = self.counts()
        resp = self.client_as("admin").get("/cc/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Sync unsubscribes now", resp.get_data(as_text=True))
        self.assertEqual(self.counts(), before)
        self.sync.assert_not_called()

    def test_the_connection_page_is_admin_only(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.client_as(role).get("/cc/").status_code, 403, role)


if __name__ == "__main__":
    unittest.main()
