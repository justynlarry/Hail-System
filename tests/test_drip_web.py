"""Tests for the campaign screens (hailsys/web/drip.py).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_web

The real app renders the real templates; the reads (dripscreen, quota, sendjobs) and the writes
(dripactions, start_drip_step) are stubbed, so nothing is written and nothing is sent.  Sign-in is
by writing the session.  What they pin: only an admin gets in; each page shows what it should and
hides the buttons that cannot work; opening the step page starts nothing; the start button refuses
when the list changed; the POST routes pass the right ids and reason and say what happened.
"""

import contextlib
import os
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import dripactions, render
    from hailsys.settings import fetch_settings
    from hailsys.web import create_app, drip
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
SENDER = {"from_name": "RBI", "from_email": "from@example.com", "reply_to": "from@example.com"}


def campaign(status="active"):
    return {"campaign_id": 5, "name": "Storm 2026-10-01", "status": status,
            "created_at": datetime(2026, 10, 2, 15, 0, tzinfo=UTC), "first_batch_id": "b",
            "status_changed_at": None, "created_by_name": "Pat Admin",
            "status_changed_by_name": None}


def step(n, batch=None, due=None, late=None, gap=3):
    return {"campaign_id": 5, "step_no": n, "gap_days": gap, "template_name": "follow-up",
            "batch_id": batch, "started_at": datetime(2026, 10, 5, 15, 0, tzinfo=UTC) if batch else None,
            "previous_sent_at": None, "due": due, "days_late": late}


def recipient(rid, name, stopped=False):
    return {"realtor_id": rid, "agent_name": name, "recipient_email": f"{name.lower()}@example.com",
            "stopped_at": datetime(2026, 10, 6, tzinfo=UTC) if stopped else None,
            "stop_reason": "replied" if stopped else None,
            "stopped_by_name": "Pat Admin" if stopped else None}


def detail(status="active", steps=None, runs=(), recipients=None):
    steps = steps if steps is not None else [step(1, due=date(2026, 10, 8), late=2), step(2)]
    return {"campaign": campaign(status), "steps": steps,
            "next_step": next((s for s in steps if s["batch_id"] is None), None),
            "runs": list(runs),
            "recipients": recipients if recipients is not None
            else [recipient(11, "Alice"), recipient(12, "Bob", stopped=True)]}


def preview(**over):
    base = {"listings": 3, "reused": 1, "to_check": 2,
            "plan": {"method": "by_id", "calls": 2, "by_id_calls": 2, "zip_pull_calls": 9,
                     "saves": 7},
            "emails_at_most": 2, "will_stop": 1,
            "excluded": {"within_cap": {"matches": 2, "realtors": 1},
                         "on_dnc_list": {"matches": 0, "realtors": 0}},
            "considered": {"matches": 5, "realtors": 3}}
    base.update(over)
    return base


