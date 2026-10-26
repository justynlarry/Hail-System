"""Tests for the Hail zones section of the admin page (hailsys/web/admin.py, _hail_zones.html; slice 7).

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_admin_hail_zones

The writes (hailsys/queries/likelihoodadmin.py, tested on the real database in test_likelihood_admin) are replaced in
every test, so nothing here adds a set or changes the storm states.  The page itself is the real one and only reads.

What they pin:  the section shows the set in use with its seven numbers and 20 levels, the history with a "use again"
link on the older sets (a link that fills the form, never a write), and the days waiting for a rebuild with the
commands;  only an admin can post, every post needs a CSRF token, and a refusal comes back as a message with what
was typed still in the form.
"""

import contextlib
import os
import re
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app, admin as admin_web
    from hailsys.queries import likelihoodadmin as la
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


def grid(level="one_source", **special):
    out = {(layer, band): level for layer in ("core", "margin", "radar_indicated", "outer", "none")
           for band in ("le_1", "1_3", "3_5", "over_5")}
    out[("core", "le_1")] = "agree"
    out.update(special)
    return out


def a_set(set_id, name, band_2="3.0", **rules):
    return {"set_id": set_id, "name": name, "radar_set_id": 1,
            "created_at": datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
            "created_by_name": "someone" if set_id > 1 else None,
            "hail_threshold_in": Decimal("1.00"), "margin_km": Decimal("3.0"),
            "report_radius_km": Decimal("5.0"), "cocorahs_min_in": Decimal("1.00"),
            "band_1_mi": Decimal("1.0"), "band_2_mi": Decimal(band_2), "band_3_mi": Decimal("5.0"),
            "rules": grid(**rules), "in_use": set_id == 7}


SETS = [a_set(7, "Newest", band_2="4.0"), a_set(1, "Initial placeholders")]
SETS[0]["rules"][("margin", "3_5")] = "agree"

FORM = {"hail_threshold_in": "1.25", "margin_km": "3.0", "report_radius_km": "5.0",
        "cocorahs_min_in": "1.00", "band_1_mi": "1.0", "band_2_mi": "3.0", "band_3_mi": "5.0",
        "name": "Wider threshold"}
FORM.update({f"rule_{layer}_{band}": "one_source" for layer in ("core", "margin", "radar_indicated", "outer", "none")
             for band in ("le_1", "1_3", "3_5", "over_5")})


