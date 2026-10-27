"""Tests for /pull/zones, the read-only zones page (hailsys/web/views.py zone_estimate, pull_zones.html; step 2a).

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_zone_estimate_web

The Pull button (POST /pull/zones/start, the step 2b route that spends RentCast requests) is tested here with start_pull replaced:
the zip list always comes from the database and never from the form, every refusal happens before start_pull, and the page shows
the button only for a built day inside the age limit with zips to pull.
The match button (POST /pull/zones/match) is tested here too, with zonematch.match_zones replaced:  it passes the admin, the day,
today and the storm age limit;  shows what was written or the refusal;  needs a token and a sender or admin;  and the
zones page only enables it for a built day inside the age limit.

The zone reads and the two estimates are replaced, so the page is predictable and nothing touches the pull history.
What they pin:  the numbers for today's pull and for the zones side by side, a row per zip with its basis, level, overlap
and share (tiny overlaps shown as "<0.1" and "<1%"), the Context column only when asked for, the not-built message with
the rebuild command, and 400 for a missing or bad date;  who may see it (signed out goes to login, a viewer is refused);
that it is GET only, starts no pull and writes nothing;  and that the estimate page links to it for hail days only.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app, views as views_web, jobs as jobs_web
    from hailsys.matching import zonematch
    from hailsys.tuning import DISPLAY_TZ
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DAY = date(2026, 9, 22)


def row(zcta5, name, basis, level, agree=0.0, one=0.0, ctx=0.0, share=0.0):
    return {"zcta5": zcta5, "area_name": name, "basis": basis, "best_level": level, "zip_km2": 100.0,
            "agree_km2": agree, "one_source_km2": one, "context_km2": ctx, "share": share}


ROWS = [row("80901", "COLORADO SPRINGS", "both", "agree", agree=40.0, share=0.4),
        row("80817", "FOUNTAIN", "both", "one_source", one=0.01, share=0.0001),
        row("80106", "ELBERT", "zones", "one_source", one=68.9, share=0.15),
        row("80999", "NOWHERE", "reports", None)]
VIEW = {"day": DAY, "set": {"set_id": 7, "name": "Newest"}, "built": True, "include_context": False,
        "rows": ROWS, "zips": ["80106", "80817", "80901", "80999"],
        "counts": {"union": 4, "zones": 3, "reports": 3, "both": 2, "zones_only": 1, "reports_only": 1,
                   "agree": 1, "one_source": 2, "context": 0}}
TODAY = {"zip_count": 3, "estimated_api_calls": 3, "zips": ["80817", "80901", "80999"],
         "pulled_recently": [], "not_pulled_recently": ["80817", "80901", "80999"]}
UNION = {"zip_count": 4, "estimated_api_calls": 5, "zips": VIEW["zips"], "pulled_recently": [],
         "not_pulled_recently": VIEW["zips"]}
MATCHED = {"day": DAY, "set_id": 7, "built_at": datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc), "in_zones": 20,
           "has_report": 5, "outside_crop": 0, "no_evidence": 3, "already": 2, "written": 10,
           "by_basis": {"radar": 6, "both": 4}, "by_level": {"agree": 4, "one_source": 6}}
USAGE = {"used": 10, "quota": 1000, "remaining": 990, "over": 0, "period_start": date(2026, 10, 1),
         "period_end": date(2026, 11, 1)}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class ZoneEstimateWebTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib_stack(self)
        self.zone_zips = stack.enter_context(mock.patch.object(views_web.zonepull, "zone_zips", return_value=VIEW))
        self.estimate_pull = stack.enter_context(mock.patch.object(views_web, "estimate_pull", return_value=TODAY))
        self.estimate_zips = stack.enter_context(mock.patch.object(views_web, "estimate_zips", return_value=UNION))
        stack.enter_context(mock.patch.object(views_web.quota, "fetch_usage", return_value=USAGE))
        self.start_pull = stack.enter_context(mock.patch.object(views_web, "start_pull"))
        self.recent_pulls = stack.enter_context(mock.patch.object(views_web.zonepull, "recent_pulls", return_value=[]))
        self.zone_rows = stack.enter_context(mock.patch.object(views_web.zonepull, "zone_row_counts", return_value=[]))
        self.match_zones = stack.enter_context(mock.patch.object(views_web.zonematch, "match_zones", return_value=MATCHED))
        self.max_age = self.conn.execute("SELECT match_max_age_days AS n FROM settings").fetchone()["n"]
        self.today = datetime.now(DISPLAY_TZ).date()

    def client_as(self, role):
        # The role is read from the session cookie, so a database with no viewer or sender can still be tested as one:
        # sign in as the admin's account with the other role.  The real roles are checked in the same way on every route.
        emp = self.users.get(role) or self.users.get("admin")
        if emp is None:
            self.skipTest("no active admin user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = emp
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def page(self, query="date=2026-09-22", role="admin"):
        return self.client_as(role).get(f"/pull/zones?{query}")

    def text(self, query="date=2026-09-22"):
        return self.page(query).get_data(as_text=True)

    # ---- the page ----

    def test_it_shows_todays_pull_and_the_zones_side_by_side(self):
        html = self.text()
        self.assertRegex(html, r"Today's pull from storm reports:\s*<strong>3</strong>\s*zips, about\s*<strong>3</strong>")
        self.assertRegex(html, r"With the hail zones:\s*<strong>4</strong>\s*zips,\s*about\s*<strong>5</strong>")
        self.assertIn("1 added by the zones, 1 from reports only", html)
        self.assertIn("10</strong> of 1000 requests used", html)

    def test_each_zip_has_a_row_with_its_basis_level_overlap_and_share(self):
        html = self.text()
        for zcta5, name, basis, level in (("80901", "COLORADO SPRINGS", "Zones and reports", "Agree"),
                                          ("80106", "ELBERT", "Zones only", "One source"),
                                          ("80999", "NOWHERE", "Reports only", "–")):
            with self.subTest(zip=zcta5):
                self.assertRegex(html, rf"<td>{zcta5}</td>\s*<td>{name}</td>\s*<td>{basis}</td>\s*<td>{level}</td>")
        self.assertRegex(html, r"<td>40\.0</td>\s*<td>–</td>\s*<td>40%</td>")
        self.assertRegex(html, r"<td>–</td>\s*<td>68\.9</td>\s*<td>15%</td>")

    def test_a_tiny_overlap_reads_as_less_than_not_as_zero(self):
        html = self.text()
        self.assertIn("&lt;0.1", html)
        self.assertIn("&lt;1%", html)

    def test_the_context_column_shows_only_when_asked_for(self):
        self.assertNotIn("Context km²", self.text())
        self.zone_zips.return_value = dict(VIEW, include_context=True)
        html = self.text("date=2026-09-22&context=1")
        self.assertIn("Context km²", html)
        self.assertIn("(Context included)", html)
        self.assertRegex(html, r'name="context" value="1" checked')
        self.assertTrue(self.zone_zips.call_args.kwargs["include_context"])

    def test_context_is_off_by_default(self):
        self.text()
        self.assertFalse(self.zone_zips.call_args.kwargs["include_context"])

    def test_a_day_not_built_says_so_and_names_the_rebuild_command_and_lists_nothing(self):
        self.zone_zips.return_value = dict(VIEW, built=False, rows=[], zips=[], counts={})
        html = self.text()
        self.assertIn("has not been built", html)
        self.assertIn("likelihood_ingest.py backfill", html)
        self.assertIn("set 7, Newest", html)
        self.assertNotIn("<table>", html)
        self.assertEqual(self.estimate_zips.call_count, 0)

    def test_no_parameter_set_at_all_is_a_message_not_an_error(self):
        self.zone_zips.return_value = dict(VIEW, built=False, set=None, rows=[], zips=[], counts={})
        self.assertIn("There is no hail-zone parameter set.", self.text())

    def test_no_rows_is_a_message(self):
        self.zone_zips.return_value = dict(VIEW, rows=[], zips=[], counts=dict(VIEW["counts"], union=0))
        self.assertIn("No coverage zips overlap the zones", self.text())

    def test_it_asks_for_hail_only_over_the_storm_day_with_the_zip_radius_setting(self):
        self.text()
        kw = self.zone_zips.call_args.kwargs
        self.assertEqual((kw["day"], kw["report_text"], kw["actionable_only"]), (DAY, "HAIL", True))
        self.assertEqual(self.estimate_pull.call_args.kwargs["report_text"], "HAIL")
        self.assertEqual(self.estimate_zips.call_args.args[1], VIEW["zips"])

    def test_a_missing_or_bad_date_is_a_400(self):
        for query in ("", "date=", "date=notadate", "date=2026-13-40"):
            with self.subTest(query=query):
                self.assertEqual(self.page(query).status_code, 400)

    def test_the_page_says_it_pulls_nothing_and_links_back_to_the_estimate_page(self):
        html = self.text()
        self.assertIn("Looking at this page pulls nothing", html)
        self.assertIn("/pull/estimate?date=2026-09-22", html)

    # ---- who, and what it cannot do ----

    def test_signed_out_goes_to_login(self):
        resp = self.app.test_client().get("/pull/zones?date=2026-09-22")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(self.zone_zips.call_count, 0)

    def test_a_viewer_is_refused(self):
        self.assertEqual(self.page(role="viewer").status_code, 403)
        self.assertEqual(self.zone_zips.call_count, 0)

    def test_a_sender_may_see_it(self):
        self.assertEqual(self.page(role="sender").status_code, 200)

    def test_it_is_get_only(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        for method in ("post", "put", "delete"):
            with self.subTest(method=method):
                resp = getattr(self.client_as("admin"), method)("/pull/zones?date=2026-09-22")
                self.assertEqual(resp.status_code, 405)

    def test_viewing_it_starts_no_pull(self):
        self.text()
        self.text("date=2026-09-22&context=1")
        self.assertEqual(self.start_pull.call_count, 0)

    # ---- the link from the estimate page ----

    def estimate_page(self, type_):
        with mock.patch.object(views_web, "estimate_pull", return_value=TODAY):
            return self.client_as("admin").get(
                f"/pull/estimate?date=2026-09-22&type={type_}&submitted=1&actionable=1").get_data(as_text=True)

    def test_the_estimate_page_links_to_the_zones_for_hail(self):
        html = self.estimate_page("HAIL")
        self.assertIn("Compare with the hail zones", html)
        self.assertIn("/pull/zones?date=2026-09-22", html)

    def test_the_estimate_page_has_no_link_for_other_types(self):
        self.assertNotIn("Compare with the hail zones", self.estimate_page("TSTM%20WND%20GST"))

    def test_the_estimate_pages_own_numbers_and_pull_button_are_unchanged(self):
        html = self.estimate_page("HAIL")
        self.assertIn("<strong>3</strong> zips", html)
        self.assertIn('action="/pull"', html)
        self.assertIn('name="zip_count" value="3"', html)

    # ---- the match button ----

    def button(self, html):
        import re
        m = re.search(r'<button type="submit" ?(disabled)?>Match listings in the zones', html)
        return None if m is None else ("disabled" if m.group(1) else "enabled")

    def test_the_button_is_enabled_for_a_built_day_inside_the_age_limit(self):
        for age in (0, 1, self.max_age):
            with self.subTest(age=age):
                html = self.text(f"date={(self.today - timedelta(days=age)).isoformat()}")
                self.assertEqual(self.button(html), "enabled")
                self.assertIn("Nothing is sent or pulled", " ".join(html.split()))

    def test_the_button_is_disabled_past_the_limit_and_says_why(self):
        html = self.text(f"date={(self.today - timedelta(days=self.max_age + 1)).isoformat()}")
        self.assertEqual(self.button(html), "disabled")
        self.assertIn(f"Only days within {self.max_age} days", html)

    def test_the_button_is_disabled_for_a_day_in_the_future(self):
        self.assertEqual(self.button(self.text(f"date={(self.today + timedelta(days=1)).isoformat()}")), "disabled")

    def test_an_unbuilt_day_has_no_zone_match_section_at_all(self):
        self.zone_zips.return_value = dict(VIEW, built=False, rows=[], zips=[], counts={})
        html = self.text(f"date={self.today.isoformat()}")
        self.assertNotIn("Zone matches", html)
        self.assertIsNone(self.button(html))

    def test_the_page_lists_what_is_already_written_as_counts(self):
        self.zone_rows.return_value = [{"basis": "radar", "level": "agree", "n": 12},
                                       {"basis": "both", "level": "one_source", "n": 3}]
        html = self.text(f"date={self.today.isoformat()}")
        self.assertRegex(html, r"<td>radar</td><td>Agree</td><td>12</td>")
        self.assertRegex(html, r"<td>both</td><td>One source</td><td>3</td>")
        self.assertEqual(self.zone_rows.call_args.kwargs["set_id"], 7)

    def test_with_nothing_written_the_page_says_so(self):
        self.assertIn("No zone matches have been written for this day.", self.text())

    def test_viewing_the_page_never_writes_zone_matches(self):
        self.text(f"date={self.today.isoformat()}")
        self.text(f"date={self.today.isoformat()}&context=1")
        self.assertEqual(self.match_zones.call_count, 0)

    # ---- POST /pull/zones/match ----

    def post_match(self, role="admin", csrf=False, **data):
        self.app.config["WTF_CSRF_ENABLED"] = csrf
        return self.client_as(role).post("/pull/zones/match", data=data, follow_redirects=False)

    def test_the_post_needs_a_token(self):
        resp = self.post_match(csrf=True, date="2026-09-22")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.match_zones.call_count, 0)

    def test_signed_out_goes_to_login_and_writes_nothing(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        resp = self.app.test_client().post("/pull/zones/match", data={"date": "2026-09-22"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(self.match_zones.call_count, 0)

    def test_a_viewer_is_refused(self):
        self.assertEqual(self.post_match(role="viewer", date="2026-09-22").status_code, 403)
        self.assertEqual(self.match_zones.call_count, 0)

    def test_a_sender_may_match(self):
        self.assertEqual(self.post_match(role="sender", date="2026-09-22").status_code, 302)

    def test_the_post_is_not_a_get(self):
        self.assertEqual(self.client_as("admin").get("/pull/zones/match?date=2026-09-22").status_code, 405)

    def test_a_good_post_passes_the_admin_the_day_today_and_the_age_limit_and_redirects_to_the_page(self):
        resp = self.post_match(date="2026-09-22")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/pull/zones?date=2026-09-22", resp.headers["Location"])
        args, kwargs = self.match_zones.call_args
        self.assertEqual(args[1], DAY)
        self.assertEqual(args[2], DISPLAY_TZ)
        self.assertEqual((kwargs["emp_id"], kwargs["today"], kwargs["max_age_days"]),
                         (self.users["admin"], self.today, self.max_age))

    def test_the_result_is_shown_after_the_redirect(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        client = self.client_as("admin")
        html = client.post("/pull/zones/match", data={"date": "2026-09-22"},
                           follow_redirects=True).get_data(as_text=True)
        self.assertIn("Zone matches for 2026-09-22: wrote 10 (4 both, 6 radar).", html)
        self.assertIn("5 already matched to a report", html)
        self.assertIn("3 with no radar or observer", html)
        self.assertIn("2 already written", html)

    def test_a_refusal_is_a_message_and_nothing_else(self):
        self.match_zones.side_effect = zonematch.ZoneMatchError("2026-01-01 is 299 days ago, past the storm age limit.")
        self.app.config["WTF_CSRF_ENABLED"] = False
        html = self.client_as("admin").post("/pull/zones/match", data={"date": "2026-01-01"},
                                            follow_redirects=True).get_data(as_text=True)
        self.assertIn("past the storm age limit", html)
        self.assertNotIn("wrote", html)

    def test_a_missing_or_bad_date_is_a_400_and_writes_nothing(self):
        for data in ({}, {"date": ""}, {"date": "June 1"}, {"date": "2026-13-40"}):
            with self.subTest(data=data):
                self.assertEqual(self.post_match(**data).status_code, 400)
        self.assertEqual(self.match_zones.call_count, 0)

    def test_the_post_starts_no_pull_and_runs_no_report_match(self):
        self.post_match(date="2026-09-22")
        self.assertEqual(self.start_pull.call_count, 0)

    # ---- the pull button ----

    def pull_form(self, html):
        import re
        return re.search(r'<form method="post" action="/pull/zones/start".*?</form>', html, re.S)

    def test_the_pull_button_shows_the_count_and_the_estimate_for_a_built_day_inside_the_limit(self):
        html = self.text(f"date={self.today.isoformat()}")
        form = self.pull_form(html)
        self.assertIsNotNone(form)
        self.assertIn("Pull 4 zips", form.group(0))
        self.assertIn("about 5 requests", form.group(0))
        self.assertIn('name="zip_count" value="4"', form.group(0))
        self.assertIn(f'name="date" value="{self.today.isoformat()}"', form.group(0))
        self.assertNotIn('name="context"', form.group(0))
        self.assertIn("Spends RentCast requests", form.group(0))

    def test_a_ticked_context_box_is_carried_into_the_pull_form(self):
        html = self.text(f"date={self.today.isoformat()}&context=1")
        self.assertIn('name="context" value="1"', self.pull_form(html).group(0))

    def test_the_form_carries_the_count_only_never_the_zips(self):
        form = self.pull_form(self.text(f"date={self.today.isoformat()}")).group(0)
        for zcta5 in VIEW["zips"]:
            self.assertNotIn(zcta5, form)

    def test_there_is_no_pull_button_past_the_limit_for_the_future_unbuilt_or_empty(self):
        old = (self.today - timedelta(days=self.max_age + 1)).isoformat()
        html = self.text(f"date={old}")
        self.assertIsNone(self.pull_form(html))
        self.assertIn(f"Only days within {self.max_age} days can be pulled.", html)
        self.assertIsNone(self.pull_form(self.text(f"date={(self.today + timedelta(days=1)).isoformat()}")))
        self.zone_zips.return_value = dict(VIEW, built=False, rows=[], zips=[], counts={})
        self.assertIsNone(self.pull_form(self.text(f"date={self.today.isoformat()}")))
        self.zone_zips.return_value = dict(VIEW, rows=[], zips=[], counts=dict(VIEW["counts"], union=0))
        self.estimate_zips.return_value = dict(UNION, zip_count=0, estimated_api_calls=0, zips=[])
        self.assertIsNone(self.pull_form(self.text(f"date={self.today.isoformat()}")))

    def test_the_page_warns_when_the_pull_would_go_over_the_plan_and_still_offers_it(self):
        with mock.patch.object(views_web.quota, "fetch_usage", return_value=dict(USAGE, used=998)):
            html = self.text(f"date={self.today.isoformat()}")
        self.assertIn("roughly", html)
        self.assertIn("over the plan", html)
        self.assertIn("Overage is billed", html)
        self.assertIsNotNone(self.pull_form(html))

    def test_the_page_does_not_warn_when_the_pull_fits(self):
        self.assertNotIn("over the plan", self.text(f"date={self.today.isoformat()}"))

    def test_the_page_names_zips_pulled_recently(self):
        self.estimate_zips.return_value = dict(UNION, pulled_recently=[{"zip_code": "80808", "last_pulled": datetime.now(timezone.utc)}])
        html = self.text(f"date={self.today.isoformat()}")
        self.assertIn("1 of these zips were pulled in the last", html)
        self.assertIn("80808", html)

    # ---- POST /pull/zones/start ----

    def post_pull(self, role="admin", csrf=False, client=None, **data):
        self.app.config["WTF_CSRF_ENABLED"] = csrf
        client = client or self.client_as(role)
        return client.post("/pull/zones/start", data=data, follow_redirects=False)

    def good(self, **changes):
        data = {"date": self.today.isoformat(), "zip_count": "4"}
        data.update(changes)
        return data

    def test_the_pull_post_needs_a_token(self):
        self.assertEqual(self.post_pull(csrf=True, **self.good()).status_code, 400)
        self.assertEqual(self.start_pull.call_count, 0)

    def test_signed_out_goes_to_login_and_pulls_nothing(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        resp = self.app.test_client().post("/pull/zones/start", data=self.good())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(self.start_pull.call_count, 0)

    def test_a_viewer_is_refused_and_a_sender_may_pull(self):
        self.assertEqual(self.post_pull(role="viewer", **self.good()).status_code, 403)
        self.assertEqual(self.start_pull.call_count, 0)
        self.assertEqual(self.post_pull(role="sender", **self.good()).status_code, 302)
        self.assertEqual(self.start_pull.call_count, 1)

    def test_the_pull_post_is_not_a_get(self):
        self.assertEqual(self.client_as("admin").get("/pull/zones/start?date=2026-09-22").status_code, 405)

    def test_a_good_post_starts_one_pull_with_the_zips_from_the_database_and_their_reasons(self):
        resp = self.post_pull(**self.good())
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/pull/zones?date={self.today.isoformat()}", resp.headers["Location"])
        self.assertEqual(self.start_pull.call_count, 1)
        kw = self.start_pull.call_args.kwargs
        self.assertEqual((kw["emp_id"], kw["storm_date"], kw["report_text"]), (self.users["admin"], self.today, "HAIL"))
        self.assertEqual(kw["zip_codes"], UNION["zips"])
        self.assertEqual(kw["estimated_api_calls"], UNION["estimated_api_calls"])
        self.assertEqual((kw["window_start"], kw["window_end"]), views_web.denver_day_bounds(self.today))
        self.assertEqual(kw["zone"]["set_id"], 7)
        self.assertEqual([(r["zcta5"], r["basis"], r["best_level"]) for r in kw["zone"]["rows"]],
                         [(r["zcta5"], r["basis"], r["best_level"]) for r in ROWS])

    def test_the_zip_list_is_never_taken_from_the_form(self):
        self.post_pull(**self.good(zips="99999,88888", zip_codes="99999", zone="evil", estimated_api_calls="0",
                                   report_text="WIND", storm_date="1999-01-01", type="WIND", actionable="1"))
        kw = self.start_pull.call_args.kwargs
        self.assertEqual(kw["zip_codes"], UNION["zips"])
        self.assertEqual((kw["report_text"], kw["storm_date"], kw["estimated_api_calls"]), ("HAIL", self.today, 5))
        self.assertNotIn("99999", repr(kw["zone"]))

    def test_a_ticked_context_box_asks_the_database_for_context_zips(self):
        self.post_pull(**self.good(context="1"))
        self.assertTrue(self.zone_zips.call_args.kwargs["include_context"])
        self.post_pull(**self.good())
        self.assertFalse(self.zone_zips.call_args.kwargs["include_context"])

    def test_a_good_post_says_what_started_and_remembers_it_for_the_storm_list(self):
        client = self.client_as("admin")
        self.app.config["WTF_CSRF_ENABLED"] = False
        html = client.post("/pull/zones/start", data=self.good(), follow_redirects=True).get_data(as_text=True)
        self.assertIn("Pull started: 4 zips, about 5 requests.", html)
        self.assertIn("Match listings in the zones", html)
        with client.session_transaction() as s:
            self.assertEqual((s["pull_watch"]["date"], s["pull_watch"]["type"], s["pull_watch"]["zips"]),
                             (self.today.isoformat(), "HAIL", 4))

    def refused(self, data, message, start_pull_calls=0):
        """A POST that must pull nothing:  start_pull is not reached (or, for a pull already running, is reached and refuses)."""
        client = self.client_as("admin")
        self.app.config["WTF_CSRF_ENABLED"] = False
        resp = client.post("/pull/zones/start", data=data, follow_redirects=True)
        html = " ".join(resp.get_data(as_text=True).split())
        self.assertEqual(self.start_pull.call_count, start_pull_calls, "start_pull was reached when it should not be")
        self.assertIn(message, html)
        self.assertIn("nothing was pulled", html.lower())
        with client.session_transaction() as s:
            self.assertNotIn("pull_watch", s)

    def test_a_changed_zip_count_pulls_nothing(self):
        self.refused(self.good(zip_count="3"), "The zip list changed since this page was shown (3 to 4)")

    def test_a_day_past_the_limit_or_in_the_future_pulls_nothing(self):
        old = (self.today - timedelta(days=self.max_age + 1)).isoformat()
        self.refused(self.good(date=old), f"not within the last {self.max_age} days")
        self.refused(self.good(date=(self.today + timedelta(days=1)).isoformat()), "not within the last")
        # The age check comes first:  the POST itself never asks the database for a zone list.  (The redirect target,
        # the zones page, does, so look at the POST alone.)
        self.zone_zips.reset_mock()
        self.assertEqual(self.post_pull(**self.good(date=old)).status_code, 302)
        self.assertEqual(self.zone_zips.call_count, 0)
        self.assertEqual(self.start_pull.call_count, 0)

    def test_an_unbuilt_day_pulls_nothing(self):
        self.zone_zips.return_value = dict(VIEW, built=False, rows=[], zips=[], counts={})
        self.refused(self.good(), "has not been built with the hail-zone set now in use")

    def test_no_zips_pulls_nothing(self):
        self.zone_zips.return_value = dict(VIEW, rows=[], zips=[])
        self.estimate_zips.return_value = dict(UNION, zip_count=0, estimated_api_calls=0, zips=[])
        self.refused(self.good(zip_count="0"), "There are no zips to pull")

    def test_a_pull_already_running_is_a_message_and_not_remembered(self):
        self.start_pull.side_effect = jobs_web.PullInProgress("Sam", datetime.now(timezone.utc))
        self.refused(self.good(), "is already running, started by Sam", start_pull_calls=1)

    def test_a_missing_or_bad_date_or_count_is_a_400_and_pulls_nothing(self):
        for data in ({}, {"date": self.today.isoformat()}, {"zip_count": "4"}, self.good(date="June 1"),
                     self.good(zip_count="four"), self.good(zip_count=""), self.good(date="2026-13-40")):
            with self.subTest(data=data):
                self.assertEqual(self.post_pull(**data).status_code, 400)
        self.assertEqual(self.start_pull.call_count, 0)

    def test_the_pull_route_never_runs_the_matchers_or_the_report_estimate(self):
        with mock.patch.object(views_web, "match_storm") as report_matcher:
            self.post_pull(**self.good())
        self.assertEqual(report_matcher.call_count, 0)
        self.assertEqual(self.match_zones.call_count, 0)
        self.assertEqual(self.estimate_pull.call_count, 0)

    # ---- pull status, and the working state ----

    def a_pull(self, state="complete", **changes):
        row = {"pull_id": 1, "pull_source": "zones", "api_status": state if state != "stalled" else "running",
               "state": state, "zip_count": 1, "estimated_api_calls": 1, "actual_api_calls": 1,
               "listings_returned": 158, "started_by": "Sam", "started_local": "Oct 27 11:55",
               "finished_local": "11:55:45"}
        row.update(changes)
        return row

    def test_no_pulls_means_no_pulls_section_and_no_reload(self):
        html = self.text()
        self.assertNotIn("Pulls for this day", html)
        self.assertNotIn("location.reload", html)

    def test_a_finished_pull_is_listed_with_its_facts_and_the_page_does_not_reload(self):
        self.recent_pulls.return_value = [self.a_pull()]
        html = " ".join(self.text().split())
        self.assertIn("Pulls for this day", html)
        for want in ("Oct 27 11:55", "Sam", "hail zones", "Complete", "1 (est. 1)", "158", "11:55:45"):
            self.assertIn(want, html)
        self.assertNotIn("location.reload", html)
        self.assertNotIn("A pull is running", html)

    def test_a_pull_from_reports_is_labelled_as_such(self):
        self.recent_pulls.return_value = [self.a_pull(pull_source="reports")]
        self.assertIn("storm reports", self.text())

    def test_a_running_pull_says_so_and_the_page_reloads_itself(self):
        self.recent_pulls.return_value = [self.a_pull("running", actual_api_calls=None, listings_returned=None,
                                                      finished_local=None)]
        html = " ".join(self.text().split())
        self.assertIn("A pull is running. This page reloads by itself", html)
        self.assertIn("Running", html)
        self.assertEqual(html.count("location.reload"), 1)
        self.assertIn("– (est. 1)", html)

    def test_a_stalled_pull_is_flagged_and_the_page_does_not_reload(self):
        self.recent_pulls.return_value = [self.a_pull("stalled", actual_api_calls=None, listings_returned=None,
                                                      finished_local=None)]
        html = " ".join(self.text().split())
        self.assertIn("Stopped? (no update for over 10 minutes)", html)
        self.assertNotIn("location.reload", html)

    def test_any_running_pull_among_several_keeps_the_page_reloading(self):
        self.recent_pulls.return_value = [self.a_pull("running", pull_id=3, finished_local=None),
                                          self.a_pull("complete", pull_id=2), self.a_pull("failed", pull_id=1)]
        html = self.text()
        self.assertEqual(html.count("location.reload"), 1)
        self.assertIn("Failed", html)

    def test_the_page_asks_for_this_days_hail_pulls_with_the_stale_limit(self):
        self.text()
        kw = self.recent_pulls.call_args.kwargs
        self.assertEqual((kw["day"], kw["report_text"], kw["tz"]), (DAY, "HAIL", DISPLAY_TZ))
        self.assertEqual(kw["stale_after"], views_web.workstate.PULL_STALE_AFTER)

    def test_both_buttons_that_write_show_working_and_cannot_be_pressed_twice(self):
        import re
        html = self.text(f"date={self.today.isoformat()}")
        forms = re.findall(r"<form method=\"post\"[^>]*>", html)
        # The logout form in the header is not one of ours.
        ours = [f for f in forms if "/pull/zones/" in f]
        self.assertEqual(len(ours), 2)
        for f in ours:
            self.assertIn("data-working", f)
        self.assertEqual(html.count("form[data-working]"), 2)           # the submit handler and the pageshow reset
        self.assertIn("b.disabled = true;", html)
        self.assertIn("Working…", html)
        self.assertIn("e.persisted", html)                              # coming back with Back restores the button

    def test_the_get_forms_are_left_alone(self):
        html = self.text()
        self.assertNotIn('<form method="get" action="/pull/zones" class="confirm" data-working', html)


def contextlib_stack(test):
    import contextlib
    stack = contextlib.ExitStack()
    test.addCleanup(stack.close)
    return stack


if __name__ == "__main__":
    unittest.main()
