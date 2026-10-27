"""Tests for /pull/zones, the read-only zones page (hailsys/web/views.py zone_estimate, pull_zones.html; step 2a).

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_zone_estimate_web

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
    from hailsys.web import create_app, views as views_web
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
        self.zone_rows = stack.enter_context(mock.patch.object(views_web.zonepull, "zone_row_counts", return_value=[]))
        self.match_zones = stack.enter_context(mock.patch.object(views_web.zonematch, "match_zones", return_value=MATCHED))
        self.max_age = self.conn.execute("SELECT match_max_age_days AS n FROM settings").fetchone()["n"]
        self.today = datetime.now(DISPLAY_TZ).date()

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
        self.assertIn("Nothing is pulled from it", html)
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
                self.assertIn("Nothing is sent or pulled", html)

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


def contextlib_stack(test):
    import contextlib
    stack = contextlib.ExitStack()
    test.addCleanup(stack.close)
    return stack


if __name__ == "__main__":
    unittest.main()