TRIAL = {"day": date(2026, 6, 1), "changed": True,
         "current": {"set_id": 7, "areas": {"agree": 100.0, "one_source": 200.0, "context": 50.0},
                     "iem_reports": 3, "cocorahs_reports": 1, "patches": 4, "patches_anchored": 2},
         "proposed": {"set_id": 8, "areas": {"agree": 150.0, "one_source": 150.0},
                      "iem_reports": 3, "cocorahs_reports": 1, "patches": 3, "patches_anchored": 3}}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class AdminHailZonesTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.fns = {name: stack.enter_context(mock.patch.object(admin_web.likelihoodadmin, name))
                    for name in ("save", "save_storm_states", "try_day")}
        self.fns["save"].return_value = {"set_id": 8, "radar_set_id": 2, "radar_changed": True}
        self.fns["save_storm_states"].return_value = "CO,WY"
        self.fns["try_day"].return_value = TRIAL
        stack.enter_context(mock.patch.object(admin_web.likelihoodadmin, "suggest_day",
                                              return_value=date(2026, 6, 1)))
        self.history = stack.enter_context(mock.patch.object(admin_web.likelihoodadmin, "history",
                                                             return_value=SETS))
        self.unbuilt = stack.enter_context(mock.patch.object(admin_web.likelihoodadmin, "unbuilt_days",
                                                             return_value={"radar": 0, "likelihood": 0}))

    def client_as(self, role):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.users[role]
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def get(self, path="/admin/", role="admin"):
        return self.client_as(role).get(path)

    def post(self, path, role="admin", follow=True, **data):
        self.app.config["WTF_CSRF_ENABLED"] = False
        return self.client_as(role).post(path, data=data, follow_redirects=follow)

    def writes_called(self):
        return [name for name, fn in self.fns.items() if fn.called]

    def selected(self, html, layer, band):
        block = re.search(rf'<select name="rule_{layer}_{band}".*?</select>', html, re.S).group(0)
        return re.search(r'<option value="(\w+)" selected', block).group(1)

    # ---- the section ----

    def test_the_section_shows_the_set_in_use(self):
        html = self.get().get_data(as_text=True)
        self.assertIn('id="hail-zones"', html)
        for key, value in (("hail_threshold_in", "1.00"), ("margin_km", "3.0"), ("report_radius_km", "5.0"),
                           ("cocorahs_min_in", "1.00"), ("band_1_mi", "1.0"), ("band_2_mi", "4.0"),
                           ("band_3_mi", "5.0")):
            with self.subTest(key=key):
                self.assertRegex(html, rf'name="{key}"[^>]*value="{re.escape(value)}"')

    def test_all_twenty_levels_are_shown_with_the_right_one_chosen(self):
        html = self.get().get_data(as_text=True)
        self.assertEqual(len(re.findall(r'<select name="rule_', html)), 20)
        self.assertEqual(self.selected(html, "core", "le_1"), "agree")
        self.assertEqual(self.selected(html, "margin", "3_5"), "agree")
        self.assertEqual(self.selected(html, "outer", "over_5"), "one_source")

    def test_the_name_starts_blank_so_the_admin_has_to_say_why(self):
        html = self.get().get_data(as_text=True)
        self.assertRegex(html, r'name="name"[^>]*value=""')

    def test_the_history_marks_the_set_in_use_and_links_only_the_older_ones(self):
        html = self.get().get_data(as_text=True)
        self.assertIn("7 (in use)", html)
        self.assertEqual(html.count("Use these values again"), 1)
        self.assertIn("use_set=1", html)
        self.assertNotIn("use_set=7", html)
        self.assertIn("migration", html)                    # set 1 has no author

    def test_use_again_fills_the_form_from_the_older_set_and_writes_nothing(self):
        html = self.get("/admin/?use_set=1").get_data(as_text=True)
        self.assertRegex(html, r'name="band_2_mi"[^>]*value="3.0"')
        self.assertEqual(self.selected(html, "margin", "3_5"), "one_source")
        self.assertRegex(html, r'name="name"[^>]*value=""')
        self.assertEqual(self.writes_called(), [])

    def test_an_unknown_use_again_id_falls_back_to_the_set_in_use(self):
        html = self.get("/admin/?use_set=999").get_data(as_text=True)
        self.assertRegex(html, r'name="band_2_mi"[^>]*value="4.0"')

    def test_days_waiting_for_a_rebuild_show_the_commands(self):
        self.unbuilt.return_value = {"radar": 2194, "likelihood": 2195}
        html = self.get().get_data(as_text=True)
        self.assertIn("2194 for the radar layers, 2195 for the hail zones", html)
        self.assertIn("radar_ingest.py rebuild --days 2300", html)
        self.assertIn("likelihood_ingest.py backfill", html)

    def test_only_the_likelihood_command_shows_when_the_radar_is_built(self):
        self.unbuilt.return_value = {"radar": 0, "likelihood": 5}
        html = self.get().get_data(as_text=True)
        self.assertNotIn("radar_ingest.py rebuild", html)
        self.assertIn("likelihood_ingest.py backfill", html)

    def test_nothing_waiting_shows_no_box(self):
        self.assertNotIn("Days waiting for a rebuild", self.get().get_data(as_text=True))

    # ---- who may write ----

    PATHS = ("/admin/hail-zones", "/admin/hail-zones/states", "/admin/hail-zones/try")

    def test_every_write_needs_a_token(self):
        client = self.client_as("admin")
        for path in self.PATHS:
            self.assertEqual(client.post(path, data=FORM).status_code, 400, path)
        self.assertEqual(self.writes_called(), [])

    def test_every_write_is_post_only(self):
        client = self.client_as("admin")
        for path in self.PATHS:
            self.assertEqual(client.get(path).status_code, 405, path)

    def test_signed_out_goes_to_login_and_writes_nothing(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        for path in self.PATHS:
            resp = self.app.test_client().post(path, data=FORM)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(self.writes_called(), [])

    def test_only_admin_may_write(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                for path in self.PATHS:
                    self.assertEqual(self.post(path, role, **FORM).status_code, 403, f"{role} {path}")
        self.assertEqual(self.writes_called(), [])

    def test_only_admin_may_see_the_section(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.get("/admin/", role).status_code, 403, role)

    # ---- what each route calls ----

    def test_saving_passes_the_admin_the_numbers_the_name_and_the_twenty_levels(self):
        resp = self.post("/admin/hail-zones", **FORM)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Saved hail-zone set 8.", resp.get_data(as_text=True))
        _, emp_id, values = self.fns["save"].call_args.args
        self.assertEqual(emp_id, self.users["admin"])
        self.assertEqual(values["hail_threshold_in"], "1.25")
        self.assertEqual(values["band_3_mi"], "5.0")
        self.assertEqual(values["name"], "Wider threshold")
        self.assertEqual(len(values["rules"]), 20)
        self.assertEqual(values["rules"][("core", "le_1")], "one_source")

    def test_a_missing_level_arrives_empty_for_the_module_to_refuse(self):
        data = dict(FORM)
        del data["rule_outer_3_5"]
        self.post("/admin/hail-zones", **data)
        self.assertEqual(self.fns["save"].call_args.args[2]["rules"][("outer", "3_5")], "")

    def test_a_refusal_is_shown_with_what_was_typed_still_in_the_form(self):
        self.fns["save"].side_effect = la.SetAdminError("Margin must be from 0 to 50.")
        data = dict(FORM, margin_km="99", name="My reason", rule_core_le_1="context")
        resp = self.post("/admin/hail-zones", **data)
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Margin must be from 0 to 50.", html)
        self.assertRegex(html, r'name="margin_km"[^>]*value="99"')
        self.assertRegex(html, r'name="name"[^>]*value="My reason"')
        self.assertEqual(self.selected(html, "core", "le_1"), "context")

    def test_a_refusal_saves_nothing_more_than_the_one_call(self):
        self.fns["save"].side_effect = la.SetAdminError("Nothing changed, so nothing was saved.")
        self.post("/admin/hail-zones", **FORM)
        self.assertEqual(self.fns["save"].call_count, 1)

    def test_saving_the_storm_states_passes_the_admin_and_the_text(self):
        resp = self.post("/admin/hail-zones/states", storm_states="wy, co")
        self.assertIn("Storm states set to CO,WY.", resp.get_data(as_text=True))
        self.assertEqual(self.fns["save_storm_states"].call_args.args[1:], (self.users["admin"], "wy, co"))

    def test_a_refused_storm_state_is_a_message(self):
        self.fns["save_storm_states"].side_effect = la.SetAdminError("Storm states must be two letter codes.")
        resp = self.post("/admin/hail-zones/states", storm_states="colorado")
        self.assertIn("Storm states must be two letter codes.", resp.get_data(as_text=True))

    # ---- trying a day ----

    def test_the_page_offers_the_suggested_day_and_a_try_button_before_the_save_button(self):
        html = self.get().get_data(as_text=True)
        self.assertRegex(html, r'name="day" type="date" value="2026-06-01"')
        self.assertIn('formaction="/admin/hail-zones/try"', html)
        self.assertLess(html.index("Try on this day"), html.index("Save as a new set"))
        self.assertNotIn("Trial for", html)                      # nothing tried yet

    def test_trying_a_day_shows_both_sides_and_the_change_and_saves_nothing(self):
        resp = self.post("/admin/hail-zones/try", day="2026-06-01", **FORM)
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Trial for 2026-06-01", html)
        self.assertIn("Set in use (7)", html)
        self.assertIn("100 km²", html)
        self.assertIn("150 km²", html)
        self.assertIn("+50 km²", html)
        self.assertIn("-50 km²", html)                           # one source fell
        self.assertIn("-50 km²", html)
        self.assertIn("Nothing was saved", html)
        self.assertEqual(self.fns["save"].call_count, 0)
        self.assertEqual(self.fns["save_storm_states"].call_count, 0)

    def test_a_level_that_vanished_reads_as_zero_not_an_error(self):
        html = self.post("/admin/hail-zones/try", day="2026-06-01", **FORM).get_data(as_text=True)
        self.assertRegex(html, r"<th>Context</th><td>50 km²</td>\s*<td>0 km²</td><td>-50 km²</td>")

    def test_trying_passes_the_admin_the_form_and_the_day(self):
        self.post("/admin/hail-zones/try", day="2026-06-01", **dict(FORM, name=""))
        _, emp_id, values, day = self.fns["try_day"].call_args.args
        self.assertEqual(emp_id, self.users["admin"])
        self.assertEqual(day, date(2026, 6, 1))
        self.assertEqual(values["hail_threshold_in"], "1.25")
        self.assertEqual(len(values["rules"]), 20)

    def test_the_form_keeps_what_was_typed_after_a_trial(self):
        html = self.post("/admin/hail-zones/try", day="2026-06-01",
                         **dict(FORM, margin_km="4.5")).get_data(as_text=True)
        self.assertRegex(html, r'name="margin_km"[^>]*value="4.5"')
        self.assertRegex(html, r'name="day" type="date" value="2026-06-01"')

    def test_no_day_or_a_bad_day_is_a_message_and_builds_nothing(self):
        for bad in ("", "June 1", "2026-13-40"):
            with self.subTest(day=bad):
                resp = self.post("/admin/hail-zones/try", day=bad, **FORM)
                self.assertEqual(resp.status_code, 400)
                self.assertIn("Pick a day to try it on.", resp.get_data(as_text=True))
        self.assertEqual(self.fns["try_day"].call_count, 0)

    def test_a_refused_trial_is_a_message_with_the_form_kept_and_the_day_kept(self):
        self.fns["try_day"].side_effect = la.SetAdminError("1999-01-01 has no radar file loaded.")
        resp = self.post("/admin/hail-zones/try", day="1999-01-01", **dict(FORM, name="My reason"))
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("has no radar file loaded.", html)
        self.assertRegex(html, r'name="name"[^>]*value="My reason"')
        self.assertRegex(html, r'name="day" type="date" value="1999-01-01"')
        self.assertNotIn("Trial for", html)

    def test_an_unchanged_trial_says_both_columns_are_the_same(self):
        self.fns["try_day"].return_value = dict(TRIAL, changed=False)
        html = self.post("/admin/hail-zones/try", day="2026-06-01", **FORM).get_data(as_text=True)
        self.assertIn("so both columns are the same", html)


if __name__ == "__main__":
    unittest.main()
