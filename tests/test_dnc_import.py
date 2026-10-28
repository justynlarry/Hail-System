"""Tests for the do-not-contact import (hailsys/queries/dncimport.py and the /admin DNC routes):  the CSV upload, the
typed entry (item 269) and the source each batch records (item 268, sql/061).

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_dnc_import

The parsers need nothing.  The staging and commit tests run in a transaction that is always rolled back.  The page
tests replace the staging and commit functions, so no test writes to the real list.

What they pin:  the upload needs Constant Contact's exact column names and keeps only Unsubscribed rows, strips
non-breaking spaces, and leaves an unreadable date empty;  typed addresses are split, lower-cased, kept once, dated now
and checked;  a batch must say where it came from, never 'unsubscribe' (the hail system's own account, which the
by-level report credits to our emails);  the commit writes the batch's source and a reason that says how the rows
came in, skips what is already suppressed and what was rejected;  and the pages are admin only, refuse a missing
source, and pass the note through.
"""

import contextlib
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from hailsys.queries import dncimport

HEADER = "Email address,Email status,Created At\n"


def csv_bytes(*lines, header=HEADER):
    return (header + "".join(line + "\n" for line in lines)).encode("utf-8")


class ParseUploadTest(unittest.TestCase):
    def test_a_constant_contact_export_is_read(self):
        rows, err = dncimport.parse_upload(csv_bytes("A@Example.test,Unsubscribed,10/28/2026"))
        self.assertIsNone(err)
        (row,) = rows
        self.assertEqual((row["line_no"], row["email_raw"], row["rejected"]), (2, "A@Example.test", None))
        self.assertEqual(row["added_at"], datetime(2026, 10, 28))

    def test_the_column_names_must_be_exact(self):
        for header in ("Email Address,Email Status,created at\n", "email,status,date\n"):
            with self.subTest(header=header):
                rows, err = dncimport.parse_upload(csv_bytes("a@example.test,Unsubscribed,10/28/2026", header=header))
                self.assertEqual(rows, [])
                self.assertIn("Missing column(s)", err)

    def test_only_unsubscribed_rows_and_real_addresses_are_kept(self):
        rows, _ = dncimport.parse_upload(csv_bytes("a@example.test,Active,10/28/2026", "nobody,Unsubscribed,10/28/2026",
                                                   ",Unsubscribed,10/28/2026", "b@example.test,unsubscribed,10/28/2026"))
        self.assertEqual([r["rejected"] for r in rows],
                         ["status is Active, not Unsubscribed", "not an email address", "no email address", None])

    def test_a_non_breaking_space_copied_from_an_email_is_stripped(self):
        rows, _ = dncimport.parse_upload(csv_bytes("a@example.test  ,Unsubscribed,10/28/2026"))
        self.assertEqual(rows[0]["email_raw"], "a@example.test")

    def test_a_two_digit_year_is_not_read_and_the_row_is_still_kept(self):
        rows, _ = dncimport.parse_upload(csv_bytes("a@example.test,Unsubscribed,10/28/26"))
        self.assertEqual((rows[0]["added_at"], rows[0]["rejected"]), (None, None))

    def test_a_byte_order_mark_is_fine_and_a_non_utf8_file_is_refused(self):
        rows, err = dncimport.parse_upload(b"\xef\xbb\xbf" + csv_bytes("a@example.test,Unsubscribed,10/28/2026"))
        self.assertEqual((len(rows), err), (1, None))
        rows, err = dncimport.parse_upload(b"\xff\xfe\x00bad")
        self.assertIn("UTF-8", err)


