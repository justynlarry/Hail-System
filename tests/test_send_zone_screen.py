"""Tests for the hail-zones mode of the send page, first half (Phase 5c step 4, piece 4b-3):  hailsys/web/sendzone.py,
zonesend.current_set and fetch_zone_send_days, send_zone.html and the mode switch.

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_send_zone_screen

The day-list tests run in a rolled-back transaction on made-up houses.  The page tests read the real database (the
page only reads) and replace the list builder where they need a known list.

What they pin:  the day list counts zone matches by level for one set and leaves out report matches, other sets and
old days;  the page is admin only, shows the switch on both modes, the set in use, Agree ticked and One
source not, and never a Context box;  a forged or empty level choice builds nothing and says why;  the list is built
for the set in use and the levels ticked;  only zone templates are offered and an unknown one falls back to the
built-in;  the sample is a zone email;  the counts by level and basis and the held-back observer matches are shown;
the page writes nothing and does not import the send engine.
"""

import contextlib
import os
import re
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock

import psycopg
from psycopg.rows import dict_row

from hailsys.email import templatestore, zonewording
from hailsys.queries import zonesend
from hailsys.tuning import DISPLAY_TZ
from hailsys.web import sendzone

NOW = datetime.now(timezone.utc)
WRITTEN_TABLES = ("sent_emails", "send_log", "email_templates", "settings_history", "cc_sync_runs")


