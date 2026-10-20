"""Tests for hailsys/queries/dripscreen.py -- the reads behind the campaign screens.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_screen

What they pin: a step is due the Denver date of the previous delivery plus its gap, never due
when sent or when the previous step is undelivered, and says how late; the step to click is the
first unsent one; the preview follows the same rules as the click (assumes active, reuses only
definite answers it will skip); and the SQL against real rows built in a rolled-back transaction.
"""

import os
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.queries import dripscreen
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

NOW = datetime(2026, 10, 19, 5, 30, tzinfo=timezone.utc)


def step(n=1, gap=2, batch=None, prev=None):
    return {"campaign_id": 1, "step_no": n, "gap_days": gap, "template_name": "t",
            "batch_id": batch, "started_at": None, "previous_sent_at": prev}


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class WithDueTest(unittest.TestCase):
    def test_due_is_the_denver_date_of_the_send_plus_the_gap(self):
        # 2026-10-02 05:30 UTC is still 2026-10-01 in Denver.
        s = dripscreen.with_due(step(gap=2, prev=datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)),
                                date(2026, 10, 3))
        self.assertEqual(s["due"], date(2026, 10, 3))
        self.assertEqual(s["days_late"], 0)

    def test_how_late_it_is(self):
        s = dripscreen.with_due(step(gap=2, prev=datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)),
                                date(2026, 10, 10))
        self.assertEqual(s["days_late"], 7)

    def test_not_yet_due_is_zero_late_not_negative(self):
        s = dripscreen.with_due(step(gap=5, prev=datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)),
                                date(2026, 10, 3))
        self.assertEqual((s["due"], s["days_late"]), (date(2026, 10, 7), 0))

    def test_a_sent_step_has_no_due_date(self):
        s = dripscreen.with_due(step(batch="b", prev=datetime(2026, 10, 2, tzinfo=timezone.utc)),
                                date(2026, 10, 9))
        self.assertEqual((s["due"], s["days_late"]), (None, None))

    def test_an_undelivered_previous_step_has_no_due_date(self):
        s = dripscreen.with_due(step(prev=None), date(2026, 10, 9))
        self.assertEqual((s["due"], s["days_late"]), (None, None))

    def test_the_step_itself_is_kept(self):
        s = dripscreen.with_due(step(n=3, gap=4), date(2026, 10, 9))
        self.assertEqual((s["step_no"], s["gap_days"], s["template_name"]), (3, 4, "t"))

    def test_next_step_is_the_first_unsent(self):
        steps = [step(1, batch="b"), step(2), step(3)]
        self.assertEqual(dripscreen._next_step(steps)["step_no"], 2)

    def test_next_step_is_none_when_all_sent(self):
        self.assertIsNone(dripscreen._next_step([step(1, batch="b")]))


