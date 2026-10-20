"""Tests for the drip-campaign limits on the admin page (hailsys/web/admin.py, item 204 piece 6.0).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_admin_drip

The page tests are the real admin page and only read.  The save tests replace the database
connection with a recorder, so no setting changes and no settings_history row is written; what is
pinned is the statement and values the route would run, and what the person is told.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app, admin as admin_web
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

FORM = dict(zip_radius="5", match_radius="3", billing_day="9", monthly_quota="1000",
            freshness_days="7", match_max_age_days="30", email_cap_days="14",
            drip_max_followups="4", drip_max_gap_days="14")


class Recorder:
    """Stands in for get_connection():  records what runs, writes nothing."""

    def __init__(self, error=None):
        self.executed = []
        self.commits = 0
        self.error = error

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        if self.error is not None and "UPDATE settings" in sql:
            raise self.error
        self.executed.append((sql, params))

    def commit(self):
        self.commits += 1

    def update(self):
        return [(s, p) for s, p in self.executed if "UPDATE settings" in s]


def check_violation(constraint):
    class Violation(psycopg.errors.CheckViolation):
        diag = SimpleNamespace(constraint_name=constraint)
    return Violation("refused")


class TemplateSourceTest(unittest.TestCase):
    """The page test cannot tell a value read from the database from the default it equals
    (3 and 7), nor see history cells while no history row has the columns, so the source is
    checked too."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "hailsys/web/templates/admin.html").read_text()
        start = cls.source.index("<h2>Settings history</h2>")
        cls.history = cls.source[start:cls.source.index("</section>", start)]

    def test_the_boxes_show_the_saved_values(self):
        self.assertIn('value="{{ settings.drip_max_followups }}"', self.source)
        self.assertIn('value="{{ settings.drip_max_gap_days }}"', self.source)

    def test_the_history_shows_both_values_with_a_dash_for_old_rows(self):
        for column in ("drip_max_followups", "drip_max_gap_days"):
            self.assertIn(f'{{{{ h.{column} if h.{column} is not none else "—" }}}}',
                          self.history)

    def test_the_empty_history_row_spans_every_column(self):
        header = self.history[self.history.index("<thead>"):self.history.index("</thead>")]
        self.assertIn(f'colspan="{header.count("<th>")}"', self.history)

    def test_each_history_row_has_a_cell_for_every_header(self):
        header = self.history[self.history.index("<thead>"):self.history.index("</thead>")]
        row = self.history[self.history.index("{% for h in history %}"):
                           self.history.index("{% else %}")]
        self.assertEqual(row.count("<td"), header.count("<th>"))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class AdminDripTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        row = self.conn.execute("SELECT emp_id FROM users WHERE is_active AND role = 'admin' "
                                "ORDER BY emp_id LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no active admin user in this database")
        self.admin_id = row["emp_id"]

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.admin_id
            s["role"] = "admin"
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def save(self, recorder=None, **overrides):
        recorder = recorder or Recorder()
        data = dict(FORM, **overrides)
        data = {k: v for k, v in data.items() if v is not None}
        client = self.client()
        with mock.patch.object(admin_web, "get_connection", recorder):
            response = client.post("/admin/settings", data=data)
        with client.session_transaction() as s:
            flashed = [m for _, m in s.get("_flashes", [])]
        return response, recorder, flashed

    # ---- the page ----

    def test_every_constraint_with_a_message_exists_in_the_database(self):
        names = {r["conname"] for r in self.conn.execute("SELECT conname FROM pg_constraint")}
        self.conn.rollback()
        for name in admin_web.CHECK_MESSAGES:
            self.assertIn(name, names)

    def test_the_drip_limits_have_plain_messages(self):
        self.assertIn("0 and 10", admin_web.CHECK_MESSAGES["settings_drip_max_followups_check"])
        self.assertIn("1 and 30", admin_web.CHECK_MESSAGES["settings_drip_max_gap_days_check"])

    def test_the_page_shows_both_boxes_with_the_current_values(self):
        row = self.conn.execute("SELECT drip_max_followups AS f, drip_max_gap_days AS g "
                                "FROM settings").fetchone()
        self.conn.rollback()
        page = self.client().get("/admin/").get_data(as_text=True)
        self.assertIn('name="drip_max_followups"', page)
        self.assertIn('name="drip_max_gap_days"', page)
        self.assertIn(f'name="drip_max_followups" type="number"\n                       step="1" '
                      f'min="0" max="10"\n                       value="{row["f"]}"', page)
        self.assertIn(f'min="1" max="30"\n                       value="{row["g"]}"', page)

    def test_the_boxes_are_inside_the_settings_form(self):
        page = self.client().get("/admin/").get_data(as_text=True)
        form_start = page.index('action="/admin/settings"')
        form_end = page.index("</form>", form_start)
        self.assertGreater(page.index('name="drip_max_followups"'), form_start)
        self.assertLess(page.index('name="drip_max_gap_days"'), form_end)

    def test_the_history_table_has_the_two_columns(self):
        page = self.client().get("/admin/").get_data(as_text=True)
        self.assertIn("<th>Follow-ups</th><th>Longest gap</th>", page)

    # ---- saving ----

    def test_a_save_writes_both_limits(self):
        response, rec, flashed = self.save()
        sql, params = rec.update()[0]
        self.assertIn("drip_max_followups = %s", sql)
        self.assertIn("drip_max_gap_days = %s", sql)
        self.assertEqual(sql.count("%s"), len(params))
        self.assertEqual(params[-2:], (4, 14))
        self.assertEqual(rec.commits, 1)
        self.assertEqual(response.status_code, 302)

    def test_a_save_still_writes_the_older_settings(self):
        _, rec, _ = self.save()
        _, params = rec.update()[0]
        self.assertEqual([str(p) for p in params[:7]], ["5", "3", "9", "1000", "7", "30", "14"])

    def test_the_acting_admin_is_set_for_the_history_trigger(self):
        _, rec, _ = self.save()
        self.assertTrue(any("app.current_emp_id" in s for s, _ in rec.executed))

    def test_the_message_names_the_limits(self):
        _, _, flashed = self.save()
        self.assertTrue(any("Campaigns: up to 4 follow-ups, gaps up to 14 days." in m
                            for m in flashed))

    def test_zero_follow_ups_is_accepted(self):
        _, rec, flashed = self.save(drip_max_followups="0")
        self.assertEqual(rec.update()[0][1][-2], 0)
        self.assertTrue(any("up to 0 follow-ups" in m for m in flashed))

    def test_a_missing_follow_up_limit_is_refused_before_any_write(self):
        _, rec, flashed = self.save(drip_max_followups=None)
        self.assertEqual(rec.update(), [])
        self.assertTrue(any("must all be numbers" in m for m in flashed))

    def test_a_missing_gap_limit_is_refused_before_any_write(self):
        _, rec, flashed = self.save(drip_max_gap_days=None)
        self.assertEqual(rec.update(), [])
        self.assertTrue(any("must all be numbers" in m for m in flashed))

    def test_a_non_number_is_refused_before_any_write(self):
        _, rec, flashed = self.save(drip_max_gap_days="soon")
        self.assertEqual(rec.update(), [])
        self.assertTrue(any("must all be numbers" in m for m in flashed))

    def test_too_many_follow_ups_shows_the_plain_message(self):
        _, rec, flashed = self.save(
            Recorder(error=check_violation("settings_drip_max_followups_check")),
            drip_max_followups="11")
        self.assertEqual(flashed, ["Most follow-ups must be between 0 and 10."])

    def test_a_gap_out_of_range_shows_the_plain_message(self):
        _, rec, flashed = self.save(
            Recorder(error=check_violation("settings_drip_max_gap_days_check")),
            drip_max_gap_days="31")
        self.assertEqual(flashed, ["Longest gap must be between 1 and 30 days."])

    def test_a_refused_save_does_not_claim_success(self):
        _, _, flashed = self.save(
            Recorder(error=check_violation("settings_drip_max_gap_days_check")),
            drip_max_gap_days="31")
        self.assertFalse(any("Settings updated" in m for m in flashed))

    def test_only_an_admin_can_save(self):
        client = self.app.test_client()
        with mock.patch.object(admin_web, "get_connection", Recorder()) as rec:
            response = client.post("/admin/settings", data=FORM)
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(rec.update(), [])


if __name__ == "__main__":
    unittest.main()