class PureTest(unittest.TestCase):
    def test_levels_come_back_in_order(self):
        self.assertEqual(sendzone.parse_levels(["one_source", "agree"]), (["agree", "one_source"], None))
        self.assertEqual(sendzone.parse_levels(["one_source"]), (["one_source"], None))

    def test_context_or_anything_unknown_is_refused_not_dropped(self):
        for values in (["context"], ["agree", "context"], ["none"], ["Agree"]):
            with self.subTest(values=values):
                levels, problem = sendzone.parse_levels(values)
                self.assertEqual(levels, [])
                self.assertIn("Only Agree and One source", problem)

    def test_no_level_is_a_problem(self):
        self.assertEqual(sendzone.parse_levels([]), ([], "Tick at least one level."))

    def test_counts_by_level_and_basis(self):
        found = {"realtors": [
            {"events": [{"matches": [{"level": "agree", "basis": "radar"}, {"level": "agree", "basis": "both"}]}]},
            {"events": [{"matches": [{"level": "one_source", "basis": "radar"}]}]}]}
        self.assertEqual(sendzone.counts(found), {"level": {"agree": 2, "one_source": 1},
                                                  "basis": {"radar": 2, "both": 1}})
        self.assertEqual(sendzone.counts(None), {"level": {}, "basis": {}})

    def test_the_page_module_cannot_send(self):
        for name in ("deliver", "campaigns", "send", "start_send"):
            self.assertFalse(hasattr(sendzone, name), name)


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class DayListTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('storm_listing_matches', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs hail_admin")
        self.set_id = zonesend.current_set(self.conn)["set_id"]
        self.today = NOW.astimezone(DISPLAY_TZ).date()
        self.day = date(1999, 1, 1)         # far from any real match, so the counts are only ours
        self.n = 0

    def match(self, *, level="agree", day=None, set_id=None, report=False):
        self.n += 1
        rid = f"ZDL-{uuid.uuid4().hex[:10]}"
        self.conn.execute("INSERT INTO properties (rentcast_id, property_address) VALUES (%s, %s)",
                          (rid, f"{self.n} Day St, Calhan, CO 80808"))
        listing = self.conn.execute("INSERT INTO listings (rentcast_id, list_status, list_date) VALUES (%s, 'Active', now()) "
                                    "RETURNING listing_id", (rid,)).fetchone()["listing_id"]
        if report:
            iem = self.conn.execute("SELECT iem_id FROM iem_data WHERE report_text = 'HAIL' LIMIT 1").fetchone()["iem_id"]
            self.conn.execute("INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, storm_date) "
                              "VALUES (%s, %s, 1, 5, %s)", (iem, listing, day or self.day))
        else:
            self.conn.execute("INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
                              "VALUES (%s, %s, 'radar', %s, 'core', %s)", (listing, day or self.day, level, set_id or self.set_id))

    def days(self, max_age_days=None):
        age = max_age_days or (self.today - self.day).days + 1
        return {r["day"]: (r["agree"], r["one_source"])
                for r in zonesend.fetch_zone_send_days(self.conn, today=self.today, set_id=self.set_id,
                                                       max_age_days=age)
                if r["day"] <= self.day + timedelta(days=1)}

    def test_the_current_set_is_the_newest_with_its_radar_numbers(self):
        got = zonesend.current_set(self.conn)
        newest = self.conn.execute("SELECT max(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        self.assertEqual(got["set_id"], newest)
        self.assertEqual(set(got), {"set_id", "name", "hail_in", "radius_km"})

    def test_matches_are_counted_by_level_and_day(self):
        self.match(level="agree")
        self.match(level="agree")
        self.match(level="one_source")
        self.match(level="one_source", day=self.day + timedelta(days=1))
        self.assertEqual(self.days(), {self.day: (2, 1), self.day + timedelta(days=1): (0, 1)})

    def test_report_matches_and_other_sets_are_not_counted(self):
        # A Context match cannot exist:  sql/057 refuses any zone level but agree and one_source.
        self.match(level="agree")
        self.match(report=True)
        other = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "SELECT 'day list test', radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi "
            "FROM likelihood_parameter_sets WHERE set_id = %s RETURNING set_id", (self.set_id,)).fetchone()["set_id"]
        self.match(level="agree", set_id=other)
        self.assertEqual(self.days(), {self.day: (1, 0)})

    def test_a_day_older_than_the_age_limit_is_left_out(self):
        self.match()
        self.assertEqual(self.days(max_age_days=(self.today - self.day).days - 1), {})
        self.assertEqual(self.days(max_age_days=(self.today - self.day).days), {self.day: (1, 0)})

    def test_newest_day_first(self):
        self.match(day=self.day)
        self.match(day=self.day + timedelta(days=1))
        rows = [r["day"] for r in zonesend.fetch_zone_send_days(
            self.conn, today=self.today, set_id=self.set_id, max_age_days=(self.today - self.day).days)
            if r["day"] <= self.day + timedelta(days=1)]
        self.assertEqual(rows, [self.day + timedelta(days=1), self.day])


def a_found(set_row, **excluded):
    reasons = {r: {"matches": 0, "realtors": 0} for r in (*zonesend.sendlist.REASONS, zonesend.BLOCKED)}
    for key, n in excluded.items():
        reasons[key] = {"matches": n, "realtors": n}
    events = [{"listing_id": 1, "address": "12 Proof Rd, Calhan, CO 80808",
               "utc_datetime": datetime(2026, 9, 29, 18, tzinfo=timezone.utc), "bases": ["radar"],
               "match_ids": [11], "matches": [{"match_id": 11, "basis": "radar", "level": "agree", "set_id": 1}]},
              {"listing_id": 2, "address": "14 Proof Rd, Calhan, CO 80808",
               "utc_datetime": datetime(2026, 9, 29, 18, tzinfo=timezone.utc), "bases": ["both"],
               "match_ids": [12], "matches": [{"match_id": 12, "basis": "both", "level": "one_source", "set_id": 1}]}]
    return {"realtors": [{"realtor_id": 5, "email": "pat@example.test", "agent_name": "Pat Jones", "events": events}],
            "considered": {"matches": 2 + sum(excluded.values()), "realtors": 1}, "excluded": reasons,
            "set": {"set_id": set_row["set_id"], "name": set_row["name"],
                    "hail_in": set_row["hail_in"], "radius_km": set_row["radius_km"]},
            "levels": ["agree", "one_source"]}


@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class ZonePageTest(unittest.TestCase):
    def setUp(self):
        from hailsys.web import create_app
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        self.current = zonesend.current_set(self.conn)
        self.conn.rollback()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.stack = stack

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(emp_id=self.users[role], role=role, user_name="test",
                     issued_at=datetime.now(timezone.utc).isoformat())
        return client

    def page(self, query="", role="admin"):
        return self.client_as(role).get("/send/?mode=zones" + query)

    def html(self, query=""):
        resp = self.page(query)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def builder(self, found=None):
        return self.stack.enter_context(mock.patch.object(
            sendzone.zonesend, "build_zone_send_list", return_value=found or a_found(self.current)))

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in WRITTEN_TABLES}

    # ---- who, and the switch ----

    def test_only_an_admin_may_open_it(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                with self.subTest(role=role):
                    self.assertNotEqual(self.page(role=role).status_code, 200)

    def test_both_modes_show_the_switch_and_link_to_each_other(self):
        report = self.client_as().get("/send/").get_data(as_text=True)
        self.assertIn("Send from:", report)
        self.assertIn('href="/send/?mode=zones"', report)
        self.assertIn("<strong>NWS reports</strong>", report)
        zones = self.html()
        self.assertIn("<strong>Hail zones</strong>", zones)
        self.assertIn('href="/send/"', zones)

    def test_any_other_mode_is_the_report_page(self):
        html = self.client_as().get("/send/?mode=nonsense").get_data(as_text=True)
        self.assertIn("<strong>NWS reports</strong>", html)

    # ---- the form ----

    def test_the_set_in_use_is_shown(self):
        self.assertIn(f"Hail-zone set in use: <strong>{self.current['set_id']}</strong>", self.html())

    def test_agree_is_ticked_one_source_is_not_and_there_is_no_context_box(self):
        html = self.html()
        self.assertRegex(html, r'name="level" value="agree" checked')
        self.assertRegex(html, r'name="level" value="one_source" >')
        self.assertNotIn('value="context"', html)
        self.assertIn('name="levels_shown" value="1"', html)
        self.assertIn('name="mode" value="zones"', html)

    def test_the_ticks_sent_are_kept(self):
        html = self.html("&levels_shown=1&level=one_source")
        self.assertRegex(html, r'name="level" value="agree" >')
        self.assertRegex(html, r'name="level" value="one_source" checked')

    def test_a_forged_level_builds_nothing_and_says_why(self):
        build = self.builder()
        html = self.html("&levels_shown=1&level=context&day=2026-09-29")
        self.assertIn("Only Agree and One source can be sent.", html)
        build.assert_not_called()
        self.assertNotIn("would be queued", html)

    def test_no_level_builds_nothing_and_says_why(self):
        build = self.builder()
        html = self.html("&levels_shown=1&day=2026-09-29")
        self.assertIn("Tick at least one level.", html)
        build.assert_not_called()

    def test_a_bad_day_is_a_400(self):
        self.assertEqual(self.page("&day=2026-13-01").status_code, 400)

    def test_no_day_builds_nothing(self):
        build = self.builder()
        self.assertNotIn("would be queued", self.html())
        build.assert_not_called()

    def test_the_list_is_built_for_the_set_in_use_and_the_ticked_levels(self):
        build = self.builder()
        self.html("&levels_shown=1&level=one_source&level=agree&day=2026-09-29&day=2026-09-28")
        args, kw = build.call_args
        self.assertEqual(args[1], [date(2026, 9, 28), date(2026, 9, 29)])
        self.assertEqual((kw["set_id"], kw["levels"]), (self.current["set_id"], ["agree", "one_source"]))
        settings = self.conn.execute("SELECT email_cap_days, match_max_age_days FROM settings").fetchone()
        self.conn.rollback()
        self.assertEqual((kw["cap_days"], kw["max_age_days"]),
                         (settings["email_cap_days"], settings["match_max_age_days"]))

    # ---- templates ----

    def test_only_zone_templates_are_offered(self):
        kinds = []
        real = sendzone.templatelist.fetch_all

        def spy(conn, kind=None):
            kinds.append(kind)
            return real(conn, kind=kind)

        self.stack.enter_context(mock.patch.object(sendzone.templatelist, "fetch_all", spy))
        html = self.html()
        self.assertEqual(kinds, ["zone"])
        self.assertIn(f'value="{templatestore.ZONE_TEMPLATE_NAME}"', html)
        self.assertNotIn(f'value="{templatestore.TEMPLATE_NAME}"', html)

    def test_an_unknown_or_report_template_falls_back_to_the_built_in(self):
        for name in ("nope-" + uuid.uuid4().hex[:6], templatestore.TEMPLATE_NAME):
            with self.subTest(name=name):
                html = self.html(f"&template={name}")
                self.assertRegex(html, rf'value="{templatestore.ZONE_TEMPLATE_NAME}" id="tpl-1"\s+checked')

    # ---- the preview ----

    def test_the_preview_counts_by_level_and_basis(self):
        self.builder()
        html = self.html("&day=2026-09-29")
        self.assertIn("would be queued", html)
        self.assertRegex(html, r"Level: Agree</td><td>1<")
        self.assertRegex(html, r"Level: One source</td><td>1<")
        self.assertRegex(html, r"Basis: Radar</td><td>1<")
        self.assertRegex(html, r"Basis: Radar and observer</td><td>1<")

    def test_held_back_observer_matches_are_explained_with_a_link(self):
        self.builder(a_found(self.current, observer_wording_not_approved=3))
        html = self.html("&day=2026-09-29")
        self.assertIn(sendzone.BLOCKED_LABEL, html)
        self.assertRegex(html, r"3\s+matches\s+rest on a volunteer observer report")
        self.assertIn('href="/admin/#observer-wording"', html)

    def test_every_reason_has_a_row(self):
        self.builder()
        html = self.html("&day=2026-09-29")
        from hailsys.web import send as screen
        for label in [*screen.REASON_LABELS.values(), sendzone.BLOCKED_LABEL]:
            self.assertIn(f"<td>{label}</td>", html.replace("&#39;", "'"))

    def test_the_sample_is_a_zone_email_from_the_first_realtor(self):
        self.builder()
        html = self.html("&day=2026-09-29")
        self.assertIn("Sample (the first realtor on the list)", html)
        radar = zonewording.radar_sentence(self.current["hail_in"], self.current["radius_km"])
        self.assertIn(radar, html)
        self.assertIn("12 Proof Rd", html)

    def test_the_preview_counts_the_listings(self):
        self.builder()
        self.assertRegex(self.html("&day=2026-09-29"), r"covering <strong>2</strong>\s+listings")

    def test_the_sample_is_checked_by_the_zone_wording_rules(self):
        subject, body = templatestore.load_files(templatestore.ZONE_TEMPLATE_NAME)
        got = sendzone.sample(None, self.current, subject, body + "<p>Your roof was damaged.</p>")
        self.assertIsNone(got["html"])
        self.assertIn("wording rules", got["problem"])

    def test_with_nothing_previewed_the_sample_is_stand_in(self):
        self.assertIn("Sample (stand-in data)", self.html())

    def test_the_page_writes_nothing(self):
        before = self.counts()
        self.html("&levels_shown=1&level=agree&level=one_source&day=2026-09-29")
        self.conn.rollback()
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class ZoneConfirmStartTest(unittest.TestCase):
    """The second half:  the review form, /send/zones/confirm and /send/zones/start.  The list builder and
    start_send are replaced, so nothing is sent and nothing is written."""

    def setUp(self):
        from hailsys.web import create_app
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        self.current = zonesend.current_set(self.conn)
        self.settings = self.conn.execute(
            "SELECT match_max_age_days, email_cap_days, listing_freshness_days FROM settings").fetchone()
        self.conn.rollback()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.build = stack.enter_context(mock.patch.object(
            sendzone.zonesend, "build_zone_send_list", return_value=a_found(self.current)))
        self.start = stack.enter_context(mock.patch.object(sendzone.sendjobs, "start_send"))
        stack.enter_context(mock.patch.object(sendzone.sendjobs, "is_busy", return_value=False))
        self.form = {"day": "2026-09-29", "level": ["agree", "one_source"], "set_id": str(self.current["set_id"]),
                     "template": templatestore.ZONE_TEMPLATE_NAME, "allowed": "pat@example.test", "max_emails": "5"}

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(emp_id=self.users[role], role=role, user_name="test",
                     issued_at=datetime.now(timezone.utc).isoformat())
        return client

    def post(self, path, role="admin", follow=True, **over):
        data = dict(self.form, **over)
        for key in [k for k, v in data.items() if v is None]:
            del data[key]
        return self.client_as(role).post(path, data=data, follow_redirects=follow)

    def confirm(self, **over):
        return self.post("/send/zones/confirm", **over)

    def start_(self, **over):
        over.setdefault("expected", "1")
        return self.post("/send/zones/start", **over)

    def text(self, resp):
        return re.sub(r"\s+", " ", resp.get_data(as_text=True))

    def refused(self, path, **over):
        """The page's message for a refused POST, after checking that the POST itself built no list and
        started nothing (following the redirect shows the preview, which builds its own list)."""
        data = {k: v for k, v in dict(self.form, **over).items() if v is not None}
        client = self.client_as()
        self.build.reset_mock()
        resp = client.post(path, data=data)
        self.assertEqual(resp.status_code, 302)
        self.build.assert_not_called()
        self.start.assert_not_called()
        return self.text(client.get(resp.headers["Location"]))

    # ---- the review form on the preview ----

    def test_the_review_form_carries_the_set_the_levels_and_no_cap_or_follow_up(self):
        html = self.client_as().get("/send/?mode=zones&levels_shown=1&level=agree&level=one_source"
                                    "&day=2026-09-29").get_data(as_text=True)
        form = html[html.index('action="/send/zones/confirm"'):]
        form = form[:form.index("</form>")]
        self.assertIn(f'name="set_id" value="{self.current["set_id"]}"', form)
        self.assertIn('name="level" value="agree"', form)
        self.assertIn('name="level" value="one_source"', form)
        self.assertIn('name="day" value="2026-09-29"', form)
        self.assertIn('name="csrf_token"', form)
        for absent in ("override_cap", "campaign", "gap_"):
            self.assertNotIn(absent, form)

    def test_no_review_form_without_anyone_on_the_list(self):
        self.build.return_value = dict(a_found(self.current), realtors=[])
        html = self.client_as().get("/send/?mode=zones&day=2026-09-29").get_data(as_text=True)
        self.assertNotIn("/send/zones/confirm", html)

    # ---- confirm ----

    def test_only_an_admin_may_confirm_or_start(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                with self.subTest(role=role):
                    self.assertNotEqual(self.confirm(role=role, follow=False).status_code, 200)
                    self.start_(role=role, follow=False)
        self.build.assert_not_called()
        self.start.assert_not_called()

    def test_the_confirm_page_shows_the_send(self):
        resp = self.confirm()
        text = self.text(resp)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<strong>1</strong> email ready for 1 storm day, from the hail zones", text)
        self.assertIn("about 10 Constant Contact calls", text)
        self.assertIn(f"Hail-zone set: <strong>{self.current['set_id']}</strong>", text)
        self.assertIn("Levels: <strong>Agree, One source</strong>", text)
        self.assertIn('action="/send/zones/start"', text)
        self.assertIn('name="expected" value="1"', text)
        self.assertIn(f'name="set_id" value="{self.current["set_id"]}"', text)
        _, kw = self.build.call_args
        self.assertEqual((kw["set_id"], kw["levels"]), (self.current["set_id"], ["agree", "one_source"]))
        self.assertEqual((kw["max_age_days"], kw["cap_days"], kw["freshness_days"]),
                         (self.settings["match_max_age_days"], self.settings["email_cap_days"],
                          self.settings["listing_freshness_days"]))
        start_form = text[text.index('action="/send/zones/start"'):]
        start_form = start_form[:start_form.index("</form>")]
        for field in ('name="level" value="agree"', 'name="level" value="one_source"', 'name="day" value="2026-09-29"',
                      f'name="template" value="{templatestore.ZONE_TEMPLATE_NAME}"', 'name="max_emails" value="5"',
                      'name="allowed" value="pat@example.test"'):
            self.assertIn(field, start_form)
        self.start.assert_not_called()

    def test_the_expected_count_is_the_whole_list_not_the_number_going_now(self):
        two = a_found(self.current)
        other = dict(two["realtors"][0], realtor_id=6, email="sam@example.test")
        two["realtors"] = [two["realtors"][0], other]
        self.build.return_value = two
        text = self.text(self.confirm(allowed="pat@example.test sam@example.test", max_emails="1"))
        self.assertIn("This send delivers at most <strong>1</strong> now", text)
        self.assertIn('name="expected" value="2"', text)

    def test_each_bad_form_is_refused_with_its_reason(self):
        for over, words in (({"day": None}, "Tick at least one storm day."),
                            ({"level": None}, "Tick at least one level."),
                            ({"level": "context"}, "Only Agree and One source can be sent."),
                            ({"allowed": ""}, "There is no send-to-everyone option."),
                            ({"allowed": "nonsense"}, "These are not email addresses: nonsense"),
                            ({"max_emails": "0"}, "must be between 1 and"),
                            ({"max_emails": "1001"}, "must be between 1 and"),
                            ({"max_emails": "x"}, "must be between 1 and")):
            with self.subTest(over=over):
                self.build.reset_mock()
                text = self.refused("/send/zones/confirm", **over)
                self.assertIn(words.replace("'", "&#39;"), text.replace("'", "&#39;"))

    def test_a_missing_or_bad_set_id_is_a_400(self):
        for value in (None, "", "one"):
            with self.subTest(value=value):
                self.assertEqual(self.confirm(set_id=value, follow=False).status_code, 400)

    def test_a_newer_set_is_refused_and_nothing_is_built(self):
        text = self.refused("/send/zones/confirm", set_id=str(self.current["set_id"] - 1 or 999))
        self.assertIn("The hail-zone set changed since the preview", text)
        self.assertIn("Nothing was sent. Preview again.", text)

    def test_a_report_or_unknown_template_is_refused(self):
        for name in (templatestore.TEMPLATE_NAME, "nope-" + uuid.uuid4().hex[:6]):
            with self.subTest(name=name):
                self.assertIn("That template is not a zone template.",
                              self.refused("/send/zones/confirm", template=name))

    def test_an_address_not_on_the_list_sends_nothing(self):
        text = self.text(self.confirm(allowed="someone@else.test"))
        self.assertIn("None of those addresses is on the zone send list", text)

    def test_unmatched_addresses_are_named(self):
        text = self.text(self.confirm(allowed="pat@example.test, someone@else.test"))
        self.assertIn("they will be ignored: someone@else.test", text)

    def test_a_refusal_goes_back_to_the_zone_preview_with_the_choices(self):
        resp = self.confirm(allowed="", follow=False)
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers["Location"]
        for part in ("mode=zones", "levels_shown=1", "day=2026-09-29", "level=agree", "level=one_source",
                     f"template={templatestore.ZONE_TEMPLATE_NAME}"):
            self.assertIn(part, loc)

    # ---- start ----

    def test_start_hands_the_pinned_zone_to_the_send_job_and_nothing_else(self):
        resp = self.start_(follow=False)
        self.assertEqual((resp.status_code, resp.headers["Location"]), (302, "/send/batches"))
        kw = self.start.call_args.kwargs
        self.assertEqual(kw["zone"], {"set_id": self.current["set_id"], "levels": ["agree", "one_source"]})
        self.assertEqual(kw["template_name"], templatestore.ZONE_TEMPLATE_NAME)
        self.assertEqual((kw["allowed_emails"], kw["max_emails"], kw["storm_days"]),
                         (["pat@example.test"], 5, [date(2026, 9, 29)]))
        self.assertEqual(kw["user_id"], self.users["admin"])
        self.assertEqual((kw["max_age_days"], kw["cap_days"], kw["freshness_days"]),
                         (self.settings["match_max_age_days"], self.settings["email_cap_days"],
                          self.settings["listing_freshness_days"]))
        for absent in ("campaign", "override_cap"):
            self.assertNotIn(absent, kw)

    def test_a_changed_count_is_refused(self):
        text = self.text(self.start_(expected="2"))
        self.assertIn("The list changed since you reviewed it (2 to 1 emails). Nothing was sent.", text)
        self.start.assert_not_called()

    def test_a_newer_set_at_start_is_refused(self):
        self.assertIn("The hail-zone set changed since the preview",
                      self.refused("/send/zones/start", expected="1", set_id=str(self.current["set_id"] + 1)))

    def test_a_bad_form_at_start_is_refused(self):
        self.assertIn("There is no send-to-everyone option.",
                      self.refused("/send/zones/start", expected="1", allowed=""))
        self.assertIn("Only Agree and One source",
                      self.refused("/send/zones/start", expected="1", level="context"))

    def test_a_missing_expected_count_is_a_400(self):
        self.assertEqual(self.start_(expected=None, follow=False).status_code, 400)
        self.start.assert_not_called()

    def test_a_running_send_is_a_message_not_a_second_send(self):
        self.start.side_effect = sendzone.sendjobs.SendInProgress("busy")
        self.assertIn("A send is already running.", self.text(self.start_()))

    def test_the_start_without_a_csrf_token_is_refused(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.start_(follow=False).status_code, 400)
        self.start.assert_not_called()

    def test_confirm_and_start_write_nothing(self):
        before = {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in WRITTEN_TABLES}
        self.conn.rollback()
        self.confirm()
        self.start_()
        after = {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in WRITTEN_TABLES}
        self.conn.rollback()
        self.assertEqual(after, before)
