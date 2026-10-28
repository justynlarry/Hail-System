"""Tests for the zone kind in hailsys/email/templatestore.py (Phase 5c step 4, piece 3b).

Needs the database; every test rolls back.

    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_zone_templatestore

What they pin:  the built-in zone template is saved with match_kind 'zone' and the same id on a repeat;  a
kind never changes for a name (here and in the database);  a zone-kind text is checked as a zone template
and a report text as a report one;  the built-in names cannot be rebuilt or retired by the editor;  and the
report built-in still saves as 'report'.
"""

import os
import unittest
import uuid

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import templatestore as ts, render
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

ZONE_SUBJECT = "Hail near {{ total }} listings"
ZONE_BODY = "<p>{{ agent_first_name }}</p>{% for e in evidence %}<p>{{ e }}</p>{% endfor %}[[trackingImage]]"
REPORT_SUBJECT = "Hail near {{ total }} of your listings"
REPORT_BODY = "<p>{{ agent_first_name }} {{ nearest_miles }}</p>[[trackingImage]]"


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZoneStoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.user = self.conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.name = f"t-{uuid.uuid4().hex[:12]}"

    def ensure(self, name=None, **kw):
        return ts.ensure_current(self.conn, created_by=self.user, name=name or self.name, **kw)

    def kinds(self, name):
        return [r["match_kind"] for r in self.conn.execute(
            "SELECT match_kind FROM email_templates WHERE template_name = %s ORDER BY template_id",
            (name,)).fetchall()]

    # ---- the built-in zone template ----

    def test_the_built_in_zone_template_is_saved_as_zone_with_its_shipped_files(self):
        template_id = self.ensure(ts.ZONE_TEMPLATE_NAME)
        row = self.conn.execute("SELECT * FROM email_templates WHERE template_id = %s",
                                (template_id,)).fetchone()
        subject, body = ts.load_files(ts.ZONE_TEMPLATE_NAME)
        self.assertEqual((row["match_kind"], row["subject"], row["body"]), ("zone", subject, body))
        self.assertIsNone(row["blocks"])

    def test_the_zone_files_are_not_the_report_files(self):
        self.assertNotEqual(ts.load_files(ts.ZONE_TEMPLATE_NAME), ts.load_files(ts.TEMPLATE_NAME))

    def test_a_repeat_returns_the_same_id_and_adds_no_row(self):
        first = self.ensure(ts.ZONE_TEMPLATE_NAME)
        count = self.conn.execute("SELECT count(*) AS n FROM email_templates").fetchone()["n"]
        self.assertEqual(self.ensure(ts.ZONE_TEMPLATE_NAME), first)
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM email_templates").fetchone()["n"], count)

    def test_a_kind_that_disagrees_with_the_built_in_is_refused(self):
        with self.assertRaises(ts.TemplateRefused):
            self.ensure(ts.ZONE_TEMPLATE_NAME, match_kind="report")
        with self.assertRaises(ts.TemplateRefused):
            self.ensure(ts.TEMPLATE_NAME, match_kind="zone")

    def test_the_report_built_in_still_saves_as_report(self):
        template_id = self.ensure(ts.TEMPLATE_NAME)
        row = self.conn.execute("SELECT match_kind FROM email_templates WHERE template_id = %s",
                                (template_id,)).fetchone()
        self.assertEqual(row["match_kind"], "report")

    # ---- other names ----

    def test_no_kind_means_report(self):
        self.ensure(subject_src=REPORT_SUBJECT, body_src=REPORT_BODY)
        self.assertEqual(self.kinds(self.name), ["report"])

    def test_a_zone_text_is_saved_as_zone_and_a_second_version_keeps_the_kind(self):
        self.ensure(subject_src=ZONE_SUBJECT, body_src=ZONE_BODY, match_kind="zone")
        self.ensure(subject_src=ZONE_SUBJECT + "!", body_src=ZONE_BODY, match_kind="zone")
        self.assertEqual(self.kinds(self.name), ["zone", "zone"])

    def test_a_zone_text_with_a_report_variable_is_refused(self):
        with self.assertRaises(render.RenderError):
            self.ensure(subject_src=ZONE_SUBJECT, body_src=REPORT_BODY, match_kind="zone")
        self.assertEqual(self.kinds(self.name), [])

    def test_a_report_text_with_a_zone_variable_is_refused(self):
        with self.assertRaises(render.RenderError):
            self.ensure(subject_src=REPORT_SUBJECT, body_src=ZONE_BODY, match_kind="report")
        self.assertEqual(self.kinds(self.name), [])

    def test_a_zone_text_with_a_banned_claim_is_refused_and_nothing_is_saved(self):
        with self.assertRaises(render.RenderError):
            self.ensure(subject_src=ZONE_SUBJECT, body_src=ZONE_BODY + "<p>Guaranteed hail.</p>",
                        match_kind="zone")
        self.assertEqual(self.kinds(self.name), [])

    def test_a_name_cannot_change_kind(self):
        self.ensure(subject_src=REPORT_SUBJECT, body_src=REPORT_BODY)
        with self.assertRaises(ts.TemplateRefused) as ctx:
            self.ensure(subject_src=ZONE_SUBJECT, body_src=ZONE_BODY, match_kind="zone")
        self.assertIn("cannot change", str(ctx.exception))
        self.assertEqual(self.kinds(self.name), ["report"])

    def test_the_database_also_refuses_a_kind_change(self):
        self.ensure(subject_src=REPORT_SUBJECT, body_src=REPORT_BODY)
        with self.assertRaises(psycopg.Error):
            self.conn.execute("UPDATE email_templates SET match_kind = 'zone' WHERE template_name = %s",
                              (self.name,))

    # ---- kind_of and load ----

    def test_kind_of(self):
        self.assertEqual(ts.kind_of(self.conn, ts.TEMPLATE_NAME), "report")
        self.assertEqual(ts.kind_of(self.conn, ts.ZONE_TEMPLATE_NAME), "zone")
        self.ensure(subject_src=ZONE_SUBJECT, body_src=ZONE_BODY, match_kind="zone")
        self.assertEqual(ts.kind_of(self.conn, self.name), "zone")

    def test_kind_of_an_unknown_or_retired_name_is_not_found(self):
        with self.assertRaises(ts.TemplateNotFound):
            ts.kind_of(self.conn, self.name)
        self.ensure(subject_src=ZONE_SUBJECT, body_src=ZONE_BODY, match_kind="zone")
        ts.retire(self.conn, self.name)
        with self.assertRaises(ts.TemplateNotFound):
            ts.kind_of(self.conn, self.name)

    def test_load_of_the_zone_built_in_comes_from_its_files_without_an_id_and_writes_nothing(self):
        count = self.conn.execute("SELECT count(*) AS n FROM email_templates").fetchone()["n"]
        subject, body, template_id = ts.load(self.conn, ts.ZONE_TEMPLATE_NAME)
        self.assertIsNone(template_id)
        self.assertEqual((subject, body), ts.load_files(ts.ZONE_TEMPLATE_NAME))
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM email_templates").fetchone()["n"], count)

    # ---- the editor cannot touch the built-ins ----

    def test_the_editor_cannot_rebuild_or_retire_either_built_in(self):
        template = {"title": "T", "subject": {"text": "Storms near {total} homes"},
                    "blocks": [{"kind": "text", "text": "Hello"}, {"kind": "footer"}]}
        for name in (ts.TEMPLATE_NAME, ts.ZONE_TEMPLATE_NAME, ts.ZONE_TEMPLATE_NAME.upper()):
            with self.subTest(name=name):
                with self.assertRaises(ts.TemplateRefused):
                    ts.save_blocks(self.conn, created_by=self.user, name=name, template=template)
                with self.assertRaises(ts.TemplateRefused):
                    ts.retire(self.conn, name)


if __name__ == "__main__":
    unittest.main()