class ParseTypedTest(unittest.TestCase):
    def test_addresses_are_split_lower_cased_kept_once_and_checked(self):
        rows, err = dncimport.parse_typed(" A@Example.TEST, b@example.test;\n a@example.test  nobody c@example ")
        self.assertIsNone(err)
        self.assertEqual([(r["line_no"], r["email_raw"], r["rejected"]) for r in rows],
                         [(1, "a@example.test", None), (2, "b@example.test", None),
                          (3, "nobody", "not an email address"), (4, "c@example", "not an email address")])

    def test_typed_addresses_are_dated_now(self):
        before = datetime.now(timezone.utc)
        rows, _ = dncimport.parse_typed("a@example.test")
        self.assertTrue(before <= rows[0]["added_at"] <= datetime.now(timezone.utc))
        self.assertIsNone(rows[0]["name_at_add"])

    def test_nothing_typed_is_an_error(self):
        for text in (None, "", "  ,; \n"):
            with self.subTest(text=text):
                self.assertEqual(dncimport.parse_typed(text), ([], "Type at least one address."))

    def test_more_than_the_limit_is_refused(self):
        many = " ".join(f"a{n}@example.test" for n in range(dncimport.MAX_TYPED + 1))
        rows, err = dncimport.parse_typed(many)
        self.assertEqual(rows, [])
        self.assertIn(f"At most {dncimport.MAX_TYPED}", err)
        rows, err = dncimport.parse_typed(" ".join(f"a{n}@example.test" for n in range(dncimport.MAX_TYPED)))
        self.assertEqual((len(rows), err), (dncimport.MAX_TYPED, None))


