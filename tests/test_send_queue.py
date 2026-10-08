"""Tests for hailsys/email/send.py (Phase A: queue_batch), on the real database.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_send_queue

Uses the real 2026-09-22 storm and its listings, inside a transaction that is rolled back:
nothing is committed and Constant Contact is never called.  Skips without a database, or when
the data is not there.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import render, send
    from hailsys.queries import sendlist
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
DAY = date(2026, 9, 22)
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)       # 8 days after the storm: listings fresh


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class QueueBatchTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.user = self.conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        found = sendlist.build_send_list(self.conn, [DAY], now=NOW)["realtors"]
        if len(found) < 3:
            self.skipTest("the 2026-09-22 storm data is not in this database")
        self.realtors = found[:2]
        self.emails = [r["email"] for r in self.realtors]
        self.settings = render.EmailSettings(
            logo_url="https://files.example.com/logo.png",
            badge_cra_url="https://files.example.com/cra.jpg",
            badge_bbb_url="https://files.example.com/bbb.png",
            contact_email="office@example.com",
            schedule_url="https://www.example.com/Orders/Create")
        self.run_id = self.sync_run("ok", NOW)

    def sync_run(self, status, finished):
        return self.conn.execute(
            "INSERT INTO cc_sync_runs (started_at, finished_at, status, triggered_by) "
            "VALUES (%s, %s, %s, %s) RETURNING run_id",
            (finished - timedelta(minutes=1), finished, status, self.user)).fetchone()["run_id"]

    def queue(self, **over):
        args = dict(user_id=self.user, storm_days=[DAY], email_settings=self.settings,
                    now=NOW, sync_run_id=self.run_id, allowed_emails=self.emails,
                    contacts={e: None for e in self.emails})
        args.update(over)
        return send.queue_batch(self.conn, **args)

    def count(self, table, where="TRUE", params=()):
        return self.conn.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}",
                                 params).fetchone()["n"]

    # ---- what is written ----

    def test_one_email_per_allowed_realtor_with_the_rendered_text(self):
        out = self.queue()
        self.assertEqual(out["emails"], 2)
        rows = self.conn.execute(
            "SELECT * FROM sent_emails WHERE batch_id = %s ORDER BY email_id",
            (out["batch_id"],)).fetchall()
        self.assertEqual(sorted(r["recipient_email"] for r in rows), sorted(self.emails))
        for row in rows:
            self.assertIn("[[trackingImage]]", row["html_body"])
            self.assertIn("hail storm", row["subject"].lower())
            self.assertEqual(row["sync_run_id"], self.run_id)
            self.assertEqual(row["created_by"], self.user)
            self.assertEqual(row["permission_asserted"], "implicit")
            self.assertIsNone(row["cc_contact_id"])
            self.assertIsNone(row["error_detail"])

    def test_one_queued_send_log_row_per_match(self):
        out = self.queue()
        expected = sorted(mid for r in self.realtors for e in r["events"] for mid in e["match_ids"])
        rows = self.conn.execute(
            "SELECT s.* FROM send_log s JOIN sent_emails e USING (email_id) "
            "WHERE e.batch_id = %s", (out["batch_id"],)).fetchall()
        self.assertEqual(sorted(r["match_id"] for r in rows), expected)
        self.assertEqual(out["matches"], len(expected))
        for row in rows:
            self.assertEqual(row["send_status"], "queued")
            self.assertIsNone(row["sent_at"])
            self.assertEqual(row["sent_by"], self.user)

    def test_the_template_is_saved_once_and_used(self):
        out = self.queue()
        ids = {r["template_id"] for r in self.conn.execute(
            "SELECT template_id FROM sent_emails WHERE batch_id = %s", (out["batch_id"],))}
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.count("email_templates", "template_id = %s", (ids.pop(),)), 1)

    # ---- who is left out ----

    def test_only_allowed_addresses_are_queued(self):
        out = self.queue(allowed_emails=[self.emails[0].upper()],
                         contacts={self.emails[0]: None, self.emails[1]: None})
        self.assertEqual(out["emails"], 1)
        self.assertGreater(out["skipped"]["not_allowed"], 0)
        self.assertEqual(self.count("sent_emails", "recipient_email = %s", (self.emails[1],)), 0)

    def test_nobody_allowed_writes_nothing(self):
        before = self.count("email_templates")
        out = self.queue(allowed_emails=[], contacts={})
        self.assertEqual((out["batch_id"], out["emails"]), (None, 0))
        self.assertEqual(self.count("email_templates"), before)

    def test_an_address_on_the_dnc_list_is_not_queued(self):
        self.conn.execute(
            "INSERT INTO dnc_list (email_raw, added_by, source) VALUES (%s, %s, 'manual')",
            (self.emails[0], self.user))
        out = self.queue()
        self.assertEqual(out["emails"], 1)
        self.assertGreaterEqual(out["excluded"]["on_dnc_list"]["realtors"], 1)
        self.assertEqual(self.count("sent_emails", "recipient_email = %s", (self.emails[0],)), 0)

    def test_unsubscribed_in_constant_contact_is_skipped(self):
        contacts = {self.emails[0]: {"contact_id": "c-1", "permission": "unsubscribed"},
                    self.emails[1]: None}
        out = self.queue(contacts=contacts)
        self.assertEqual(out["emails"], 1)
        self.assertEqual(out["skipped"]["unsubscribed_in_cc"], 1)
        self.assertEqual(self.count("sent_emails", "recipient_email = %s", (self.emails[0],)), 0)

    def test_an_existing_contact_asserts_nothing_and_keeps_its_id(self):
        contacts = {self.emails[0]: {"contact_id": "c-1", "permission": "explicit"},
                    self.emails[1]: None}
        out = self.queue(contacts=contacts)
        by_email = {r["recipient_email"]: r for r in self.conn.execute(
            "SELECT * FROM sent_emails WHERE batch_id = %s", (out["batch_id"],))}
        self.assertEqual(by_email[self.emails[0]]["permission_asserted"], "none")
        self.assertEqual(by_email[self.emails[0]]["cc_contact_id"], "c-1")
        self.assertEqual(by_email[self.emails[1]]["permission_asserted"], "implicit")

    def test_one_unrenderable_email_does_not_stop_the_others(self):
        real = render.render
        calls = []

        def flaky(subject, body, context):
            calls.append(1)
            if len(calls) == 1:
                raise render.RenderError("broken")
            return real(subject, body, context)

        with mock.patch.object(render, "render", flaky):
            out = self.queue()
        self.assertEqual(out["emails"], 1)
        self.assertEqual(len(out["skipped"]["unrenderable"]), 1)

    # ---- never twice ----

    def test_a_second_queue_finds_nothing_left(self):
        self.queue()
        again = self.queue()
        self.assertEqual(again["emails"], 0)
        self.assertEqual(again["excluded"]["already_emailed"]["realtors"], 2)

    def test_the_cap_override_does_not_bypass_already_emailed(self):
        self.queue()
        again = self.queue(override_cap=True)
        self.assertEqual(again["emails"], 0)
        self.assertEqual(again["excluded"]["already_emailed"]["realtors"], 2)

    # ---- refusals write nothing ----

    def refused(self, **over):
        before = (self.count("sent_emails"), self.count("send_log"))
        with self.assertRaises(send.SendError):
            self.queue(**over)
        self.assertEqual((self.count("sent_emails"), self.count("send_log")), before)

    def test_a_failed_sync_run_is_refused(self):
        self.refused(sync_run_id=self.sync_run("failed", NOW))

    def test_an_old_sync_run_is_refused(self):
        self.refused(sync_run_id=self.sync_run("ok", NOW - timedelta(minutes=16)))

    def test_a_missing_sync_run_is_refused(self):
        self.refused(sync_run_id=10**9)

    def test_a_recipient_nobody_looked_up_is_refused(self):
        self.refused(contacts={self.emails[0]: None})


if __name__ == "__main__":
    unittest.main()
