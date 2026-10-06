"""Tests for hailsys/queries/ccstate.py -- pure, with a mock connection."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hailsys.queries import ccstate

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def conn_returning(row):
    conn = mock.Mock()
    conn.execute.return_value.fetchone.return_value = row
    return conn


class StateTest(unittest.TestCase):
    def test_no_row_means_not_connected(self):
        self.assertEqual(ccstate.fetch_state(conn_returning(None)),
                         {"connected": False, "issued_at": None})

    def test_a_row_means_connected(self):
        self.assertEqual(ccstate.fetch_state(conn_returning({"created_at": NOW})),
                         {"connected": True, "issued_at": NOW})

    def test_it_never_selects_a_token_column(self):
        conn = conn_returning(None)
        ccstate.fetch_state(conn)
        self.assertNotIn("_enc", conn.execute.call_args[0][0])


if __name__ == "__main__":
    unittest.main()