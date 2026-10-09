"""Tests for the read-only send preview (hailsys/web/send.py).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_send_screen

The page tests use the real app and database, sign in by writing the session directly, and only
make GET requests.  sweep_stale_pulls is mocked so building the app does not touch api_pulls.
"""

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
    from hailsys.queries import sendlist
    from hailsys.settings import fetch_settings
    from hailsys.web import create_app, send as screen
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

WRITTEN_TABLES = ("sent_emails", "send_log", "email_templates", "api_pulls", "cc_sync_runs")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class LabelsTest(unittest.TestCase):
    def test_every_rule_has_a_label(self):
        self.assertEqual(set(screen.REASON_LABELS), set(sendlist.REASONS))

    def test_the_preview_does_not_import_the_send_engine(self):
        # Previewing must never be able to send.  If this fails, read why before changing it.
        self.assertFalse(hasattr(screen, "deliver"))
        self.assertFalse(hasattr(screen, "campaigns"))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class SendScreenTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])

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

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in WRITTEN_TABLES}

    def test_signed_out_goes_to_login(self):
        resp = self.app.test_client().get("/send/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_only_admin_may_open_it(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.client_as(role).get("/send/").status_code, 403, role)
        self.assertEqual(self.client_as("admin").get("/send/").status_code, 200)

    def test_a_bad_day_is_a_400(self):
        self.assertEqual(self.client_as("admin").get("/send/?day=not-a-date").status_code, 400)

    def test_a_preview_lists_every_rule_and_writes_nothing(self):
        before = self.counts()
        resp = self.client_as("admin").get("/send/?day=2026-09-19&day=2026-09-22")
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        for label in screen.REASON_LABELS.values():
            self.assertIn(label, text)
        self.assertEqual(self.counts(), before)

    def test_settings_carry_the_freshness_days(self):
        self.assertIsInstance(fetch_settings(self.conn)["listing_freshness_days"], int)


if __name__ == "__main__":
    unittest.main()
