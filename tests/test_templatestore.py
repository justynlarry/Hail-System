"""Tests for hailsys/email/templatestore.py.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_templatestore

Runs against the real email_templates inside a transaction that is rolled back, with a
unique template name per test, so nothing is left behind.  Skips without a database.
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
    from hailsys.email import render, templatestore as ts
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

SUBJECT = "Hail near {{ total }} of your listings"
BODY = "<p>Hello {{ agent_first_name }}</p>[[trackingImage]]"


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class EnsureCurrentTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.user = self.conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.name = f"test-{uuid.uuid4()}"

    def ensure(self, subject=SUBJECT, body=BODY):
        return ts.ensure_current(self.conn, created_by=self.user, name=self.name,
                                 subject_src=subject, body_src=body)

    def rows(self):
        return self.conn.execute(
            "SELECT * FROM email_templates WHERE template_name = %s ORDER BY template_id",
            (self.name,)).fetchall()

    def test_the_first_call_saves_an_active_version(self):
        template_id = self.ensure()
        (row,) = self.rows()
        self.assertEqual(row["template_id"], template_id)
        self.assertEqual((row["subject"], row["body"]), (SUBJECT, BODY))
        self.assertTrue(row["is_active"])
        self.assertIsNone(row["supersedes_id"])
        self.assertEqual(row["created_by"], self.user)

    def test_the_same_text_changes_nothing(self):
        first = self.ensure()
        self.assertEqual(self.ensure(), first)
        self.assertEqual(len(self.rows()), 1)

    def test_changed_text_supersedes_and_retires_the_old_version(self):
        first = self.ensure()
        second = self.ensure(body=BODY + "<p>more</p>")
        self.assertNotEqual(first, second)
        old, new = self.rows()
        self.assertEqual((old["template_id"], new["template_id"]), (first, second))
        self.assertEqual(new["supersedes_id"], first)
        self.assertFalse(old["is_active"])
        self.assertTrue(new["is_active"])
        self.assertEqual(old["body"], BODY)                  # the old text is untouched

    def test_changing_back_makes_a_third_version(self):
        self.ensure()
        self.ensure(body=BODY + "<p>more</p>")
        self.ensure()
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["is_active"] for r in rows], [False, False, True])

    def test_a_broken_template_is_refused_and_nothing_is_saved(self):
        with self.assertRaises(render.RenderError):
            self.ensure(body="<p>{{ not_a_placeholder }}</p>[[trackingImage]]")
        self.assertEqual(self.rows(), [])

    def test_the_shipped_files_are_saved_as_they_are(self):
        subject, body = ts.load_files()
        template_id = ts.ensure_current(self.conn, created_by=self.user, name=self.name)
        row = self.conn.execute("SELECT subject, body FROM email_templates "
                                "WHERE template_id = %s", (template_id,)).fetchone()
        self.assertEqual((row["subject"], row["body"]), (subject, body))


if __name__ == "__main__":
    unittest.main()
