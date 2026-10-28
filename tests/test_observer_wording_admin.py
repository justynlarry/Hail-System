"""Tests for the CoCoRaHS observer-wording switch (Phase 5c step 4 piece 4b-2):  likelihoodadmin's reader and
writer, and the form in the admin page's Hail zones section.

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_observer_wording_admin

The query tests run in a transaction that is rolled back.  The page tests replace the writer, so no test changes
the real setting (it stays false until the CoCoRaHS usage policy is read).

What they pin:  the writer really changes the setting both ways and logs who did it to settings_history;  the page
shows the state and a checkbox that matches it;  only an admin can post;  a tick posts true, anything else
(unticked, missing, a forged value) posts false, so a malformed post can only switch it off.
"""

import contextlib
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app, admin as admin_web
    from hailsys.queries import likelihoodadmin as la
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ObserverWordingQueryTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users WHERE role = 'admin'").fetchone()["e"]

    def stored(self):
        return self.conn.execute("SELECT zone_observer_wording_approved AS a FROM settings WHERE id = 1").fetchone()["a"]

    def test_the_reader_reads_the_setting(self):
        self.assertIs(la.observer_wording_approved(self.conn), self.stored())

    def test_the_writer_switches_it_on_and_off(self):
        self.assertIs(la.save_observer_wording(self.conn, self.emp, True), True)
        self.assertIs(self.stored(), True)
        self.assertIs(la.observer_wording_approved(self.conn), True)
        self.assertIs(la.save_observer_wording(self.conn, self.emp, False), False)
        self.assertIs(self.stored(), False)

    def test_each_change_is_logged_with_who_made_it(self):
        before = self.conn.execute("SELECT count(*) AS n FROM settings_history").fetchone()["n"]
        la.save_observer_wording(self.conn, self.emp, not self.stored())
        rows = self.conn.execute(
            "SELECT changed_by, zone_observer_wording_approved AS a FROM settings_history "
            "ORDER BY changed_at DESC, ctid DESC LIMIT 1").fetchall()
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM settings_history").fetchone()["n"], before + 1)
        self.assertEqual((rows[0]["changed_by"], rows[0]["a"]), (self.emp, self.stored()))

    def test_a_truthy_value_is_stored_as_a_boolean(self):
        self.assertIs(la.save_observer_wording(self.conn, self.emp, "yes"), True)
        self.assertIs(self.stored(), True)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class ObserverWordingPageTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        with psycopg.connect(row_factory=dict_row) as conn:
            self.users = {}
            for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                  "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
                self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.save = stack.enter_context(mock.patch.object(admin_web.likelihoodadmin, "save_observer_wording"))
        self.state = stack.enter_context(mock.patch.object(admin_web.likelihoodadmin, "observer_wording_approved",
                                                           return_value=False))

    def client_as(self, role):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(emp_id=self.users[role], role=role, user_name="test",
                     issued_at=datetime.now(timezone.utc).isoformat())
        return client

    def post(self, role="admin", csrf=False, **data):
        self.app.config["WTF_CSRF_ENABLED"] = csrf
        return self.client_as(role).post("/admin/hail-zones/observer-wording", data=data, follow_redirects=True)

    def checkbox(self, html):
        start = html.index('name="approved"')
        return html[html.rindex("<input", 0, start):html.index(">", start) + 1]

    def test_the_page_shows_not_approved_and_an_unticked_box(self):
        html = self.client_as("admin").get("/admin/").get_data(as_text=True)
        self.assertIn("CoCoRaHS observer wording", html)
        self.assertIn("Now: <strong>not approved</strong>", html)
        self.assertNotIn("checked", self.checkbox(html))
        self.assertIn('action="/admin/hail-zones/observer-wording"', html)

    def test_the_page_shows_approved_and_a_ticked_box(self):
        self.state.return_value = True
        html = self.client_as("admin").get("/admin/").get_data(as_text=True)
        self.assertIn("Now: <strong>approved</strong>", html)
        self.assertIn("checked", self.checkbox(html))

    def test_a_tick_approves_and_passes_the_admin(self):
        html = self.post(approved="1").get_data(as_text=True)
        self.assertEqual(self.save.call_args.args[1:], (self.users["admin"], True))
        self.assertIn("Observer wording approved", html)

    def test_anything_but_a_tick_switches_it_off(self):
        for data in ({}, {"approved": ""}, {"approved": "0"}, {"approved": "true"}, {"approved": "on"}):
            with self.subTest(data=data):
                self.save.reset_mock()
                html = self.post(**data).get_data(as_text=True)
                self.assertEqual(self.save.call_args.args[1:], (self.users["admin"], False))
                self.assertIn("Observer wording not approved", html)

    def test_the_change_is_committed(self):
        # Only the handler's own connection is replaced;  the sign-in check still reads the real database.
        conn = mock.MagicMock()
        self.app.config["WTF_CSRF_ENABLED"] = False
        client = self.client_as("admin")
        with mock.patch.object(admin_web, "get_connection", return_value=conn):
            client.post("/admin/hail-zones/observer-wording", data={"approved": "1"})
        self.assertIs(self.save.call_args.args[0], conn.__enter__.return_value)
        conn.__enter__.return_value.commit.assert_called_once()

    def test_the_form_carries_its_csrf_token(self):
        html = self.client_as("admin").get("/admin/").get_data(as_text=True)
        start = html.index('action="/admin/hail-zones/observer-wording"')
        form = html[start:html.index("</form>", start)]
        self.assertRegex(form, r'<input type="hidden" name="csrf_token" value="[^"]+">')

    def test_only_an_admin_may_post(self):
        for role in ("sender", "viewer"):
            if role in self.users:
                with self.subTest(role=role):
                    self.post(role=role, approved="1")
        self.save.assert_not_called()

    def test_a_post_without_a_csrf_token_is_refused(self):
        resp = self.post(csrf=True, approved="1")
        self.assertEqual(resp.status_code, 400)
        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