def hit_listing(n):
    return {"listing_id": n, "rentcast_id": f"rc{n}", "email": "a@x.com"}


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class StepPreviewTest(unittest.TestCase):
    def preview(self, listings, skip=(), verdicts=None, plan=None, found=None):
        plan = plan or {"method": "by_id", "calls": len(listings), "by_id_calls": len(listings),
                        "zip_pull_calls": 9, "saves": 0}
        found = found or {"realtors": [1, 2], "to_stop": [], "excluded": {"x": 1},
                          "considered": {"matches": 3, "realtors": 2}}
        df, ck = mock.patch.object(dripscreen, "dripfollowup"), mock.patch.object(dripscreen, "checks")
        with df as d, ck as c:
            d.CAP_DAYS = 14
            d.fetch_hits.return_value = ["h"]
            d.fetch_dead.return_value = {99}
            d.listings_to_check.return_value = listings
            d.fetch_verdicts.return_value = verdicts or {}
            d.select_followup.return_value = found
            c.recent_definite.return_value = set(skip)
            c.plan_method.return_value = ({}, plan)
            out = dripscreen.step_preview(mock.sentinel.conn, 5, 2, now=NOW, cap_days=3)
        self.d, self.c = d, c
        return out

    def test_counts_and_plan(self):
        out = self.preview([hit_listing(1), hit_listing(2), hit_listing(3)], skip={2})
        self.assertEqual((out["listings"], out["reused"], out["to_check"]), (3, 1, 2))
        self.assertEqual(out["plan"]["method"], "by_id")
        self.assertEqual(out["emails_at_most"], 2)
        self.assertEqual(out["considered"], {"matches": 3, "realtors": 2})
        self.assertEqual(out["excluded"], {"x": 1})

    def test_reused_counts_listings_not_skip_entries(self):
        out = self.preview([hit_listing(1), hit_listing(2)], skip={1, 50, 51})
        self.assertEqual((out["reused"], out["to_check"]), (1, 1))

    def test_the_plan_is_for_the_listings_not_skipped(self):
        self.preview([hit_listing(1), hit_listing(2)], skip={1})
        self.assertEqual([i["listing_id"] for i in self.c.plan_method.call_args.args[1]], [2])

    def test_the_cap_and_ids_are_passed_on(self):
        self.preview([hit_listing(1)])
        self.assertEqual(self.d.fetch_hits.call_args.kwargs, {"now": NOW, "cap_days": 3})
        self.assertEqual(self.d.fetch_dead.call_args.args[1:], (5, 2))
        self.assertEqual(self.c.recent_definite.call_args.args[1:], (5, 2))

    def test_listings_to_check_are_assumed_active(self):
        self.preview([hit_listing(1), hit_listing(2)])
        self.assertEqual(self.d.select_followup.call_args.args[2], {1: "active", 2: "active"})

    def test_a_reused_answer_is_counted_as_it_is(self):
        self.preview([hit_listing(1), hit_listing(2)], skip={1}, verdicts={1: "inactive"})
        self.assertEqual(self.d.select_followup.call_args.args[2], {1: "inactive", 2: "active"})

    def test_an_earlier_answer_for_a_listing_to_be_rechecked_is_not_used(self):
        self.preview([hit_listing(1), hit_listing(2)], skip={1}, verdicts={2: "error"})
        self.assertEqual(self.d.select_followup.call_args.args[2], {1: "active", 2: "active"})

    def test_dead_and_hits_go_to_the_rules(self):
        self.preview([hit_listing(1)])
        args = self.d.select_followup.call_args.args
        self.assertEqual((args[0], args[1]), (["h"], {99}))

    def test_will_stop_counts_the_recipients_that_would_be_stopped(self):
        found = {"realtors": [], "to_stop": [{"realtor_id": 1}, {"realtor_id": 2}], "excluded": {},
                 "considered": {"matches": 0, "realtors": 0}}
        self.assertEqual(self.preview([hit_listing(1)], found=found)["will_stop"], 2)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ScreenQueryTest(unittest.TestCase):
    """The SQL against real rows, built inside a transaction that is always rolled back."""

    SENT = datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)       # 2026-10-01 in Denver

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
        self.user, self.match = user["emp_id"], match["match_id"]
        self.conn.execute(
            "INSERT INTO email_templates (template_name, subject, body, created_by) "
            "VALUES ('test-dripscreen', 's', 'b', %s)", (self.user,))
        self.template = q("SELECT template_id FROM email_templates "
                          "WHERE template_name = 'test-dripscreen'")["template_id"]
        self.sync = q("INSERT INTO cc_sync_runs (triggered_by, watermark) "
                      "VALUES (%s, '1999-02-01') RETURNING run_id", self.user)["run_id"]
        self.first = self.batch("first", 4)
        self.conn.execute(
            "INSERT INTO drip_campaigns (name, first_batch_id, created_by) "
            "VALUES ('test-dripscreen', %s, %s)", (self.first, self.user))
        self.campaign = q("SELECT campaign_id FROM drip_campaigns "
                          "WHERE name = 'test-dripscreen'")["campaign_id"]
        for n, gap in ((1, 2), (2, 5)):
            self.conn.execute(
                "INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name) "
                "VALUES (%s, %s, %s, 'test-dripscreen')", (self.campaign, n, gap))
        for i in range(3):
            self.conn.execute(
                "INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email) "
                "VALUES (%s, %s, %s)",
                (self.campaign, self.realtors[i], f"test-dripscreen-first-{i}@example.invalid"))

    def batch(self, tag, count):
        import uuid
        batch_id = uuid.uuid4()
        for i in range(count):
            email = f"test-dripscreen-{tag}-{i}@example.invalid"
            email_id = self.conn.execute(
                "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
                "sync_run_id, permission_asserted, created_by, subject, html_body) "
                "VALUES (%s, %s, %s, %s, %s, 'none', %s, 's', '<p>b</p>') RETURNING email_id",
                (batch_id, self.realtors[i], email, self.template, self.sync,
                 self.user)).fetchone()["email_id"]
            self.conn.execute(
                "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, "
                "sent_by, email_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (self.realtors[i], email, self.match, self.template, self.user, email_id))
        return batch_id

    def deliver(self, batch_id, when):
        self.conn.execute(
            "UPDATE send_log SET send_status = 'sent', status_updated_at = now(), sent_at = %s "
            "WHERE email_id IN (SELECT email_id FROM sent_emails WHERE batch_id = %s)",
            (when, batch_id))

    def mine(self, rows):
        return [r for r in rows if r["campaign_id"] == self.campaign][0]

    def other_campaign(self):
        """A second campaign, created after the first, with one step and one run."""
        batch = self.batch("other", 2)
        self.conn.execute("INSERT INTO drip_campaigns (name, first_batch_id, created_by) "
                          "VALUES ('test-dripscreen-other', %s, %s)", (batch, self.user))
        cid = self.conn.execute("SELECT campaign_id FROM drip_campaigns "
                                "WHERE name = 'test-dripscreen-other'").fetchone()["campaign_id"]
        self.conn.execute("INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name) "
                          "VALUES (%s, 1, 3, 'test-dripscreen')", (cid,))
        self.conn.execute("INSERT INTO drip_step_runs (campaign_id, step_no, started_by, status) "
                          "VALUES (%s, 1, %s, 'running')", (cid, self.user))
        return cid

    def test_the_list_is_newest_first(self):
        other = self.other_campaign()
        ids = [r["campaign_id"] for r in dripscreen.list_campaigns(self.conn,
                                                                    today=date(2026, 10, 3))]
        self.assertLess(ids.index(other), ids.index(self.campaign))

    def test_each_campaigns_next_step_is_its_own(self):
        other = self.other_campaign()
        rows = dripscreen.list_campaigns(self.conn, today=date(2026, 10, 3))
        self.assertEqual(self.mine(rows)["next_step"]["campaign_id"], self.campaign)
        self.assertEqual([r for r in rows if r["campaign_id"] == other][0]
                         ["next_step"]["campaign_id"], other)

    def test_detail_shows_only_its_own_steps_and_runs(self):
        other = self.other_campaign()
        d = dripscreen.campaign_detail(self.conn, self.campaign, today=date(2026, 10, 3))
        self.assertEqual({s["campaign_id"] for s in d["steps"]}, {self.campaign})
        self.assertEqual(d["runs"], [])
        o = dripscreen.campaign_detail(self.conn, other, today=date(2026, 10, 3))
        self.assertEqual(len(o["steps"]), 1)
        self.assertEqual(len(o["runs"]), 1)

    def test_the_previous_send_is_the_latest_delivery_in_the_batch(self):
        # sent_at is write-once, so deliver two emails at different times and leave two queued.
        ids = [r["email_id"] for r in self.conn.execute(
            "SELECT email_id FROM sent_emails WHERE batch_id = %s ORDER BY email_id",
            (self.first,)).fetchall()]
        for email_id, when in ((ids[0], datetime(2026, 10, 4, 5, 30, tzinfo=timezone.utc)),
                               (ids[1], self.SENT)):
            self.conn.execute("UPDATE send_log SET send_status = 'sent', "
                              "status_updated_at = now(), sent_at = %s WHERE email_id = %s",
                              (when, email_id))
        d = dripscreen.campaign_detail(self.conn, self.campaign, today=date(2026, 10, 3))
        self.assertEqual(d["steps"][0]["due"], date(2026, 10, 5))

    def test_the_list_counts_steps_and_recipients(self):
        row = self.mine(dripscreen.list_campaigns(self.conn, today=date(2026, 10, 3)))
        self.assertEqual((row["name"], row["status"]), ("test-dripscreen", "active"))
        self.assertEqual((row["step_count"], row["steps_sent"]), (2, 0))
        self.assertEqual((row["recipients"], row["still_going"]), (3, 3))
        self.assertTrue(row["created_by_name"])

    def test_a_stopped_recipient_is_counted_but_not_still_going(self):
        self.conn.execute(
            "UPDATE drip_recipients SET stop_reason = 'manual', stopped_by = %s "
            "WHERE campaign_id = %s AND realtor_id = %s",
            (self.user, self.campaign, self.realtors[0]))
        row = self.mine(dripscreen.list_campaigns(self.conn, today=date(2026, 10, 3)))
        self.assertEqual((row["recipients"], row["still_going"]), (3, 2))

    def test_the_next_step_is_due_from_the_first_batch_delivery(self):
        self.deliver(self.first, self.SENT)
        row = self.mine(dripscreen.list_campaigns(self.conn, today=date(2026, 10, 6)))
        self.assertEqual(row["next_step"]["step_no"], 1)
        self.assertEqual((row["next_step"]["due"], row["next_step"]["days_late"]),
                         (date(2026, 10, 3), 3))

    def test_undelivered_first_batch_means_no_due_date(self):
        row = self.mine(dripscreen.list_campaigns(self.conn, today=date(2026, 10, 6)))
        self.assertIsNone(row["next_step"]["due"])

    def test_step_two_is_due_from_step_ones_batch_not_the_first(self):
        self.deliver(self.first, self.SENT)
        second = self.batch("second", 2)
        self.deliver(second, datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc))
        self.conn.execute("UPDATE drip_steps SET batch_id = %s, started_by = %s "
                          "WHERE campaign_id = %s AND step_no = 1",
                          (second, self.user, self.campaign))
        d = dripscreen.campaign_detail(self.conn, self.campaign, today=date(2026, 10, 20))
        first, second_step = d["steps"]
        self.assertIsNone(first["due"])
        self.assertEqual(second_step["due"], date(2026, 10, 15))
        self.assertEqual(second_step["days_late"], 5)
        self.assertEqual(d["next_step"]["step_no"], 2)

    def test_a_completed_campaign_has_no_next_step(self):
        self.deliver(self.first, self.SENT)
        for n in (1, 2):
            b = self.batch(f"s{n}", 1)
            self.conn.execute("UPDATE drip_steps SET batch_id = %s, started_by = %s "
                              "WHERE campaign_id = %s AND step_no = %s",
                              (b, self.user, self.campaign, n))
        d = dripscreen.campaign_detail(self.conn, self.campaign, today=date(2026, 10, 20))
        self.assertIsNone(d["next_step"])

    def test_detail_of_a_missing_campaign_is_none(self):
        self.assertIsNone(dripscreen.campaign_detail(self.conn, -1, today=date(2026, 10, 20)))

    def test_detail_steps_belong_to_that_campaign_only(self):
        d = dripscreen.campaign_detail(self.conn, self.campaign, today=date(2026, 10, 20))
        self.assertEqual([(s["campaign_id"], s["step_no"]) for s in d["steps"]],
                         [(self.campaign, 1), (self.campaign, 2)])
        self.assertEqual(d["campaign"]["first_batch_id"], self.first)

    def test_detail_lists_runs_newest_first(self):
        for _ in range(2):
            self.conn.execute(
                "INSERT INTO drip_step_runs (campaign_id, step_no, started_by, status) "
                "VALUES (%s, 1, %s, 'running')", (self.campaign, self.user))
            self.conn.execute(
                "UPDATE drip_step_runs SET status = 'failed', detail = 'x' "
                "WHERE campaign_id = %s AND status = 'running'", (self.campaign,))
        runs = dripscreen.campaign_detail(self.conn, self.campaign,
                                          today=date(2026, 10, 20))["runs"]
        self.assertEqual(len(runs), 2)
        self.assertGreater(runs[0]["run_id"], runs[1]["run_id"])
        self.assertTrue(runs[0]["started_by_name"])

    def test_detail_recipients_put_active_before_stopped(self):
        self.conn.execute(
            "UPDATE drip_recipients SET stop_reason = 'replied', stopped_by = %s "
            "WHERE campaign_id = %s AND realtor_id = %s",
            (self.user, self.campaign, self.realtors[0]))
        rec = dripscreen.campaign_detail(self.conn, self.campaign,
                                         today=date(2026, 10, 20))["recipients"]
        self.assertEqual(len(rec), 3)
        self.assertEqual(rec[-1]["realtor_id"], self.realtors[0])
        self.assertEqual(rec[-1]["stop_reason"], "replied")
        self.assertTrue(rec[-1]["stopped_by_name"])
        self.assertIsNone(rec[0]["stopped_at"])


if __name__ == "__main__":
    unittest.main()
