"""Tests for hailsys/queries/templatelist.py, on the real database (item 202).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_templatelist

Everything runs in a transaction that is rolled back, with unique template names, so nothing is
left behind.  Skips without a database.
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
    from hailsys.email import templatestore as ts
    from hailsys.queries import templatelist as tl
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

SUBJECT = "Hail near {{ total }} of your listings"
BODY = "<p>Hello {{ agent_first_name }}</p>[[trackingImage]]"


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class TemplateListTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.user = self.conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.prefix = f"zz-test-{uuid.uuid4()}"

    def make(self, n, body=BODY):
        name = f"{self.prefix}-{n:03d}"
        template_id = ts.ensure_current(self.conn, created_by=self.user, name=name,
                                        subject_src=SUBJECT, body_src=body)
        return name, template_id

    def active_names(self):
        return [r["template_name"] for r in self.conn.execute(
            "SELECT template_name FROM email_templates WHERE is_active ORDER BY template_name")]

    def use(self, template_id, batch=None):
        """One queued email that uses this template (the record of use).  Emails given the
        same batch belong to one send."""
        run_id = self.conn.execute(
            "INSERT INTO cc_sync_runs (triggered_by, finished_at, status) "
            "VALUES (%s, now(), 'ok') RETURNING run_id", (self.user,)).fetchone()["run_id"]
        batch = batch or uuid.uuid4()
        realtor_id = self.conn.execute(         # one email per realtor per batch
            "SELECT realtor_id FROM realtors WHERE realtor_id NOT IN "
            "(SELECT realtor_id FROM sent_emails WHERE batch_id = %s) "
            "ORDER BY realtor_id LIMIT 1", (batch,)).fetchone()["realtor_id"]
        self.conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
            "sync_run_id, permission_asserted, created_by, subject, html_body) "
            "VALUES (%s, %s, 'list-test@example.invalid', %s, %s, 'none', %s, "
            "'s', '<p>b</p>')",
            (batch, realtor_id, template_id, run_id, self.user))

    def test_a_page_holds_at_most_the_page_size_in_name_order(self):
        for n in reversed(range(25)):                   # created out of name order
            self.make(n)
        got = tl.fetch_page(self.conn, 1)
        names = [r["template_name"] for r in got["rows"]]
        self.assertEqual(len(names), tl.PAGE_SIZE)
        self.assertEqual(names, sorted(names))

    def test_walking_every_page_lists_each_active_template_once(self):
        for n in reversed(range(25)):                   # created out of name order
            self.make(n)
        first = tl.fetch_page(self.conn, 1)
        seen = []
        for page in range(1, first["pages"] + 1):
            seen += [r["template_name"] for r in tl.fetch_page(self.conn, page)["rows"]]
        self.assertEqual(seen, self.active_names())
        self.assertEqual(first["total"], len(seen))
        self.assertEqual(first["pages"], -(-len(seen) // tl.PAGE_SIZE))

    def test_a_page_outside_the_range_is_moved_into_it(self):
        for n in reversed(range(25)):                   # created out of name order
            self.make(n)
        last = tl.fetch_page(self.conn, 10 ** 6)
        self.assertEqual(last["page"], last["pages"])
        self.assertTrue(last["rows"])
        self.assertEqual(tl.fetch_page(self.conn, 0)["page"], 1)
        self.assertEqual(tl.fetch_page(self.conn, -5)["page"], 1)

    def test_a_retired_template_is_not_listed(self):
        name, template_id = self.make(1)
        self.conn.execute("UPDATE email_templates SET is_active = FALSE WHERE template_id = %s",
                          (template_id,))
        self.assertNotIn(name, [r["template_name"] for r in
                                tl.fetch_page(self.conn, 1, per_page=10 ** 6)["rows"]])
        self.assertIsNone(tl.fetch_one(self.conn, name))

    def test_only_the_newest_version_is_listed_and_versions_are_counted(self):
        name, first = self.make(1)
        second = ts.ensure_current(self.conn, created_by=self.user, name=name,
                                   subject_src=SUBJECT, body_src=BODY + "<p>more</p>")
        row = tl.fetch_one(self.conn, name)
        self.assertEqual((row["template_id"], row["versions"]), (second, 2))
        rows = [r for r in tl.fetch_page(self.conn, 1, per_page=10 ** 6)["rows"]
                if r["template_name"] == name]
        self.assertEqual(len(rows), 1)

    def test_a_template_nobody_used_has_no_sends_and_no_last_used(self):
        name, _ = self.make(1)
        row = tl.fetch_one(self.conn, name)
        self.assertEqual(row["sends"], 0)
        self.assertIsNone(row["last_used"])

    def test_use_is_counted_across_all_versions_of_the_name(self):
        name, first = self.make(1)
        self.use(first)
        ts.ensure_current(self.conn, created_by=self.user, name=name,
                          subject_src=SUBJECT, body_src=BODY + "<p>more</p>")
        row = tl.fetch_one(self.conn, name)
        self.assertEqual(row["sends"], 1)
        self.assertIsNotNone(row["last_used"])

    def test_two_emails_of_one_send_count_as_one_send(self):
        name, template_id = self.make(1)
        batch = uuid.uuid4()
        self.use(template_id, batch)
        self.use(template_id, batch)
        self.assertEqual(tl.fetch_one(self.conn, name)["sends"], 1)
        self.use(template_id)                               # a second send
        self.assertEqual(tl.fetch_one(self.conn, name)["sends"], 2)

    def test_created_by_carries_the_users_name(self):
        name, _ = self.make(1)
        row = tl.fetch_one(self.conn, name)
        who = self.conn.execute("SELECT emp_fname, emp_lname FROM users WHERE emp_id = %s",
                                (self.user,)).fetchone()
        self.assertEqual((row["emp_fname"], row["emp_lname"]), (who["emp_fname"], who["emp_lname"]))

    def test_an_unknown_name_is_none(self):
        self.assertIsNone(tl.fetch_one(self.conn, f"no-such-{uuid.uuid4()}"))


if __name__ == "__main__":
    unittest.main()
