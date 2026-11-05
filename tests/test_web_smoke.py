"""Smoke test of the web app's routes that no other test reached (item 53).

    docker compose --profile test run --rm -T test python -m unittest tests.test_web_smoke

Measured 2026-11-02 with a hook on every request: the suite reached 53 of the app's 81 routes, and these 28 never.
Signs in by writing the session, as the other screen tests do; a role comes from the session, so the one active
user can be presented as viewer, sender or admin.  Nothing is written and nothing outside is called:  every page is
a GET (/search/ without an address, so no geocoding), and every action is a POST that is refused, by a missing CSRF
token or by a role that may not use it.  No test makes an allowed POST:  that would create users, start a RentCast
pull (which costs money) or run the matcher.  Login and logout run whole;  a failed login's row in login_attempts
(item 299) is caught, so nothing is written.  Each test checks that the tables these routes write are unchanged.

What they pin:  signed out, each page redirects to /login;  signed in, each answers 200 with the right content type,
and refuses the roles it should;  each action refuses a missing token, a signed-out request and each role that may
not use it;  a wrong password is a 401 and a logout signs the session out.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

# The tables these routes could write, counted before and after each test.
WRITTEN = ("users", "api_pulls", "storm_listing_matches", "match_runs", "dnc_import_batches", "oauth_tokens",
           "address_searches", "geocode_cache", "login_attempts")

ROLES = ("viewer", "sender", "admin")

# Pages: path (a date is filled in) -> the roles that may open it.
PAGES = {
    "/login": ROLES,
    "/account/password": ROLES,
    "/activity": ROLES,
    "/activity.csv": ROLES,
    "/exports": ROLES,
    "/exports/matches.csv": ROLES,
    "/exports/realtors.csv": ("sender", "admin"),
    "/export.csv": ROLES,
    "/search/": ROLES,
    "/storms/zips?date={day}": ROLES,
    "/storms/matches?date={day}": ROLES,
    "/storms/matches.csv?date={day}": ROLES,
    "/storms/state?date={day}": ROLES,
    "/storms/banner?date={day}&zips=1&since=2026-01-01T00:00:00%2B00:00": ROLES,
}

# Actions: path -> the roles that may use it (never exercised here, only refused).
ACTIONS = {
    "/logout": ROLES,
    "/account/password": ROLES,
    "/pull": ("sender", "admin"),
    "/match": ("sender", "admin"),
    "/admin/users": ("admin",),
    "/admin/users/1/boot": ("admin",),
    "/admin/users/1/deactivate": ("admin",),
    "/admin/users/1/password": ("admin",),
    "/admin/users/1/reactivate": ("admin",),
    "/admin/users/1/role": ("admin",),
    "/admin/boot-all": ("admin",),
    "/admin/dnc/discard/no-such-token": ("admin",),
    "/cc/connect": ("admin",),
    "/cc/refresh": ("admin",),
}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class WebSmokeTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row, autocommit=True)
        self.addCleanup(self.conn.close)
        row = self.conn.execute("SELECT emp_id FROM users WHERE is_active AND role <> 'system' "
                                "ORDER BY emp_id LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no active user")
        self.emp_id = row["emp_id"]
        row = self.conn.execute("SELECT (max(utc_datetime) AT TIME ZONE 'America/Denver')::date AS day "
                                "FROM iem_data").fetchone()
        self.day = (row["day"] or datetime(1999, 6, 8).date()).isoformat()
        before = self.counts()
        self.addCleanup(lambda: self.assertEqual(self.counts(), before, "a smoke test wrote to the database"))

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in WRITTEN}

    def client(self, role=None):
        client = self.app.test_client()
        if role:
            with client.session_transaction() as s:
                s["emp_id"], s["role"], s["user_name"] = self.emp_id, role, "smoke"
                s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    # ---- pages ----

    def test_signed_out_each_page_goes_to_login(self):
        for path in PAGES:
            if path == "/login":
                continue
            with self.subTest(path=path):
                resp = self.client().get(path.format(day=self.day))
                self.assertEqual(resp.status_code, 302)
                self.assertIn("/login", resp.headers["Location"])

    def test_each_page_opens_for_the_roles_that_may_and_refuses_the_rest(self):
        for path, allowed in PAGES.items():
            for role in ROLES:
                with self.subTest(path=path, role=role):
                    resp = self.client(role).get(path.format(day=self.day))
                    self.assertEqual(resp.status_code, 200 if role in allowed else 403)
                    if role in allowed:
                        kind = "text/csv" if path.split("?")[0].endswith(".csv") else "text/html"
                        self.assertTrue(resp.content_type.startswith(kind), resp.content_type)

    def test_the_constant_contact_callback_is_admin_only_and_needs_its_state(self):
        for role in ("viewer", "sender"):
            with self.subTest(role=role):
                self.assertEqual(self.client(role).get("/cc/callback").status_code, 403)
        resp = self.client("admin").get("/cc/callback?code=x&state=forged")
        self.assertEqual(resp.status_code, 302)                     # refused:  no state was issued
        self.assertTrue(resp.headers["Location"].endswith("/cc/"))

    # ---- actions:  refused only ----

    def test_every_action_refuses_a_missing_csrf_token(self):
        for path in ACTIONS:
            for role in (None,) + ROLES:
                with self.subTest(path=path, role=role):
                    self.assertEqual(self.client(role).post(path).status_code, 400)

    def test_every_action_refuses_the_signed_out_and_the_roles_that_may_not_use_it(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        for path, allowed in ACTIONS.items():
            if path in ("/logout", "/account/password"):
                continue                                    # every signed-in role may use these
            with self.subTest(path=path, role=None):
                resp = self.client().post(path)
                self.assertEqual(resp.status_code, 302)
                self.assertIn("/login", resp.headers["Location"])
            for role in ROLES:
                if role in allowed:
                    continue                                # never make an allowed POST here
                with self.subTest(path=path, role=role):
                    self.assertEqual(self.client(role).post(path).status_code, 403)

    # ---- login and logout, whole ----

    def test_a_wrong_password_is_a_401_and_signs_nobody_in(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        client = self.client()
        # The attempt is recorded in login_attempts (item 299);  tests/test_sign_in_limits.py pins that.
        # Here the write is caught, so the database stays as it was.
        with mock.patch("hailsys.web.views.record_login_attempt") as record:
            resp = client.post("/login", data={"user_name": "no-such-user", "password": "not-the-password"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(record.call_args.args[1:], ("no-such-user", "failed"))
        with client.session_transaction() as s:
            self.assertNotIn("emp_id", s)

    def test_logout_clears_the_session(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        client = self.client("viewer")
        resp = client.post("/logout")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        with client.session_transaction() as s:
            self.assertNotIn("emp_id", s)
        self.assertEqual(client.get("/activity").status_code, 302)


if __name__ == "__main__":
    unittest.main()
