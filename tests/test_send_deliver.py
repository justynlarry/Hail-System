"""Tests for hailsys/email/deliver.py (Phase B), on the real database with a fake Constant Contact.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_send_deliver

Queues the real 2026-09-22 storm's emails (send.queue_batch), then delivers them through a fake
that stands in for the campaigns module: it records every call and can be told to fail or crash.
sent_emails and send_log are append-only, so the test connection is wrapped in NoCommit: commit()
and rollback() do nothing and tearDown rolls the whole transaction back.  Nothing is committed
and Constant Contact is never called.  Skips without a database or without the storm data.
"""

import os
import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.constantcontact import campaigns
    from hailsys.constantcontact.api import ApiError
    from hailsys.email import deliver, render, send
    from hailsys.queries import sendlist, sendstatus
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
DAY = date(2026, 9, 22)
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SENDER = {"from_name": "RBI", "from_email": "from@example.com", "reply_to": "from@example.com"}
HAPPY = ["create_list", "create_contact", "create_campaign", "update_campaign",
         "schedule", "wait_until_done", "delete_list"]


class Crash(Exception):
    """Not an ApiError: stands for a killed process or a database error."""


class NoCommit:
    """The test connection: commit() and rollback() do nothing, everything else is passed on."""
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        pass

    def rollback(self):
        pass


