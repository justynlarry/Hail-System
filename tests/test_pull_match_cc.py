"""/pull, /match and the Constant Contact connect, callback and refresh, doing their work (item 286, part 2).

    docker compose --profile test run --rm -T test python -m unittest tests.test_pull_match_cc

tests/test_web_smoke.py reaches these routes only as refusals.  Here each runs as a user allowed to, with what would
spend money or reach a provider replaced:  /pull's estimate and start_pull (the thread that calls RentCast, tested in
test_pull_jobs.py), /match's match_storm (tested in its own files), and the token calls of oauth.  So nothing is
written and no request leaves the machine;  each test checks the tables these routes could write are unchanged.
What is pinned is what the route itself decides.

What they pin:  /pull starts a pull with exactly the zips and estimate it recomputed, the user from the session and the
storm's local-day window, and starts nothing when the zip count changed since it was shown, when there is nothing to
pull, or for a missing date or type;  a pull already running is reported, not doubled.  /match runs the matcher for the
storm's window as the session's user and says what it found.  /cc/connect sends the admin to Constant Contact with a
state it keeps;  the callback with that state completes the connection, once;  /cc/refresh forces a refresh and shows
its error text when it fails.
"""

import os
import sys
import unittest
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.constantcontact import oauth
    from hailsys.tuning import denver_day_bounds
    from hailsys.web import create_app, views, cc as cc_web
    from hailsys.web.jobs import PullInProgress
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DAY = date(2026, 6, 8)
WRITTEN = ("api_pulls", "api_call_log", "storm_listing_matches", "match_runs", "oauth_tokens")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class RouteWorkTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.conn = psycopg.connect(row_factory=dict_row, autocommit=True)
        self.addCleanup(self.conn.close)
        row = self.conn.execute("SELECT emp_id FROM users WHERE is_active AND role <> 'system' "
                                "ORDER BY emp_id LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no active user")
        self.emp_id = row["emp_id"]
        before = self.counts()
        self.addCleanup(lambda: self.assertEqual(self.counts(), before, "a test wrote to the database"))

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in WRITTEN}

    def client(self, role):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.emp_id, role, "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def flashes(self, client):
        with client.session_transaction() as s:
            return [m for _, m in s.get("_flashes", [])]

    # ---- /pull ----

    def pull(self, zips=("80014", "80016"), sent=2, **form):
        """POST /pull as a sender with the estimate and start_pull replaced.  Returns (client, response, start_pull)."""
        estimate = {"zip_count": len(zips), "zips": list(zips), "estimated_api_calls": 3 * len(zips)}
        data = {"date": DAY.isoformat(), "type": "HAIL", "zip_count": str(sent)}
        data.update(form)
        client = self.client("sender")
        with mock.patch.object(views, "estimate_pull", return_value=estimate), \
             mock.patch.object(views, "start_pull") as start:
            resp = client.post("/pull", data=data)
        return client, resp, start

    def test_a_pull_starts_with_the_zips_it_recomputed_and_the_session_user(self):
        client, resp, start = self.pull()
        self.assertEqual(resp.status_code, 302)
        start.assert_called_once()
        kw = start.call_args.kwargs
        window_start, window_end = denver_day_bounds(DAY)
        self.assertEqual((kw["emp_id"], kw["storm_date"], kw["report_text"]), (self.emp_id, DAY, "HAIL"))
        self.assertEqual((kw["zip_codes"], kw["estimated_api_calls"]), (["80014", "80016"], 6))
        self.assertEqual((kw["window_start"], kw["window_end"]), (window_start, window_end))
        with client.session_transaction() as s:
            self.assertEqual(s["pull_watch"]["zips"], 2)            # the banner follows this pull

    def test_a_changed_zip_count_starts_nothing_and_goes_back_to_the_estimate(self):
        client, resp, start = self.pull(sent=5)
        start.assert_not_called()
        self.assertIn("/pull/estimate", resp.headers["Location"])
        self.assertTrue(any("changed since this estimate" in m for m in self.flashes(client)))

    def test_nothing_to_pull_starts_nothing(self):
        client, resp, start = self.pull(zips=(), sent=0)
        start.assert_not_called()
        self.assertTrue(any("Nothing was pulled" in m for m in self.flashes(client)))

    def test_a_missing_date_or_type_is_a_400_and_starts_nothing(self):
        for form in ({"date": ""}, {"date": "not-a-date"}, {"type": ""}):
            with self.subTest(form=form):
                _, resp, start = self.pull(**form)
                self.assertEqual(resp.status_code, 400)
                start.assert_not_called()

    def test_a_pull_already_running_is_reported_not_doubled(self):
        client = self.client("sender")
        estimate = {"zip_count": 1, "zips": ["80014"], "estimated_api_calls": 3}
        with mock.patch.object(views, "estimate_pull", return_value=estimate), \
             mock.patch.object(views, "start_pull",
                               side_effect=PullInProgress("Someone", datetime.now(timezone.utc))) as start:
            resp = client.post("/pull", data={"date": DAY.isoformat(), "type": "HAIL", "zip_count": "1"})
        self.assertEqual(resp.status_code, 302)
        start.assert_called_once()
        self.assertTrue(any("already running" in m and "Someone" in m for m in self.flashes(client)))

    # ---- /match ----

    def match(self, found):
        client = self.client("sender")
        with mock.patch.object(views, "match_storm", return_value=found) as matcher:
            resp = client.post("/match", data={"date": DAY.isoformat(), "type": "HAIL"})
        return client, resp, matcher

    def test_a_match_runs_for_the_storm_window_as_the_session_user(self):
        client, resp, matcher = self.match(4)
        self.assertEqual(resp.status_code, 302)
        kw = matcher.call_args.kwargs
        window_start, window_end = denver_day_bounds(DAY)
        self.assertEqual((kw["emp_id"], kw["storm_date"], kw["report_text"]), (self.emp_id, DAY, "HAIL"))
        self.assertEqual((kw["window_start"], kw["window_end"]), (window_start, window_end))
        self.assertIn("4 new matches", " ".join(self.flashes(client)))

    def test_a_match_that_finds_nothing_says_so(self):
        client, _, _ = self.match(0)
        text = " ".join(self.flashes(client))
        self.assertTrue("already matched" in text or "No listings within range" in text, text)

    def test_a_match_without_a_date_or_type_is_a_400(self):
        client = self.client("sender")
        with mock.patch.object(views, "match_storm") as matcher:
            for form in ({"type": "HAIL"}, {"date": DAY.isoformat()}):
                with self.subTest(form=form):
                    self.assertEqual(client.post("/match", data=form).status_code, 400)
        matcher.assert_not_called()

    # ---- /cc/connect, the callback, /cc/refresh ----

    def test_connect_sends_the_admin_to_constant_contact_with_a_state_it_keeps(self):
        client = self.client("admin")
        resp = client.post("/cc/connect")
        self.assertEqual(resp.status_code, 302)
        target = urllib.parse.urlsplit(resp.headers["Location"])
        self.assertEqual(f"{target.scheme}://{target.netloc}{target.path}", oauth.AUTHZ_URL)
        state = urllib.parse.parse_qs(target.query)["state"][0]
        with client.session_transaction() as s:
            self.assertEqual(s[cc_web.STATE_KEY], state)

    def test_the_callback_with_the_kept_state_completes_the_connection_once(self):
        client = self.client("admin")
        with client.session_transaction() as s:
            s[cc_web.STATE_KEY] = "the-state"
        with mock.patch.object(cc_web.oauth, "complete_authorization", return_value="acct-1") as complete:
            resp = client.get("/cc/callback?code=the-code&state=the-state")
            again = client.get("/cc/callback?code=the-code&state=the-state")      # a state is good once
        self.assertEqual((resp.status_code, again.status_code), (302, 302))
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[1], "the-code")
        text = " ".join(self.flashes(client))
        self.assertIn("Connected to Constant Contact account acct-1", text)
        self.assertIn("state check failed", text)

    def test_refresh_forces_a_new_token(self):
        client = self.client("admin")
        with mock.patch.object(cc_web.oauth, "get_access_token") as get:
            client.post("/cc/refresh")
        self.assertTrue(get.call_args.kwargs["force"])
        self.assertIn("Refreshed the Constant Contact Token.", self.flashes(client))

    def test_a_failed_refresh_shows_its_fixed_text(self):
        client = self.client("admin")
        with mock.patch.object(cc_web.oauth, "get_access_token",
                               side_effect=oauth.OAuthError("Constant Contact refused the request (400, invalid_grant).")):
            resp = client.post("/cc/refresh")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("Constant Contact refused the request (400, invalid_grant).", self.flashes(client))


if __name__ == "__main__":
    unittest.main()