USAGE = {"used": 100, "quota": 1000, "remaining": 900, "over": 0}
BUDGET = {"used": 10, "limit": 1000, "headroom": 100, "remaining": 990, "usable": 890}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class DripWebTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.users[role]
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(UTC).isoformat()
        return client

    def patch(self, target, name, **kw):
        p = mock.patch.object(target, name, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def stub_reads(self, found=None, prev=None, problem=None, busy=False):
        self.found = self.patch(drip.dripscreen, "campaign_detail",
                                return_value=found if found is not None else detail())
        self.patch(drip.dripscreen, "list_campaigns", return_value=[])
        self.preview = self.patch(drip.dripscreen, "step_preview",
                                  return_value=prev if prev is not None else preview())
        self.patch(drip.quota, "fetch_usage", return_value=USAGE)
        self.problem = self.patch(drip.sendjobs, "step_problem", return_value=problem)
        self.patch(drip.sendjobs, "is_busy", return_value=busy)
        self.patch(drip.sendjobs, "calls_budget", return_value=BUDGET)

    def get(self, path, role="admin"):
        return self.client_as(role).get(path)

    def text(self, path):
        resp = self.get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp.get_data(as_text=True)

    def post(self, path, role="admin", **data):
        self.app.config["WTF_CSRF_ENABLED"] = False
        client = self.client_as(role)
        resp = client.post(path, data=data)
        with client.session_transaction() as s:
            self.flashes = [m for _, m in s.get("_flashes", [])]
        return resp

    # ---- who may open them ----

    def test_signed_out_goes_to_login(self):
        resp = self.app.test_client().get("/campaigns/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_only_an_admin_may_open_them(self):
        self.stub_reads()
        for role in ("viewer", "sender"):
            if role in self.users:
                for path in ("/campaigns/", "/campaigns/5", "/campaigns/5/step/1"):
                    self.assertEqual(self.get(path, role).status_code, 403, (role, path))

    def test_every_post_needs_a_token(self):
        client = self.client_as()
        for path in ("/campaigns/5/step/1/start", "/campaigns/5/recipients/11/stop",
                     "/campaigns/5/stop-addresses", "/campaigns/5/status"):
            self.assertEqual(client.post(path, data={}).status_code, 400, path)

    def test_only_an_admin_may_post(self):
        start = self.patch(drip.sendjobs, "start_drip_step")
        status = self.patch(drip.dripactions, "set_status")
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post("/campaigns/5/status", role, move="cancel").status_code, 403)
                self.assertEqual(self.post("/campaigns/5/step/1/start", role).status_code, 403)
        start.assert_not_called()
        status.assert_not_called()

    # ---- the list ----

    def test_the_list_shows_each_campaign_and_its_next_follow_up(self):
        self.stub_reads()
        rows = [dict(campaign(), step_count=2, steps_sent=0, recipients=3, still_going=2,
                     next_step=step(1, due=date(2026, 10, 8), late=2)),
                dict(campaign("cancelled"), campaign_id=6, name="Old one", step_count=1,
                     steps_sent=0, recipients=1, still_going=1,
                     next_step=step(1, due=date(2026, 10, 8), late=2)),
                dict(campaign(), campaign_id=7, name="Waiting", step_count=2, steps_sent=1,
                     recipients=4, still_going=4, next_step=step(2))]
        self.patch(drip.dripscreen, "list_campaigns", return_value=rows)
        text = self.text("/campaigns/")
        self.assertIn("/campaigns/5", text)
        self.assertIn("Storm 2026-10-01", text)
        self.assertIn("0 of 2", text)
        self.assertIn("2 of 3", text)
        self.assertIn("due Oct 08, 2 days late", text)
        self.assertIn("after follow-up 1 is delivered", text)
        self.assertEqual(text.count("due Oct 08"), 1)         # the cancelled one shows no next step

    def test_an_empty_list_says_so(self):
        self.stub_reads()
        self.assertIn("No campaigns yet.", self.text("/campaigns/"))

    def test_the_nav_has_a_campaigns_link_for_an_admin(self):
        self.stub_reads()
        self.assertIn('href="/campaigns/"', self.text("/campaigns/"))

    # ---- one campaign ----

    def test_a_missing_campaign_is_404(self):
        self.stub_reads()
        self.found.return_value = None
        self.assertEqual(self.get("/campaigns/999").status_code, 404)

    def test_an_active_campaign_offers_pause_cancel_and_the_next_step(self):
        self.stub_reads()
        text = self.text("/campaigns/5")
        self.assertIn('value="pause"', text)
        self.assertNotIn('value="resume"', text)
        self.assertIn('value="cancel"', text)
        self.assertIn("/campaigns/5/step/1", text)
        self.assertNotIn("/campaigns/5/step/2", text)
        self.assertIn("due Oct 08, 2 days late", text)
        self.assertIn("waiting for the one before it", text)

    def test_a_paused_campaign_offers_resume_and_no_review_link(self):
        self.stub_reads(found=detail("paused"))
        text = self.text("/campaigns/5")
        self.assertIn('value="resume"', text)
        self.assertNotIn('value="pause"', text)
        self.assertNotIn("/campaigns/5/step/1", text)

    def test_a_closed_campaign_has_no_buttons(self):
        for status in ("cancelled", "complete"):
            self.stub_reads(found=detail(status))
            text = self.text("/campaigns/5")
            for needle in ('name="move"', 'name="addresses"', "/stop\"", "Review and send"):
                self.assertNotIn(needle, text, (status, needle))

    def test_only_recipients_still_going_get_a_stop_button(self):
        self.stub_reads()
        text = self.text("/campaigns/5")
        self.assertIn("/campaigns/5/recipients/11/stop", text)
        self.assertNotIn("/campaigns/5/recipients/12/stop", text)
        self.assertIn("stopped: replied by Pat Admin", text)
        self.assertIn("still going", text)

    def test_the_reasons_are_offered(self):
        self.stub_reads()
        text = self.text("/campaigns/5")
        self.assertIn('<option value="manual">', text)
        self.assertIn('<option value="replied">', text)
        self.assertNotIn('<option value="bounced">', text)

    def test_runs_are_listed(self):
        run = {"run_id": 1, "step_no": 1, "status": "nothing_to_send",
               "started_at": datetime(2026, 10, 8, 16, 0, tzinfo=UTC),
               "finished_at": None, "method": "zip_pull", "calls_made": 4, "emails": 0,
               "detail": "nobody was left to email", "started_by_name": "Pat Admin"}
        self.stub_reads(found=detail(runs=[run]))
        text = self.text("/campaigns/5")
        self.assertIn("nothing to send", text)
        self.assertIn("zip pull", text)
        self.assertIn("nobody was left to email", text)
        self.stub_reads(found=detail())
        self.assertIn("No follow-up has been started yet.", self.text("/campaigns/5"))

    def test_a_running_send_refreshes_the_page(self):
        self.stub_reads(busy=True)
        self.assertIn("location.reload", self.text("/campaigns/5"))
        self.stub_reads(busy=False)
        self.assertNotIn("location.reload", self.text("/campaigns/5"))

    # ---- the step page ----

    def test_an_unknown_step_is_404(self):
        self.stub_reads()
        self.assertEqual(self.get("/campaigns/5/step/9").status_code, 404)

    def test_the_step_page_shows_the_preview_and_starts_nothing(self):
        self.stub_reads()
        start = self.patch(drip.sendjobs, "start_drip_step")
        text = self.text("/campaigns/5/step/1")
        start.assert_not_called()
        self.assertIn("Opening this page spends nothing", text)
        self.assertIn("<strong>3</strong> listings", text)
        self.assertIn("<strong>2</strong> need", text.replace("needs", "need"))
        self.assertIn("1 answered within the last day", text)
        self.assertIn("Up to <strong>2</strong> emails", text)
        self.assertIn("one lookup per listing", text)
        self.assertIn("by listing: 2; by zip: 9", text)
        self.assertIn("100 of 1000 used", text)
        self.assertIn("about\n    102", text)
        self.assertIn("1 recipient\n    will be stopped", text)

    def test_the_step_page_reads_with_the_settings_cap_and_a_now(self):
        self.stub_reads()
        self.text("/campaigns/5/step/1")
        args, kw = self.preview.call_args
        self.assertEqual(args[1:], (5, 1))
        self.assertIsInstance(kw["now"], datetime)
        self.assertIsNotNone(kw["cap_days"])

    def test_zip_pull_is_described(self):
        plan = {"method": "zip_pull", "calls": 3, "by_id_calls": 8, "zip_pull_calls": 3, "saves": 5}
        self.stub_reads(prev=preview(plan=plan))
        self.assertIn("a fresh pull of their zip codes", self.text("/campaigns/5/step/1"))

    def test_only_reasons_with_matches_are_listed(self):
        self.stub_reads()
        text = self.text("/campaigns/5/step/1")
        self.assertIn("Emailed recently by another send: 2 matches, 1 recipient", text)
        self.assertNotIn("On the do-not-contact list", text)

    def test_over_the_monthly_allowance_warns(self):
        self.stub_reads()
        self.patch(drip.quota, "fetch_usage", return_value=dict(USAGE, used=999))
        self.assertIn("over the monthly RentCast allowance", self.text("/campaigns/5/step/1"))
        self.patch(drip.quota, "fetch_usage", return_value=USAGE)
        self.assertNotIn("over the monthly RentCast allowance", self.text("/campaigns/5/step/1"))

    def test_nothing_to_check_shows_no_rentcast_cost(self):
        self.stub_reads(prev=preview(to_check=0, reused=3, plan={
            "method": "by_id", "calls": 0, "by_id_calls": 0, "zip_pull_calls": 0, "saves": 0}))
        self.assertNotIn("Checking costs", self.text("/campaigns/5/step/1"))

    def test_the_form_carries_the_expected_numbers_and_the_default_limit(self):
        self.stub_reads()
        text = self.text("/campaigns/5/step/1")
        self.assertIn('name="expected_checks" value="2"', text)
        self.assertIn('name="expected_emails" value="2"', text)
        self.assertIn('name="max_emails"', text)
        self.assertIn(f'value="{drip.DEFAULT_MAX_EMAILS}"', text)
        self.assertIn("Check listings and send", text)
        self.assertIn('action="/campaigns/5/step/1/start"', text)

    def test_a_busy_send_disables_the_button(self):
        self.stub_reads(busy=True)
        text = self.text("/campaigns/5/step/1")
        self.assertIn("disabled>Check listings and send", text)
        self.stub_reads(busy=False)
        self.assertNotIn("disabled>Check listings and send", self.text("/campaigns/5/step/1"))

    def test_a_refused_step_shows_why_and_has_no_form(self):
        self.stub_reads(problem="2 emails of the first send are still queued.")
        text = self.text("/campaigns/5/step/1")
        self.assertIn("cannot be started now: 2 emails of the first send are still queued.", text)
        self.assertNotIn("Check listings and send", text)
        self.preview.assert_not_called()

    def test_a_tight_constant_contact_budget_warns(self):
        self.stub_reads()
        self.patch(drip.sendjobs, "calls_budget", return_value=dict(BUDGET, usable=5))
        self.assertIn("only 5 can be used", self.text("/campaigns/5/step/1"))
        self.patch(drip.sendjobs, "calls_budget", return_value=BUDGET)
        self.assertNotIn("only 5 can be used", self.text("/campaigns/5/step/1"))

    # ---- the start click ----

    START = "/campaigns/5/step/2/start"

    def stub_start(self, prev=None, config_error=None, start_error=None):
        self.patch(drip.dripscreen, "step_preview", return_value=prev or preview())
        self.patch(drip.sendjobs, "load_config",
                   **({"side_effect": config_error} if config_error
                      else {"return_value": ("SETTINGS", SENDER)}))
        return self.patch(drip.sendjobs, "start_drip_step", side_effect=start_error)

    FORM = dict(expected_checks="2", expected_emails="2", max_emails="5")

    def test_the_click_starts_the_step_with_the_right_arguments(self):
        start = self.stub_start()
        resp = self.post(self.START, **self.FORM)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/campaigns/5"))
        kw = start.call_args.kwargs
        self.assertEqual((kw["campaign_id"], kw["step_no"], kw["max_emails"]), (5, 2, 5))
        self.assertEqual(kw["user_id"], self.users["admin"])
        self.assertEqual((kw["email_settings"], kw["sender"]), ("SETTINGS", SENDER))
        self.assertIsInstance(kw["freshness_days"], int)
        self.assertIsInstance(kw["cap_days"], int)
        self.assertIn("Follow-up 2 started", self.flashes[0])
        self.assertIn("up to 5", self.flashes[0])

    def test_the_click_passes_the_chosen_limit_and_the_settings_through(self):
        start = self.stub_start()
        real = fetch_settings(self.conn)
        self.patch(sys.modules["hailsys.web.auth"], "fetch_settings",
                   return_value=dict(real, listing_freshness_days=11, email_cap_days=13))
        self.post(self.START, **{**self.FORM, "max_emails": "7"})
        kw = start.call_args.kwargs
        self.assertEqual(kw["max_emails"], 7)
        self.assertEqual(kw["freshness_days"], 11)
        self.assertEqual(kw["cap_days"], 13)

    def test_the_top_of_the_limit_is_allowed(self):
        start = self.stub_start()
        self.post(self.START, **{**self.FORM, "max_emails": str(drip.MAX_EMAILS_LIMIT)})
        self.assertEqual(start.call_args.kwargs["max_emails"], drip.MAX_EMAILS_LIMIT)

    def test_the_bottom_of_the_limit_is_allowed(self):
        start = self.stub_start()
        self.post(self.START, **{**self.FORM, "max_emails": "1"})
        self.assertEqual(start.call_args.kwargs["max_emails"], 1)

    def test_the_click_refuses_when_the_list_changed(self):
        for prev in (preview(to_check=3), preview(emails_at_most=1)):
            start = self.stub_start(prev=prev)
            resp = self.post(self.START, **self.FORM)
            start.assert_not_called()
            self.assertTrue(resp.headers["Location"].endswith("/campaigns/5/step/2"))
            self.assertIn("The list changed since you reviewed it", self.flashes[0])

    def test_the_click_needs_both_expected_numbers(self):
        start = self.stub_start()
        for data in ({"expected_emails": "2", "max_emails": "5"},
                     {"expected_checks": "2", "max_emails": "5"},
                     {**self.FORM, "expected_checks": "x"}, {**self.FORM, "expected_emails": ""}):
            self.assertEqual(self.post(self.START, **data).status_code, 400, data)
        start.assert_not_called()

    def test_the_click_refuses_a_bad_limit(self):
        start = self.stub_start()
        for bad in ("0", "-1", "x", "", "100000"):
            resp = self.post(self.START, **{**self.FORM, "max_emails": bad})
            self.assertEqual(resp.status_code, 302, bad)
            self.assertIn("must be between 1 and", self.flashes[0], bad)
        start.assert_not_called()

    def test_the_click_says_when_sending_is_not_configured(self):
        start = self.stub_start(config_error=render.SettingsError("no logo"))
        self.post(self.START, **self.FORM)
        start.assert_not_called()
        self.assertIn("Sending is not configured: no logo", self.flashes[0])

    def test_the_click_says_when_a_send_is_running(self):
        self.stub_start(start_error=drip.sendjobs.SendInProgress("busy"))
        resp = self.post(self.START, **self.FORM)
        self.assertIn("A send is already running", self.flashes[0])
        self.assertTrue(resp.headers["Location"].endswith("/campaigns/5/step/2"))

    def test_the_click_says_why_a_step_is_refused(self):
        self.stub_start(start_error=drip.sendjobs.StepRefused("The campaign is paused."))
        resp = self.post(self.START, **self.FORM)
        self.assertEqual(self.flashes[0], "The campaign is paused. Nothing was started.")
        self.assertTrue(resp.headers["Location"].endswith("/campaigns/5/step/2"))

    def test_the_constant_contact_estimate_is_for_what_one_click_sends(self):
        # 50 emails could go out, but one click sends at most the default limit.
        self.stub_reads(prev=preview(emails_at_most=50))
        self.patch(drip.sendjobs, "calls_budget", return_value=dict(BUDGET, usable=5))
        n = drip.DEFAULT_MAX_EMAILS
        text = self.text("/campaigns/5/step/1")
        self.assertIn(f"Sending {n} emails needs about {n * drip.CALLS_PER_EMAIL} Constant Contact", text)

    # ---- stop, paste, status ----

    def test_stop_one_recipient(self):
        stop = self.patch(drip.dripactions, "stop_recipient")
        resp = self.post("/campaigns/5/recipients/11/stop", reason="replied")
        kw = stop.call_args.kwargs
        self.assertEqual((kw["campaign_id"], kw["realtor_id"], kw["reason"], kw["user_id"]),
                         (5, 11, "replied", self.users["admin"]))
        self.assertTrue(resp.headers["Location"].endswith("/campaigns/5"))
        self.assertIn("Recipient stopped", self.flashes[0])

    def stub_connection(self):
        conn = mock.MagicMock()
        cm = mock.MagicMock()
        cm.__enter__.return_value = conn
        self.patch(drip, "get_connection", return_value=cm)
        return conn

    def test_a_stop_is_committed_and_a_refusal_is_rolled_back(self):
        conn = self.stub_connection()
        self.patch(drip.dripactions, "stop_recipient")
        self.post("/campaigns/5/recipients/11/stop", reason="manual")
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()
        conn = self.stub_connection()
        self.patch(drip.dripactions, "stop_recipient",
                   side_effect=dripactions.ActionError("no"))
        self.post("/campaigns/5/recipients/11/stop", reason="manual")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_a_paste_is_committed_and_a_refusal_is_rolled_back(self):
        conn = self.stub_connection()
        self.patch(drip.dripactions, "stop_by_addresses",
                   return_value={"stopped": [], "already": [], "unknown": []})
        self.post("/campaigns/5/stop-addresses", addresses="a@x.com", reason="manual")
        conn.commit.assert_called_once()
        conn = self.stub_connection()
        self.patch(drip.dripactions, "stop_by_addresses",
                   side_effect=dripactions.ActionError("no"))
        self.post("/campaigns/5/stop-addresses", addresses="a@x.com", reason="manual")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_a_status_move_is_committed_and_a_refusal_is_rolled_back(self):
        conn = self.stub_connection()
        self.patch(drip.dripactions, "set_status")
        self.post("/campaigns/5/status", move="pause")
        conn.commit.assert_called_once()
        conn = self.stub_connection()
        self.patch(drip.dripactions, "set_status", side_effect=dripactions.ActionError("no"))
        self.post("/campaigns/5/status", move="pause")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()

    def test_stop_one_recipient_says_why_it_failed(self):
        self.patch(drip.dripactions, "stop_recipient",
                   side_effect=dripactions.ActionError("That recipient is already stopped."))
        self.post("/campaigns/5/recipients/11/stop", reason="manual")
        self.assertEqual(self.flashes, ["That recipient is already stopped."])

    def test_the_paste_box_reports_all_three_lists(self):
        stop = self.patch(drip.dripactions, "stop_by_addresses", return_value={
            "stopped": ["a@x.com", "b@x.com"], "already": ["c@x.com"], "unknown": ["d@x.com"]})
        self.post("/campaigns/5/stop-addresses", addresses="a@x.com b@x.com", reason="replied")
        kw = stop.call_args.kwargs
        self.assertEqual((kw["campaign_id"], kw["text"], kw["reason"], kw["user_id"]),
                         (5, "a@x.com b@x.com", "replied", self.users["admin"]))
        self.assertEqual(self.flashes, ["Stopped 2. Already stopped: c@x.com. "
                                        "Not in this campaign: d@x.com."])

    def test_the_paste_box_with_nothing_unusual_says_only_how_many(self):
        self.patch(drip.dripactions, "stop_by_addresses",
                   return_value={"stopped": ["a@x.com"], "already": [], "unknown": []})
        resp = self.post("/campaigns/5/stop-addresses", addresses="a@x.com", reason="manual")
        self.assertEqual(self.flashes, ["Stopped 1."])
        self.assertTrue(resp.headers["Location"].endswith("/campaigns/5"))

    def test_the_paste_box_says_why_it_failed(self):
        self.patch(drip.dripactions, "stop_by_addresses",
                   side_effect=dripactions.ActionError("No email addresses were found."))
        self.post("/campaigns/5/stop-addresses", addresses="", reason="manual")
        self.assertEqual(self.flashes, ["No email addresses were found."])

    def test_status_moves(self):
        move = self.patch(drip.dripactions, "set_status")
        for name, said in (("pause", "Campaign paused."), ("resume", "Campaign is active again."),
                           ("cancel", "Campaign cancelled.")):
            resp = self.post("/campaigns/5/status", move=name)
            kw = move.call_args.kwargs
            self.assertEqual((kw["campaign_id"], kw["move"], kw["user_id"]),
                             (5, name, self.users["admin"]))
            self.assertEqual(self.flashes, [said])
            self.assertTrue(resp.headers["Location"].endswith("/campaigns/5"))

    def test_status_says_why_it_failed(self):
        self.patch(drip.dripactions, "set_status",
                   side_effect=dripactions.ActionError("This campaign cannot be paused now."))
        self.post("/campaigns/5/status", move="pause")
        self.assertEqual(self.flashes, ["This campaign cannot be paused now."])


if __name__ == "__main__":
    unittest.main()
