"""Tests for hailsys/email/testsend.py and the two new campaigns calls (item 203, piece 6.2).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_testsend

Constant Contact is a fake and the call counter (cc_api_calls, append-only) is never reached.
The database is real but every change is rolled back: the connection's commit() does nothing.
"""

import logging
import os
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.constantcontact import campaigns
    from hailsys.constantcontact.api import ApiError
    from hailsys.email import blockform, render, samples, testsend
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

SENDER = {"from_name": "Sender", "from_email": "from@example.test", "reply_to": "r@example.test"}


class NoCommit:
    """The test connection: commit() and rollback() do nothing, everything else is passed on."""
    def __init__(self, conn, events=None):
        self._conn = conn
        self.events = events if events is not None else []     # what happened, in order

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, *args, **kwargs):
        self.events.append("log row" if sql.lstrip().startswith("INSERT INTO email_test_sends")
                           else "sql")
        return self._conn.execute(sql, *args, **kwargs)

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        pass


class FakeCC:
    """Stands in for the campaigns module.  `hooks` maps a call name to an exception to raise."""
    # Looked up when called, not when the class is defined:  on a host without the app's packages the
    # module is skipped rather than failing to import (item 273).
    @staticmethod
    def is_unknown_outcome(*args, **kw):
        return campaigns.is_unknown_outcome(*args, **kw)
    @staticmethod
    def campaign_fields(*args, **kw):
        return campaigns.campaign_fields(*args, **kw)

    def __init__(self, events=None):
        self.calls = []
        self.hooks = {}
        self.events = events if events is not None else []
        self.created = self.tested = self.deleted = None

    def _do(self, name):
        self.calls.append(name)
        self.events.append(name)
        hook = self.hooks.get(name)
        if isinstance(hook, BaseException):
            raise hook

    def create_campaign(self, name, fields):
        self._do("create_campaign")
        self.created = (name, fields)
        return {"campaign_id": "camp1", "activity_id": "act1"}

    def send_test(self, activity_id, address, message):
        self._do("send_test")
        self.tested = (activity_id, address, message)

    def delete_campaign(self, campaign_id):
        self._do("delete_campaign")
        self.deleted = campaign_id


