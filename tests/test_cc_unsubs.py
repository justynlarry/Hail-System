"""Tests for hailsys/constantcontact/unsubs.py -- run order and failure
handling against a fake connection.  No network, no database.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_unsubs
"""

import contextlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.constantcontact import api, unsubs
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, *, lock=True, last_ok=None):
        self.lock = lock
        self.last_ok = last_ok
        self.events = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.events.append(("sql", text, params))
        if "pg_try_advisory_lock" in text:
            return FakeCursor({"got": self.lock})
        if text.startswith("SELECT emp_id FROM users"):
            return FakeCursor({"emp_id": 1})
        if text.startswith("SELECT started_at FROM cc_sync_runs"):
            return FakeCursor({"started_at": self.last_ok} if self.last_ok else None)
        if text.startswith("INSERT INTO cc_sync_runs"):
            return FakeCursor({"run_id": 7})
        return FakeCursor(None)

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        self.events.append(("rollback",))


def tag(event):
    if event[0] != "sql":
        return event[0]
    text = event[1]
    for key, name in (("pg_try_advisory_lock", "lock"),
                      ("pg_advisory_unlock", "unlock"),
                      ("SET status = 'failed'", "abandon"),
                      ("INSERT INTO cc_sync_runs", "start"),
                      ("UPDATE cc_sync_runs SET status = %s", "finish")):
        if key in text:
            return name
    return "other"


def run(conn, contacts=(), outcomes=(), iterate_raises=None):
    @contextlib.contextmanager
    def fake_connection():
        yield conn

    def fake_iter(updated_after=None, **kwargs):
        conn.events.append(("iterate", updated_after))
        if iterate_raises:
            raise iterate_raises
        yield from contacts

    with mock.patch.object(unsubs, "get_connection", fake_connection), \
         mock.patch.object(unsubs.api, "iter_unsubscribed", side_effect=fake_iter), \
         mock.patch.object(unsubs, "_record", side_effect=list(outcomes)):
        return unsubs.run_sync(triggered_by=5)


def finish_of(conn):
    return [e for e in conn.events if tag(e) == "finish"][0]


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RunTest(unittest.TestCase):
    def test_a_busy_lock_raises_and_does_nothing(self):
        conn = FakeConn(lock=False)
        with self.assertRaises(unsubs.SyncBusy):
            run(conn)
        order = [tag(e) for e in conn.events]
        self.assertNotIn("start", order)
        self.assertNotIn("iterate", order)
        self.assertNotIn("unlock", order)

    def test_order_and_counts(self):
        conn = FakeConn()
        result = run(conn, contacts=[{}, {}, {}],
                     outcomes=["inserted", "already_present", "conflict"])
        order = [tag(e) for e in conn.events]
        for earlier, later in (("lock", "abandon"), ("abandon", "start"),
                               ("start", "iterate"), ("iterate", "finish"),
                               ("finish", "unlock")):
            self.assertLess(order.index(earlier), order.index(later))
        # the run row is committed before any work
        self.assertEqual(order[order.index("start") + 1], "commit")
        self.assertEqual(finish_of(conn)[2][:5], ("ok", 3, 1, 1, 1))
        self.assertEqual((result["fetched"], result["inserted"], result["conflicts"]),
                         (3, 1, 1))

    def test_a_failure_marks_the_run_failed_and_reraises(self):
        conn = FakeConn()
        err = api.ApiError("Constant Contact refused the request (500, unknown).")
        with self.assertRaises(api.ApiError):
            run(conn, iterate_raises=err)
        params = finish_of(conn)[2]
        self.assertEqual(params[0], "failed")
        self.assertIn("refused the request", params[5])
        self.assertIn("unlock", [tag(e) for e in conn.events])

    def test_an_unknown_failure_records_only_its_class_name(self):
        conn = FakeConn()
        with self.assertRaises(RuntimeError):
            run(conn, iterate_raises=RuntimeError("TOKEN-SECRET detail"))
        self.assertEqual(finish_of(conn)[2][5], "RuntimeError")

    def test_the_watermark_is_the_last_good_start_minus_the_overlap(self):
        conn = FakeConn(last_ok=datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc))
        run(conn)
        iterate = [e for e in conn.events if e[0] == "iterate"][0]
        self.assertEqual(iterate[1], "2026-10-06T12:00:00Z")

    def test_the_first_run_is_a_full_pull(self):
        conn = FakeConn()
        run(conn)
        iterate = [e for e in conn.events if e[0] == "iterate"][0]
        self.assertIsNone(iterate[1])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class HelperTest(unittest.TestCase):
    def test_parse_when(self):
        want = datetime(2026, 10, 7, 17, 18, 52, tzinfo=timezone.utc)
        self.assertEqual(unsubs._parse_when("2026-10-07T17:18:52Z"), want)
        self.assertEqual(unsubs._parse_when("2026-10-07T17:18:52+00:00"), want)
        self.assertEqual(unsubs._parse_when("2026-10-07T17:18:52"), want)
        self.assertIsNone(unsubs._parse_when("garbage"))
        self.assertIsNone(unsubs._parse_when(None))

    def test_clean_text(self):
        self.assertEqual(unsubs._clean("Contact"), "Contact")
        self.assertEqual(unsubs._clean("x; DROP TABLE"), "unknown")
        self.assertEqual(unsubs._clean(None), "unknown")


if __name__ == "__main__":
    unittest.main()