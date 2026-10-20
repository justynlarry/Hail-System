"""Tests for hailsys/email/dripactions.py -- stop recipients, pause, resume, cancel.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_actions

Real rows in a transaction that is always rolled back, because the triggers are what enforce
the rules and the module has to work with them: a stop needs a person and a reason, a stopped
recipient stays stopped, the status only moves along the allowed paths.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.email import dripactions
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class PureTest(unittest.TestCase):
    def test_moves(self):
        self.assertEqual(dripactions.MOVES["pause"], (("active",), "paused"))
        self.assertEqual(dripactions.MOVES["resume"], (("paused",), "active"))
        self.assertEqual(dripactions.MOVES["cancel"], (("active", "paused"), "cancelled"))

    def test_reasons_are_the_two_a_person_can_give(self):
        self.assertEqual(dripactions.REASONS, ("manual", "replied"))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ActionsTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()
        user = q("SELECT emp_id FROM users ORDER BY emp_id LIMIT 1")
        self.realtors = [r["realtor_id"] for r in self.conn.execute(
            "SELECT realtor_id FROM realtors ORDER BY realtor_id LIMIT 4").fetchall()]
        match = q("SELECT match_id FROM storm_listing_matches ORDER BY match_id LIMIT 1")
        if not (user and match and len(self.realtors) == 4):
            self.skipTest("the database has no user, match or four realtors to build on")
        self.user = user["emp_id"]
        self.conn.execute("INSERT INTO email_templates (template_name, subject, body, created_by) "
                          "VALUES ('test-dripactions', 's', 'b', %s)", (self.user,))
        template = q("SELECT template_id FROM email_templates "
                     "WHERE template_name = 'test-dripactions'")["template_id"]
        sync = q("INSERT INTO cc_sync_runs (triggered_by, watermark) "
                 "VALUES (%s, '1999-02-01') RETURNING run_id", self.user)["run_id"]
        self.first = uuid.uuid4()
        self.emails = {}
        for i in range(3):
            email = f"Test-Dripactions-{i}@Example.invalid"
            self.emails[self.realtors[i]] = email
            email_id = q("INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, "
                         "template_id, sync_run_id, permission_asserted, created_by, subject, "
                         "html_body) VALUES (%s, %s, %s, %s, %s, 'none', %s, 's', '<p>b</p>') "
                         "RETURNING email_id", self.first, self.realtors[i], email, template,
                         sync, self.user, )["email_id"]
            self.conn.execute("INSERT INTO send_log (realtor_id, recipient_email, match_id, "
                              "template_id, sent_by, email_id) VALUES (%s, %s, %s, %s, %s, %s)",
                              (self.realtors[i], email, match["match_id"], template, self.user,
                               email_id))
        self.conn.execute("INSERT INTO drip_campaigns (name, first_batch_id, created_by) "
                          "VALUES ('test-dripactions', %s, %s)", (self.first, self.user))
        self.campaign = q("SELECT campaign_id FROM drip_campaigns "
                          "WHERE name = 'test-dripactions'")["campaign_id"]
        self.conn.execute("INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name) "
                          "VALUES (%s, 1, 2, 'test-dripactions')", (self.campaign,))
        for rid, email in self.emails.items():
            self.conn.execute("INSERT INTO drip_recipients (campaign_id, realtor_id, "
                              "recipient_email) VALUES (%s, %s, %s)", (self.campaign, rid, email))

    def recipient(self, i):
        return self.conn.execute(
            "SELECT * FROM drip_recipients WHERE campaign_id = %s AND realtor_id = %s",
            (self.campaign, self.realtors[i])).fetchone()

    def status(self):
        return self.conn.execute("SELECT * FROM drip_campaigns WHERE campaign_id = %s",
                                 (self.campaign,)).fetchone()

    def stop(self, i, reason="manual"):
        dripactions.stop_recipient(self.conn, campaign_id=self.campaign,
                                   realtor_id=self.realtors[i], reason=reason, user_id=self.user)

    def paste(self, text, reason="replied"):
        return dripactions.stop_by_addresses(self.conn, campaign_id=self.campaign, text=text,
                                             reason=reason, user_id=self.user)

    def move(self, move):
        dripactions.set_status(self.conn, campaign_id=self.campaign, move=move, user_id=self.user)

    # ---- one recipient ----

    def test_a_recipient_is_stopped_with_the_reason_and_the_person(self):
        self.stop(0, "replied")
        r = self.recipient(0)
        self.assertEqual((r["stop_reason"], r["stopped_by"]), ("replied", self.user))
        self.assertIsNotNone(r["stopped_at"])

    def test_the_others_are_not_stopped(self):
        self.stop(0)
        self.assertIsNone(self.recipient(1)["stopped_at"])
        self.assertIsNone(self.recipient(2)["stopped_at"])

    def test_stopping_does_not_touch_the_do_not_contact_list(self):
        before = self.conn.execute("SELECT count(*) AS n FROM dnc_list").fetchone()["n"]
        self.stop(0)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM dnc_list").fetchone()["n"],
                         before)

    def test_a_stopped_recipient_cannot_be_stopped_again_or_changed(self):
        self.stop(0, "manual")
        with self.assertRaises(dripactions.ActionError):
            self.stop(0, "replied")
        self.assertEqual(self.recipient(0)["stop_reason"], "manual")

    def test_a_recipient_of_another_campaign_is_not_found(self):
        with self.assertRaises(dripactions.ActionError):
            dripactions.stop_recipient(self.conn, campaign_id=self.campaign,
                                       realtor_id=self.realtors[3], reason="manual",
                                       user_id=self.user)

    def test_the_reason_must_be_one_a_person_can_give(self):
        for bad in ("bounced", "unsubscribed", "no_active_listings", "", None):
            with self.assertRaises(dripactions.ActionError):
                self.stop(0, bad)
        self.assertIsNone(self.recipient(0)["stopped_at"])

    def test_a_missing_campaign_is_refused(self):
        with self.assertRaises(dripactions.ActionError) as cm:
            dripactions.stop_recipient(self.conn, campaign_id=-1, realtor_id=self.realtors[0],
                                       reason="manual", user_id=self.user)
        self.assertIn("does not exist", str(cm.exception))

    def test_a_cancelled_campaign_cannot_change_its_recipients(self):
        self.move("cancel")
        with self.assertRaises(dripactions.ActionError) as cm:
            self.stop(0)
        self.assertIn("cancelled", str(cm.exception))
        self.assertIsNone(self.recipient(0)["stopped_at"])

    def test_a_paused_campaign_can_still_stop_recipients(self):
        self.move("pause")
        self.stop(0)
        self.assertEqual(self.recipient(0)["stop_reason"], "manual")

    # ---- the paste box ----

    def test_pasted_addresses_are_matched_whatever_the_case_and_punctuation(self):
        out = self.paste(f"Please stop <{self.emails[self.realtors[0]].upper()}>,\n"
                         f"{self.emails[self.realtors[1]].lower()}; thanks")
        self.assertEqual(len(out["stopped"]), 2)
        self.assertEqual(out["already"] + out["unknown"], [])
        for i in (0, 1):
            self.assertEqual((self.recipient(i)["stop_reason"], self.recipient(i)["stopped_by"]),
                             ("replied", self.user))
        self.assertIsNone(self.recipient(2)["stopped_at"])

    def test_the_result_lists_are_lowercase_and_sorted(self):
        out = self.paste(f"{self.emails[self.realtors[1]]} {self.emails[self.realtors[0]]}")
        self.assertEqual(out["stopped"], sorted(e.lower() for e in
                                                (self.emails[self.realtors[0]],
                                                 self.emails[self.realtors[1]])))

    def test_already_stopped_and_unknown_addresses_are_reported_not_changed(self):
        self.stop(0, "manual")
        out = self.paste(f"{self.emails[self.realtors[0]]} stranger@example.invalid "
                         f"{self.emails[self.realtors[1]]}")
        self.assertEqual(out["stopped"], [self.emails[self.realtors[1]].lower()])
        self.assertEqual(out["already"], [self.emails[self.realtors[0]].lower()])
        self.assertEqual(out["unknown"], ["stranger@example.invalid"])
        self.assertEqual(self.recipient(0)["stop_reason"], "manual")

    def test_the_same_address_twice_is_stopped_once(self):
        e = self.emails[self.realtors[0]]
        out = self.paste(f"{e} {e.upper()}")
        self.assertEqual(out["stopped"], [e.lower()])

    def test_the_lists_come_back_sorted(self):
        names = [f"zz{n}-{c}@example.invalid" for n, c in enumerate("hdfbgace")]
        out = self.paste(" ".join(names))
        self.assertEqual(out["unknown"], sorted(names))

    def other_campaign_with_realtor_zero(self):
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()
        old = q("SELECT template_id, sync_run_id FROM sent_emails WHERE batch_id = %s LIMIT 1",
                self.first)
        batch = uuid.uuid4()
        email = self.emails[self.realtors[0]]
        email_id = q("INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
                     "sync_run_id, permission_asserted, created_by, subject, html_body) "
                     "VALUES (%s, %s, %s, %s, %s, 'none', %s, 's', '<p>b</p>') RETURNING email_id",
                     batch, self.realtors[0], email, old["template_id"], old["sync_run_id"],
                     self.user)["email_id"]
        self.conn.execute("INSERT INTO send_log (realtor_id, recipient_email, match_id, "
                          "template_id, sent_by, email_id) SELECT %s, %s, match_id, %s, %s, %s "
                          "FROM storm_listing_matches ORDER BY match_id LIMIT 1",
                          (self.realtors[0], email, old["template_id"], self.user, email_id))
        self.conn.execute("INSERT INTO drip_campaigns (name, first_batch_id, created_by) "
                          "VALUES ('test-dripactions-2', %s, %s)", (batch, self.user))
        other = q("SELECT campaign_id FROM drip_campaigns "
                  "WHERE name = 'test-dripactions-2'")["campaign_id"]
        self.conn.execute("INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email) "
                          "VALUES (%s, %s, %s)", (other, self.realtors[0], email))
        return other

    def test_stopping_in_one_campaign_leaves_the_same_agent_in_another(self):
        other = self.other_campaign_with_realtor_zero()
        self.stop(0)
        self.paste(self.emails[self.realtors[1]])
        left = self.conn.execute("SELECT stopped_at FROM drip_recipients WHERE campaign_id = %s",
                                 (other,)).fetchone()
        self.assertIsNone(left["stopped_at"])

    def test_pasting_in_one_campaign_leaves_the_same_agent_in_another(self):
        other = self.other_campaign_with_realtor_zero()
        self.paste(self.emails[self.realtors[0]])
        left = self.conn.execute("SELECT stopped_at FROM drip_recipients WHERE campaign_id = %s",
                                 (other,)).fetchone()
        self.assertIsNone(left["stopped_at"])

    def test_text_with_no_address_is_refused(self):
        for text in ("", None, "no addresses here"):
            with self.assertRaises(dripactions.ActionError):
                self.paste(text)

    def test_the_paste_box_needs_a_valid_reason_and_an_open_campaign(self):
        with self.assertRaises(dripactions.ActionError):
            self.paste(self.emails[self.realtors[0]], reason="bounced")
        self.move("cancel")
        with self.assertRaises(dripactions.ActionError):
            self.paste(self.emails[self.realtors[0]])
        self.assertIsNone(self.recipient(0)["stopped_at"])

    # ---- status ----

    def test_pause_then_resume(self):
        self.move("pause")
        s = self.status()
        self.assertEqual((s["status"], s["status_changed_by"]), ("paused", self.user))
        self.assertIsNotNone(s["status_changed_at"])
        self.move("resume")
        self.assertEqual(self.status()["status"], "active")

    def test_cancel_from_active_and_from_paused(self):
        self.move("cancel")
        self.assertEqual(self.status()["status"], "cancelled")

    def test_cancel_from_paused(self):
        self.move("pause")
        self.move("cancel")
        self.assertEqual(self.status()["status"], "cancelled")

    def test_a_cancelled_campaign_cannot_move(self):
        self.move("cancel")
        for move in ("pause", "resume", "cancel"):
            with self.assertRaises(dripactions.ActionError):
                self.move(move)
        self.assertEqual(self.status()["status"], "cancelled")

    def test_resume_needs_a_paused_campaign_and_pause_an_active_one(self):
        with self.assertRaises(dripactions.ActionError) as cm:
            self.move("resume")
        self.assertIn("paused", str(cm.exception))
        self.move("pause")
        with self.assertRaises(dripactions.ActionError):
            self.move("pause")

    def test_an_unknown_move_is_refused(self):
        with self.assertRaises(dripactions.ActionError):
            self.move("delete")
        self.assertEqual(self.status()["status"], "active")

    def test_a_missing_campaign_cannot_move(self):
        with self.assertRaises(dripactions.ActionError):
            dripactions.set_status(self.conn, campaign_id=-1, move="pause", user_id=self.user)


if __name__ == "__main__":
    unittest.main()