class FakeCC:
    """Stands in for the campaigns module.  `hooks` maps a call name to an exception to raise or
    a function to run (before the call answers)."""
    is_unknown_outcome = staticmethod(campaigns.is_unknown_outcome)
    stops_batch = staticmethod(campaigns.stops_batch)
    campaign_fields = staticmethod(campaigns.campaign_fields)

    def __init__(self, contacts=None):
        self.calls = []
        self.hooks = {}
        self.contacts = contacts or {}
        self.status = "DRAFT"
        self.n = 0

    def _do(self, name):
        self.calls.append(name)
        hook = self.hooks.get(name)
        if isinstance(hook, BaseException):
            raise hook
        if hook:
            hook()

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    def find_contact(self, email):
        self._do("find_contact")
        return self.contacts.get(email)

    def create_list(self, name):
        self._do("create_list")
        return self._id("L")

    def create_contact(self, email, first_name, list_id):
        self._do("create_contact")
        return self._id("C")

    def add_to_list(self, contact_id, list_id):
        self._do("add_to_list")

    def create_campaign(self, name, fields):
        self._do("create_campaign")
        return {"campaign_id": self._id("camp"), "activity_id": self._id("act")}

    def update_campaign(self, activity_id, fields, list_id):
        self._do("update_campaign")

    def campaign_status(self, activity_id):
        self._do("campaign_status")
        return self.status

    def schedule(self, activity_id):
        self._do("schedule")

    def wait_until_done(self, activity_id):
        self._do("wait_until_done")

    def delete_list(self, list_id):
        self._do("delete_list")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class DeliverTest(unittest.TestCase):
    def setUp(self):
        raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(raw.close)
        self.addCleanup(raw.rollback)
        self.conn = NoCommit(raw)
        self.user = raw.execute("SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        found = sendlist.build_send_list(raw, [DAY], now=NOW)["realtors"]
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
        self.run_id = self.new_sync_run()
        self.cc = FakeCC()

    # ---- helpers ----

    def new_sync_run(self):
        return self.conn.execute(
            "INSERT INTO cc_sync_runs (started_at, finished_at, status, triggered_by) "
            "VALUES (%s, %s, 'ok', %s) RETURNING run_id",
            (NOW - timedelta(minutes=1), NOW, self.user)).fetchone()["run_id"]

    def queue(self, contacts=None):
        contacts = contacts if contacts is not None else {e: None for e in self.emails}
        out = send.queue_batch(
            self.conn, user_id=self.user, storm_days=[DAY], email_settings=self.settings,
            now=NOW, sync_run_id=self.run_id, allowed_emails=self.emails, contacts=contacts)
        self.batch_id = out["batch_id"]
        self.email_ids = out["email_ids"]
        return out

    def deliver(self, index=0):
        return deliver.deliver_email(self.conn, self.email_ids[index], sender=SENDER, cc=self.cc)

    def email(self, index=0):
        return self.conn.execute("SELECT * FROM sent_emails WHERE email_id = %s",
                                 (self.email_ids[index],)).fetchone()

    def log_status(self, index=0):
        rows = self.conn.execute("SELECT send_status, error_detail, sent_at, provider_message_id "
                                 "FROM send_log WHERE email_id = %s",
                                 (self.email_ids[index],)).fetchall()
        self.assertTrue(rows)
        return rows

    def statuses(self, index=0):
        return {r["send_status"] for r in self.log_status(index)}

    def add_to_dnc(self, address):
        self.conn.execute("INSERT INTO dnc_list (email_raw, added_by, source) "
                          "VALUES (%s, %s, 'manual')", (address, self.user))

    # ---- the normal path ----

    def test_the_normal_path_in_order_and_recorded(self):
        self.queue()
        self.assertEqual(self.deliver(), "sent")
        self.assertEqual(self.cc.calls, HAPPY)
        row = self.email()
        for column in ("cc_list_id", "cc_contact_id", "cc_campaign_id", "cc_activity_id",
                       "scheduled_at", "list_deleted_at"):
            self.assertIsNotNone(row[column], column)
        self.assertIsNone(row["error_detail"])
        for log in self.log_status():
            self.assertEqual(log["send_status"], "sent")
            self.assertIsNotNone(log["sent_at"])
            self.assertEqual(log["provider_message_id"], row["cc_activity_id"])

    def test_an_existing_contact_is_added_not_created(self):
        self.queue({self.emails[0]: {"contact_id": "c-1", "permission": "explicit"},
                    self.emails[1]: None})
        self.assertEqual(self.deliver(), "sent")
        self.assertIn("add_to_list", self.cc.calls)
        self.assertNotIn("create_contact", self.cc.calls)
        self.assertEqual(self.email()["cc_contact_id"], "c-1")

    def test_a_finished_email_is_skipped_and_nothing_is_called(self):
        self.queue()
        self.deliver()
        self.cc.calls.clear()
        self.assertEqual(self.deliver(), "skipped")
        self.assertEqual(self.cc.calls, [])

    # ---- the DNC list ----

    def test_an_address_on_the_dnc_list_at_send_time_calls_nothing(self):
        self.queue()
        self.add_to_dnc(self.emails[0])
        self.assertEqual(self.deliver(), "failed")
        self.assertEqual(self.cc.calls, [])
        self.assertIn("DNC", self.email()["error_detail"])
        self.assertEqual(self.statuses(), {"failed"})

    def test_an_address_added_just_before_scheduling_is_never_scheduled(self):
        self.queue()
        self.cc.hooks["update_campaign"] = lambda: self.add_to_dnc(self.emails[0])
        self.assertEqual(self.deliver(), "failed")
        self.assertNotIn("schedule", self.cc.calls)
        self.assertEqual(self.statuses(), {"failed"})

    # ---- failures ----

    def test_a_clean_refusal_before_scheduling_fails_the_email(self):
        self.queue()
        self.cc.hooks["create_campaign"] = ApiError("refused", status=422)
        self.assertEqual(self.deliver(), "failed")
        self.assertNotIn("schedule", self.cc.calls)
        self.assertEqual(self.statuses(), {"failed"})

    def test_an_unknown_outcome_before_scheduling_is_only_a_stray_draft(self):
        self.queue()
        self.cc.hooks["create_campaign"] = ApiError("no answer")
        self.assertEqual(self.deliver(), "failed")
        self.assertIn("stray draft", self.email()["error_detail"])
        self.assertNotIn("schedule", self.cc.calls)

    def test_an_unknown_outcome_at_the_schedule_call_needs_review(self):
        self.queue()
        self.cc.hooks["schedule"] = ApiError("server error", status=503)
        self.assertEqual(self.deliver(), "needs_review")
        self.assertTrue(self.email()["error_detail"].startswith("needs review"))
        self.assertEqual(self.statuses(), {"queued"})          # still counted as emailed
        self.assertNotIn("delete_list", self.cc.calls)
        self.cc.calls.clear()
        self.assertEqual(self.deliver(), "skipped")            # never retried automatically
        self.assertEqual(self.cc.calls, [])

    def test_a_clean_refusal_at_the_schedule_call_fails_the_email(self):
        self.queue()
        self.cc.hooks["schedule"] = ApiError("refused", status=422)
        self.assertEqual(self.deliver(), "failed")
        self.assertEqual(self.statuses(), {"failed"})

    def test_not_reaching_done_needs_review(self):
        self.queue()
        self.cc.hooks["wait_until_done"] = ApiError("did not reach DONE in time")
        self.assertEqual(self.deliver(), "needs_review")
        self.assertIsNotNone(self.email()["scheduled_at"])
        self.assertEqual(self.statuses(), {"queued"})

    def test_a_contact_who_unsubscribed_is_not_added(self):
        self.queue()
        self.cc.hooks["create_contact"] = ApiError("exists", status=409)
        self.cc.contacts[self.emails[0]] = {"contact_id": "c-9", "permission": "unsubscribed"}
        self.assertEqual(self.deliver(), "failed")
        self.assertNotIn("create_campaign", self.cc.calls)
        self.assertIn("unsubscribed", self.email()["error_detail"])

    def test_a_contact_who_appeared_meanwhile_is_read_and_used(self):
        self.queue()
        self.cc.hooks["create_contact"] = ApiError("exists", status=409)
        self.cc.contacts[self.emails[0]] = {"contact_id": "c-9", "permission": "implicit"}
        self.assertEqual(self.deliver(), "sent")
        self.assertIn("add_to_list", self.cc.calls)
        self.assertEqual(self.email()["cc_contact_id"], "c-9")

    def test_credentials_or_quota_stop_the_batch(self):
        self.queue()
        self.cc.hooks["create_list"] = ApiError("denied", status=401)
        with self.assertRaises(deliver.BatchStopped):
            self.deliver()
        self.assertEqual(self.statuses(), {"failed"})

    # ---- crash and resume ----

    def test_a_crash_before_scheduling_resumes_without_a_second_campaign(self):
        self.queue()
        self.cc.hooks["update_campaign"] = Crash("killed")
        with self.assertRaises(Crash):
            self.deliver()
        self.assertIsNotNone(self.email()["cc_activity_id"])
        self.assertIsNone(self.email()["scheduled_at"])
        self.cc.hooks.clear()
        self.assertEqual(self.deliver(), "sent")
        self.assertEqual(self.cc.calls.count("create_campaign"), 1)
        self.assertEqual(self.cc.calls.count("create_list"), 1)
        self.assertEqual(self.cc.calls.count("schedule"), 1)

    def test_a_crash_right_after_scheduling_never_schedules_twice(self):
        self.queue()

        def scheduled_then_killed():
            self.cc.status = "SCHEDULED"
            raise Crash("killed")

        self.cc.hooks["schedule"] = scheduled_then_killed
        with self.assertRaises(Crash):
            self.deliver()
        self.assertIsNone(self.email()["scheduled_at"])
        self.cc.hooks.clear()
        self.assertEqual(self.deliver(), "sent")
        self.assertEqual(self.cc.calls.count("schedule"), 1)
        self.assertIsNotNone(self.email()["scheduled_at"])

    # ---- the whole batch ----

    def test_a_batch_delivers_every_email(self):
        self.queue()
        counts = self.batch()
        self.assertEqual(counts["sent"], 2)
        self.assertEqual(self.cc.calls.count("schedule"), 2)

    def batch(self, **kw):
        with self.patched():
            return deliver.deliver_batch(self.batch_id, sender=SENDER, cc=self.cc, **kw)

    def patched(self):
        @contextmanager
        def fake():
            yield self.conn
        return mock.patch.object(deliver, "get_connection", fake)

    def test_max_emails_leaves_the_rest_queued_for_an_explicit_later_run(self):
        self.queue()
        first = self.batch(max_emails=1)
        self.assertEqual((first["sent"], first["remaining"]), (1, 1))
        self.assertEqual(self.statuses(1), {"queued"})
        second = self.batch(max_emails=1)
        self.assertEqual((second["sent"], second["remaining"]), (1, 0))
        self.assertEqual(self.cc.calls.count("schedule"), 2)         # each exactly once

    def test_a_stopping_error_leaves_the_rest_untouched(self):
        self.queue()
        self.cc.hooks["create_list"] = ApiError("denied", status=403)
        counts = self.batch()
        self.assertTrue(counts["stopped"])
        self.assertEqual(counts["remaining"], 1)
        self.assertEqual(self.statuses(1), {"queued"})

    def test_a_batch_being_delivered_elsewhere_is_refused(self):
        self.queue()
        other = psycopg.connect()
        self.addCleanup(other.close)
        other.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"deliver:{self.batch_id}",))
        with self.assertRaises(deliver.BatchBusy):
            self.batch()
        self.assertEqual(self.cc.calls, [])

    # ---- the cap counts what was queued, parked or sent, not what failed ----

    def test_the_cap_counts_queued_and_parked_emails_but_not_failed_ones(self):
        self.queue()
        self.cc.hooks["schedule"] = ApiError("server error", status=503)
        self.assertEqual(self.deliver(0), "needs_review")                 # parked
        self.cc.hooks.clear()
        self.cc.hooks["create_campaign"] = ApiError("refused", status=422)
        self.assertEqual(self.deliver(1), "failed")
        params = {"days": [DAY], "tz": sendlist.DISPLAY_TZ.key, "since": NOW - timedelta(days=14)}
        capped = {}
        for hit in self.conn.execute(sendlist._HITS_SQL, params).fetchall():
            capped[hit["realtor_id"]] = hit["in_cap"]
        self.assertTrue(capped[self.realtors[0]["realtor_id"]])
        self.assertFalse(capped[self.realtors[1]["realtor_id"]])

    # ---- how the history pages read each outcome ----

    def state(self, index=0):
        found = sendstatus.fetch_batch(self.conn, self.batch_id)
        return {e["email_id"]: e for e in found["emails"]}[self.email_ids[index]]

    def test_status_of_a_sent_email(self):
        self.queue()
        self.deliver()
        e = self.state()
        self.assertEqual(e["state"], "sent")
        self.assertIsNotNone(e["sent_at"])

    def test_status_of_a_refused_email_is_failed_with_a_reason(self):
        self.queue()
        self.cc.hooks["create_campaign"] = ApiError("refused", status=422)
        self.deliver()
        e = self.state()
        self.assertEqual(e["state"], "failed")
        self.assertIsNotNone(e["email_error"])

    def test_status_of_a_parked_email_is_needs_review_with_its_activity_id(self):
        self.queue()
        self.cc.hooks["schedule"] = ApiError("server error", status=503)
        self.deliver()
        e = self.state()
        self.assertEqual(e["state"], "needs_review")
        self.assertIsNotNone(e["cc_activity_id"])

    def test_status_of_an_untouched_email_is_queued(self):
        self.queue()
        self.assertEqual(self.state()["state"], "queued")

    def test_status_after_a_crash_before_scheduling_is_in_progress(self):
        self.queue()
        self.cc.hooks["update_campaign"] = Crash("killed")
        with self.assertRaises(Crash):
            self.deliver()
        self.assertEqual(self.state()["state"], "in_progress")

    def test_batch_counts_add_up_and_the_list_agrees(self):
        self.queue()
        self.batch(max_emails=1)
        found = sendstatus.fetch_batch(self.conn, self.batch_id)
        self.assertEqual(found["counts"], {"sent": 1, "failed": 0, "needs_review": 0,
                                           "in_progress": 0, "queued": 1})
        listed = {str(b["batch_id"]): b for b in sendstatus.fetch_batches(self.conn)}
        row = listed[str(self.batch_id)]
        self.assertEqual((row["emails"], row["sent"], row["queued"]), (2, 1, 1))

    def test_an_unknown_batch_has_no_status(self):
        self.assertIsNone(sendstatus.fetch_batch(self.conn, uuid.uuid4()))

    # ---- the whole click ----

    def run_send(self, sync, **kw):
        with self.patched():
            return deliver.run_send(
                user_id=self.user, storm_days=[DAY], email_settings=self.settings, sender=SENDER,
                allowed_emails=self.emails, now=NOW, sync=sync, cc=self.cc, **kw)

    def test_a_failed_sync_writes_and_sends_nothing(self):
        before = self.conn.execute("SELECT count(*) AS n FROM sent_emails").fetchone()["n"]

        def broken(*, triggered_by):
            raise RuntimeError("sync failed")

        with self.assertRaises(RuntimeError):
            self.run_send(broken)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM sent_emails").fetchone()["n"],
                         before)
        self.assertEqual(self.cc.calls, [])

    def test_a_whole_click_syncs_first_then_queues_and_delivers(self):
        order = []

        def sync(*, triggered_by):
            order.append(("sync", triggered_by))
            self.assertEqual(self.cc.calls, [])
            return {"run_id": self.run_id}

        out = self.run_send(sync)
        self.assertEqual(order, [("sync", self.user)])
        self.assertEqual(out["emails"], 2)
        self.assertEqual(out["delivery"]["sent"], 2)
        self.assertEqual(self.cc.calls[:2], ["find_contact", "find_contact"])   # the pre-check

    def test_lookup_contacts_maps_each_address(self):
        self.cc.contacts[self.emails[0]] = {"contact_id": "c-1", "permission": "implicit"}
        got = deliver.lookup_contacts(self.emails, cc=self.cc)
        self.assertEqual(got, {self.emails[0]: {"contact_id": "c-1", "permission": "implicit"},
                               self.emails[1]: None})

@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SenderFromEnvTest(unittest.TestCase):
    ENV = {"CC_FROM_NAME": " RBI ", "CC_FROM_EMAIL": "from@example.com",
           "CC_REPLY_TO": "reply@example.com"}

    def test_reads_all_three_and_strips(self):
        with mock.patch.dict(os.environ, self.ENV):
            self.assertEqual(deliver.sender_from_env(),
                             {"from_name": "RBI", "from_email": "from@example.com",
                              "reply_to": "reply@example.com"})

    def test_a_missing_variable_is_loud(self):
        for missing in self.ENV:
            env = {k: v for k, v in self.ENV.items() if k != missing}
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(render.SettingsError) as ctx:
                    deliver.sender_from_env()
            self.assertIn(missing, str(ctx.exception))



if __name__ == "__main__":
    unittest.main()
