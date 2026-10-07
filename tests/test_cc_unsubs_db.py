"""Database tests for unsubs._record against the real dnc_list and realtors.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_unsubs_db

Nothing is committed: _record never commits and tearDown rolls back.  Skips
without a database.  Each test uses its own address.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.constantcontact import unsubs
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

HAVE_DB = bool(os.environ.get("PGHOST"))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(HAVE_DB, "no database (PGHOST unset)")
class RecordTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.system = self.conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.address = f"sync-{uuid.uuid4()}@example.invalid"

    def contact(self, address=None, when="2026-10-07T17:18:52Z"):
        return {"email_address": {
            "address": address or self.address,
            "permission_to_send": "unsubscribed",
            "opt_out_date": when, "opt_out_source": "Contact",
            "opt_out_reason": "Other"}}

    def row(self, address=None):
        return self.conn.execute(
            "SELECT * FROM dnc_list WHERE email_norm = lower(trim(%s))",
            (address or self.address,)).fetchone()

    def test_a_new_address_is_inserted_as_an_unsubscribe(self):
        self.assertEqual(unsubs._record(self.conn, self.contact(), self.system),
                         "inserted")
        row = self.row()
        self.assertEqual(row["source"], "unsubscribe")
        self.assertEqual(row["added_by"], self.system)
        self.assertEqual(row["added_at"],
                         datetime(2026, 10, 7, 17, 18, 52, tzinfo=timezone.utc))
        self.assertTrue(row["reason"].startswith("Constant Contact: unsubscribed"))
        self.assertIn("opt_out_source=Contact", row["reason"])
        self.assertIsNone(row["removed_at"])

    def test_a_second_time_is_already_present(self):
        unsubs._record(self.conn, self.contact(), self.system)
        self.assertEqual(unsubs._record(self.conn, self.contact(), self.system),
                         "already_present")

    def test_a_removed_row_is_a_conflict_and_stays_removed(self):
        unsubs._record(self.conn, self.contact(), self.system)
        self.conn.execute(
            "UPDATE dnc_list SET removed_at = now(), removed_by = %s "
            "WHERE email_norm = lower(%s)", (self.system, self.address))
        self.assertEqual(unsubs._record(self.conn, self.contact(), self.system),
                         "conflict")
        self.assertIsNotNone(self.row()["removed_at"])

    def test_the_realtor_is_linked_by_address(self):
        realtor = self.conn.execute(
            "INSERT INTO realtors (agent_email) VALUES (%s) RETURNING realtor_id",
            (self.address,)).fetchone()["realtor_id"]
        unsubs._record(self.conn, self.contact(), self.system)
        self.assertEqual(self.row()["realtor_id"], realtor)

    def test_case_and_spaces_are_normalised(self):
        messy = f" Mixed-{uuid.uuid4()}@Example.INVALID "
        self.assertEqual(
            unsubs._record(self.conn, self.contact(address=messy), self.system),
            "inserted")
        self.assertEqual(self.row(messy)["email_norm"], messy.strip().lower())

    def test_a_contact_without_an_address_is_skipped(self):
        self.assertEqual(unsubs._record(self.conn, {"email_address": {}}, self.system),
                         "skipped")


if __name__ == "__main__":
    unittest.main()