def settings():
    images = {"logo": {"url": "https://x.test/l.png", "alt": "Logo"},
              "badge_cra": {"url": "https://x.test/c.png", "alt": "CRA"},
              "badge_bbb": {"url": "https://x.test/b.png", "alt": "BBB"}}
    return render.EmailSettings("https://x.test/l.png", "https://x.test/c.png",
                                "https://x.test/b.png", "e@x.test", "https://x.test/s",
                                images=images)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class SendTestTest(unittest.TestCase):
    def setUp(self):
        real = psycopg.connect(row_factory=dict_row)
        self.addCleanup(real.close)
        self.addCleanup(real.rollback)
        self.events = []
        self.conn = NoCommit(real, self.events)
        # Throwaway users, made inside this rolled-back transaction: a real test send by a real
        # admin leaves a real row in email_test_sends, and these tests count rows and the
        # hourly limit, so they must never share a user with real use.
        self.user, self.address = self.make_user(real, "a")
        self.other, _ = self.make_user(real, "b")
        self.system = real.execute("SELECT emp_id FROM users WHERE role = 'system'"
                                   ).fetchone()["emp_id"]
        self.cc = FakeCC(self.events)
        self.template = blockform.starter()
        self.budget = SimpleNamespace(remaining=lambda: 5000, HEADROOM=200)
        patcher = mock.patch.object(testsend, "budget", self.budget)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def make_user(real, tag):
        token = uuid.uuid4().hex[:10]
        row = real.execute(
            "INSERT INTO users (user_name, emp_fname, emp_lname, emp_email, password_hash, role) "
            "VALUES (%s, 'Test', 'User', %s, 'x', 'admin') RETURNING emp_id, emp_email",
            (f"zz-test-{tag}-{token}", f"zz-test-{tag}-{token}@example.test")).fetchone()
        return row["emp_id"], row["emp_email"]

    def go(self, **over):
        args = dict(user_id=self.user, template=self.template, template_name="spring",
                    situation="one", settings=settings(), sender=SENDER, cc=self.cc)
        args.update(over)
        return testsend.send_test(self.conn, **args)

    def rows(self, user=None):
        return self.conn.execute("SELECT * FROM email_test_sends WHERE sent_by = %s "
                                 "ORDER BY test_id", (user or self.user,)).fetchall()

    def add_old_rows(self, count, user, age="0 minutes"):
        for _ in range(count):
            self.conn.execute(
                "INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes,"
                " outcome, campaign_cleanup, created_at) VALUES (%s, 'a@example.test', 'one', 's',"
                " 1, 'failed', 'none', now() - %s::interval)", (user, age))

    def refused(self, *words, **over):
        with self.assertRaises(testsend.TestSendRefused) as ctx:
            self.go(**over)
        for w in words:
            self.assertIn(w, str(ctx.exception))
        self.assertEqual(self.cc.calls, [], "no Constant Contact call may be made")
        self.assertEqual(self.rows(), [], "nothing was spent, so nothing is logged")

    # ---- the happy path ----

    def test_a_test_is_three_calls_to_the_users_own_address(self):
        got = self.go()
        self.assertEqual(self.cc.calls, ["create_campaign", "send_test", "delete_campaign"])
        self.assertEqual(self.cc.tested[:2], ("act1", self.address))
        self.assertEqual(self.cc.deleted, "camp1")
        self.assertEqual((got["outcome"], got["cleanup"], got["recipient"]),
                         ("accepted", "deleted", self.address))

    def test_nothing_stays_open_across_the_calls_and_the_log_row_is_committed(self):
        self.go()
        calls = ["create_campaign", "send_test", "delete_campaign"]
        first = self.events.index("create_campaign")
        self.assertEqual(self.events[first - 1], "commit")           # nothing open when the calls start
        self.assertEqual(self.events[first:first + 3], calls)         # the calls together
        tail = self.events[first + 3:]
        self.assertEqual(tail, ["log row", "commit"])                 # then the row, then commit

    def test_a_failed_attempt_is_committed_too(self):
        self.cc.hooks["create_campaign"] = ApiError("refused", status=400, key="k")
        self.go()
        self.assertEqual(self.events[-2:], ["log row", "commit"])

    def test_a_refusal_commits_nothing_after_its_reads(self):
        self.conn.execute("UPDATE users SET emp_email = '' WHERE emp_id = %s", (self.user,))
        with self.assertRaises(testsend.TestSendRefused):
            self.go()
        self.assertNotIn("log row", self.events)

    def test_the_content_is_the_previews_subject_and_html(self):
        got = self.go(situation="several")
        name, fields = self.cc.created
        self.assertEqual(fields["subject"], got["subject"])
        self.assertIn("Hail storms were reported near several of your listings", fields["subject"])
        self.assertIn("[[trackingImage]]", fields["html_content"])
        self.assertEqual((fields["from_name"], fields["from_email"], fields["reply_to_email"]),
                         ("Sender", "from@example.test", "r@example.test"))
        self.assertIn("sample data: Several storms and listings", self.cc.tested[2])
        self.assertIn("spring", self.cc.tested[2])

    def test_the_campaign_is_named_as_a_test_with_the_time_and_user(self):
        from datetime import datetime, timezone
        got = self.go(now=datetime(2026, 10, 15, 9, 8, 7, tzinfo=timezone.utc))
        self.assertEqual(got["campaign_name"], f"TEST 2026-10-15 09:08:07 user {self.user}")
        self.assertEqual(self.cc.created[0], got["campaign_name"])

    def test_one_row_is_logged_with_the_outcome(self):
        got = self.go(template_name="spring", situation="more")
        (row,) = self.rows()
        self.assertEqual((row["recipient"], row["template_name"], row["situation"]),
                         (self.address, "spring", "more"))
        self.assertEqual((row["outcome"], row["campaign_cleanup"]), ("accepted", "deleted"))
        self.assertEqual((row["cc_campaign_id"], row["cc_activity_id"]), ("camp1", "act1"))
        self.assertEqual(row["subject"], got["subject"])
        self.assertGreater(row["html_bytes"], 5000)
        self.assertIsNone(row["detail"])

    def test_a_new_unsaved_template_has_no_name_and_says_so(self):
        for blank in ("", None):
            self.go(template_name=blank)
        self.assertEqual([r["template_name"] for r in self.rows()], [None, None])
        self.assertIn("new template", self.cc.tested[2])

    def test_a_long_template_name_is_cut_for_the_log(self):
        self.go(template_name="n" * 300)
        self.assertEqual(len(self.rows()[0]["template_name"]), 100)

    def test_an_unknown_situation_falls_back_to_the_default(self):
        for junk in ("nope", None, 5, ["one"]):
            self.go(situation=junk)
        self.assertEqual({r["situation"] for r in self.rows()}, {samples.DEFAULT})

    def test_it_records_no_send_and_no_queued_email(self):
        before = [self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                  for t in ("sent_emails", "send_log")]
        self.go()
        after = [self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                 for t in ("sent_emails", "send_log")]
        self.assertEqual(before, after)
        self.assertNotIn("schedule", self.cc.calls)

    # ---- refusals: nothing is spent, nothing is logged ----

    def test_no_usable_address_on_file(self):
        for bad in ("", "   ", "not an address", "a@"):
            self.conn.execute("UPDATE users SET emp_email = %s WHERE emp_id = %s",
                              (bad, self.user))
            self.refused("no usable email address")

    def test_an_unknown_or_inactive_user_has_nowhere_to_send(self):
        self.refused("no usable email address", user_id=-1)
        self.conn.execute("UPDATE users SET is_active = FALSE WHERE emp_id = %s", (self.other,))
        self.refused("no usable email address", user_id=self.other)

    def test_an_address_on_the_do_not_contact_list(self):
        self.conn.execute("INSERT INTO dnc_list (email_raw, added_by, source) "
                          "VALUES (%s, %s, 'manual')", (f"  {self.address.upper()} ", self.system))
        self.refused("do-not-contact")

    def test_a_removed_do_not_contact_entry_does_not_block(self):
        self.conn.execute("INSERT INTO dnc_list (email_raw, added_by, source, removed_at, removed_by) "
                          "VALUES (%s, %s, 'manual', now(), %s)",
                          (self.address, self.system, self.system))
        self.assertEqual(self.go()["outcome"], "accepted")

    def test_the_sixth_test_in_an_hour_is_refused(self):
        self.add_old_rows(testsend.MAX_PER_HOUR - 1, self.user)
        self.assertEqual(self.go()["outcome"], "accepted")            # the fifth is allowed
        self.cc.calls.clear()
        with self.assertRaises(testsend.TestSendRefused) as ctx:
            self.go()
        self.assertIn("5 tests in the last hour", str(ctx.exception))
        self.assertEqual(self.cc.calls, [])
        self.assertEqual(len(self.rows()), testsend.MAX_PER_HOUR)

    def test_old_tests_and_other_users_tests_do_not_count(self):
        self.add_old_rows(10, self.user, "2 hours")
        self.add_old_rows(10, self.other)
        self.assertEqual(self.go()["outcome"], "accepted")

    def test_failed_and_unknown_attempts_count_toward_the_limit(self):
        for outcome in ("failed", "unknown"):
            self.conn.execute(
                "INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes,"
                " outcome, campaign_cleanup) VALUES (%s, 'a@example.test', 'one', 's', 1, %s, 'none')"
                "", (self.user, outcome))
        self.add_old_rows(testsend.MAX_PER_HOUR - 2, self.user)
        with self.assertRaises(testsend.TestSendRefused):
            self.go()

    def test_too_few_calls_left_today(self):
        for left in (0, 200, 202):
            self.budget.remaining = lambda left=left: left            # headroom 200 + 3 needed
            self.refused("calls are left today")
        self.budget.remaining = lambda: 203
        self.assertEqual(self.go()["outcome"], "accepted")

    def test_a_template_that_does_not_build(self):
        broken = blockform.starter()
        broken["blocks"][6]["text"] = "[b]never closed"
        self.refused("cannot be built yet", "never closed", template=broken)
        for junk in (None, [], "x", {}):
            with self.assertRaises(testsend.TestSendRefused):
                self.go(template=junk)
        self.assertEqual(self.cc.calls, [])

    def test_a_missing_image_is_named(self):
        draft = blockform.starter()
        draft["blocks"][0]["image"] = "ghost"
        self.refused("ghost", template=draft)

    # ---- Constant Contact trouble ----

    def test_a_refused_create_is_a_failure_with_no_cleanup(self):
        self.cc.hooks["create_campaign"] = ApiError("refused", status=400, key="bad")
        got = self.go()
        self.assertEqual(self.cc.calls, ["create_campaign"])
        (row,) = self.rows()
        self.assertEqual((row["outcome"], row["campaign_cleanup"]), ("failed", "none"))
        self.assertEqual((got["outcome"], got["cleanup"]), ("failed", "none"))
        self.assertIn("refused", row["detail"])
        self.assertIsNone(row["cc_campaign_id"])

    def test_an_ambiguous_create_is_unknown_not_retried_and_names_the_campaign(self):
        for status in (None, 500, 502):
            self.cc.calls.clear()
            self.cc.hooks["create_campaign"] = ApiError("no answer", status=status)
            got = self.go()
            self.assertEqual(self.cc.calls, ["create_campaign"], status)      # one attempt only
            self.assertEqual(got["outcome"], "unknown", status)
            self.assertIn("may have been created", got["detail"])
            self.assertIn(got["campaign_name"], got["detail"])

    def test_a_refused_test_still_deletes_the_campaign(self):
        for status in (400, 401, 403, 429):
            self.cc.calls.clear()
            self.cc.hooks["send_test"] = ApiError("refused", status=status, key="k")
            got = self.go()
            self.assertEqual(self.cc.calls, ["create_campaign", "send_test", "delete_campaign"])
            self.assertEqual((got["outcome"], got["cleanup"]), ("failed", "deleted"), status)

    def test_an_ambiguous_test_is_unknown_and_still_cleans_up(self):
        self.cc.hooks["send_test"] = ApiError("no answer", status=None)
        got = self.go()
        self.assertEqual(self.cc.calls, ["create_campaign", "send_test", "delete_campaign"])
        self.assertEqual((got["outcome"], got["cleanup"]), ("unknown", "deleted"))
        self.assertNotIn("may have been created", got["detail"])           # we know the campaign

    def test_a_campaign_that_cannot_be_deleted_is_left_behind_and_reported(self):
        self.cc.hooks["delete_campaign"] = ApiError("no", status=500)
        with self.assertLogs("hailsys.email.testsend", level=logging.WARNING):
            got = self.go()
        self.assertEqual((got["outcome"], got["cleanup"]), ("accepted", "left_behind"))
        self.assertIn(got["campaign_name"], got["detail"])
        self.assertIn("could not be deleted", got["detail"])
        self.assertEqual(self.rows()[0]["campaign_cleanup"], "left_behind")

    def test_a_failed_test_and_a_failed_delete_report_both(self):
        self.cc.hooks["send_test"] = ApiError("refused", status=400, key="k")
        self.cc.hooks["delete_campaign"] = ApiError("no", status=500)
        got = self.go()
        self.assertEqual((got["outcome"], got["cleanup"]), ("failed", "left_behind"))
        self.assertIn("refused", got["detail"])
        self.assertIn("could not be deleted", got["detail"])

    def test_an_unexpected_error_is_recorded_as_unknown_and_cleans_up(self):
        self.cc.hooks["send_test"] = RuntimeError("boom")
        with self.assertLogs("hailsys.email.testsend", level=logging.ERROR):
            got = self.go()
        self.assertEqual((got["outcome"], got["cleanup"]), ("unknown", "deleted"))
        self.assertNotIn("boom", got["detail"])                           # no internals shown
        self.assertEqual(len(self.rows()), 1)

    def test_the_detail_is_cut_to_what_the_table_allows(self):
        self.cc.hooks["create_campaign"] = ApiError("x" * 1000, status=400)
        self.go()
        self.assertLessEqual(len(self.rows()[0]["detail"]), 300)

    def test_a_logged_row_is_always_consistent_with_the_tables_rules(self):
        # every combination above wrote a row the table accepted; this adds the odd ones
        for hooks in ({}, {"send_test": ApiError("r", status=400)},
                      {"delete_campaign": ApiError("r", status=500)},
                      {"create_campaign": ApiError("r", status=None)}):
            self.cc.hooks = hooks
            self.go()
        self.assertEqual(len(self.rows()), 4)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class CampaignCallsTest(unittest.TestCase):
    def test_send_test_posts_the_address_and_message_and_expects_204(self):
        with mock.patch.object(campaigns.api, "request") as request:
            campaigns.send_test("act-1", "me@example.test", "a note")
        request.assert_called_once_with(
            "POST", "/emails/activities/act-1/tests",
            body={"email_addresses": ["me@example.test"], "personal_message": "a note"},
            expect=(204,))

    def test_delete_campaign_deletes_by_id_and_expects_204(self):
        with mock.patch.object(campaigns.api, "request") as request:
            campaigns.delete_campaign("camp-1")
        request.assert_called_once_with("DELETE", "/emails/camp-1", expect=(204,))

    def test_a_malformed_id_never_reaches_a_url(self):
        for bad in ("", "../x", "a/b", "a b", "x" * 65, None, 5, "a?b=1", "a#b"):
            with mock.patch.object(campaigns.api, "request") as request:
                with self.assertRaises(ApiError, msg=repr(bad)):
                    campaigns.send_test(bad, "me@example.test", "m")
                with self.assertRaises(ApiError, msg=repr(bad)):
                    campaigns.delete_campaign(bad)
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
