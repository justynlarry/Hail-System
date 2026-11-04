"""Tests for the send-permission list (item 189):  hailsys/queries/permission.py, the /admin permission
routes and the database guard on sent_emails (sql/066).

    docker compose --profile test run --rm -T test python -m unittest tests.test_permission

The parser needs nothing.  The database tests run in a transaction that is always rolled back.  The page
tests replace the staging and commit functions, so no test writes to the real list.

What they pin:  the email column is found by its heading in any case;  a Constant Contact export gives
permission only for its Active rows;  bad and repeated addresses are kept and marked rejected;  the commit
skips rejected rows and addresses already listed on that basis, but adds an existing client as a subscriber;
a removal is a mark, never a delete, and covers every basis;  the database refuses a sent_emails row with no
active permission and accepts one with it;  and the pages are admin only and refuse a missing basis or date.
"""

import contextlib
import io
import os
import unittest
import uuid
from datetime import date, datetime, timezone
from unittest import mock

MISSING = None
try:
    from hailsys.queries import permission
except ImportError as exc:      # the module is skipped rather than failing to import (item 273)
    MISSING = exc.name


def csv_bytes(*lines, header="Email\n"):
    return (header + "".join(line + "\n" for line in lines)).encode("utf-8")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ParseUploadTest(unittest.TestCase):
    def test_the_email_column_is_found_by_its_heading_in_any_case(self):
        for header in ("Email\n", "EMAIL ADDRESS\n", "  e-mail \n", "email_address\n"):
            with self.subTest(header=header):
                rows, err = permission.parse_upload(csv_bytes("a@example.test", header=header))
                self.assertIsNone(err)
                self.assertEqual([r["email_raw"] for r in rows], ["a@example.test"])

    def test_a_file_without_an_email_column_is_refused(self):
        rows, err = permission.parse_upload(csv_bytes("a@example.test", header="Address\n"))
        self.assertEqual(rows, [])
        self.assertIn("No email column", err)

    def test_a_non_utf8_file_is_refused_and_a_byte_order_mark_is_fine(self):
        self.assertIn("UTF-8", permission.parse_upload(b"\xff\xfe\x00bad")[1])
        rows, err = permission.parse_upload(b"\xef\xbb\xbf" + csv_bytes("a@example.test"))
        self.assertEqual((len(rows), err), (1, None))

    def test_bad_blank_and_repeated_addresses_are_kept_and_rejected(self):
        # A wholly blank line is no row at all (the csv module skips it), so the blank address has a name.
        rows, _ = permission.parse_upload(csv_bytes("a@example.test,", "nobody,", ",Pat", "A@Example.test,",
                                                    header="Email,Name\n"))
        self.assertEqual([(r["line_no"], r["rejected"]) for r in rows],
                         [(2, None), (3, "not an email address"), (4, "no email address"),
                          (5, "already earlier in this file")])

    def test_a_constant_contact_export_gives_permission_only_for_active_rows(self):
        rows, _ = permission.parse_upload(csv_bytes(
            "a@example.test,Active", "b@example.test,Unsubscribed", "c@example.test,",
            header="Email address,Email status\n"))
        self.assertEqual([r["rejected"] for r in rows],
                         [None, "status is Unsubscribed, not Active", "status is (blank), not Active"])

    def test_a_name_is_taken_from_first_and_last_name(self):
        rows, _ = permission.parse_upload(csv_bytes("a@example.test,Pat,Jones",
                                                    header="Email,First name,Last name\n"))
        self.assertEqual(rows[0]["name"], "Pat Jones")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class StageCommitRemoveTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users WHERE role = 'admin'").fetchone()["e"]
        if self.emp is None:
            self.skipTest("no admin user in this database")
        self.tag = uuid.uuid4().hex[:8]

    def addr(self, name):
        return f"{name}-{self.tag}@example.test"

    def stage(self, *names, basis="service_client", note=None):
        rows, _ = permission.parse_upload(csv_bytes(*names))
        token = permission.stage(self.conn, rows=rows, filename="clients.csv", uploaded_by=self.emp,
                                 basis=basis, as_of=date(2026, 11, 1), note=note)
        return permission.fetch_batch(self.conn, token)

    def commit(self, batch):
        return permission.commit_batch(self.conn, batch_id=batch["batch_id"], added_by=self.emp)

    def active(self, name):
        return {r["basis"] for r in self.conn.execute(
            "SELECT basis FROM send_permission WHERE email_norm = %s AND removed_at IS NULL",
            (self.addr(name).lower(),))}

    def test_an_unknown_basis_or_a_long_note_is_refused(self):
        with self.assertRaises(ValueError):
            self.stage(self.addr("a"), basis="newsletter")
        with self.assertRaises(ValueError):
            self.stage(self.addr("a"), note="x" * (permission.MAX_NOTE + 1))

    def test_the_preview_counts_what_the_commit_will_do(self):
        permission.commit_batch(self.conn, batch_id=self.stage(self.addr("old"))["batch_id"], added_by=self.emp)
        batch = self.stage(self.addr("old"), self.addr("new"), "nobody")
        summary, sample, rejects = permission.fetch_preview(self.conn, batch["batch_id"])
        self.assertEqual((summary["to_add"], summary["already_listed"], summary["rejected"]), (1, 1, 1))
        self.assertEqual([r["email_raw"] for r in sample], [self.addr("new")])
        self.assertEqual([r["rejected"] for r in rejects], ["not an email address"])

    def test_the_commit_adds_only_new_good_rows_and_marks_the_batch(self):
        self.commit(self.stage(self.addr("old")))
        batch = self.stage(self.addr("old"), self.addr("New").upper(), "nobody")
        self.assertEqual(self.commit(batch), 1)
        self.assertEqual(self.active("new"), {"service_client"})
        self.assertIsNotNone(permission.fetch_batch(self.conn, batch["token"])["committed_at"])
        detail = self.conn.execute("SELECT detail, as_of FROM send_permission WHERE batch_id = %s",
                                   (batch["batch_id"],)).fetchone()
        self.assertEqual((detail["detail"], detail["as_of"]), ("Uploaded clients.csv", date(2026, 11, 1)))

    def test_a_client_can_also_be_added_as_a_subscriber(self):
        self.commit(self.stage(self.addr("a")))
        self.assertEqual(self.commit(self.stage(self.addr("a"), basis="cc_subscriber")), 1)
        self.assertEqual(self.active("a"), {"service_client", "cc_subscriber"})

    def test_a_removal_marks_every_basis_and_deletes_nothing(self):
        self.commit(self.stage(self.addr("a")))
        self.commit(self.stage(self.addr("a"), basis="cc_subscriber"))
        before = self.conn.execute("SELECT count(*) AS n FROM send_permission").fetchone()["n"]
        self.assertEqual(permission.remove_address(self.conn, email=" " + self.addr("A").upper(),
                                                   removed_by=self.emp), 2)
        self.assertEqual(self.active("a"), set())
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM send_permission").fetchone()["n"], before)
        self.assertEqual(permission.remove_address(self.conn, email=self.addr("a"), removed_by=self.emp), 0)

    def test_a_removed_address_can_be_added_again(self):
        self.commit(self.stage(self.addr("a")))
        permission.remove_address(self.conn, email=self.addr("a"), removed_by=self.emp)
        self.assertEqual(self.commit(self.stage(self.addr("a"))), 1)
        self.assertEqual(self.active("a"), {"service_client"})

    def test_counts_cover_every_basis(self):
        before = permission.counts(self.conn)
        self.assertEqual(set(before), set(permission.BASES))
        self.commit(self.stage(self.addr("a"), self.addr("b")))
        after = permission.counts(self.conn)
        self.assertEqual((after["service_client"] - before["service_client"],
                          after["cc_subscriber"] - before["cc_subscriber"]), (2, 0))

    def test_a_discarded_batch_is_gone(self):
        batch = self.stage(self.addr("a"))
        permission.discard_batch(self.conn, batch["batch_id"])
        self.assertIsNone(permission.fetch_batch(self.conn, batch["token"]))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class DatabaseGuardTest(unittest.TestCase):
    """The trigger on sent_emails:  the last line of the gate, whatever the code above it does."""

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.psycopg = psycopg
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.user = self.conn.execute("SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        found = self.conn.execute(
            "SELECT (SELECT realtor_id FROM realtors ORDER BY realtor_id LIMIT 1) AS realtor, "
            "(SELECT max(template_id) FROM email_templates) AS template, "
            "(SELECT max(run_id) FROM cc_sync_runs) AS sync").fetchone()
        if None in found.values():
            self.skipTest("the database has no realtor, template or sync run to build on")
        self.found = found
        self.email = f"guard-{uuid.uuid4().hex[:8]}@example.test"

    def insert(self):
        self.conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, sync_run_id, "
            "permission_asserted, created_by, subject, html_body) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'none', %s, 's', '<p>b</p>')",
            (self.found["realtor"], self.email, self.found["template"], self.found["sync"], self.user))

    def permit(self, email):
        self.conn.execute("INSERT INTO send_permission (email_raw, basis, as_of, added_by) "
                          "VALUES (%s, 'service_client', current_date, %s)", (email, self.user))

    def test_an_address_with_no_permission_is_refused(self):
        with self.assertRaises(self.psycopg.errors.CheckViolation) as caught:
            self.insert()
        self.assertIn("no recorded permission", str(caught.exception))

    def test_an_address_with_permission_is_accepted_whatever_its_case(self):
        self.permit("  " + self.email.upper() + " ")
        self.insert()

    def test_a_removed_permission_is_refused(self):
        self.permit(self.email)
        self.conn.execute("UPDATE send_permission SET removed_at = now(), removed_by = %s "
                          "WHERE email_norm = %s", (self.user, self.email))
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            self.insert()

    def test_the_drip_stop_reason_is_allowed(self):
        row = self.conn.execute(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'drip_recipients_reason_known'").fetchone()
        self.assertIn("no_permission", row["d"])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class PermissionPagesTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        from hailsys.web import admin as admin_web, create_app
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
        mod = admin_web.permission
        self.stage = stack.enter_context(mock.patch.object(mod, "stage", return_value="tok"))
        self.commit = stack.enter_context(mock.patch.object(mod, "commit_batch", return_value=2))
        self.remove = stack.enter_context(mock.patch.object(mod, "remove_address", return_value=1))
        self.batch = {"batch_id": 1, "token": "tok", "filename": "clients.csv", "basis": "cc_subscriber",
                      "as_of": date(2026, 11, 1), "note": None, "committed_at": None, "row_count": 2,
                      "uploaded_at": datetime.now(timezone.utc), "user_name": "someone"}
        stack.enter_context(mock.patch.object(mod, "fetch_batch", return_value=self.batch))
        stack.enter_context(mock.patch.object(mod, "fetch_preview", return_value=(
            {"to_add": 2, "already_listed": 0, "rejected": 0, "on_dnc": 1}, [], [])))

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(emp_id=self.users[role], role=role, user_name="test",
                     issued_at=datetime.now(timezone.utc).isoformat())
        return client

    def upload(self, role="admin", follow=True, **over):
        data = {"file": (io.BytesIO(csv_bytes("a@example.test", "b@example.test")), "clients.csv"),
                "basis": "service_client", "as_of": "2026-11-01", "note": " office export "}
        data.update(over)
        data = {k: v for k, v in data.items() if v is not None}
        return self.client_as(role).post("/admin/permission/upload", data=data,
                                         content_type="multipart/form-data", follow_redirects=follow)

    def test_the_admin_page_has_the_permission_tab(self):
        html = self.client_as().get("/admin/").get_data(as_text=True)
        self.assertIn('id="permission"', html)
        self.assertIn('action="/admin/permission/upload"', html)
        self.assertIn('action="/admin/permission/remove"', html)
        for basis in permission.BASES:
            self.assertIn(f'name="basis" value="{basis}"', html)

    def test_an_upload_is_staged_with_its_basis_date_and_note_and_goes_to_the_preview(self):
        resp = self.upload(follow=False)
        self.assertTrue(resp.headers["Location"].endswith("/admin/permission/preview/tok"))
        kw = self.stage.call_args.kwargs
        self.assertEqual((kw["filename"], kw["basis"], kw["as_of"], kw["note"], kw["uploaded_by"]),
                         ("clients.csv", "service_client", date(2026, 11, 1), "office export",
                          self.users["admin"]))
        self.assertEqual([r["email_raw"] for r in kw["rows"]], ["a@example.test", "b@example.test"])

    def test_a_missing_basis_or_date_is_refused_and_nothing_is_staged(self):
        for over, words in (({"basis": None}, "Say what kind of list this is."),
                            ({"basis": "newsletter"}, "Say what kind of list this is."),
                            ({"as_of": ""}, "Give the date the list is from."),
                            ({"note": "x" * 201}, "The note is longer than")):
            with self.subTest(over=over):
                self.assertIn(words, self.upload(**over).get_data(as_text=True))
        self.stage.assert_not_called()

    def test_only_an_admin_may_upload_commit_or_remove(self):
        for role in ("sender", "viewer"):
            if role in self.users:
                with self.subTest(role=role):
                    self.upload(role=role, follow=False)
                    self.client_as(role).post("/admin/permission/commit/tok")
                    self.client_as(role).post("/admin/permission/remove", data={"email": "a@example.test"})
        self.stage.assert_not_called()
        self.commit.assert_not_called()
        self.remove.assert_not_called()

    def test_the_preview_names_the_basis_and_the_do_not_contact_overlap(self):
        html = self.client_as().get("/admin/permission/preview/tok").get_data(as_text=True)
        self.assertIn(permission.BASES["cc_subscriber"], html)
        self.assertIn("on the do-not-contact list", html)
        self.assertIn("Add 2", html)

    def test_the_commit_adds_and_says_how_many(self):
        html = self.client_as().post("/admin/permission/commit/tok", follow_redirects=True).get_data(as_text=True)
        self.assertEqual(self.commit.call_args.kwargs["added_by"], self.users["admin"])
        self.assertIn("Added 2 addresses to the permission list from clients.csv.", html)

    def test_a_committed_batch_is_not_committed_twice(self):
        self.batch["committed_at"] = datetime.now(timezone.utc)
        html = self.client_as().post("/admin/permission/commit/tok", follow_redirects=True).get_data(as_text=True)
        self.assertIn("That file was already imported.", html)
        self.commit.assert_not_called()

    def test_a_removal_passes_the_address_and_who_removed_it(self):
        html = self.client_as().post("/admin/permission/remove", data={"email": " a@example.test "},
                                     follow_redirects=True).get_data(as_text=True)
        self.assertEqual(self.remove.call_args.kwargs, {"email": "a@example.test",
                                                        "removed_by": self.users["admin"]})
        self.assertIn("no longer has permission", html)


if __name__ == "__main__":
    unittest.main()
