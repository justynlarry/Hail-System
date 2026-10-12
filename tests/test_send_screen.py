"""Tests for the read-only send preview (hailsys/web/send.py).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_send_screen

The page tests use the real app and database, sign in by writing the session directly, and only
make GET requests.  sweep_stale_pulls is mocked so building the app does not touch api_pulls.
"""

import contextlib
import os
import sys
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import render
    from hailsys.queries import sendlist
    from hailsys.settings import fetch_settings
    from hailsys.web import create_app, send as screen
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

WRITTEN_TABLES = ("sent_emails", "send_log", "email_templates", "api_pulls", "cc_sync_runs")
SENDER = {"from_name": "RBI", "from_email": "from@example.com", "reply_to": "from@example.com"}
FAKE = [{"realtor_id": 1, "email": "a@example.com", "agent_name": "A",
         "events": [{"listing_id": 1}]},
        {"realtor_id": 2, "email": "b@example.com", "agent_name": "B",
         "events": [{"listing_id": 2}, {"listing_id": 3}]}]


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class LabelsTest(unittest.TestCase):
    def test_every_rule_has_a_label(self):
        self.assertEqual(set(screen.REASON_LABELS), set(sendlist.REASONS))

    def test_the_preview_does_not_import_the_send_engine(self):
        # Previewing must never be able to send.  If this fails, read why before changing it.
        self.assertFalse(hasattr(screen, "deliver"))
        self.assertFalse(hasattr(screen, "campaigns"))

    def test_parse_allow_list(self):
        emails, bad = screen.parse_allow_list(" A@x.com, b@y.org;\n a@x.com  nonsense @x.com ")
        self.assertEqual(emails, ["a@x.com", "b@y.org"])
        self.assertEqual(bad, ["nonsense", "@x.com"])

    def test_an_empty_allow_list_parses_to_nothing(self):
        self.assertEqual(screen.parse_allow_list(""), ([], []))
        self.assertEqual(screen.parse_allow_list(None), ([], []))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class SendScreenTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])

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

    def counts(self):
        return {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in WRITTEN_TABLES}

    def test_signed_out_goes_to_login(self):
        resp = self.app.test_client().get("/send/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_only_admin_may_open_it(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.client_as(role).get("/send/").status_code, 403, role)
        self.assertEqual(self.client_as("admin").get("/send/").status_code, 200)

    def test_a_bad_day_is_a_400(self):
        self.assertEqual(self.client_as("admin").get("/send/?day=not-a-date").status_code, 400)

    def test_a_preview_lists_every_rule_and_writes_nothing(self):
        before = self.counts()
        resp = self.client_as("admin").get("/send/?day=2026-09-19&day=2026-09-22")
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        for label in screen.REASON_LABELS.values():
            self.assertIn(label, text)
        self.assertEqual(self.counts(), before)

    def test_settings_carry_the_freshness_days(self):
        self.assertIsInstance(fetch_settings(self.conn)["listing_freshness_days"], int)

    # ---- confirm and start (the engine is always stubbed here) ----

    def post(self, path, role="admin", **data):
        self.app.config["WTF_CSRF_ENABLED"] = False
        return self.client_as(role).post(path, data=data, follow_redirects=True)

    def stubs(self, mine=None, config_error=None, start_error=None):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(
            screen, "_plan", return_value=({}, FAKE if mine is None else mine, [])))
        stack.enter_context(mock.patch.object(
            screen.sendjobs, "load_config",
            **({"side_effect": config_error} if config_error
               else {"return_value": (object(), SENDER)})))
        stack.enter_context(mock.patch.object(screen.sendjobs, "is_busy", return_value=False))
        return stack.enter_context(mock.patch.object(
            screen.sendjobs, "start_send", side_effect=start_error))

    FORM = dict(day=["2026-09-19"], allowed="a@example.com b@example.com", max_emails="5")

    def test_confirm_and_start_need_a_token(self):
        client = self.client_as("admin")
        for path in ("/send/confirm", "/send/start"):
            self.assertEqual(client.post(path, data=self.FORM).status_code, 400, path)

    def test_only_admin_may_confirm_or_start(self):
        start = self.stubs()
        for role in ("viewer", "sender"):
            if role in self.users:
                for path in ("/send/confirm", "/send/start"):
                    self.assertEqual(self.post(path, role, **self.FORM).status_code, 403)
        start.assert_not_called()

    def test_confirm_refuses_an_empty_allow_list(self):
        start = self.stubs()
        text = self.post("/send/confirm", **{**self.FORM, "allowed": ""}).get_data(as_text=True)
        self.assertIn("no send-to-everyone option", text)
        start.assert_not_called()

    def test_confirm_refuses_a_malformed_address(self):
        self.stubs()
        text = self.post("/send/confirm", **{**self.FORM, "allowed": "not-an-address"}
                         ).get_data(as_text=True)
        self.assertIn("are not email addresses", text)

    def test_confirm_refuses_a_bad_limit(self):
        self.stubs()
        for bad in ("0", "-1", "x", "100000"):
            text = self.post("/send/confirm", **{**self.FORM, "max_emails": bad}
                             ).get_data(as_text=True)
            self.assertIn("must be between 1 and", text, bad)

    def test_confirm_shows_the_recipients_and_never_starts(self):
        start = self.stubs()
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertIn("a@example.com", text)
        self.assertIn("Send 2 emails now", text)
        start.assert_not_called()

    def test_confirm_with_nothing_on_the_list_offers_no_button(self):
        self.stubs(mine=[])
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertIn("nothing to send", text)
        self.assertNotIn("emails now", text)

    def test_confirm_on_real_data_writes_nothing_and_never_starts(self):
        before = self.counts()
        with mock.patch.object(screen.sendjobs, "start_send") as start:
            self.post("/send/confirm", **self.FORM)
        start.assert_not_called()
        self.assertEqual(self.counts(), before)

    def test_start_hands_the_engine_exactly_what_was_reviewed(self):
        start = self.stubs()
        self.post("/send/start", expected="2", **self.FORM)
        start.assert_called_once()
        kw = start.call_args.kwargs
        self.assertEqual(kw["allowed_emails"], ["a@example.com", "b@example.com"])
        self.assertEqual(kw["max_emails"], 5)
        self.assertEqual(kw["storm_days"], [date(2026, 9, 19)])
        self.assertIs(kw["override_cap"], False)
        self.assertEqual(kw["user_id"], self.users["admin"])
        self.assertEqual(kw["sender"], SENDER)

    def test_start_refuses_when_the_list_changed(self):
        start = self.stubs()
        text = self.post("/send/start", expected="3", **self.FORM).get_data(as_text=True)
        self.assertIn("list changed", text)
        start.assert_not_called()

    def test_start_refuses_without_a_count(self):
        start = self.stubs()
        self.assertEqual(self.post("/send/start", **self.FORM).status_code, 400)
        start.assert_not_called()

    def test_start_says_so_when_a_send_is_already_running(self):
        self.stubs(start_error=screen.sendjobs.SendInProgress("busy"))
        text = self.post("/send/start", expected="2", **self.FORM).get_data(as_text=True)
        self.assertIn("already running", text)

    def test_start_refuses_when_sending_is_not_configured(self):
        start = self.stubs(config_error=render.SettingsError("CC_FROM_EMAIL is not set"))
        text = self.post("/send/start", expected="2", **self.FORM).get_data(as_text=True)
        self.assertIn("not configured", text)
        start.assert_not_called()

    # ---- the history pages ----

    def test_history_pages_are_admin_only(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                for path in ("/send/batches", f"/send/batch/{uuid.uuid4()}"):
                    self.assertEqual(self.client_as(role).get(path).status_code, 403, path)

    def test_the_history_page_renders_and_writes_nothing(self):
        before = self.counts()
        resp = self.client_as("admin").get("/send/batches")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.counts(), before)

    def test_an_unknown_or_malformed_batch_is_a_404(self):
        client = self.client_as("admin")
        self.assertEqual(client.get(f"/send/batch/{uuid.uuid4()}").status_code, 404)
        self.assertEqual(client.get("/send/batch/not-a-uuid").status_code, 404)

    def test_a_real_batch_page_renders(self):
        row = self.conn.execute("SELECT batch_id FROM sent_emails ORDER BY email_id LIMIT 1"
                                ).fetchone()
        if row is None:
            self.skipTest("no sends in this database yet")
        resp = self.client_as("admin").get(f"/send/batch/{row['batch_id']}")
        self.assertEqual(resp.status_code, 200)

    def test_the_page_refreshes_itself_only_while_a_send_is_running(self):
        client = self.client_as("admin")
        with mock.patch.object(screen.sendjobs, "is_busy", return_value=True):
            self.assertIn("location.reload", client.get("/send/batches").get_data(as_text=True))
        with mock.patch.object(screen.sendjobs, "is_busy", return_value=False):
            self.assertNotIn("location.reload", client.get("/send/batches").get_data(as_text=True))

    # ---- the bounce check button (the check itself is always stubbed here) ----

    def test_the_check_needs_a_token(self):
        self.assertEqual(self.client_as("admin").post("/send/check").status_code, 400)

    def test_the_check_is_post_only(self):
        # Stubbed: if a GET were ever allowed it must not reach the real check, which writes
        # cc_status_runs on hail-dev (item 197).
        with mock.patch.object(screen.statuses, "run_check") as run:
            self.assertEqual(self.client_as("admin").get("/send/check").status_code, 405)
        run.assert_not_called()

    def test_only_admin_may_run_the_check(self):
        with mock.patch.object(screen.statuses, "run_check") as run:
            for role in ("viewer", "sender"):
                if role in self.users:
                    self.assertEqual(self.post("/send/check", role).status_code, 403, role)
        run.assert_not_called()

    def test_a_check_reports_what_it_found(self):
        result = {"run_id": 1, "checked": 3, "bounced": 1, "suppressed": 1}
        with mock.patch.object(screen.statuses, "run_check", return_value=result) as run:
            text = self.post("/send/check").get_data(as_text=True)
        run.assert_called_once_with(triggered_by=self.users["admin"])
        self.assertIn("3 emails checked", text)

    def test_a_check_already_running_is_said_so(self):
        busy = screen.statuses.StatusBusy("A bounce check is already running.")
        with mock.patch.object(screen.statuses, "run_check", side_effect=busy):
            text = self.post("/send/check").get_data(as_text=True)
        self.assertIn("already running", text)

    def test_a_constant_contact_error_is_said_so(self):
        with mock.patch.object(screen.statuses, "run_check",
                               side_effect=screen.api.ApiError("nope")):
            text = self.post("/send/check").get_data(as_text=True)
        self.assertIn("bounce check stopped", text)

    def test_an_unexpected_error_is_said_so_and_logged(self):
        with mock.patch.object(screen.statuses, "run_check", side_effect=RuntimeError("x")):
            with self.assertLogs("hailsys.web.send", level="ERROR"):
                text = self.post("/send/check").get_data(as_text=True)
        self.assertIn("failed unexpectedly", text)

    # ---- the resume button (the engine door is always stubbed here) ----

    BATCH = uuid.UUID("12345678-1234-5678-1234-567812345678")
    RESUME_PATH = f"/send/batch/{BATCH}/resume"

    def resume_stubs(self, counts=None, start_error=None, busy=False):
        """Everything a resume can reach is replaced: the batch read, the config, the lock
        check and the engine door.  Returns the mock for the engine door."""
        base = {"sent": 0, "bounced": 0, "failed": 0, "needs_review": 0,
                "in_progress": 0, "queued": 0}
        email = {"created_at": datetime.now(timezone.utc), "state": "queued",
                 "recipient_email": "a@example.com", "sent_at": None, "bounce_detail": None,
                 "email_error": None, "cc_activity_id": None}
        found = {"emails": [email], "counts": {**base, **(counts or {})}}
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(screen.sendstatus, "fetch_batch",
                                              return_value=found))
        stack.enter_context(mock.patch.object(screen.sendjobs, "load_config",
                                              return_value=(object(), SENDER)))
        stack.enter_context(mock.patch.object(screen.sendjobs, "is_busy", return_value=busy))
        return stack.enter_context(mock.patch.object(screen.sendjobs, "start_resume",
                                                     side_effect=start_error))

    def test_resume_needs_a_token(self):
        start = self.resume_stubs({"queued": 2})
        self.assertEqual(self.client_as("admin").post(self.RESUME_PATH).status_code, 400)
        start.assert_not_called()

    def test_resume_is_post_only(self):
        start = self.resume_stubs({"queued": 2})
        self.assertEqual(self.client_as("admin").get(self.RESUME_PATH).status_code, 405)
        start.assert_not_called()

    def test_resume_is_admin_only(self):
        start = self.resume_stubs({"queued": 2})
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post(self.RESUME_PATH, role, expected="2",
                                           max_emails="2").status_code, 403, role)
        start.assert_not_called()

    def test_resume_without_a_count_is_a_400(self):
        start = self.resume_stubs({"queued": 2})
        self.assertEqual(self.post(self.RESUME_PATH, max_emails="2").status_code, 400)
        start.assert_not_called()

    def test_resume_of_an_unknown_batch_is_a_404(self):
        start = self.resume_stubs({"queued": 2})
        with mock.patch.object(screen.sendstatus, "fetch_batch", return_value=None):
            resp = self.post(self.RESUME_PATH, expected="2", max_emails="2")
        self.assertEqual(resp.status_code, 404)
        start.assert_not_called()

    def test_resume_refuses_a_bad_limit(self):
        start = self.resume_stubs({"queued": 2})
        for bad in ("", "0", "abc", str(screen.MAX_EMAILS_LIMIT + 1)):
            text = self.post(self.RESUME_PATH, expected="2", max_emails=bad).get_data(as_text=True)
            self.assertIn("must be between 1 and", text, bad)
        start.assert_not_called()

    def test_resume_refuses_when_the_count_changed(self):
        start = self.resume_stubs({"queued": 2})
        text = self.post(self.RESUME_PATH, expected="3", max_emails="2").get_data(as_text=True)
        self.assertIn("changed since you looked", text)
        start.assert_not_called()

    def test_resume_counts_queued_and_in_progress_and_starts_the_engine(self):
        start = self.resume_stubs({"queued": 1, "in_progress": 1})
        text = self.post(self.RESUME_PATH, expected="2", max_emails="5").get_data(as_text=True)
        self.assertIn("Resume started", text)
        start.assert_called_once_with(user_id=self.users["admin"], batch_id=self.BATCH,
                                      sender=SENDER, max_emails=5)

    def test_resume_does_not_count_parked_emails(self):
        start = self.resume_stubs({"queued": 1, "needs_review": 3})
        text = self.post(self.RESUME_PATH, expected="4", max_emails="5").get_data(as_text=True)
        self.assertIn("changed since you looked", text)
        start.assert_not_called()

    def test_resume_says_why_it_was_refused(self):
        for error, words in ((screen.sendjobs.SendInProgress("busy"), "already running"),
                             (screen.sendjobs.BatchTooOld("too old to resume"), "too old"),
                             (screen.sendjobs.NothingToResume("nothing queued"), "nothing queued")):
            with self.subTest(error=type(error).__name__):
                self.resume_stubs({"queued": 2}, start_error=error)
                text = self.post(self.RESUME_PATH, expected="2",
                                 max_emails="2").get_data(as_text=True)
                self.assertIn(words, text)
                self.assertNotIn("Resume started", text)

    def test_resume_stops_when_sending_is_not_configured(self):
        start = self.resume_stubs({"queued": 2})
        err = render.SettingsError("CC_FROM_NAME is not set")
        with mock.patch.object(screen.sendjobs, "load_config", side_effect=err):
            text = self.post(self.RESUME_PATH, expected="2", max_emails="2").get_data(as_text=True)
        self.assertIn("not configured", text)
        start.assert_not_called()

    def test_the_batch_page_offers_resume_only_when_something_can_be_resumed(self):
        self.resume_stubs({"queued": 2, "in_progress": 1, "needs_review": 4})
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertIn("Resume queued emails", text)
        self.assertIn('name="expected" value="3"', text)
        self.assertIn(str(screen.sendjobs.RESUME_MAX_DAYS), text)

    def test_the_batch_page_hides_resume_when_nothing_is_queued(self):
        self.resume_stubs({"sent": 2, "needs_review": 1})
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertNotIn("Resume queued emails", text)

    def test_the_resume_button_is_disabled_while_a_send_runs(self):
        self.resume_stubs({"queued": 2}, busy=True)
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertIn('<button type="submit" disabled>Resume queued emails', text)


if __name__ == "__main__":
    unittest.main()
