"""Tests for hailsys/constantcontact/budget.py, on the real database (item 191).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_budget

cc_api_calls is append-only (no UPDATE, no DELETE), so a row written here could never be removed.
The test connection is wrapped in NoCommit, as in test_send_deliver: commit() does nothing and
the whole transaction is rolled back at the end, so nothing is left behind.
"""

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.constantcontact import budget
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


class NoCommit:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        pass

    def rollback(self):
        pass


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class BudgetTest(unittest.TestCase):
    def setUp(self):
        raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(raw.close)
        self.addCleanup(raw.rollback)
        self.conn = NoCommit(raw)

        @contextmanager
        def fake():
            yield self.conn

        patcher = mock.patch.object(budget, "get_connection", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.start = budget.used()          # whatever is really there, so the tests add to it

    def rows(self):
        return self.conn.execute("SELECT method, endpoint FROM cc_api_calls "
                                 "ORDER BY call_id DESC").fetchall()

    def test_a_call_is_recorded_with_its_method_and_first_path_segment(self):
        budget.record_call("POST", "/emails/activities/abc123/schedules")
        self.assertEqual(self.rows()[0], {"method": "POST", "endpoint": "emails"})
        self.assertEqual(budget.used(), self.start + 1)

    def test_a_query_string_and_the_root_path_are_handled(self):
        budget.record_call("GET", "/contacts?email=a@example.com")
        self.assertEqual(self.rows()[0]["endpoint"], "contacts")
        budget.record_call("GET", "/")
        self.assertEqual(self.rows()[0]["endpoint"], "root")

    def test_an_unknown_method_is_refused(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            budget.record_call("PATCH", "/contacts")

    def test_only_the_last_24_hours_count(self):
        for age in ("25 hours", "23 hours", "1 minute"):
            self.conn.execute("INSERT INTO cc_api_calls (called_at, method, endpoint) "
                              "VALUES (now() - %s::interval, 'GET', 'contacts')", (age,))
        self.assertEqual(budget.used(), self.start + 2)

    def test_the_log_cannot_be_changed(self):
        budget.record_call("GET", "/contacts")
        with self.assertRaises(psycopg.Error):
            self.conn.execute("UPDATE cc_api_calls SET endpoint = 'x'")

    def test_the_log_cannot_be_deleted(self):
        budget.record_call("GET", "/contacts")
        with self.assertRaises(psycopg.Error):
            self.conn.execute("DELETE FROM cc_api_calls")

    def test_remaining_is_the_limit_less_what_was_used(self):
        with mock.patch.object(budget, "used", return_value=1234):
            self.assertEqual(budget.remaining(), budget.DAILY_LIMIT - 1234)

    def test_remaining_is_never_negative(self):
        with mock.patch.object(budget, "used", return_value=budget.DAILY_LIMIT + 50):
            self.assertEqual(budget.remaining(), 0)

    def test_the_budget_runs_out_at_the_headroom(self):
        edge = budget.DAILY_LIMIT - budget.HEADROOM
        with mock.patch.object(budget, "used", return_value=edge - 1):
            self.assertFalse(budget.exhausted())
        with mock.patch.object(budget, "used", return_value=edge):
            self.assertTrue(budget.exhausted())


if __name__ == "__main__":
    unittest.main()
