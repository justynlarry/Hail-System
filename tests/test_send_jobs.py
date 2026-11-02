"""Tests for hailsys/web/sendjobs.py: the lock around a send.  Real advisory locks, no writes,
and the engine (deliver.run_send) is always replaced.

The send_runs record (sql/065, item 196):  in SendJobsTest the four functions that write it are replaced by a
recorder, because the thread commits and send_runs rows can never be deleted;  RunRecordTest runs those functions
against the real table on a connection that is always rolled back.  What they pin:  a send or resume writes one
row, closed complete with its batch and count, nothing_to_send, failed with the exception's class name and never
its message, or busy;  a job that holds the lock closes any other running row as abandoned.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_send_jobs
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from hailsys.web import sendjobs
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DONE = {"batch_id": None, "emails": 0}
BATCH = "7d1f0c52-3b5e-4c39-9d3e-2f6a0e4c1b8a"


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class SendJobsTest(unittest.TestCase):
    def setUp(self):
        # What the job would write to send_runs, in order:  (what, kind or status, details).
        self.record = []

        def open_run(conn, kind, user_id, batch_id=None):
            self.record.append(("open", kind, batch_id))
            return 99

        def close_run(conn, run_id, status, *, batch_id=None, emails=None, detail=None):
            self.record.append(("close", status, {"batch_id": batch_id, "emails": emails, "detail": detail}))

        def abandon(conn, run_id):
            self.record.append(("abandon", run_id, None))

        def busy(conn, kind, user_id, batch_id=None):
            self.record.append(("busy", kind, batch_id))

        for name, fake in (("_open_run", open_run), ("_close_run", close_run),
                           ("_abandon_stale", abandon), ("_record_busy", busy)):
            patcher = mock.patch.object(sendjobs, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def hold(self):
        other = psycopg.connect()
        self.addCleanup(other.close)
        other.execute("SELECT pg_advisory_lock(hashtext(%s))", (sendjobs.LOCK_KEY,))

    def test_idle_is_not_busy(self):
        self.assertFalse(sendjobs.is_busy())

    def test_the_lock_is_held_during_the_run_and_released_after(self):
        seen = []

        def run_send(**kw):
            seen.append(sendjobs.is_busy())
            return DONE

        with mock.patch.object(sendjobs.deliver, "run_send", side_effect=run_send):
            sendjobs._run(1, {})
        self.assertEqual(seen, [True])
        self.assertFalse(sendjobs.is_busy())

    def test_a_failing_run_releases_the_lock_and_does_not_raise(self):
        with mock.patch.object(sendjobs.deliver, "run_send", side_effect=RuntimeError("x")):
            with self.assertLogs("hailsys.web.sendjobs", level="ERROR"):
                sendjobs._run(1, {})
        self.assertFalse(sendjobs.is_busy())

    def test_a_busy_lock_means_the_run_never_starts(self):
        self.hold()
        with mock.patch.object(sendjobs.deliver, "run_send") as run_send:
            sendjobs._run(1, {})
        run_send.assert_not_called()

    # ---- the send_runs record (item 196) ----

    def test_a_send_that_queues_a_batch_is_recorded_complete(self):
        done = {"batch_id": BATCH, "emails": 3, "delivery": {"sent": 3}}
        with mock.patch.object(sendjobs.deliver, "run_send", return_value=done):
            sendjobs._run(1, {})
        self.assertEqual(self.record, [
            ("open", "send", None), ("abandon", 99, None),
            ("close", "complete", {"batch_id": BATCH, "emails": 3, "detail": None})])

    def test_a_send_that_finds_nobody_is_recorded_nothing_to_send(self):
        with mock.patch.object(sendjobs.deliver, "run_send", return_value=DONE):
            sendjobs._run(1, {})
        self.assertEqual(self.record[-1], ("close", "nothing_to_send",
                                           {"batch_id": None, "emails": 0, "detail": None}))

    def test_a_failed_send_is_recorded_with_the_class_name_not_the_message(self):
        class SyncFailed(Exception):
            pass
        with mock.patch.object(sendjobs.deliver, "run_send", side_effect=SyncFailed("provider said SECRET")):
            with self.assertLogs("hailsys.web.sendjobs", level="ERROR"):
                sendjobs._run(1, {})
        self.assertEqual(self.record[-1], ("close", "failed",
                                           {"batch_id": None, "emails": None, "detail": "SyncFailed"}))
        self.assertNotIn("SECRET", repr(self.record))

    def test_a_send_that_finds_the_lock_taken_is_recorded_busy(self):
        self.hold()
        with mock.patch.object(sendjobs.deliver, "run_send"):
            sendjobs._run(1, {})
        self.assertEqual(self.record, [("busy", "send", None)])

    def test_a_resume_is_recorded_with_its_batch_and_the_number_delivered(self):
        with mock.patch.object(sendjobs.deliver, "run_resume", return_value={"sent": 2}):
            sendjobs._run_resume(1, BATCH, {})
        self.assertEqual(self.record, [
            ("open", "resume", BATCH), ("abandon", 99, None),
            ("close", "complete", {"batch_id": None, "emails": 2, "detail": None})])

    def test_a_failed_resume_is_recorded_failed(self):
        with mock.patch.object(sendjobs.deliver, "run_resume", side_effect=RuntimeError("x")):
            with self.assertLogs("hailsys.web.sendjobs", level="ERROR"):
                sendjobs._run_resume(1, BATCH, {})
        self.assertEqual(self.record[-1][:2], ("close", "failed"))
        self.assertEqual(self.record[-1][2]["detail"], "RuntimeError")

    def test_a_resume_that_finds_the_lock_taken_is_recorded_busy(self):
        self.hold()
        with mock.patch.object(sendjobs.deliver, "run_resume"):
            sendjobs._run_resume(1, BATCH, {})
        self.assertEqual(self.record, [("busy", "resume", BATCH)])

    def test_start_send_refuses_when_busy(self):
        self.hold()
        with self.assertRaises(sendjobs.SendInProgress):
            sendjobs.start_send(user_id=1)

    def test_start_send_starts_one_daemon_thread(self):
        with mock.patch.object(sendjobs, "is_busy", return_value=False), \
             mock.patch.object(sendjobs.threading, "Thread") as thread:
            sendjobs.start_send(user_id=7, storm_days=["d"])
        thread.assert_called_once_with(target=sendjobs._run, args=(7, {"storm_days": ["d"]}),
                                       daemon=True)
        thread.return_value.start.assert_called_once()

    # ---- resume (item 180, 5d): same lock, same thread rules ----

    def test_the_lock_is_held_during_a_resume_and_released_after(self):
        seen = []

        def run_resume(**kw):
            seen.append(sendjobs.is_busy())
            return {"sent": 0}

        with mock.patch.object(sendjobs.deliver, "run_resume", side_effect=run_resume):
            sendjobs._run_resume(1, BATCH, {})
        self.assertEqual(seen, [True])
        self.assertFalse(sendjobs.is_busy())

    def test_a_failing_resume_releases_the_lock_and_does_not_raise(self):
        with mock.patch.object(sendjobs.deliver, "run_resume", side_effect=RuntimeError("x")):
            with self.assertLogs("hailsys.web.sendjobs", level="ERROR"):
                sendjobs._run_resume(1, BATCH, {})
        self.assertFalse(sendjobs.is_busy())

    def test_a_busy_lock_means_the_resume_never_starts(self):
        self.hold()
        with mock.patch.object(sendjobs.deliver, "run_resume") as run_resume:
            sendjobs._run_resume(1, BATCH, {})
        run_resume.assert_not_called()

    def test_start_resume_refuses_when_busy_and_checks_nothing(self):
        self.hold()
        with mock.patch.object(sendjobs.deliver, "check_resumable") as check:
            with self.assertRaises(sendjobs.SendInProgress):
                sendjobs.start_resume(user_id=1, batch_id="b")
        check.assert_not_called()

    def test_start_resume_refuses_before_a_thread_starts(self):
        with mock.patch.object(sendjobs, "is_busy", return_value=False), \
             mock.patch.object(sendjobs.deliver, "check_resumable",
                               side_effect=sendjobs.BatchTooOld("too old")), \
             mock.patch.object(sendjobs.threading, "Thread") as thread:
            with self.assertRaises(sendjobs.BatchTooOld):
                sendjobs.start_resume(user_id=1, batch_id="b")
        thread.assert_not_called()

    def test_start_resume_starts_one_daemon_thread(self):
        with mock.patch.object(sendjobs, "is_busy", return_value=False), \
             mock.patch.object(sendjobs.deliver, "check_resumable"), \
             mock.patch.object(sendjobs.threading, "Thread") as thread:
            sendjobs.start_resume(user_id=7, batch_id="b", sender="s", max_emails=3)
        thread.assert_called_once_with(
            target=sendjobs._run_resume,
            args=(7, "b", {"sender": "s", "max_emails": 3}), daemon=True)
        thread.return_value.start.assert_called_once()

    # ---- drip step (item 204): the same lock, so a step and a send never overlap ----

    STEP_DONE = {"status": "complete", "method": "by_id", "calls": 4, "emails": 2,
                 "delivery": {"sent": 2}}

    def test_the_lock_is_held_during_a_drip_step_and_released_after(self):
        seen = []

        def run_step(**kw):
            seen.append(sendjobs.is_busy())
            return self.STEP_DONE

        with mock.patch.object(sendjobs.dripstep, "run_step", side_effect=run_step):
            sendjobs._run_drip_step(1, 5, 2, {})
        self.assertEqual(seen, [True])
        self.assertFalse(sendjobs.is_busy())

    def test_a_drip_step_gets_the_user_the_step_and_the_extra_arguments(self):
        with mock.patch.object(sendjobs.dripstep, "run_step", return_value=self.STEP_DONE) as run:
            sendjobs._run_drip_step(1, 5, 2, {"sender": "s", "max_emails": 3})
        run.assert_called_once_with(user_id=1, campaign_id=5, step_no=2, sender="s",
                                    max_emails=3)

    def test_a_finished_drip_step_logs_its_counts(self):
        with mock.patch.object(sendjobs.dripstep, "run_step", return_value=self.STEP_DONE):
            with self.assertLogs("hailsys.web.sendjobs", level="INFO") as logs:
                sendjobs._run_drip_step(1, 5, 2, {})
        line = logs.output[0]
        for part in ("campaign=5", "step=2", "status=complete", "method=by_id", "calls=4",
                     "emails=2"):
            self.assertIn(part, line)

    def test_a_failing_drip_step_releases_the_lock_and_does_not_raise(self):
        with mock.patch.object(sendjobs.dripstep, "run_step", side_effect=RuntimeError("x")):
            with self.assertLogs("hailsys.web.sendjobs", level="ERROR"):
                sendjobs._run_drip_step(1, 5, 2, {})
        self.assertFalse(sendjobs.is_busy())

    def test_a_refused_drip_step_is_a_warning_not_an_error_and_releases_the_lock(self):
        with mock.patch.object(sendjobs.dripstep, "run_step",
                               side_effect=sendjobs.StepRefused("The campaign is paused.")):
            with self.assertLogs("hailsys.web.sendjobs", level="WARNING") as logs:
                sendjobs._run_drip_step(1, 5, 2, {})
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, "WARNING")
        self.assertIn("campaign=5", logs.output[0])
        self.assertFalse(sendjobs.is_busy())

    def test_a_busy_lock_means_the_drip_step_never_starts(self):
        self.hold()
        with mock.patch.object(sendjobs.dripstep, "run_step") as run:
            with self.assertLogs("hailsys.web.sendjobs", level="WARNING"):
                sendjobs._run_drip_step(1, 5, 2, {})
        run.assert_not_called()

    def test_start_drip_step_refuses_when_busy_and_checks_nothing(self):
        self.hold()
        with mock.patch.object(sendjobs.dripstep, "preflight") as preflight:
            with self.assertRaises(sendjobs.SendInProgress):
                sendjobs.start_drip_step(user_id=1, campaign_id=5, step_no=2)
        preflight.assert_not_called()

    def test_start_drip_step_refuses_before_a_thread_starts(self):
        with mock.patch.object(sendjobs, "is_busy", return_value=False), \
             mock.patch.object(sendjobs.dripstep, "preflight",
                               side_effect=sendjobs.StepRefused("paused")), \
             mock.patch.object(sendjobs.threading, "Thread") as thread:
            with self.assertRaises(sendjobs.StepRefused):
                sendjobs.start_drip_step(user_id=1, campaign_id=5, step_no=2)
        thread.assert_not_called()

    def test_start_drip_step_checks_the_step_it_was_asked_for(self):
        with mock.patch.object(sendjobs, "is_busy", return_value=False), \
             mock.patch.object(sendjobs.dripstep, "preflight") as preflight, \
             mock.patch.object(sendjobs.threading, "Thread"):
            sendjobs.start_drip_step(user_id=1, campaign_id=5, step_no=2)
        self.assertEqual(preflight.call_args.args[1:], (5, 2))

    def test_step_problem_is_the_preflight_message(self):
        with mock.patch.object(sendjobs.dripstep, "preflight",
                               side_effect=sendjobs.StepRefused("The campaign is paused.")) as pre:
            self.assertEqual(sendjobs.step_problem(5, 2), "The campaign is paused.")
        self.assertEqual(pre.call_args.args[1:], (5, 2))

    def test_step_problem_is_none_when_the_step_can_run(self):
        with mock.patch.object(sendjobs.dripstep, "preflight") as pre:
            self.assertIsNone(sendjobs.step_problem(5, 2))
        pre.assert_called_once()

    def test_step_problem_starts_nothing(self):
        with mock.patch.object(sendjobs.dripstep, "preflight"), \
             mock.patch.object(sendjobs.threading, "Thread") as thread:
            sendjobs.step_problem(5, 2)
        thread.assert_not_called()

    def test_start_drip_step_starts_one_daemon_thread(self):
        with mock.patch.object(sendjobs, "is_busy", return_value=False), \
             mock.patch.object(sendjobs.dripstep, "preflight"), \
             mock.patch.object(sendjobs.threading, "Thread") as thread:
            sendjobs.start_drip_step(user_id=7, campaign_id=5, step_no=2, sender="s",
                                     max_emails=3)
        thread.assert_called_once_with(
            target=sendjobs._run_drip_step,
            args=(7, 5, 2, {"sender": "s", "max_emails": 3}), daemon=True)
        thread.return_value.start.assert_called_once()

    def test_the_route_can_catch_a_refused_step_from_sendjobs(self):
        self.assertIs(sendjobs.StepRefused, sendjobs.dripstep.StepRefused)

    # ---- discard (item 199): the same global lock, no thread ----

    def test_the_lock_is_held_during_a_discard_and_released_after(self):
        seen = []

        def discard(batch_id, *, user_id):
            seen.append(sendjobs.is_busy())
            return 2

        with mock.patch.object(sendjobs.deliver, "discard_queued", side_effect=discard):
            self.assertEqual(sendjobs.discard_queued(user_id=1, batch_id="b"), 2)
        self.assertEqual(seen, [True])
        self.assertFalse(sendjobs.is_busy())

    def test_a_discard_is_refused_while_a_send_runs(self):
        self.hold()
        with mock.patch.object(sendjobs.deliver, "discard_queued") as discard:
            with self.assertRaises(sendjobs.SendInProgress):
                sendjobs.discard_queued(user_id=1, batch_id="b")
        discard.assert_not_called()

    def test_a_failing_discard_releases_the_lock_and_raises(self):
        with mock.patch.object(sendjobs.deliver, "discard_queued",
                               side_effect=sendjobs.BatchBusy("busy")):
            with self.assertRaises(sendjobs.BatchBusy):
                sendjobs.discard_queued(user_id=1, batch_id="b")
        self.assertFalse(sendjobs.is_busy())

    def test_the_discard_count_comes_from_the_engine(self):
        with mock.patch.object(sendjobs.deliver, "count_untouched", return_value=3) as count:
            self.assertEqual(sendjobs.count_discardable("b"), 3)
        count.assert_called_once_with("b")

    # ---- the call count for the screens (item 191) ----

    def test_calls_budget_reports_what_is_used_what_remains_and_what_is_usable(self):
        with mock.patch.object(sendjobs.budget, "used", return_value=1234):
            self.assertEqual(sendjobs.calls_budget(),
                             {"used": 1234, "limit": 10000, "headroom": 200,
                              "remaining": 8766, "usable": 8566})

    def test_the_headroom_is_held_back_from_what_is_usable_but_not_from_what_remains(self):
        with mock.patch.object(sendjobs.budget, "used", return_value=9900):
            got = sendjobs.calls_budget()
        self.assertEqual((got["remaining"], got["usable"]), (100, 0))

    def test_calls_budget_is_never_negative(self):
        with mock.patch.object(sendjobs.budget, "used", return_value=12000):
            got = sendjobs.calls_budget()
        self.assertEqual((got["remaining"], got["usable"]), (0, 0))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class RunRecordTest(unittest.TestCase):
    """The send_runs writers against the real table and its guard (sql/065).  Nothing is committed."""

    def setUp(self):
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if self.conn.execute("SELECT to_regclass('send_runs') IS NULL AS missing").fetchone()["missing"]:
            self.fail("sql/065_send_runs.sql is not applied")
        self.user = self.conn.execute("SELECT emp_id FROM users ORDER BY emp_id LIMIT 1").fetchone()["emp_id"]

    def row(self, run_id):
        return self.conn.execute("SELECT * FROM send_runs WHERE run_id = %s", (run_id,)).fetchone()

    def test_a_run_opens_running_and_closes_once(self):
        run_id = sendjobs._open_run(self.conn, "send", self.user)
        self.assertEqual(self.row(run_id)["status"], "running")
        sendjobs._close_run(self.conn, run_id, "complete", batch_id=BATCH, emails=3)
        got = self.row(run_id)
        self.assertEqual((got["status"], str(got["batch_id"]), got["emails"]), ("complete", BATCH, 3))
        self.assertIsNotNone(got["finished_at"])
        with self.assertRaises(psycopg.errors.RestrictViolation):
            sendjobs._close_run(self.conn, run_id, "failed", detail="X")

    def test_a_resume_keeps_the_batch_it_was_opened_with(self):
        run_id = sendjobs._open_run(self.conn, "resume", self.user, BATCH)
        sendjobs._close_run(self.conn, run_id, "complete", emails=2)
        self.assertEqual(str(self.row(run_id)["batch_id"]), BATCH)

    def test_busy_is_written_already_closed(self):
        with mock.patch.object(self.conn, "commit"):           # the job commits;  this test must not
            sendjobs._record_busy(self.conn, "send", self.user)
        got = self.conn.execute("SELECT * FROM send_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        self.assertEqual(got["status"], "busy")
        self.assertIsNotNone(got["finished_at"])

    def test_a_job_holding_the_lock_closes_other_running_rows_as_abandoned(self):
        stale = sendjobs._open_run(self.conn, "send", self.user)
        mine = sendjobs._open_run(self.conn, "send", self.user)
        sendjobs._abandon_stale(self.conn, mine)
        self.assertEqual((self.row(stale)["status"], self.row(mine)["status"]), ("abandoned", "running"))


if __name__ == "__main__":
    unittest.main()
