"""Tests for hailsys/email/dripstep.py -- one click on a drip step, start to delivery.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_run

What they pin: the pre-flight refusals write and spend nothing; a failed sync stops before a run row;
the order (sync, run row, re-check, rebuild, queue, close the run row, deliver); the queue
transaction (step marked sent, stops written, campaign completed on the last step only); the
empty step (stops written, nothing queued or delivered); the failure paths close the run row as
failed and say nothing from a response; the sync is run again when the re-check took long; the
cheaper method is used, except that a listing with no zip forces by-id.  Everything is faked:  no
database, no network, no Constant Contact.
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
    from hailsys.email import dripstep
    from hailsys.queries import sendlist
    from hailsys.rentcast import client
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

NOW = datetime(2026, 10, 19, 5, 30, tzinfo=timezone.utc)      # 2026-10-18 23:30 in Denver
STEP = {"status": "active", "template_name": "follow-up", "batch_id": None, "step_count": 2,
        "previous_sent": True, "queued_before": 0}


class Stopped(Exception):
    def __init__(self, reason, calls):
        super().__init__(reason)
        self.reason = reason
        self.calls = calls


def listing(n, email=None):
    return {"listing_id": n, "rentcast_id": f"rc{n}", "email": email or f"a{n}@x.com"}


def followup(*emails, to_stop=()):
    return {"realtors": [{"realtor_id": i, "email": e, "agent_name": "A", "events": []}
                         for i, e in enumerate(emails, 1)],
            "considered": 0, "excluded": {}, "to_stop": list(to_stop)}


class FakeConn:
    def __init__(self, log, step=STEP):
        self.log = log
        self.step = step
        self.executed = []
        self.names = {id(getattr(dripstep, n)): n for n in dir(dripstep) if n.endswith("_SQL")}
        self.last = None

    def execute(self, sql, params=None):
        name = self.names.get(id(sql), "other")
        self.executed.append((name, params))
        self.log.append(name)
        self.last = name
        return self

    def fetchone(self):
        if self.last == "_STEP_SQL":
            return self.step
        if self.last == "_START_SQL":
            return {"run_id": 9}
        return None

    def commit(self):
        self.log.append("commit")

    def rollback(self):
        self.log.append("rollback")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def params(self, name):
        return [p for n, p in self.executed if n == name]


class Rig:
    """Everything run_step touches, faked, with one event log."""

    def __init__(self, test, *, step=STEP, listings=(listing(1),), skip=frozenset(),
                 zips=None, found=None, zip_estimate=5, queued="b-1", sync_error=None):
        self.log = []
        self.conn = FakeConn(self.log, step)
        self.zips = {i["listing_id"]: "80202" for i in listings} if zips is None else zips
        self.found = found if found is not None else followup("a1@x.com")
        self.checks = mock.Mock()
        self.checks.StepStopped = Stopped
        self.checks.recent_definite.return_value = set(skip)
        self.checks.listing_zips.return_value = self.zips
        self.checks.check_by_id.side_effect = lambda *a, **k: (
            self.log.append("check_by_id") or {"calls": 3})
        self.checks.check_by_zip_pull.side_effect = lambda *a, **k: (
            self.log.append("check_by_zip_pull") or {"calls": 2})
        self.clock_values = iter([0, 1, 2, 3, 4, 5])
        self.sync = mock.Mock(side_effect=sync_error or (
            lambda **k: self.log.append("sync") or {"run_id": 100 + self.log.count("sync")}))
        self.cc = object()
        self.summary = {"batch_id": queued, "emails": 1 if queued else 0, "email_ids": [1]}
        p = mock.patch.object
        patches = [
            p(dripstep, "get_connection", return_value=self.conn),
            p(dripstep, "preflight", side_effect=lambda *a: self.log.append("preflight") or step),
            p(dripstep.dripfollowup, "fetch_hits",
              side_effect=lambda *a, **k: self.log.append("fetch_hits") or []),
            p(dripstep.dripfollowup, "fetch_dead", return_value=set()),
            p(dripstep.dripfollowup, "listings_to_check", return_value=list(listings)),
            p(dripstep.dripfollowup, "build_followup_list",
              side_effect=lambda *a, **k: self.log.append("build") or self.found),
            p(dripstep.dripplan, "zip_calls_estimate", return_value=zip_estimate),
            p(dripstep.deliver, "lookup_contacts",
              side_effect=lambda *a, **k: self.log.append("lookup") or {}),
            p(dripstep.send, "queue_batch",
              side_effect=lambda *a, **k: self.log.append("queue") or self.summary),
            p(dripstep.deliver, "deliver_batch",
              side_effect=lambda *a, **k: self.log.append("deliver") or {"sent": 1}),
        ]
        (self.fetch_hits, self.fetch_dead, self.to_check, self.build, self.estimate,
         self.lookup, self.queue, self.deliver) = [None] * 8
        started = [x.start() for x in patches]
        for x in patches:
            test.addCleanup(x.stop)
        (_, self.preflight, self.fetch_hits, self.fetch_dead, self.to_check, self.build,
         self.estimate, self.lookup, self.queue, self.deliver) = started

    def go(self, **overrides):
        kwargs = dict(user_id=3, campaign_id=5, step_no=1, email_settings={"x": 1},
                      sender={"s": 1}, freshness_days=7, sync=self.sync, cc=self.cc,
                      checks=self.checks, clock=lambda: next(self.clock_values), now=NOW)
        kwargs.update(overrides)
        return dripstep.run_step(**kwargs)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class PreflightTest(unittest.TestCase):
    def conn(self, step):
        return FakeConn([], step)

    def run_preflight(self, step, *, template_ok=True, key_ok=True, step_no=2):
        load = mock.Mock(side_effect=None if template_ok else dripstep.templatestore.TemplateNotFound("t"))
        key = mock.Mock(side_effect=None if key_ok else client.RentCastAuthError("no key"))
        conn = self.conn(step)
        with mock.patch.object(dripstep.templatestore, "load", load), \
                mock.patch.object(client, "_api_key", key):
            return dripstep.preflight(conn, 5, step_no), conn

    def refused(self, step, **kw):
        with self.assertRaises(dripstep.StepRefused) as cm:
            self.run_preflight(step, **kw)
        return str(cm.exception)

    def test_a_runnable_step_returns_its_row_and_writes_nothing(self):
        row, conn = self.run_preflight(STEP)
        self.assertEqual(row, STEP)
        self.assertEqual([n for n, _ in conn.executed], ["_STEP_SQL"])

    def test_no_such_step(self):
        self.assertIn("does not exist", self.refused(None))

    def test_a_campaign_that_is_not_active(self):
        self.assertEqual(self.refused(dict(STEP, status="paused")), "The campaign is paused.")

    def test_a_step_already_sent(self):
        self.assertIn("already sent", self.refused(dict(STEP, batch_id="b")))

    def test_the_step_before_must_be_sent_first(self):
        self.assertEqual(self.refused(dict(STEP, previous_sent=False)),
                         "Step 1 has to be sent first.")

    def test_a_retired_template(self):
        self.assertIn("template", self.refused(STEP, template_ok=False))

    def test_a_missing_rentcast_key(self):
        self.assertIn("RentCast key", self.refused(STEP, key_ok=False))

    def test_queued_emails_of_the_first_send_refuse_step_one(self):
        self.assertEqual(self.refused(dict(STEP, queued_before=4), step_no=1),
                         "4 emails of the first send are still queued: resume or discard them first.")

    def test_one_queued_email_is_worded_in_the_singular(self):
        self.assertEqual(self.refused(dict(STEP, queued_before=1), step_no=1),
                         "1 email of the first send is still queued: resume or discard it first.")

    def test_queued_emails_of_an_earlier_step_name_that_step(self):
        self.assertEqual(self.refused(dict(STEP, queued_before=2), step_no=3),
                         "2 emails of follow-up 2 are still queued: resume or discard them first.")

    def test_no_queued_emails_is_no_refusal(self):
        row, _ = self.run_preflight(dict(STEP, queued_before=0))
        self.assertEqual(row["queued_before"], 0)

    def test_an_unsent_previous_step_is_reported_before_queued_emails(self):
        self.assertEqual(self.refused(dict(STEP, previous_sent=False, queued_before=3)),
                         "Step 1 has to be sent first.")

    def test_the_queue_count_is_read_in_the_pre_flight_query(self):
        sql = dripstep._STEP_SQL
        self.assertIn("l.send_status = 'queued'", sql)
        self.assertIn("e.error_detail IS NULL", sql)
        self.assertIn("c.first_batch_id", sql)

    def test_the_template_is_the_steps_own(self):
        load = mock.Mock()
        with mock.patch.object(dripstep.templatestore, "load", load), \
                mock.patch.object(client, "_api_key"):
            dripstep.preflight(self.conn(STEP), 5, 1)
        self.assertEqual(load.call_args.args[1], "follow-up")


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class QueuedBeforeQueryTest(unittest.TestCase):
    """_STEP_SQL against real rows, built inside a transaction that is always rolled back."""

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
            "VALUES ('test-queued-before', 's', 'b', %s)", (self.user,))
        self.template = q("SELECT template_id FROM email_templates "
                          "WHERE template_name = 'test-queued-before'")["template_id"]
        self.sync = q("INSERT INTO cc_sync_runs (triggered_by, watermark) "
                      "VALUES (%s, '1999-02-01') RETURNING run_id", self.user)["run_id"]
        self.first = self.batch("first", [("queued", None), ("queued", "needs review"),
                                          ("sent", None), ("failed", None)])
        self.conn.execute(
            "INSERT INTO drip_campaigns (name, first_batch_id, created_by) "
            "VALUES ('test-queued-before', %s, %s)", (self.first, self.user))
        self.campaign = q("SELECT campaign_id FROM drip_campaigns "
                          "WHERE name = 'test-queued-before'")["campaign_id"]
        for n in (1, 2):
            self.conn.execute(
                "INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name) "
                "VALUES (%s, %s, 2, 'test-queued-before')", (self.campaign, n))

    def batch(self, tag, rows):
        """A batch with one email per (send_status, error_detail) pair."""
        import uuid
        batch_id = uuid.uuid4()
        for i, (status, error) in enumerate(rows):
            email_id = self.conn.execute(
                "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
                "sync_run_id, permission_asserted, created_by, subject, html_body) "
                "VALUES (%s, %s, %s, %s, %s, 'none', %s, 's', '<p>b</p>') RETURNING email_id",
                (batch_id, self.realtors[i], f"test-queued-{tag}-{i}@example.invalid", self.template,
                 self.sync, self.user)).fetchone()["email_id"]
            self.conn.execute(
                "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, "
                "sent_by, email_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (self.realtors[i], f"test-queued-{tag}-{i}@example.invalid", self.match,
                 self.template, self.user, email_id))
            if status != "queued":
                self.conn.execute(
                    "UPDATE send_log SET send_status = %s, status_updated_at = now(), "
                    "sent_at = CASE WHEN %s = 'sent' THEN now() END WHERE email_id = %s",
                    (status, status, email_id))
            if error:
                self.conn.execute("UPDATE sent_emails SET error_detail = %s WHERE email_id = %s",
                                  (error, email_id))
        return batch_id

    def queued_before(self, step_no):
        return self.conn.execute(dripstep._STEP_SQL, {"campaign_id": self.campaign,
                                                      "step_no": step_no}).fetchone()["queued_before"]

    def test_step_one_counts_only_untouched_queued_emails_of_the_first_batch(self):
        # queued + no error counts; queued + needs review, sent and failed do not.
        self.assertEqual(self.queued_before(1), 1)

    def test_a_step_whose_previous_step_is_unsent_counts_nothing(self):
        self.assertEqual(self.queued_before(2), 0)

    def test_step_two_counts_step_ones_batch_and_not_the_first_batch(self):
        second = self.batch("second", [("queued", None), ("queued", None)])
        self.conn.execute("UPDATE drip_steps SET batch_id = %s, started_by = %s "
                          "WHERE campaign_id = %s AND step_no = 1",
                          (second, self.user, self.campaign))
        self.assertEqual(self.queued_before(2), 2)

    def test_the_count_falls_to_zero_when_the_emails_are_delivered(self):
        second = self.batch("second", [("queued", None)])
        self.conn.execute("UPDATE drip_steps SET batch_id = %s, started_by = %s "
                          "WHERE campaign_id = %s AND step_no = 1",
                          (second, self.user, self.campaign))
        self.conn.execute("UPDATE send_log SET send_status = 'sent', sent_at = now() "
                          "WHERE email_id IN (SELECT email_id FROM sent_emails WHERE batch_id = %s)",
                          (second,))
        self.assertEqual(self.queued_before(2), 0)

    def test_preflight_refuses_with_the_real_count(self):
        with mock.patch.object(dripstep.templatestore, "load"), \
                mock.patch.object(client, "_api_key"):
            with self.assertRaises(dripstep.StepRefused) as cm:
                dripstep.preflight(self.conn, self.campaign, 1)
        self.assertEqual(str(cm.exception),
                         "1 email of the first send is still queued: resume or discard it first.")


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class RunStepTest(unittest.TestCase):
    def test_a_refused_step_writes_and_spends_nothing(self):
        rig = Rig(self)
        rig.preflight.side_effect = dripstep.StepRefused("no")
        with self.assertRaises(dripstep.StepRefused):
            rig.go()
        rig.sync.assert_not_called()
        self.assertEqual(rig.conn.params("_START_SQL"), [])
        rig.checks.check_by_id.assert_not_called()

    def test_a_failed_sync_is_recorded_and_stops_before_any_call(self):
        rig = Rig(self, sync_error=RuntimeError("SECRET-BODY-TEXT"))
        with self.assertRaises(RuntimeError):
            rig.go()
        self.assertEqual(len(rig.conn.params("_START_SQL")), 1)
        p = rig.conn.params("_FINISH_SQL")[0]
        self.assertEqual((p["run_id"], p["status"], p["detail"], p["method"], p["calls"]),
                         (9, "failed", "the unsubscribe sync failed (RuntimeError)", None, None))
        rig.checks.recent_definite.assert_not_called()
        rig.checks.check_by_id.assert_not_called()
        rig.queue.assert_not_called()
        rig.deliver.assert_not_called()

    def test_a_failed_sync_records_no_message_text(self):
        rig = Rig(self, sync_error=RuntimeError("SECRET-BODY-TEXT"))
        with self.assertRaises(RuntimeError):
            rig.go()
        self.assertNotIn("SECRET", str(rig.conn.params("_FINISH_SQL")))

    def test_a_failed_sync_closes_the_run_row_once_and_commits_it(self):
        rig = Rig(self, sync_error=RuntimeError("x"))
        with self.assertRaises(RuntimeError):
            rig.go()
        self.assertEqual(len(rig.conn.params("_FINISH_SQL")), 1)
        self.assertEqual(rig.log[rig.log.index("_FINISH_SQL") + 1], "commit")

    def test_the_run_row_starts_before_the_sync(self):
        rig = Rig(self)
        rig.go()
        self.assertLess(rig.log.index("_START_SQL"), rig.log.index("sync"))

    def test_the_order_of_a_full_click(self):
        rig = Rig(self)
        rig.go()
        wanted = ["preflight", "_ABANDON_SQL", "_START_SQL", "sync", "check_by_id", "build",
                  "lookup", "queue", "_SEND_STEP_SQL", "_FINISH_SQL", "deliver"]
        self.assertEqual([e for e in rig.log if e in wanted], wanted)

    def test_a_stale_running_row_is_closed_before_the_new_one_starts(self):
        rig = Rig(self)
        rig.go()
        names = [n for n, _ in rig.conn.executed]
        self.assertLess(names.index("_ABANDON_SQL"), names.index("_START_SQL"))
        self.assertEqual(rig.conn.params("_ABANDON_SQL"), [{"campaign_id": 5, "step_no": 1}])
        self.assertEqual(rig.conn.params("_START_SQL"),
                         [{"campaign_id": 5, "step_no": 1, "user_id": 3}])

    def test_the_run_row_is_committed_before_any_call_is_spent(self):
        rig = Rig(self)
        rig.go()
        start = rig.log.index("_START_SQL")
        self.assertEqual(rig.log[start + 1], "commit")
        self.assertLess(start, rig.log.index("check_by_id"))

    def test_result_of_a_queued_step(self):
        rig = Rig(self)
        out = rig.go()
        self.assertEqual((out["status"], out["emails"], out["batch_id"], out["calls"],
                          out["method"], out["run_id"]), ("complete", 1, "b-1", 3, "by_id", 9))
        self.assertEqual(out["delivery"], {"sent": 1})

    def test_the_step_is_marked_sent_by_the_clicker(self):
        rig = Rig(self)
        rig.go()
        self.assertEqual(rig.conn.params("_SEND_STEP_SQL"),
                         [{"campaign_id": 5, "step_no": 1, "user_id": 3, "batch_id": "b-1"}])

    def test_the_marked_step_is_committed_with_the_queue_and_before_delivery(self):
        rig = Rig(self)
        rig.go()
        sent = rig.log.index("_SEND_STEP_SQL")
        self.assertEqual(rig.log[sent + 1], "commit")
        self.assertLess(rig.log.index("queue"), sent)
        self.assertLess(sent, rig.log.index("deliver"))

    def test_a_middle_step_does_not_complete_the_campaign(self):
        rig = Rig(self)
        rig.go(step_no=1)
        self.assertEqual(rig.conn.params("_COMPLETE_SQL"), [])

    def test_the_last_step_completes_the_campaign_in_the_same_transaction(self):
        rig = Rig(self)
        rig.go(step_no=2)
        self.assertEqual(rig.conn.params("_COMPLETE_SQL")[0]["user_id"], 3)
        names = [n for n, _ in rig.conn.executed]
        self.assertLess(names.index("_SEND_STEP_SQL"), names.index("_COMPLETE_SQL"))
        sent = rig.log.index("_SEND_STEP_SQL")
        self.assertEqual(rig.log[sent + 1:sent + 3], ["_COMPLETE_SQL", "commit"])

    def test_stops_are_written_in_the_queue_transaction(self):
        stops = [{"realtor_id": 8, "email": "z@x.com", "reason": "bounced"}]
        rig = Rig(self, found=followup("a1@x.com", to_stop=stops))
        rig.go()
        self.assertEqual(rig.conn.params("_STOP_SQL"),
                         [{"campaign_id": 5, "realtor_id": 8, "reason": "bounced"}])
        stop = rig.log.index("_STOP_SQL")
        self.assertLess(rig.log.index("queue"), stop)
        self.assertLess(stop, rig.log.index("_SEND_STEP_SQL"))

    def test_the_run_row_closes_as_complete_before_delivery(self):
        rig = Rig(self)
        rig.go()
        finish = rig.log.index("_FINISH_SQL")
        self.assertLess(finish, rig.log.index("deliver"))
        self.assertEqual(rig.log[finish + 1], "commit")
        p = rig.conn.params("_FINISH_SQL")[0]
        self.assertEqual((p["run_id"], p["status"], p["method"], p["calls"], p["emails"],
                          p["detail"]), (9, "complete", "by_id", 3, 1, None))

    def test_queue_gets_the_built_list_the_step_template_and_the_builders_recipients(self):
        rig = Rig(self, found=followup("b@x.com", "a@x.com"))
        rig.go()
        kw = rig.queue.call_args.kwargs
        self.assertIs(kw["send_list"], rig.found)
        self.assertEqual(kw["template_name"], "follow-up")
        self.assertEqual(kw["allowed_emails"], {"a@x.com", "b@x.com"})
        self.assertEqual((kw["user_id"], kw["email_settings"]), (3, {"x": 1}))
        self.assertEqual(rig.lookup.call_args.args[0], ["a@x.com", "b@x.com"])
        self.assertIs(rig.lookup.call_args.kwargs["cc"], rig.cc)

    def test_queue_is_given_the_latest_sync_run(self):
        rig = Rig(self)
        rig.go()
        self.assertEqual(rig.queue.call_args.kwargs["sync_run_id"], 101)

    def test_delivery_gets_the_batch_sender_and_limit(self):
        rig = Rig(self)
        rig.go(max_emails=2)
        self.assertEqual(rig.deliver.call_args.args, ("b-1",))
        self.assertEqual(rig.deliver.call_args.kwargs,
                         {"sender": {"s": 1}, "cc": rig.cc, "max_emails": 2})

    # ---- nothing to send ----

    def test_nobody_left_writes_the_stops_and_queues_and_sends_nothing(self):
        stops = [{"realtor_id": 8, "email": "z@x.com", "reason": "no_active_listings"},
                 {"realtor_id": 9, "email": "y@x.com", "reason": "unsubscribed"}]
        rig = Rig(self, found=followup(to_stop=stops))
        out = rig.go()
        self.assertEqual((out["status"], out["emails"], out["batch_id"], out["stopped"]),
                         ("nothing_to_send", 0, None, 2))
        self.assertEqual([p["reason"] for p in rig.conn.params("_STOP_SQL")],
                         ["no_active_listings", "unsubscribed"])
        rig.queue.assert_not_called()
        rig.deliver.assert_not_called()
        self.assertEqual(rig.conn.params("_SEND_STEP_SQL"), [])
        self.assertEqual(rig.conn.params("_COMPLETE_SQL"), [])
        self.assertNotIn("delivery", out)

    def test_nobody_left_closes_the_run_row_with_a_reason(self):
        rig = Rig(self, found=followup())
        rig.go()
        p = rig.conn.params("_FINISH_SQL")[0]
        self.assertEqual((p["status"], p["emails"], p["detail"]),
                         ("nothing_to_send", 0, "nobody was left to email"))

    def test_stops_are_committed_when_nothing_is_sent(self):
        rig = Rig(self, found=followup(to_stop=[{"realtor_id": 8, "email": "z", "reason": "bounced"}]))
        rig.go()
        stop = rig.log.index("_STOP_SQL")
        self.assertEqual(rig.log[stop + 1], "commit")

    def test_nothing_queueable_rolls_back_and_still_writes_the_stops(self):
        stops = [{"realtor_id": 8, "email": "z@x.com", "reason": "bounced"}]
        rig = Rig(self, found=followup("a1@x.com", to_stop=stops), queued=None)
        out = rig.go()
        self.assertEqual(out["status"], "nothing_to_send")
        self.assertIn("rollback", rig.log)
        self.assertEqual(len(rig.conn.params("_STOP_SQL")), 1)
        self.assertEqual(rig.conn.params("_SEND_STEP_SQL"), [])
        rig.deliver.assert_not_called()

    def test_the_last_step_with_nobody_left_does_not_complete_the_campaign(self):
        rig = Rig(self, found=followup())
        rig.go(step_no=2)
        self.assertEqual(rig.conn.params("_COMPLETE_SQL"), [])

    # ---- the re-check ----

    def test_no_listings_to_check_spends_nothing(self):
        rig = Rig(self, listings=[], found=followup())
        out = rig.go()
        rig.checks.check_by_id.assert_not_called()
        rig.checks.check_by_zip_pull.assert_not_called()
        self.assertEqual((out["calls"], out["method"]), (0, None))

    def test_listings_with_a_recent_answer_are_skipped_and_none_left_spends_nothing(self):
        rig = Rig(self, listings=[listing(1)], skip={1}, found=followup())
        out = rig.go()
        rig.checks.check_by_id.assert_not_called()
        self.assertEqual(out["calls"], 0)

    def test_skip_and_every_listing_are_passed_to_the_check(self):
        rig = Rig(self, listings=[listing(1), listing(2)], skip={1})
        rig.go()
        kw = rig.checks.check_by_id.call_args.kwargs
        self.assertEqual([i["listing_id"] for i in kw["listings"]], [1, 2])
        self.assertEqual(kw["skip"], {1})
        self.assertEqual((kw["campaign_id"], kw["step_no"], kw["freshness_days"]), (5, 1, 7))

    def test_only_listings_still_to_check_are_asked_for_their_zips(self):
        rig = Rig(self, listings=[listing(1), listing(2)], skip={1})
        rig.go()
        self.assertEqual(rig.checks.listing_zips.call_args.args[1], [2])

    def test_by_id_when_it_is_cheaper(self):
        rig = Rig(self, listings=[listing(1), listing(2)], zip_estimate=5)
        out = rig.go()
        self.assertEqual(out["method"], "by_id")
        rig.checks.check_by_zip_pull.assert_not_called()

    def test_zip_pull_when_it_is_cheaper(self):
        rig = Rig(self, listings=[listing(n) for n in range(1, 6)], zip_estimate=1)
        out = rig.go()
        self.assertEqual((out["method"], out["calls"]), ("zip_pull", 2))
        rig.checks.check_by_id.assert_not_called()
        kw = rig.checks.check_by_zip_pull.call_args.kwargs
        self.assertEqual((kw["emp_id"], kw["estimated_calls"], kw["zips"]), (3, 1, rig.zips))

    def test_a_tie_is_by_id(self):
        rig = Rig(self, listings=[listing(1), listing(2)], zip_estimate=2)
        self.assertEqual(rig.go()["method"], "by_id")

    def test_a_listing_with_no_zip_forces_by_id_even_when_a_pull_is_cheaper(self):
        rig = Rig(self, listings=[listing(n) for n in range(1, 6)], zip_estimate=1,
                  zips={1: "80202", 2: None, 3: "80202", 4: "80202", 5: "80202"})
        self.assertEqual(rig.go()["method"], "by_id")

    def test_a_listing_missing_from_the_zip_lookup_forces_by_id(self):
        rig = Rig(self, listings=[listing(n) for n in range(1, 6)], zip_estimate=1,
                  zips={1: "80202", 2: "80202", 3: "80202", 4: "80202"})
        self.assertEqual(rig.go()["method"], "by_id")

    def test_the_zip_estimate_is_for_the_zips_of_the_listings_still_to_check(self):
        rig = Rig(self, listings=[listing(1), listing(2)], zips={1: "80202", 2: None})
        rig.go()
        self.assertEqual(rig.estimate.call_args.args[1], {"80202"})

    def test_the_list_is_rebuilt_after_the_check_not_taken_from_before(self):
        rig = Rig(self)
        rig.go()
        self.assertLess(rig.log.index("check_by_id"), rig.log.index("build"))
        self.assertEqual(rig.build.call_args.args[1:3], (5, 1))

    # ---- the cap ----

    def test_the_cap_applies_by_default(self):
        rig = Rig(self)
        rig.go()
        self.assertEqual(rig.fetch_hits.call_args.kwargs["cap_days"], sendlist.CAP_DAYS)
        self.assertEqual(rig.build.call_args.kwargs["cap_days"], sendlist.CAP_DAYS)

    def test_an_override_removes_the_cap_for_both_reads(self):
        rig = Rig(self)
        rig.go(override_cap=True)
        self.assertIsNone(rig.fetch_hits.call_args.kwargs["cap_days"])
        self.assertIsNone(rig.build.call_args.kwargs["cap_days"])

    def test_a_given_cap_is_used(self):
        rig = Rig(self)
        rig.go(cap_days=3)
        self.assertEqual(rig.build.call_args.kwargs["cap_days"], 3)

    def test_today_is_the_denver_date(self):
        rig = Rig(self)
        rig.go()
        from datetime import date
        self.assertEqual(rig.checks.check_by_id.call_args.kwargs["today"], date(2026, 10, 18))

    # ---- the sync window ----

    def test_a_quick_check_syncs_once(self):
        rig = Rig(self)
        rig.clock_values = iter([0, 599])
        rig.go()
        self.assertEqual(rig.sync.call_count, 1)

    def test_exactly_the_window_does_not_sync_again(self):
        rig = Rig(self)
        rig.clock_values = iter([0, 600])
        rig.go()
        self.assertEqual(rig.sync.call_count, 1)

    def test_a_long_check_syncs_again_before_queueing(self):
        rig = Rig(self)
        rig.clock_values = iter([0, 601])
        rig.go()
        self.assertEqual(rig.sync.call_count, 2)
        self.assertEqual(rig.queue.call_args.kwargs["sync_run_id"], 102)
        self.assertLess(rig.log.index("build"), rig.log.index("sync", rig.log.index("build")))

    def test_the_second_sync_comes_before_the_queue(self):
        rig = Rig(self)
        rig.clock_values = iter([0, 601])
        rig.go()
        later = rig.log.index("sync", rig.log.index("build"))
        self.assertLess(later, rig.log.index("queue"))

    def test_a_failed_second_sync_closes_the_run_as_failed_and_queues_nothing(self):
        rig = Rig(self)
        rig.clock_values = iter([0, 601])
        calls = []

        def sync(**k):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("sync down")
            return {"run_id": 1}
        with self.assertRaises(RuntimeError):
            rig.go(sync=sync)
        rig.queue.assert_not_called()
        p = rig.conn.params("_FINISH_SQL")[0]
        self.assertEqual(p["status"], "failed")

    # ---- failure ----

    def test_a_stopped_check_closes_the_run_as_failed_with_the_calls_spent(self):
        rig = Rig(self)
        rig.checks.check_by_id.side_effect = Stopped("10 lookups in a row failed", 12)
        out = rig.go()
        self.assertEqual((out["status"], out["calls"], out["reason"], out["emails"]),
                         ("failed", 12, "10 lookups in a row failed", 0))
        p = rig.conn.params("_FINISH_SQL")[0]
        self.assertEqual((p["status"], p["calls"], p["detail"], p["method"]),
                         ("failed", 12, "10 lookups in a row failed", "by_id"))
        rig.queue.assert_not_called()
        rig.deliver.assert_not_called()
        self.assertEqual(rig.conn.params("_SEND_STEP_SQL"), [])
        self.assertEqual(rig.conn.params("_STOP_SQL"), [])

    def test_a_stopped_check_rolls_back_before_it_writes_the_failure(self):
        rig = Rig(self)
        rig.checks.check_by_id.side_effect = Stopped("x", 1)
        rig.go()
        self.assertEqual(rig.log[rig.log.index("_FINISH_SQL") - 1], "rollback")

    def test_an_unexpected_error_closes_the_run_without_its_text_and_is_raised(self):
        rig = Rig(self)
        rig.checks.check_by_id.side_effect = ValueError("SECRET-BODY-TEXT")
        with self.assertRaises(ValueError):
            rig.go()
        p = rig.conn.params("_FINISH_SQL")[0]
        self.assertEqual(p["status"], "failed")
        self.assertNotIn("SECRET", str(p))
        rig.deliver.assert_not_called()

    def test_a_queue_error_closes_the_run_as_failed_and_is_raised(self):
        rig = Rig(self)
        rig.queue.side_effect = dripstep.send.SendError("sync too old")
        with self.assertRaises(dripstep.send.SendError):
            rig.go()
        self.assertEqual(rig.conn.params("_FINISH_SQL")[0]["status"], "failed")
        self.assertEqual(rig.conn.params("_SEND_STEP_SQL"), [])

    def test_a_delivery_error_does_not_touch_the_closed_run_row(self):
        rig = Rig(self)
        rig.deliver.side_effect = RuntimeError("cc down")
        with self.assertRaises(RuntimeError):
            rig.go()
        self.assertEqual(len(rig.conn.params("_FINISH_SQL")), 1)
        self.assertEqual(rig.conn.params("_FINISH_SQL")[0]["status"], "complete")

    def test_the_run_row_detail_is_cut_to_300(self):
        conn = FakeConn([])
        dripstep._finish_run(conn, 1, "failed", detail="x" * 400)
        self.assertEqual(len(conn.params("_FINISH_SQL")[0]["detail"]), 300)


if __name__ == "__main__":
    unittest.main()
