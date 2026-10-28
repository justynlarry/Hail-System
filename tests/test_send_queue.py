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
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import render, send, templatestore
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

    # ---- the chosen template (item 202) ----

    TEMPLATE_BODY = "<p>MARKER-ZX {{ agent_first_name }}</p>[[trackingImage]]"
    TEMPLATE_SUBJECT = "Hail near {{ total }} of your listings"

    def saved_template(self):
        name = f"test-{uuid.uuid4()}"
        template_id = templatestore.ensure_current(
            self.conn, created_by=self.user, name=name,
            subject_src=self.TEMPLATE_SUBJECT, body_src=self.TEMPLATE_BODY)
        return name, template_id

    def test_a_chosen_template_is_rendered_and_saved_under_its_own_id(self):
        name, template_id = self.saved_template()
        built_in = self.count("email_templates", "template_name = 'hail_alert'")
        out = self.queue(template_name=name)
        rows = self.conn.execute(
            "SELECT template_id, subject, html_body FROM sent_emails WHERE batch_id = %s",
            (out["batch_id"],)).fetchall()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["template_id"], template_id)
            self.assertIn("MARKER-ZX", row["html_body"])
            self.assertTrue(row["subject"].startswith("Hail near"))
        logged = self.conn.execute(
            "SELECT DISTINCT s.template_id FROM send_log s JOIN sent_emails e USING (email_id) "
            "WHERE e.batch_id = %s", (out["batch_id"],)).fetchall()
        self.assertEqual([r["template_id"] for r in logged], [template_id])
        self.assertEqual(self.count("email_templates", "template_name = 'hail_alert'"), built_in)

    def test_the_default_is_still_the_built_in_template(self):
        self.saved_template()
        out = self.queue()
        html = self.conn.execute("SELECT html_body FROM sent_emails WHERE batch_id = %s LIMIT 1",
                                 (out["batch_id"],)).fetchone()["html_body"]
        self.assertNotIn("MARKER-ZX", html)

    def test_an_unknown_template_is_refused_and_nothing_is_written(self):
        before = (self.count("sent_emails"), self.count("send_log"))
        with self.assertRaises(send.SendError):
            self.queue(template_name=f"no-such-{uuid.uuid4()}")
        self.assertEqual((self.count("sent_emails"), self.count("send_log")), before)

    def test_a_retired_template_is_refused(self):
        name, template_id = self.saved_template()
        self.conn.execute("UPDATE email_templates SET is_active = FALSE WHERE template_id = %s",
                          (template_id,))
        with self.assertRaises(send.SendError):
            self.queue(template_name=name)

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

        def flaky(subject, body, context, kind="report"):
            self.assertEqual(kind, "report")        # a report send renders with the report rules
            calls.append(1)
            if len(calls) == 1:
                raise render.RenderError("broken")
            return real(subject, body, context, kind=kind)

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

    # ---- a list that is already built (drip follow-ups, item 204) ----

    def built(self, realtors=None):
        """A list in the shape build_send_list and dripfollowup.build_followup_list return."""
        return {"realtors": self.realtors if realtors is None else realtors,
                "considered": {"matches": 7, "realtors": 3},
                "excluded": {"listing_inactive": {"matches": 1, "realtors": 1}}}

    def test_a_built_list_is_queued_without_storm_days(self):
        out = self.queue(storm_days=None, send_list=self.built())
        self.assertEqual(out["emails"], 2)
        queued = {r["recipient_email"] for r in self.conn.execute(
            "SELECT recipient_email FROM sent_emails WHERE batch_id = %s", (out["batch_id"],))}
        self.assertEqual(queued, set(self.emails))
        wanted = sum(len(e["match_ids"]) for r in self.realtors for e in r["events"])
        self.assertEqual(out["matches"], wanted)
        self.assertEqual(self.count("send_log", "email_id = ANY(%s)", (out["email_ids"],)), wanted)

    def test_neither_storm_days_nor_a_list_is_a_programming_error_and_writes_nothing(self):
        before = (self.count("sent_emails"), self.count("send_log"))
        with self.assertRaises(ValueError):
            self.queue(storm_days=None)
        self.assertEqual((self.count("sent_emails"), self.count("send_log")), before)

    def test_a_list_wins_over_storm_days(self):
        out = self.queue(send_list=self.built(self.realtors[:1]))
        self.assertEqual(out["emails"], 1)
        self.assertEqual(self.count("sent_emails", "recipient_email = %s", (self.emails[1],)), 0)

    def test_the_lists_counts_come_through_in_the_summary(self):
        out = self.queue(storm_days=None, send_list=self.built())
        self.assertEqual(out["considered"], {"matches": 7, "realtors": 3})
        self.assertEqual(out["excluded"], {"listing_inactive": {"matches": 1, "realtors": 1}})

    def test_matches_already_emailed_queue_again_through_a_list(self):
        # What a follow-up depends on: the storm-days path refuses a match it has already sent,
        # a built list does not, and the database allows a second send_log row for a match.
        first = self.queue()
        self.assertEqual(first["emails"], 2)
        self.assertEqual(self.queue()["emails"], 0)
        second = self.queue(storm_days=None, send_list=self.built())
        self.assertEqual(second["emails"], 2)
        self.assertNotEqual(second["batch_id"], first["batch_id"])
        match_id = self.realtors[0]["events"][0]["match_ids"][0]
        self.assertEqual(self.count("send_log", "match_id = %s", (match_id,)), 2)

    def test_the_allowed_list_and_constant_contact_state_still_filter_a_list(self):
        contacts = {self.emails[0]: {"contact_id": "c-1", "permission": "unsubscribed"},
                    self.emails[1]: None}
        out = self.queue(storm_days=None, send_list=self.built(), contacts=contacts)
        self.assertEqual((out["emails"], out["skipped"]["unsubscribed_in_cc"]), (1, 1))
        out = self.queue(storm_days=None, send_list=self.built(),
                         allowed_emails=[self.emails[1]])
        self.assertGreater(out["skipped"]["not_allowed"], 0)

    def test_a_recipient_nobody_looked_up_is_refused_for_a_list_too(self):
        self.refused(storm_days=None, send_list=self.built(), contacts={self.emails[0]: None})

    def test_a_failed_sync_run_is_refused_for_a_list_too(self):
        self.refused(storm_days=None, send_list=self.built(),
                     sync_run_id=self.sync_run("failed", NOW))

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
