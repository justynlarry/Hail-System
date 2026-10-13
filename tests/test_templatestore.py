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

    def test_a_new_version_keeps_the_description(self):
        first = self.ensure()
        self.conn.execute("UPDATE email_templates SET description = %s WHERE template_id = %s",
                          ("A test note", first))
        self.ensure(body=BODY + "<p>more</p>")
        old, new = self.rows()
        self.assertEqual((old["description"], new["description"]), ("A test note", "A test note"))

    def test_a_template_with_no_description_stays_without_one(self):
        self.ensure()
        self.ensure(body=BODY + "<p>more</p>")
        self.assertEqual([r["description"] for r in self.rows()], [None, None])

    # ---- load: the template a send renders from (item 202) ----

    def test_the_built_in_loads_from_its_files_without_an_id_and_writes_nothing(self):
        before = self.conn.execute("SELECT count(*) AS n FROM email_templates").fetchone()["n"]
        subject, body, template_id = ts.load(self.conn)
        self.assertEqual((subject, body), ts.load_files())
        self.assertIsNone(template_id)
        after = self.conn.execute("SELECT count(*) AS n FROM email_templates").fetchone()["n"]
        self.assertEqual(after, before)

    def test_another_name_loads_from_its_active_row(self):
        template_id = self.ensure()
        self.assertEqual(ts.load(self.conn, self.name), (SUBJECT, BODY, template_id))

    def test_load_gives_the_newest_version(self):
        self.ensure()
        second = self.ensure(body=BODY + "<p>more</p>")
        self.assertEqual(ts.load(self.conn, self.name), (SUBJECT, BODY + "<p>more</p>", second))

    def test_an_unknown_name_is_not_found(self):
        with self.assertRaises(ts.TemplateNotFound):
            ts.load(self.conn, self.name)

    def test_a_retired_template_is_not_found(self):
        template_id = self.ensure()
        self.conn.execute("UPDATE email_templates SET is_active = FALSE WHERE template_id = %s",
                          (template_id,))
        with self.assertRaises(ts.TemplateNotFound):
            ts.load(self.conn, self.name)

    # ---- set_description (item 202) ----

    def use(self, template_id):
        """One queued email that uses this template (the record of use)."""
        run_id = self.conn.execute(
            "INSERT INTO cc_sync_runs (triggered_by, finished_at, status) "
            "VALUES (%s, now(), 'ok') RETURNING run_id", (self.user,)).fetchone()["run_id"]
        realtor_id = self.conn.execute(
            "SELECT realtor_id FROM realtors ORDER BY realtor_id LIMIT 1").fetchone()["realtor_id"]
        self.conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, "
            "sync_run_id, permission_asserted, created_by, subject, html_body) "
            "VALUES (gen_random_uuid(), %s, 'desc-test@example.invalid', %s, %s, 'none', %s, "
            "'s', '<p>b</p>')", (realtor_id, template_id, run_id, self.user))

    def description(self, template_id):
        return self.conn.execute("SELECT description FROM email_templates WHERE template_id = %s",
                                 (template_id,)).fetchone()["description"]

    def test_a_description_is_saved_trimmed(self):
        template_id = self.ensure()
        ts.set_description(self.conn, template_id, "  A note  ")
        self.assertEqual(self.description(template_id), "A note")

    def test_empty_text_clears_the_description(self):
        template_id = self.ensure()
        ts.set_description(self.conn, template_id, "A note")
        ts.set_description(self.conn, template_id, "   ")
        self.assertIsNone(self.description(template_id))
        ts.set_description(self.conn, template_id, None)
        self.assertIsNone(self.description(template_id))

    def test_the_length_limit_is_inclusive(self):
        template_id = self.ensure()
        ts.set_description(self.conn, template_id, "x" * ts.MAX_DESCRIPTION)
        self.assertEqual(len(self.description(template_id)), ts.MAX_DESCRIPTION)
        with self.assertRaises(ValueError):
            ts.set_description(self.conn, template_id, "x" * (ts.MAX_DESCRIPTION + 1))

    def test_a_template_that_was_used_keeps_its_description(self):
        template_id = self.ensure()
        ts.set_description(self.conn, template_id, "before")
        self.use(template_id)
        with self.assertRaises(ts.DescriptionFrozen):
            ts.set_description(self.conn, template_id, "after")

    def test_saving_the_same_text_on_a_used_template_is_allowed(self):
        template_id = self.ensure()
        ts.set_description(self.conn, template_id, "same")
        self.use(template_id)
        ts.set_description(self.conn, template_id, "same")
        self.assertEqual(self.description(template_id), "same")

    def test_an_unknown_id_is_not_found(self):
        with self.assertRaises(ts.TemplateNotFound):
            ts.set_description(self.conn, -1, "x")

    def test_a_retired_template_cannot_have_its_description_set(self):
        template_id = self.ensure()
        self.conn.execute("UPDATE email_templates SET is_active = FALSE WHERE template_id = %s",
                          (template_id,))
        with self.assertRaises(ts.TemplateNotFound):
            ts.set_description(self.conn, template_id, "x")

    def test_the_shipped_files_are_saved_as_they_are(self):
        subject, body = ts.load_files()
        template_id = ts.ensure_current(self.conn, created_by=self.user, name=self.name)
        row = self.conn.execute("SELECT subject, body FROM email_templates "
                                "WHERE template_id = %s", (template_id,)).fetchone()
        self.assertEqual((row["subject"], row["body"]), (subject, body))


if __name__ == "__main__":
    unittest.main()
