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
    from hailsys.email import blocks, emailconfig, render, templatestore as ts
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

SUBJECT = "Hail near {{ total }} of your listings"
BODY = "<p>Hello {{ agent_first_name }}</p>[[trackingImage]]"
UNSET = object()        # "use the test's own name"; None and "" are values to test


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


def block_template(text="Hello", **over):
    t = {"title": "Test", "subject": {"text": "Storms near {total} homes"},
         "blocks": [{"kind": "image", "image": "logo", "placement": "header"},
                    {"kind": "text", "text": text}, {"kind": "footer"}]}
    t.update(over)
    return t


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class SaveBlocksTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.user = self.conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.name = f"t-{uuid.uuid4().hex[:12]}"

    def save(self, template=None, name=UNSET, conn=None):
        return ts.save_blocks(conn or self.conn, created_by=self.user,
                              name=self.name if name is UNSET else name,
                              template=template or block_template())

    def rows(self, name=None):
        return self.conn.execute(
            "SELECT * FROM email_templates WHERE lower(template_name) = lower(%s) "
            "ORDER BY template_id", (name or self.name,)).fetchall()

    def refused(self, template=None, name=UNSET, *words):
        with self.assertRaises(ts.TemplateRefused) as ctx:
            self.save(template, name)
        for w in words:
            self.assertIn(w, str(ctx.exception))
        return ctx.exception

    # ---- saving and versioning ----

    def test_the_first_save_stores_the_cleaned_blocks_and_their_compiled_text(self):
        template_id = self.save()
        (row,) = self.rows()
        cleaned = blocks.clean_template(block_template())
        self.assertEqual(row["template_id"], template_id)
        self.assertEqual(row["blocks"], cleaned)
        self.assertEqual((row["subject"], row["body"]), blocks.compile_blocks(cleaned))
        self.assertTrue(row["is_active"])
        self.assertIsNone(row["supersedes_id"])
        self.assertEqual(row["created_by"], self.user)

    def test_saving_the_same_template_again_changes_nothing(self):
        first = self.save()
        self.assertEqual(self.save(), first)
        self.assertEqual(self.save(blocks.clean_template(block_template())), first)
        self.assertEqual(len(self.rows()), 1)

    def test_a_changed_block_supersedes_and_retires_the_old_version(self):
        first = self.save()
        second = self.save(block_template("Different words"))
        self.assertNotEqual(first, second)
        old, new = self.rows()
        self.assertEqual(new["supersedes_id"], first)
        self.assertFalse(old["is_active"])
        self.assertTrue(new["is_active"])
        self.assertEqual(old["blocks"]["blocks"][1]["text"], "Hello")      # old source untouched
        self.assertIn("Hello", old["body"])
        self.assertIn("Different words", new["body"])
        self.assertEqual(len([r for r in self.rows() if r["is_active"]]), 1)

    def test_the_description_is_carried_to_the_new_version(self):
        first = self.save()
        ts.set_description(self.conn, first, "A note")
        self.save(block_template("Different words"))
        self.assertEqual(self.rows()[-1]["description"], "A note")

    def test_a_change_to_the_blocks_alone_is_a_new_version(self):
        subject, body = blocks.compile_blocks(block_template())
        first = ts.ensure_current(self.conn, created_by=self.user, name=self.name,
                                  subject_src=subject, body_src=body, blocks={"a": 1})
        again = ts.ensure_current(self.conn, created_by=self.user, name=self.name,
                                  subject_src=subject, body_src=body, blocks={"a": 1})
        other = ts.ensure_current(self.conn, created_by=self.user, name=self.name,
                                  subject_src=subject, body_src=body, blocks={"a": 2})
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)

    def test_the_built_in_path_still_stores_no_blocks(self):
        template_id = ts.ensure_current(self.conn, created_by=self.user, name=self.name)
        row = self.conn.execute("SELECT blocks FROM email_templates WHERE template_id = %s",
                                (template_id,)).fetchone()
        self.assertIsNone(row["blocks"])

    # ---- names ----

    def test_bad_names_are_refused_and_nothing_is_saved(self):
        for bad in ("", "two words", "a/b", "x" * 41, "café", None, 5, "a'b"):
            self.refused(None, bad)
        self.assertEqual(self.rows(), [])

    def test_the_built_in_name_is_reserved_in_any_case(self):
        for name in ("hail_alert", "HAIL_ALERT", "Hail_Alert"):
            self.refused(None, name, "built-in")

    def test_a_forty_character_name_is_allowed(self):
        name = uuid.uuid4().hex + "-" * 8                # 40 characters
        self.assertEqual(len(name), 40)
        self.save(name=name)
        self.assertEqual(len(self.rows(name)), 1)

    def test_a_name_differing_only_in_case_is_refused_naming_the_existing_one(self):
        self.save(name=f"Zz-{self.name}")
        err = self.refused(None, f"zz-{self.name}")
        self.assertIn(f"Zz-{self.name}", str(err))
        self.assertEqual(len(self.rows(f"Zz-{self.name}")), 1)
        self.save(block_template("Different words"), name=f"Zz-{self.name}")   # the exact name edits
        self.assertEqual(len(self.rows(f"Zz-{self.name}")), 2)

    # ---- images ----

    def test_a_missing_image_is_refused_by_name(self):
        t = block_template()
        t["blocks"].insert(0, {"kind": "image", "image": "no-such-image"})
        self.refused(t, UNSET, "no-such-image")
        self.assertEqual(self.rows(), [])

    def test_a_retired_image_is_refused(self):
        self.conn.execute(
            "INSERT INTO email_images (name, url, alt_text, created_by, updated_by) "
            "SELECT 'zz-gone', 'https://x.example.test/g.png', 'g', emp_id, emp_id "
            "FROM users WHERE role = 'system'")
        t = block_template()
        t["blocks"].insert(0, {"kind": "image", "image": "zz-gone"})
        self.save(t)                                     # fine while active
        self.conn.execute("UPDATE email_images SET is_active = FALSE WHERE name = 'zz-gone'")
        self.refused(block_template("Different words", blocks=t["blocks"]), UNSET, "zz-gone")

    def test_every_missing_image_is_listed(self):
        t = block_template()
        t["blocks"][1:1] = [{"kind": "image", "image": "gone-a"},
                            {"kind": "badges", "images": ["gone-b", "badge_cra"]}]
        err = str(self.refused(t))
        self.assertIn("gone-a", err)
        self.assertIn("gone-b", err)
        self.assertNotIn("badge_cra", err)

    # ---- invalid templates ----

    def test_an_invalid_template_is_refused_with_the_reason_and_nothing_is_saved(self):
        for bad, word in ((block_template(blocks=[{"kind": "text", "text": "x"}]), "footer"),
                          (block_template("{{ 7*7 }}"), "curly braces"),
                          (block_template("[b]open"), "never closed"),
                          (block_template(subject={"text": "{nope}"}), "merge field"),
                          ("not a template", "settings")):
            self.refused(bad, UNSET, word)
        self.assertEqual(self.rows(), [])

    # ---- loading ----

    def test_load_blocks_returns_what_was_saved(self):
        self.save()
        self.assertEqual(ts.load_blocks(self.conn, self.name),
                         blocks.clean_template(block_template()))

    def test_load_blocks_follows_the_active_version(self):
        self.save()
        self.save(block_template("Different words"))
        self.assertEqual(ts.load_blocks(self.conn, self.name)["blocks"][1]["text"],
                         "Different words")

    def test_load_blocks_is_none_for_a_template_not_built_from_blocks(self):
        ts.ensure_current(self.conn, created_by=self.user, name=self.name,
                          subject_src=SUBJECT, body_src=BODY)
        self.assertIsNone(ts.load_blocks(self.conn, self.name))

    def test_load_blocks_for_an_unknown_name(self):
        with self.assertRaises(ts.TemplateNotFound):
            ts.load_blocks(self.conn, "no-such-template-" + uuid.uuid4().hex)

    def test_a_saved_block_template_loads_and_renders_for_a_send(self):
        from datetime import datetime, timezone
        from decimal import Decimal
        template_id = self.save()
        subject_src, body_src, saved_id = ts.load(self.conn, self.name)
        self.assertEqual(saved_id, template_id)
        matches = [{"listing_id": 1, "address": "1 Main St", "magnitude": Decimal("1.5"),
                    "utc_datetime": datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc),
                    "distance_miles": Decimal("1")}]
        context = render.build_context("Pat Jones", matches,
                                       emailconfig.load_settings(self.conn))
        subject, html = render.render(subject_src, body_src, context)
        self.assertEqual(subject, "Storms near 1 homes")
        self.assertIn(context["logo_url"], html)
        self.assertIn("Hello", html)

    # ---- a new template must not quietly replace another ----

    def test_must_be_new_refuses_a_name_that_is_active(self):
        self.save()
        with self.assertRaises(ts.TemplateRefused) as ctx:
            ts.save_blocks(self.conn, created_by=self.user, name=self.name,
                           template=block_template("Different"), must_be_new=True)
        self.assertIn("already a template named", str(ctx.exception))
        self.assertIn("make a new version", str(ctx.exception))
        self.assertEqual(len(self.rows()), 1)

    def test_must_be_new_allows_a_name_nobody_has_and_a_normal_save_still_versions(self):
        ts.save_blocks(self.conn, created_by=self.user, name=self.name, template=block_template(),
                       must_be_new=True)
        self.assertEqual(len(self.rows()), 1)
        self.save(block_template("Different"))                       # without it: a new version
        self.assertEqual(len(self.rows()), 2)

    def test_must_be_new_on_a_name_differing_in_case_gives_the_case_message(self):
        self.save(name=f"Zz-{self.name}")
        with self.assertRaises(ts.TemplateRefused) as ctx:
            ts.save_blocks(self.conn, created_by=self.user, name=f"zz-{self.name}",
                           template=block_template(), must_be_new=True)
        self.assertIn("ignore upper and lower case", str(ctx.exception))

    def test_a_retired_name_can_be_used_again(self):
        first = self.save()
        ts.retire(self.conn, self.name)
        again = ts.save_blocks(self.conn, created_by=self.user, name=self.name,
                               template=block_template("Second life"), must_be_new=True)
        self.assertNotEqual(first, again)
        old, new = self.rows()
        self.assertFalse(old["is_active"])
        self.assertTrue(new["is_active"])
        self.assertIsNone(new["supersedes_id"])                       # nothing was active to replace

    # ---- active_id and retire ----

    def test_active_id_follows_the_active_version(self):
        self.assertIsNone(ts.active_id(self.conn, self.name))
        first = self.save()
        self.assertEqual(ts.active_id(self.conn, self.name), first)
        second = self.save(block_template("Different"))
        self.assertEqual(ts.active_id(self.conn, self.name), second)
        ts.retire(self.conn, self.name)
        self.assertIsNone(ts.active_id(self.conn, self.name))

    def test_retiring_makes_the_active_version_inactive_and_nothing_else_changes(self):
        first = self.save()
        other = self.save(name=f"{self.name}-b")
        ts.retire(self.conn, self.name)
        (row,) = self.rows()
        self.assertEqual(row["template_id"], first)
        self.assertFalse(row["is_active"])
        self.assertEqual(row["blocks"], blocks.clean_template(block_template()))
        self.assertEqual(ts.active_id(self.conn, f"{self.name}-b"), other)

    def test_a_retired_template_can_no_longer_be_loaded_for_a_send(self):
        self.save()
        ts.retire(self.conn, self.name)
        with self.assertRaises(ts.TemplateNotFound):
            ts.load(self.conn, self.name)
        with self.assertRaises(ts.TemplateNotFound):
            ts.load_blocks(self.conn, self.name)

    def test_retiring_an_unknown_or_already_retired_template(self):
        with self.assertRaises(ts.TemplateNotFound):
            ts.retire(self.conn, "no-such-template-" + uuid.uuid4().hex)
        self.save()
        ts.retire(self.conn, self.name)
        with self.assertRaises(ts.TemplateNotFound):
            ts.retire(self.conn, self.name)
        for junk in (None, 5, ["a"]):
            with self.assertRaises(ts.TemplateNotFound):
                ts.retire(self.conn, junk)

    def test_the_built_in_cannot_be_retired_in_any_case(self):
        for name in ("hail_alert", "HAIL_ALERT", "Hail_Alert"):
            with self.assertRaises(ts.TemplateRefused) as ctx:
                ts.retire(self.conn, name)
            self.assertIn("built-in", str(ctx.exception))

    def test_a_retired_template_cannot_be_revived_by_the_database(self):
        self.save()
        ts.retire(self.conn, self.name)
        with self.assertRaises(psycopg.errors.RestrictViolation):
            self.conn.execute("UPDATE email_templates SET is_active = TRUE "
                              "WHERE template_name = %s", (self.name,))

    # ---- the name lock ----

    def test_two_saves_of_a_name_in_different_case_cannot_run_at_once(self):
        other = psycopg.connect(row_factory=dict_row)
        self.addCleanup(other.close)
        self.addCleanup(other.rollback)
        self.save(name=f"Zz-{self.name}")                # holds the lowercased lock until rollback
        other.execute("SET lock_timeout = '300ms'")
        for name in (f"Zz-{self.name}", f"zz-{self.name}"):
            with self.assertRaises(psycopg.errors.LockNotAvailable, msg=name):
                self.save(name=name, conn=other)
            other.rollback()
            other.execute("SET lock_timeout = '300ms'")


if __name__ == "__main__":
    unittest.main()
