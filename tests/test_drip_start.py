"""Tests for hailsys/email/dripstart.py -- starting a drip campaign from a send (item 204, 6.2a).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_start

parse_plan is pure.  create_campaign runs on real rows built inside a transaction that is always
rolled back.  What they pin: nothing happens unless the box is ticked; every refusal says what is
wrong in a sentence; the admin limits are the limits; rows are used in order; a campaign gets one
row per follow-up, numbered from 1 in the order given, and a recipient for everyone emailed in
the batch.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.email import dripstart
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

LIMITS = dict(max_followups=3, max_gap_days=14)


def form(**extra):
    base = {"campaign": "1", "campaign_name": "Storm 2026-10-18",
            "gap_1": "7", "template_1": "hail_alert",
            "gap_2": "14", "template_2": "second"}
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


def parse(data, ok=lambda name: True, **limits):
    return dripstart.parse_plan(data, template_ok=ok, **dict(LIMITS, **limits))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class ParsePlanTest(unittest.TestCase):
    def refused(self, data, **kw):
        with self.assertRaises(dripstart.CampaignError) as cm:
            parse(data, **kw)
        return str(cm.exception)

    def test_nothing_happens_unless_the_box_is_ticked(self):
        self.assertIsNone(parse(form(campaign=None)))
        self.assertIsNone(parse({}))
        self.assertIsNone(parse(form(campaign="")))

    def test_a_plan_with_unequal_gaps(self):
        self.assertEqual(parse(form()), {
            "name": "Storm 2026-10-18",
            "steps": [{"gap_days": 7, "template_name": "hail_alert"},
                      {"gap_days": 14, "template_name": "second"}]})

    def test_one_follow_up_is_enough(self):
        plan = parse(form(gap_2=None, template_2=None))
        self.assertEqual(len(plan["steps"]), 1)

    def test_the_most_follow_ups_is_the_limit(self):
        plan = parse(form(gap_3="3", template_3="third"))
        self.assertEqual([s["gap_days"] for s in plan["steps"]], [7, 14, 3])

    def test_rows_past_the_limit_are_ignored(self):
        plan = parse(form(gap_4="5", template_4="fourth"))
        self.assertEqual(len(plan["steps"]), 2)

    def test_follow_ups_off_in_settings(self):
        self.assertIn("turned off", self.refused(form(), max_followups=0))

    def test_the_name_is_required_and_trimmed(self):
        self.assertIn("name", self.refused(form(campaign_name="   ")))
        self.assertIn("name", self.refused(form(campaign_name=None)))
        self.assertEqual(parse(form(campaign_name="  Spring  "))["name"], "Spring")

    def test_the_name_limit(self):
        self.assertEqual(len(parse(form(campaign_name="x" * 100))["name"]), 100)
        self.assertIn("1 to 100", self.refused(form(campaign_name="x" * 101)))

    def test_no_gap_at_all(self):
        self.assertIn("at least one", self.refused(form(gap_1=None, gap_2=None)))
        self.assertIn("at least one", self.refused(form(gap_1="  ", gap_2="")))

    def test_rows_must_be_filled_in_order(self):
        message = self.refused(form(gap_1=None, template_1=None))
        self.assertIn("in order", message)
        self.assertIn("follow-up 2", message)

    def test_the_gap_limit_is_the_admin_limit(self):
        self.assertEqual(parse(form(gap_2="14"))["steps"][1]["gap_days"], 14)
        message = self.refused(form(gap_2="15"))
        self.assertIn("follow-up 2", message)
        self.assertIn("1 to 14", message)

    def test_a_raised_admin_limit_allows_a_longer_gap(self):
        self.assertEqual(parse(form(gap_2="30"), max_gap_days=30)["steps"][1]["gap_days"], 30)

    def test_a_gap_of_zero_or_less_is_refused(self):
        for bad in ("0", "-3"):
            self.assertIn("follow-up 1", self.refused(form(gap_1=bad)))

    def test_the_shortest_gap_is_one_day(self):
        self.assertEqual(parse(form(gap_1="1"))["steps"][0]["gap_days"], 1)

    def test_a_gap_must_be_a_plain_whole_number(self):
        for bad in ("soon", "2.5", "1_0", "+5", "²", "٣", "1 0"):
            self.assertIn("whole number", self.refused(form(gap_1=bad)), bad)

    def test_a_gap_with_spaces_around_it_is_read(self):
        self.assertEqual(parse(form(gap_1=" 7 "))["steps"][0]["gap_days"], 7)

    def test_the_template_must_be_available(self):
        message = self.refused(form(), ok=lambda name: name != "second")
        self.assertIn("follow-up 2", message)
        self.assertIn("template", message)

    def test_the_template_is_required(self):
        self.assertIn("template", self.refused(form(template_1=None)))
        self.assertIn("template", self.refused(form(template_1="  ")))

    def test_each_template_is_checked_by_name(self):
        seen = []
        parse(form(), ok=lambda name: seen.append(name) or True)
        self.assertEqual(seen, ["hail_alert", "second"])

    def test_a_template_is_only_checked_for_a_row_that_is_used(self):
        seen = []
        parse(form(gap_2=None, template_2="ghost"), ok=lambda name: seen.append(name) or True)
        self.assertEqual(seen, ["hail_alert"])


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class CreateCampaignTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        # The last user, not the first:  a hard-coded id of 1 would otherwise look right.
        user = self.conn.execute("SELECT emp_id FROM users WHERE emp_id <> 1 "
                                 "ORDER BY emp_id DESC LIMIT 1").fetchone()
        self.realtors = [r["realtor_id"] for r in self.conn.execute(
            "SELECT realtor_id FROM realtors ORDER BY realtor_id LIMIT 3").fetchall()]
        if user is None or len(self.realtors) < 3:
            self.skipTest("the database has no user or three realtors to build on")
        self.user = user["emp_id"]
        self.conn.execute("INSERT INTO email_templates (template_name, subject, body, created_by) "
                          "VALUES ('test-dripstart', 's', 'b', %s)", (self.user,))
        template = self.conn.execute("SELECT template_id FROM email_templates "
                                     "WHERE template_name = 'test-dripstart'").fetchone()
        sync = self.conn.execute("INSERT INTO cc_sync_runs (triggered_by, watermark) "
                                 "VALUES (%s, '1999-03-01') RETURNING run_id",
                                 (self.user,)).fetchone()
        self.batch_id = str(uuid.uuid4())
        for i, realtor in enumerate(self.realtors):
            self.conn.execute(
                "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
                "sync_run_id, permission_asserted, created_by, subject, html_body) "
                "VALUES (%s::uuid, %s, %s, %s, %s, 'none', %s, 's', '<p>b</p>')",
                (self.batch_id, realtor, f"test-dripstart-{i}@example.invalid",
                 template["template_id"], sync["run_id"], self.user))

    def make(self, steps=None, name="test-dripstart", batch_id=None):
        return dripstart.create_campaign(
            self.conn, user_id=self.user, batch_id=batch_id or self.batch_id, name=name,
            steps=steps or [{"gap_days": 7, "template_name": "hail_alert"},
                            {"gap_days": 14, "template_name": "second"}])

    def one(self, sql, *args):
        return self.conn.execute(sql, args).fetchall()

    def test_the_campaign_points_at_the_batch_and_the_person_who_sent_it(self):
        campaign_id = self.make()
        row = self.one("SELECT name, first_batch_id::text AS batch, created_by, status "
                       "FROM drip_campaigns WHERE campaign_id = %s", campaign_id)[0]
        self.assertEqual((row["name"], row["batch"], row["created_by"], row["status"]),
                         ("test-dripstart", self.batch_id, self.user, "active"))

    def test_each_follow_up_is_a_numbered_unsent_step_in_the_order_given(self):
        campaign_id = self.make()
        steps = self.one("SELECT step_no, gap_days, template_name, batch_id FROM drip_steps "
                         "WHERE campaign_id = %s ORDER BY step_no", campaign_id)
        self.assertEqual([(s["step_no"], s["gap_days"], s["template_name"], s["batch_id"])
                          for s in steps],
                         [(1, 7, "hail_alert", None), (2, 14, "second", None)])

    def test_everyone_emailed_in_the_batch_is_a_recipient_and_none_is_stopped(self):
        campaign_id = self.make()
        rows = self.one("SELECT realtor_id, recipient_email, stopped_at, stop_reason "
                        "FROM drip_recipients WHERE campaign_id = %s ORDER BY realtor_id",
                        campaign_id)
        self.assertEqual([r["realtor_id"] for r in rows], sorted(self.realtors))
        self.assertEqual([r["recipient_email"] for r in rows],
                         [f"test-dripstart-{i}@example.invalid" for i in range(3)])
        self.assertTrue(all(r["stopped_at"] is None and r["stop_reason"] is None for r in rows))

    def test_a_recipient_of_another_batch_is_not_added(self):
        other = str(uuid.uuid4())
        template = self.one("SELECT template_id FROM email_templates "
                            "WHERE template_name = 'test-dripstart'")[0]["template_id"]
        sync = self.one("SELECT run_id FROM cc_sync_runs WHERE watermark = '1999-03-01'")[0]["run_id"]
        self.conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
            "sync_run_id, permission_asserted, created_by, subject, html_body) "
            "VALUES (%s::uuid, %s, 'test-dripstart-other@example.invalid', %s, %s, 'none', %s, "
            "'s', '<p>b</p>')", (other, self.realtors[0], template, sync, self.user))
        campaign_id = self.make()
        emails = {r["recipient_email"] for r in self.one(
            "SELECT recipient_email FROM drip_recipients WHERE campaign_id = %s", campaign_id)}
        self.assertNotIn("test-dripstart-other@example.invalid", emails)

    def test_the_campaign_id_is_returned(self):
        campaign_id = self.make()
        self.assertEqual(len(self.one("SELECT 1 FROM drip_campaigns WHERE campaign_id = %s",
                                      campaign_id)), 1)

    def test_one_follow_up_is_one_step(self):
        campaign_id = self.make(steps=[{"gap_days": 3, "template_name": "hail_alert"}])
        self.assertEqual(len(self.one("SELECT 1 FROM drip_steps WHERE campaign_id = %s",
                                      campaign_id)), 1)

    def test_a_batch_with_no_emails_is_refused_by_the_database(self):
        import psycopg
        with self.assertRaises(psycopg.errors.RestrictViolation):
            self.make(batch_id=str(uuid.uuid4()))

    def test_a_gap_the_database_refuses_is_not_written(self):
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.make(steps=[{"gap_days": 31, "template_name": "hail_alert"}])


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class SettingsCarryTheLimitsTest(unittest.TestCase):
    def test_the_two_drip_limits_come_from_the_settings_row(self):
        import psycopg
        from psycopg.rows import dict_row
        from hailsys.settings import fetch_settings
        with psycopg.connect(row_factory=dict_row) as conn:
            got = fetch_settings(conn)
            row = conn.execute("SELECT drip_max_followups AS f, drip_max_gap_days AS g "
                               "FROM settings WHERE id = 1").fetchone()
            conn.rollback()
        self.assertEqual((got["drip_max_followups"], got["drip_max_gap_days"]),
                         (row["f"], row["g"]))
        self.assertIsInstance(got["drip_max_followups"], int)
        self.assertIsInstance(got["drip_max_gap_days"], int)


if __name__ == "__main__":
    unittest.main()