class SourcesTest(unittest.TestCase):
    def test_the_sources_offered(self):
        self.assertEqual(set(dncimport.SOURCES), {"newsletter", "manual", "legacy_import"})
        # 'unsubscribe' is the sync's, from the hail system's own account;  the by-level report credits it to our emails.
        self.assertNotIn("unsubscribe", dncimport.SOURCES)


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class StageAndCommitTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users WHERE role = 'admin'").fetchone()["e"]
        self.tag = uuid.uuid4().hex[:8]

    def addr(self, name):
        return f"{name}-{self.tag}@example.test"

    def stage(self, rows, **kw):
        kw.setdefault("filename", "test.csv")
        kw.setdefault("source", "newsletter")
        token = dncimport.stage(self.conn, rows=rows, uploaded_by=self.emp, **kw)
        return dncimport.fetch_batch(self.conn, token)

    def commit(self, batch):
        return dncimport.commit_batch(self.conn, batch_id=batch["batch_id"], added_by=self.emp,
                                      filename=batch["filename"], note=batch["note"])

    def listed(self, *names):
        return {r["email_norm"]: (r["source"], r["reason"]) for r in self.conn.execute(
            "SELECT email_norm, source, reason FROM dnc_list WHERE email_norm = ANY(%s)",
            ([self.addr(n) for n in names],))}

    def test_a_batch_records_its_source_and_note(self):
        rows, _ = dncimport.parse_typed(self.addr("a"))
        batch = self.stage(rows, filename=dncimport.TYPED_FILENAME, source="manual", note="  called the office ")
        self.assertEqual((batch["source"], batch["note"], batch["row_count"]), ("manual", "called the office", 1))

    def test_an_unknown_source_or_a_long_note_is_refused(self):
        rows, _ = dncimport.parse_typed(self.addr("a"))
        for source in ("unsubscribe", "", None, "Newsletter"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.stage(rows, source=source)
        with self.assertRaises(ValueError):
            self.stage(rows, note="x" * (dncimport.MAX_NOTE + 1))
        self.assertEqual(self.stage(rows, note="x" * dncimport.MAX_NOTE)["note"], "x" * dncimport.MAX_NOTE)

    def test_the_database_requires_a_known_source(self):
        import psycopg
        for source in (None, "unsubscribe"):
            with self.subTest(source=source):
                self.conn.execute("SAVEPOINT s")
                with self.assertRaises(psycopg.Error):
                    self.conn.execute("INSERT INTO dnc_import_batches (token, filename, uploaded_by, row_count, source) "
                                      "VALUES (%s, 'x', %s, 0, %s)", (uuid.uuid4().hex, self.emp, source))
                self.conn.execute("ROLLBACK TO SAVEPOINT s")

    def test_a_typed_batch_commits_with_its_source_and_a_typed_reason(self):
        rows, _ = dncimport.parse_typed(f"{self.addr('a')} {self.addr('b')} nobody")
        added = self.commit(self.stage(rows, filename=dncimport.TYPED_FILENAME, source="manual", note="called the office"))
        self.assertEqual(added, 2)
        self.assertEqual(self.listed("a", "b"), {self.addr(n): ("manual", "Typed on the admin page: called the office")
                                                for n in ("a", "b")})

    def test_a_typed_batch_without_a_note_says_only_how_it_came_in(self):
        rows, _ = dncimport.parse_typed(self.addr("a"))
        self.commit(self.stage(rows, filename=dncimport.TYPED_FILENAME, source="newsletter"))
        self.assertEqual(self.listed("a"), {self.addr("a"): ("newsletter", "Typed on the admin page")})

    def test_a_file_commits_with_its_source_and_the_file_name(self):
        for source in ("newsletter", "legacy_import"):
            with self.subTest(source=source):
                name = f"f-{source}"
                rows, _ = dncimport.parse_upload(csv_bytes(f"{self.addr(name)},Unsubscribed,10/28/2026"))
                self.commit(self.stage(rows, filename="unsubs.csv", source=source))
                self.assertEqual(self.listed(name), {self.addr(name): (source, "Uploaded DNC list: unsubs.csv")})

    def test_an_unreadable_date_is_dated_now_and_noted(self):
        rows, _ = dncimport.parse_upload(csv_bytes(f"{self.addr('a')},Unsubscribed,10/28/26"))
        self.commit(self.stage(rows))
        source, reason = self.listed("a")[self.addr("a")]
        self.assertEqual(reason, "Uploaded DNC list: test.csv (source data unreadable)")

    def test_rejected_rows_and_addresses_already_suppressed_are_not_written(self):
        rows, _ = dncimport.parse_typed(self.addr("old"))
        self.commit(self.stage(rows, source="legacy_import"))
        rows, _ = dncimport.parse_typed(f"{self.addr('old')} {self.addr('new')} nobody")
        batch = self.stage(rows, filename=dncimport.TYPED_FILENAME, source="newsletter")
        summary, _, rejects = dncimport.fetch_preview(self.conn, batch["batch_id"])
        self.assertEqual((summary["to_add"], summary["already_listed"], summary["rejected"]), (1, 1, 1))
        self.assertEqual([r["email_raw"] for r in rejects], ["nobody"])
        self.assertEqual(self.commit(batch), 1)
        self.assertEqual(self.listed("old", "new")[self.addr("old")][0], "legacy_import")     # not relabelled
        self.assertEqual(self.listed("new")[self.addr("new")][0], "newsletter")

    def test_the_commit_marks_the_batch_committed(self):
        rows, _ = dncimport.parse_typed(self.addr("a"))
        batch = self.stage(rows)
        self.assertIsNone(batch["committed_at"])
        self.commit(batch)
        self.assertIsNotNone(dncimport.fetch_batch(self.conn, batch["token"])["committed_at"])

    def test_an_old_unfinished_preview_is_swept_by_the_next_upload(self):
        rows, _ = dncimport.parse_typed(self.addr("a"))
        old = self.stage(rows)
        self.conn.execute("UPDATE dnc_import_batches SET uploaded_at = now() - interval '2 days' WHERE batch_id = %s",
                          (old["batch_id"],))
        self.stage(rows)
        self.assertIsNone(dncimport.fetch_batch(self.conn, old["token"]))


@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class DncPagesTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        from hailsys.web import admin as admin_web, create_app
        self.admin_web = admin_web
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.app.config["WTF_CSRF_ENABLED"] = False
        with psycopg.connect(row_factory=dict_row) as conn:
            self.users = {}
            for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                  "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
                self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.stage = stack.enter_context(mock.patch.object(admin_web.dncimport, "stage", return_value="tok"))
        self.commit = stack.enter_context(mock.patch.object(admin_web.dncimport, "commit_batch", return_value=2))
        self.batch = {"batch_id": 1, "token": "tok", "filename": dncimport.TYPED_FILENAME, "committed_at": None,
                      "uploaded_at": datetime.now(timezone.utc), "row_count": 2, "source": "manual",
                      "note": "called the office", "user_name": "someone"}
        stack.enter_context(mock.patch.object(admin_web.dncimport, "fetch_batch", return_value=self.batch))
        stack.enter_context(mock.patch.object(admin_web.dncimport, "fetch_preview", return_value=(
            {"to_add": 2, "already_listed": 0, "rejected": 0, "no_date": 0}, [], [])))

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(emp_id=self.users[role], role=role, user_name="test",
                     issued_at=datetime.now(timezone.utc).isoformat())
        return client

    def typed(self, follow=True, role="admin", **data):
        data.setdefault("addresses", "a@example.test b@example.test")
        data.setdefault("source", "manual")
        return self.client_as(role).post("/admin/dnc/typed", data=data, follow_redirects=follow)

    def test_the_admin_page_offers_both_forms_with_the_three_sources_newsletter_first(self):
        html = self.client_as().get("/admin/").get_data(as_text=True)
        self.assertIn('action="/admin/dnc/upload"', html)
        self.assertIn('action="/admin/dnc/typed"', html)
        self.assertEqual(html.count('name="source" value="newsletter"'), 2)
        self.assertEqual(html.count('name="source" value="unsubscribe"'), 0)
        self.assertRegex(html, r'value="newsletter" id="typed-newsletter"\s+checked')

    def test_typed_addresses_are_staged_with_the_source_and_note_and_go_to_the_preview(self):
        resp = self.typed(follow=False, note="  called the office ")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/admin/dnc/preview/tok"))
        kw = self.stage.call_args.kwargs
        self.assertEqual((kw["filename"], kw["source"], kw["note"], kw["uploaded_by"]),
                         (dncimport.TYPED_FILENAME, "manual", "called the office", self.users["admin"]))
        self.assertEqual([r["email_raw"] for r in kw["rows"]], ["a@example.test", "b@example.test"])

    def test_a_missing_or_unknown_source_is_refused_on_both_forms(self):
        for source in (None, "unsubscribe", ""):
            with self.subTest(source=source):
                data = {"addresses": "a@example.test"}
                if source is not None:
                    data["source"] = source
                html = self.client_as().post("/admin/dnc/typed", data=data, follow_redirects=True).get_data(as_text=True)
                self.assertIn("Say where these addresses came from.", html)
                import io
                up = {"file": (io.BytesIO(csv_bytes("a@example.test,Unsubscribed,10/28/2026")), "u.csv")}
                if source is not None:
                    up["source"] = source
                html = self.client_as().post("/admin/dnc/upload", data=up, content_type="multipart/form-data",
                                             follow_redirects=True).get_data(as_text=True)
                self.assertIn("Say where these addresses came from.", html)
        self.stage.assert_not_called()

    def test_an_upload_stages_with_its_source(self):
        import io
        up = {"file": (io.BytesIO(csv_bytes("a@example.test,Unsubscribed,10/28/2026")), "unsubs.csv"),
              "source": "newsletter"}
        self.client_as().post("/admin/dnc/upload", data=up, content_type="multipart/form-data")
        kw = self.stage.call_args.kwargs
        self.assertEqual((kw["filename"], kw["source"]), ("unsubs.csv", "newsletter"))

    def test_nothing_typed_or_a_long_note_is_a_message_and_nothing_is_staged(self):
        self.assertIn("Type at least one address.", self.typed(addresses="  ").get_data(as_text=True))
        self.assertIn("The note is longer than", self.typed(note="x" * 201).get_data(as_text=True))
        self.stage.assert_not_called()

    def test_only_an_admin_may_add(self):
        for role in ("sender", "viewer"):
            if role in self.users:
                with self.subTest(role=role):
                    self.typed(role=role, follow=False)
        self.stage.assert_not_called()

    def test_the_preview_says_what_the_rows_will_be_recorded_as_and_the_note(self):
        html = self.client_as().get("/admin/dnc/preview/tok").get_data(as_text=True)
        self.assertIn("These are: <strong>Other requests (a phone call, email, in person)</strong>", html)
        self.assertIn("Note: <strong>called the office</strong>", html)
        self.assertIn("<code>manual</code>", html)

    def test_the_commit_passes_the_note(self):
        self.client_as().post("/admin/dnc/commit/tok")
        kw = self.commit.call_args.kwargs
        self.assertEqual((kw["filename"], kw["note"]), (dncimport.TYPED_FILENAME, "called the office"))


if __name__ == "__main__":
    unittest.main()
