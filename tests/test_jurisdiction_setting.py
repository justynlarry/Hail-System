"""The near-boundary distance on the admin page (item 190 step 1, sql/069).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_jurisdiction_setting

The page tests read the real admin page.  The save tests replace the database connection with
test_admin_drip's recorder, so nothing is written.  The range test runs its UPDATE inside a
transaction that is rolled back.

What they pin:  fetch_settings returns the distance as an int;  the box is inside the settings form
with the current value;  a save writes it (before the two drip limits, which test_admin_drip pins
last) and the message names it;  a missing value is refused before any write;  the range CHECK has
a plain message, and the database refuses 9 and 1001;  the history shows the column with a dash
for rows written before 069.
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
    from hailsys.settings import fetch_settings
    from hailsys.web import create_app, admin as admin_web
    # Helpers only.  Importing the test class would run its tests a second time here.
    from tests.test_admin_drip import FORM, Recorder, check_violation
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


class TemplateSourceTest(unittest.TestCase):
    def test_the_history_shows_the_distance_with_a_dash_for_old_rows(self):
        source = (ROOT / "hailsys/web/templates/admin.html").read_text()
        self.assertIn("<th>Near boundary</th>", source)
        self.assertIn('{{ "%s m"|format(h.jurisdiction_near_boundary_m) '
                      'if h.jurisdiction_near_boundary_m is not none else "—" }}', source)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class NearBoundarySettingTest(unittest.TestCase):
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
        data = {k: v for k, v in dict(FORM, **overrides).items() if v is not None}
        client = self.client()
        with mock.patch.object(admin_web, "get_connection", recorder):
            response = client.post("/admin/settings", data=data)
        with client.session_transaction() as s:
            flashed = [m for _, m in s.get("_flashes", [])]
        return response, recorder, flashed

    def current(self):
        value = self.conn.execute("SELECT jurisdiction_near_boundary_m AS m FROM settings "
                                  "WHERE id = 1").fetchone()["m"]
        self.conn.rollback()
        return value

    # ---- reading ----

    def test_fetch_settings_returns_the_distance_as_an_int(self):
        got = fetch_settings(self.conn)["jurisdiction_near_boundary_m"]
        self.conn.rollback()
        self.assertEqual(got, self.current())
        self.assertIsInstance(got, int)

    def test_the_box_is_inside_the_settings_form_with_the_current_value(self):
        page = self.client().get("/admin/").get_data(as_text=True)
        flat = " ".join(page.split())
        self.assertIn('name="jurisdiction_near_boundary_m" type="number" step="1" '
                      f'min="10" max="1000" value="{self.current()}"', flat)
        form_start = page.index('action="/admin/settings"')
        form_end = page.index("</form>", form_start)
        self.assertTrue(form_start < page.index('name="jurisdiction_near_boundary_m"') < form_end)

    # ---- saving ----

    def test_a_save_writes_the_distance_and_names_it(self):
        response, rec, flashed = self.save(jurisdiction_near_boundary_m="200")
        sql, params = rec.update()[0]
        self.assertIn("jurisdiction_near_boundary_m = %s", sql)
        self.assertEqual(sql.count("%s"), len(params))
        self.assertEqual(params[-3], 200)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("Near a boundary within 200 m." in m for m in flashed))

    def test_a_missing_distance_is_refused_before_any_write(self):
        _, rec, flashed = self.save(jurisdiction_near_boundary_m=None)
        self.assertEqual(rec.update(), [])
        self.assertTrue(any("must all be numbers" in m for m in flashed))

    def test_an_out_of_range_distance_shows_the_plain_message(self):
        _, _, flashed = self.save(
            Recorder(error=check_violation("settings_jurisdiction_near_boundary_m_check")),
            jurisdiction_near_boundary_m="5")
        self.assertEqual(flashed, ["Near-boundary distance must be between 10 and 1000 metres."])

    def test_the_database_refuses_9_and_1001(self):
        before = self.current()
        for bad in (9, 1001):
            with self.subTest(metres=bad):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.transaction():
                        self.conn.execute("SELECT set_config('app.current_emp_id', %s, true)",
                                          (str(self.admin_id),))
                        self.conn.execute("UPDATE settings SET jurisdiction_near_boundary_m = %s "
                                          "WHERE id = 1", (bad,))
        self.assertEqual(self.current(), before)


if __name__ == "__main__":
    unittest.main()
