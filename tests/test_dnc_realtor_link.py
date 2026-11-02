"""Database tests for sql/064: dnc_list.realtor_id is kept by the database (item 130).

    docker compose --profile test run --rm -T test python -m unittest tests.test_dnc_realtor_link

Runs as hail_app, the role that adds suppressions and realtors.  Nothing is committed: every test rolls back,
and each uses its own example.invalid addresses.  What they pin: a suppression for a known realtor's address is
linked at insert, whatever its case and spaces;  one for an unknown address stays unlinked;  a realtor added later
links it, and the pull's upsert of an existing realtor neither fails nor duplicates;  a realtor with no email links
nothing;  a link given at insert is kept;  and every suppression whose address is a known realtor's is linked.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

# The realtor upsert of hailsys/rentcast/upsert.py, cut down to the columns these tests need.
UPSERT_REALTOR = (
    "INSERT INTO realtors (agent_name, agent_email) VALUES (%s, %s) "
    "ON CONFLICT (email_norm) WHERE email_norm IS NOT NULL DO UPDATE SET last_seen_at = now() "
    "RETURNING realtor_id")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class RealtorLinkTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if self.conn.execute("SELECT to_regproc('dnc_list_link_realtor') IS NULL AS missing").fetchone()["missing"]:
            self.fail("sql/064_dnc_list_realtor_link.sql is not applied")
        self.system = self.conn.execute("SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.tag = uuid.uuid4().hex[:12]

    def address(self, name):
        return f"{name}-{self.tag}@example.invalid"

    def add_realtor(self, email, name="Test Agent"):
        return self.conn.execute(UPSERT_REALTOR, (name, email)).fetchone()["realtor_id"]

    def suppress(self, email_raw, realtor_id=None):
        return self.conn.execute(
            "INSERT INTO dnc_list (email_raw, added_by, source, realtor_id) VALUES (%s, %s, 'manual', %s) "
            "RETURNING dnc_id, realtor_id", (email_raw, self.system, realtor_id)).fetchone()

    def linked_to(self, dnc_id):
        return self.conn.execute("SELECT realtor_id FROM dnc_list WHERE dnc_id = %s", (dnc_id,)).fetchone()["realtor_id"]

    def test_a_suppression_for_a_known_realtor_is_linked_at_insert(self):
        realtor = self.add_realtor(self.address("known"))
        row = self.suppress("  " + self.address("KNOWN").upper() + " ")
        self.assertEqual(row["realtor_id"], realtor)

    def test_an_unknown_address_stays_unlinked(self):
        self.assertIsNone(self.suppress(self.address("nobody"))["realtor_id"])

    def test_a_realtor_added_later_links_the_suppression(self):
        row = self.suppress(self.address("later"))
        realtor = self.add_realtor(self.address("later").upper())
        self.assertEqual(self.linked_to(row["dnc_id"]), realtor)

    def test_the_upsert_of_an_existing_realtor_is_one_realtor_and_one_link(self):
        first = self.add_realtor(self.address("again"))
        row = self.suppress(self.address("again"))
        self.assertEqual(self.add_realtor(self.address("again"), name="Renamed"), first)
        self.assertEqual(self.linked_to(row["dnc_id"]), first)
        n = self.conn.execute("SELECT count(*) AS n FROM realtors WHERE email_norm = %s",
                              (self.address("again"),)).fetchone()["n"]
        self.assertEqual(n, 1)

    def test_a_realtor_with_no_email_links_nothing(self):
        row = self.suppress(self.address("noemail"))
        self.conn.execute("INSERT INTO realtors (agent_name) VALUES ('No Email')")
        self.assertIsNone(self.linked_to(row["dnc_id"]))

    def test_a_link_given_at_insert_is_kept(self):
        # The Constant Contact sync links the realtor itself (unsubs.py, statuses.py):  the trigger only fills a gap.
        realtor = self.add_realtor(self.address("given"))
        self.assertEqual(self.suppress(self.address("given"), realtor_id=realtor)["realtor_id"], realtor)

    def test_every_suppression_of_a_known_realtor_is_linked(self):
        rows = self.conn.execute(
            "SELECT d.dnc_id FROM dnc_list d JOIN realtors r ON r.email_norm = d.email_norm "
            "WHERE d.realtor_id IS DISTINCT FROM r.realtor_id").fetchall()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
