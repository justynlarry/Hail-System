"""Sign-in limits (item 299):  the cookie flags, the failed sign-in lock, the session length.

    docker compose --profile test run --rm -T test python -m unittest tests.test_sign_in_limits

Runs on one connection that every get_connection() in views.py, auth.py and admin.py is handed
(TestConn, as in test_admin_users.py):  the user, the login_attempts rows and any settings change a
test makes are rolled back at the end.  Inside one transaction now() does not move, so attempts are
ordered by attempt_id, and a test that needs an old failure writes its attempted_at.  Runs as
hail_app, so its grants on login_attempts are what is tested.

What they pin:  the session cookie is Secure, HttpOnly and SameSite=Lax;  a good sign-in is recorded
'ok' and a wrong one 'failed';  the limit of failures refuses the name even with the right password,
without checking it, recorded 'locked';  one fewer does not;  a name that is not an account locks the
same way;  the lock is per name;  failures older than the window, or before a good sign-in, do not
count;  a session older than settings.session_max_hours is signed out with a message, a younger one is
not, one with no issued_at is signed out, and a changed setting applies to open sessions;  the setting
is refused outside 1 to 72;  the admin page saves it and settings_history logs it.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app, auth, views, admin as admin_web
    from hailsys.web.auth import hash_password, LOGIN_FAILURE_LIMIT, LOGIN_FAILURE_WINDOW
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

PASSWORD = "correct-horse-battery"          # longer than MIN_PASSWORD_LENGTH (12)
WRONG = "not-the-right-password"


class TestConn:
    """The test's one connection, handed to every get_connection() call.  A commit moves a savepoint
    forward and a rollback goes back to it;  the whole test is rolled back at the end."""
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
class SignInLimitsTest(unittest.TestCase):
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
        self.conn = TestConn(self.raw)
        for module in (views, auth, admin_web):
            patcher = mock.patch.object(module, "get_connection", return_value=self.conn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tag = uuid.uuid4().hex[:8]
        self.name = f"limits-{self.tag}"
        self.emp_id = self.raw.execute(
            "INSERT INTO users (user_name, emp_fname, emp_lname, emp_email, password_hash, role, created_by) "
            "VALUES (%s, 'Test', 'User', %s, %s, 'viewer', %s) RETURNING emp_id",
            (self.name, f"{self.name}@example.invalid", hash_password(PASSWORD), self.admin)).fetchone()["emp_id"]
        self.conn.commit()

    # ---- helpers ----

    def sign_in(self, name=None, password=PASSWORD):
        """(response, signed in?) for one POST to /login."""
        client = self.app.test_client()
        resp = client.post("/login", data={"user_name": name or self.name, "password": password})
        with client.session_transaction() as s:
            return resp, "emp_id" in s

    def fail(self, times, name=None):
        for _ in range(times):
            resp, signed_in = self.sign_in(name, WRONG)
            self.assertEqual(resp.status_code, 401)
            self.assertFalse(signed_in)

    def outcomes(self, name=None):
        rows = self.raw.execute("SELECT outcome FROM login_attempts WHERE user_name = %s ORDER BY attempt_id",
                                (name or self.name,)).fetchall()
        return [r["outcome"] for r in rows]

    def max_hours(self):
        return self.raw.execute("SELECT session_max_hours FROM settings").fetchone()["session_max_hours"]

    def signed_in_client(self, issued_at, emp_id=None, role="viewer"):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = emp_id or self.emp_id, role, "test"
            if issued_at is not None:
                s["issued_at"] = issued_at.isoformat()
        return client

    def ago(self, **kw):
        return datetime.now(timezone.utc) - timedelta(**kw)

    # ---- the cookie ----

    def test_the_session_cookie_is_secure_httponly_and_lax(self):
        client = self.app.test_client()
        resp = client.post("/login", data={"user_name": self.name, "password": PASSWORD})
        cookie = resp.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax"):
            with self.subTest(flag=flag):
                self.assertIn(flag, cookie)

    # ---- recording ----

    def test_a_good_sign_in_is_recorded_ok(self):
        resp, signed_in = self.sign_in()
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(signed_in)
        self.assertEqual(self.outcomes(), ["ok"])

    def test_a_wrong_password_is_recorded_failed(self):
        self.fail(1)
        self.assertEqual(self.outcomes(), ["failed"])

    # ---- the lock ----

    def test_the_limit_of_failures_refuses_the_right_password(self):
        self.fail(LOGIN_FAILURE_LIMIT)
        resp, signed_in = self.sign_in()
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(signed_in)
        self.assertIn("Invalid User Name or Password", resp.get_data(as_text=True))
        self.assertEqual(self.outcomes(), ["failed"] * LOGIN_FAILURE_LIMIT + ["locked"])

    def test_a_locked_name_is_refused_without_checking_the_password(self):
        self.fail(LOGIN_FAILURE_LIMIT)
        with mock.patch.object(views, "verify_password") as verify:
            self.sign_in()
        verify.assert_not_called()

    def test_one_failure_fewer_does_not_lock(self):
        self.fail(LOGIN_FAILURE_LIMIT - 1)
        self.assertTrue(self.sign_in()[1])

    def test_a_name_that_is_not_an_account_locks_the_same_way(self):
        ghost = f"ghost-{self.tag}"
        self.fail(LOGIN_FAILURE_LIMIT, name=ghost)
        resp, _ = self.sign_in(ghost)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.outcomes(ghost), ["failed"] * LOGIN_FAILURE_LIMIT + ["locked"])

    def test_the_lock_is_per_name(self):
        self.fail(LOGIN_FAILURE_LIMIT, name=f"other-{self.tag}")
        self.assertTrue(self.sign_in()[1])

    def test_failures_older_than_the_window_do_not_count(self):
        old = self.ago(minutes=LOGIN_FAILURE_WINDOW.total_seconds() / 60 + 1)
        for _ in range(LOGIN_FAILURE_LIMIT):
            self.raw.execute("INSERT INTO login_attempts (user_name, outcome, attempted_at) "
                             "VALUES (%s, 'failed', %s)", (self.name, old))
        self.conn.commit()
        self.assertTrue(self.sign_in()[1])

    def test_failures_before_a_good_sign_in_do_not_count(self):
        self.fail(LOGIN_FAILURE_LIMIT - 1)
        self.assertTrue(self.sign_in()[1])
        self.fail(LOGIN_FAILURE_LIMIT - 1)
        self.assertTrue(self.sign_in()[1])

    # ---- the session length ----

    def test_a_session_younger_than_the_setting_stays_signed_in(self):
        start = self.ago(hours=self.max_hours(), minutes=-5)
        self.assertEqual(self.signed_in_client(start).get("/activity").status_code, 200)

    def test_a_session_older_than_the_setting_is_signed_out_with_a_message(self):
        client = self.signed_in_client(self.ago(hours=self.max_hours(), minutes=1))
        resp = client.get("/activity")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        with client.session_transaction() as s:
            self.assertNotIn("emp_id", s)
            flashed = [m for _, m in s.get("_flashes", [])]
        self.assertTrue(any("signed out after" in m for m in flashed), flashed)

    def test_a_session_with_no_start_time_is_signed_out(self):
        self.assertEqual(self.signed_in_client(None).get("/activity").status_code, 302)

    def test_a_changed_setting_applies_to_open_sessions(self):
        self.raw.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(self.admin),))
        self.raw.execute("UPDATE settings SET session_max_hours = 1")
        self.conn.commit()
        self.assertEqual(self.signed_in_client(self.ago(hours=2)).get("/activity").status_code, 302)

    def test_the_setting_is_refused_outside_1_to_72(self):
        for bad in (0, 73):
            with self.subTest(hours=bad):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.raw.transaction():
                        self.raw.execute("UPDATE settings SET session_max_hours = %s", (bad,))

    def test_the_admin_page_saves_the_setting_and_history_logs_it(self):
        s = self.raw.execute("SELECT * FROM settings").fetchone()
        new = 24 if s["session_max_hours"] != 24 else 25
        form = dict(zip_radius=str(s["default_zip_radius_miles"]),
                    match_radius=str(s["default_match_radius_miles"]),
                    billing_day=str(s["rentcast_billing_day"]), monthly_quota=str(s["rentcast_monthly_quota"]),
                    freshness_days=str(s["listing_freshness_days"]),
                    match_max_age_days=str(s["match_max_age_days"]), email_cap_days=str(s["email_cap_days"]),
                    drip_max_followups=str(s["drip_max_followups"]),
                    drip_max_gap_days=str(s["drip_max_gap_days"]), session_max_hours=str(new))
        client = self.signed_in_client(self.ago(minutes=1), emp_id=self.admin, role="admin")
        resp = client.post("/admin/settings", data=form)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.max_hours(), new)
        row = self.raw.execute("SELECT session_max_hours, changed_by FROM settings_history "
                               "ORDER BY history_id DESC LIMIT 1").fetchone()
        self.assertEqual((row["session_max_hours"], row["changed_by"]), (new, self.admin))


if __name__ == "__main__":
    unittest.main()
