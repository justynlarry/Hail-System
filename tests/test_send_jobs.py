"""Tests for hailsys/web/sendjobs.py: the lock around a send.  Real advisory locks, no writes,
and the engine (deliver.run_send) is always replaced.

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


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class SendJobsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
