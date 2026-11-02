"""The admin user actions and the own-password change, doing their work (item 286, part 1).

    docker compose --profile test run --rm -T test python -m unittest tests.test_admin_users

tests/test_web_smoke.py reaches these routes only as refusals.  Here each runs as an allowed user and changes the
database, on one connection that every get_connection() in admin.py and views.py is handed (TestConn, as in
test_pull_jobs.py):  a commit moves a savepoint forward, and the whole test is rolled back.  Runs as hail_app, the role
the web app uses, so its grants are what is tested.  The acting admin must be a real, active admin, because the sign-in
check reads users on a connection of its own;  the users acted on are made inside the test and never committed.

The last-admin rule (sql/019) is a constraint trigger checked at COMMIT, which these tests never reach, so the one test
of it sets it to check at once.

What they pin:  an admin creates a user with the role, the creator and a password that verifies, and a duplicate or a
short password is refused with nothing written;  a role change, a deactivate, a reactivate and a sign-out each change
that user and sign them out where the rule says so;  an admin cannot act on their own row or on the system account;  a
reset password verifies and signs the user out;  "sign out everyone" stamps settings;  discarding a staged DNC upload
removes it and its rows and adds nothing to dnc_list;  a user changes their own password only with the current one;
and the last active admin cannot be demoted.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.queries import dncimport
    from hailsys.web import create_app, admin as admin_web, views
    from hailsys.web.auth import hash_password, verify_password
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

PASSWORD = "correct-horse-battery"          # longer than MIN_PASSWORD_LENGTH (12)


class TestConn:
    """The test's one connection, handed to every get_connection() call.  A commit moves a savepoint forward and a
    rollback goes back to it;  the whole test is rolled back at the end (as in test_pull_jobs.py)."""
    def __init__(self, conn):
        self._c, self._n = conn, 0
        self._savepoint()

    def _savepoint(self):
        self._n += 1
        self._c.execute(f"SAVEPOINT t{self._n}")

    def commit(self):
        self._savepoint()

    def rollback(self):
        self._c.execute(f"ROLLBACK TO SAVEPOINT t{self._n}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        return False

    def __getattr__(self, name):
        return getattr(self._c, name)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class AdminUsersTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.raw.close)
        self.addCleanup(self.raw.rollback)
        row = self.raw.execute("SELECT emp_id FROM users WHERE role = 'admin' AND is_active "
                               "ORDER BY emp_id LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no active admin")
        self.admin = row["emp_id"]
        self.system = self.raw.execute("SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.conn = TestConn(self.raw)
        for module in (admin_web, views):
            patcher = mock.patch.object(module, "get_connection", return_value=self.conn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tag = uuid.uuid4().hex[:8]

    # ---- helpers ----

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.admin, "admin", "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def setup(self, sql, params=()):
        """A write the test needs in place, kept past any rollback a route makes (the savepoint moves past it)."""
        row = self.raw.execute(sql, params).fetchone() if "RETURNING" in sql else self.raw.execute(sql, params)
        self.conn.commit()
        return row

    def make_user(self, role="sender", name="t"):
        return self.setup(
            "INSERT INTO users (user_name, emp_fname, emp_lname, emp_email, password_hash, role, created_by) "
            "VALUES (%s, 'Test', 'User', %s, %s, %s, %s) RETURNING emp_id",
            (f"{name}-{self.tag}", f"{name}-{self.tag}@example.invalid", hash_password(PASSWORD), role,
             self.admin))["emp_id"]

    def user(self, emp_id):
        return self.raw.execute("SELECT * FROM users WHERE emp_id = %s", (emp_id,)).fetchone()

    def flashes(self, client):
        with client.session_transaction() as s:
            return [m for _, m in s.get("_flashes", [])]

    def new_user_form(self, **over):
        form = {"user_name": f"new-{self.tag}", "first_name": "New", "last_name": "Person",
                "email": f"new-{self.tag}@example.invalid", "role": "viewer",
                "password": PASSWORD, "confirm": PASSWORD}
        form.update(over)
        return form

    # ---- create ----

    def test_an_admin_creates_a_user(self):
        resp = self.client().post("/admin/users", data=self.new_user_form())
        self.assertEqual(resp.status_code, 302)
        row = self.raw.execute("SELECT * FROM users WHERE user_name = %s", (f"new-{self.tag}",)).fetchone()
        self.assertEqual((row["role"], row["created_by"], row["is_active"]), ("viewer", self.admin, True))
        self.assertTrue(verify_password(PASSWORD, row["password_hash"]))

    def test_a_duplicate_user_name_is_refused(self):
        self.make_user(name="new")                          # takes new-<tag>
        resp = self.client().post("/admin/users", data=self.new_user_form(email=f"other-{self.tag}@example.invalid"))
        self.assertEqual(resp.status_code, 409)
        n = self.raw.execute("SELECT count(*) AS n FROM users WHERE user_name = %s", (f"new-{self.tag}",)).fetchone()
        self.assertEqual(n["n"], 1)

    def test_a_short_or_mismatched_password_is_refused_and_nothing_is_written(self):
        for form in (self.new_user_form(password="short", confirm="short"),
                     self.new_user_form(confirm=PASSWORD + "x")):
            with self.subTest(form=form["password"]):
                self.assertEqual(self.client().post("/admin/users", data=form).status_code, 400)
        self.assertIsNone(self.raw.execute("SELECT 1 FROM users WHERE user_name = %s",
                                           (f"new-{self.tag}",)).fetchone())

    # ---- role, deactivate, reactivate, sign out ----

    def test_a_role_change_takes_effect_and_signs_the_user_out(self):
        emp = self.make_user("sender")
        self.client().post(f"/admin/users/{emp}/role", data={"role": "viewer"})
        row = self.user(emp)
        self.assertEqual(row["role"], "viewer")
        self.assertIsNotNone(row["sessions_invalidated_at"])

    def test_an_unknown_role_is_refused(self):
        emp = self.make_user("sender")
        client = self.client()
        client.post(f"/admin/users/{emp}/role", data={"role": "owner"})
        self.assertEqual(self.user(emp)["role"], "sender")
        self.assertIn("Please pick a valid role.", self.flashes(client))

    def test_deactivate_then_reactivate(self):
        emp = self.make_user("viewer")
        self.client().post(f"/admin/users/{emp}/deactivate")
        self.assertFalse(self.user(emp)["is_active"])
        self.client().post(f"/admin/users/{emp}/reactivate")
        self.assertTrue(self.user(emp)["is_active"])

    def test_sign_out_one_user(self):
        emp = self.make_user("viewer")
        self.assertIsNone(self.user(emp)["sessions_invalidated_at"])
        self.client().post(f"/admin/users/{emp}/boot")
        self.assertIsNotNone(self.user(emp)["sessions_invalidated_at"])

    def test_an_admin_cannot_act_on_their_own_row(self):
        before = self.user(self.admin)
        for path, data in (("role", {"role": "viewer"}), ("deactivate", {}), ("boot", {}),
                           ("password", {"new_password": PASSWORD, "confirm_password": PASSWORD})):
            with self.subTest(action=path):
                self.client().post(f"/admin/users/{self.admin}/{path}", data=data)
        after = self.user(self.admin)
        for column in ("role", "is_active", "sessions_invalidated_at", "password_hash"):
            self.assertEqual(after[column], before[column], column)

    def test_the_system_account_cannot_be_changed(self):
        before = self.user(self.system)
        client = self.client()
        for path, data in (("role", {"role": "admin"}), ("reactivate", {}), ("boot", {}),
                           ("password", {"new_password": PASSWORD, "confirm_password": PASSWORD})):
            with self.subTest(action=path):
                client.post(f"/admin/users/{self.system}/{path}", data=data)
        after = self.user(self.system)
        for column in ("role", "is_active", "sessions_invalidated_at", "password_hash"):
            self.assertEqual(after[column], before[column], column)
        self.assertIn("No such user.", self.flashes(client))

    # ---- reset password ----

    def test_a_reset_password_verifies_and_signs_the_user_out(self):
        emp = self.make_user("sender")
        new = "a-brand-new-password"
        self.client().post(f"/admin/users/{emp}/password", data={"new_password": new, "confirm_password": new})
        row = self.user(emp)
        self.assertTrue(verify_password(new, row["password_hash"]))
        self.assertIsNotNone(row["sessions_invalidated_at"])

    def test_a_mismatched_reset_changes_nothing(self):
        emp = self.make_user("sender")
        before = self.user(emp)["password_hash"]
        self.client().post(f"/admin/users/{emp}/password",
                           data={"new_password": "a-brand-new-password", "confirm_password": "something-else-x"})
        self.assertEqual(self.user(emp)["password_hash"], before)

    # ---- sign out everyone ----

    def test_sign_out_everyone_stamps_settings_and_goes_to_login(self):
        before = self.raw.execute("SELECT global_sessions_invalidated_at AS t FROM settings").fetchone()["t"]
        resp = self.client().post("/admin/boot-all")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        after = self.raw.execute("SELECT global_sessions_invalidated_at AS t FROM settings").fetchone()["t"]
        self.assertIsNotNone(after)
        self.assertNotEqual(after, before)

    # ---- discard a staged DNC upload ----

    def test_discarding_an_upload_removes_it_and_adds_nothing_to_dnc_list(self):
        listed = self.raw.execute("SELECT count(*) AS n FROM dnc_list").fetchone()["n"]
        token = dncimport.stage(self.raw, rows=[{"line_no": 1, "email_raw": f"x-{self.tag}@example.invalid",
                                                 "name_at_add": None, "added_at": None, "rejected": None}],
                                filename="t.csv", uploaded_by=self.admin, source="manual")
        self.conn.commit()
        self.client().post(f"/admin/dnc/discard/{token}")
        self.assertIsNone(dncimport.fetch_batch(self.raw, token))
        self.assertEqual(self.raw.execute("SELECT count(*) AS n FROM dnc_list").fetchone()["n"], listed)

    # ---- own password ----

    def test_a_user_changes_their_own_password_with_the_current_one(self):
        self.setup("UPDATE users SET password_hash = %s WHERE emp_id = %s", (hash_password(PASSWORD), self.admin))
        new = "my-own-new-password"
        resp = self.client().post("/account/password", data={
            "current_password": PASSWORD, "new_password": new, "confirm_password": new})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(verify_password(new, self.user(self.admin)["password_hash"]))

    def test_a_wrong_current_password_changes_nothing(self):
        self.setup("UPDATE users SET password_hash = %s WHERE emp_id = %s", (hash_password(PASSWORD), self.admin))
        before = self.user(self.admin)["password_hash"]
        resp = self.client().post("/account/password", data={
            "current_password": "not-the-password", "new_password": "my-own-new-password",
            "confirm_password": "my-own-new-password"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.user(self.admin)["password_hash"], before)

    # ---- the last admin (sql/019) ----

    def test_the_last_active_admin_cannot_be_demoted(self):
        self.setup("SET CONSTRAINTS trg_last_admin IMMEDIATE")            # checked at COMMIT otherwise
        target = self.make_user("admin")
        # Inside this transaction only, every other admin is inactive, so the target is the last one.
        self.setup("UPDATE users SET is_active = FALSE WHERE role = 'admin' AND emp_id <> %s", (target,))
        client = self.client()
        client.post(f"/admin/users/{target}/role", data={"role": "viewer"})
        self.assertEqual(self.user(target)["role"], "admin")
        self.assertTrue(self.flashes(client))                              # the trigger's message is shown


if __name__ == "__main__":
    unittest.main()
