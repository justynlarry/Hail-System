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
    SETTINGS = render.EmailSettings(
        "https://x.test/logo.png", "https://x.test/cra.png", "https://x.test/bbb.png",
        "contact@example.com", "https://x.test/schedule")
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
               else {"return_value": (SETTINGS, SENDER)})))
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
        self.assertNotRegex(text, r"Send \d+ emails? now")

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
                                              return_value=(SETTINGS, SENDER)))
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


    # ---- the discard button (the engine door is always stubbed here) ----

    DISCARD_PATH = f"/send/batch/{BATCH}/discard"

    def discard_stubs(self, discardable=2, result=2, discard_error=None, busy=False,
                      exists=True):
        """Everything a discard can reach is replaced: the batch read, the count, the lock
        check and the engine door.  Returns the mock for the engine door."""
        base = {"sent": 0, "bounced": 0, "failed": 0, "needs_review": 0,
                "in_progress": 0, "queued": 0}
        email = {"created_at": datetime.now(timezone.utc), "state": "queued",
                 "recipient_email": "a@example.com", "sent_at": None, "bounce_detail": None,
                 "email_error": None, "cc_activity_id": None}
        found = {"emails": [email], "counts": base} if exists else None
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(screen.sendstatus, "fetch_batch",
                                              return_value=found))
        stack.enter_context(mock.patch.object(screen.sendjobs, "count_discardable",
                                              return_value=discardable))
        stack.enter_context(mock.patch.object(screen.sendjobs, "is_busy", return_value=busy))
        return stack.enter_context(mock.patch.object(
            screen.sendjobs, "discard_queued", return_value=result, side_effect=discard_error))

    def test_discard_needs_a_token(self):
        discard = self.discard_stubs()
        self.assertEqual(self.client_as("admin").post(self.DISCARD_PATH).status_code, 400)
        discard.assert_not_called()

    def test_discard_is_post_only(self):
        discard = self.discard_stubs()
        self.assertEqual(self.client_as("admin").get(self.DISCARD_PATH).status_code, 405)
        discard.assert_not_called()

    def test_discard_is_admin_only(self):
        discard = self.discard_stubs()
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post(self.DISCARD_PATH, role, expected="2",
                                           confirm="yes").status_code, 403, role)
        discard.assert_not_called()

    def test_discard_without_a_count_is_a_400(self):
        discard = self.discard_stubs()
        self.assertEqual(self.post(self.DISCARD_PATH, confirm="yes").status_code, 400)
        discard.assert_not_called()

    def test_discard_without_the_confirm_box_discards_nothing(self):
        discard = self.discard_stubs()
        text = self.post(self.DISCARD_PATH, expected="2").get_data(as_text=True)
        self.assertIn("Tick the box", text)
        discard.assert_not_called()

    def test_discard_of_an_unknown_batch_is_a_404(self):
        discard = self.discard_stubs(exists=False)
        self.assertEqual(self.post(self.DISCARD_PATH, expected="2",
                                   confirm="yes").status_code, 404)
        discard.assert_not_called()

    def test_discard_refuses_when_the_count_changed(self):
        discard = self.discard_stubs(discardable=1)
        text = self.post(self.DISCARD_PATH, expected="2", confirm="yes").get_data(as_text=True)
        self.assertIn("changed since you looked", text)
        discard.assert_not_called()

    def test_discard_runs_once_as_that_admin_and_reports_the_count(self):
        discard = self.discard_stubs(discardable=2, result=2)
        text = self.post(self.DISCARD_PATH, expected="2", confirm="yes").get_data(as_text=True)
        discard.assert_called_once_with(user_id=self.users["admin"], batch_id=self.BATCH)
        self.assertIn("Discarded 2 queued emails", text)

    def test_discard_of_one_email_reads_in_the_singular(self):
        self.discard_stubs(discardable=1, result=1)
        text = self.post(self.DISCARD_PATH, expected="1", confirm="yes").get_data(as_text=True)
        self.assertIn("Discarded 1 queued email.", text)

    def test_discard_says_why_it_was_refused(self):
        for error, expect in ((screen.sendjobs.SendInProgress("x"), "already running"),
                              (screen.sendjobs.BatchBusy("being delivered"), "being delivered")):
            with self.subTest(expect=expect):
                self.discard_stubs(discard_error=error)
                text = self.post(self.DISCARD_PATH, expected="2",
                                 confirm="yes").get_data(as_text=True)
                self.assertIn(expect, text)
                self.assertNotIn("Discarded 2", text)

    def test_the_batch_page_offers_discard_only_when_something_is_untouched(self):
        self.discard_stubs(discardable=3)
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertIn("Discard 3 untouched queued emails", text)
        self.assertIn('name="expected" value="3"', text)
        self.assertIn('name="confirm"', text)

    def test_the_batch_page_hides_discard_when_nothing_is_untouched(self):
        self.discard_stubs(discardable=0)
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertNotIn("untouched queued email", text)

    def test_the_discard_button_is_disabled_while_a_send_runs(self):
        self.discard_stubs(discardable=2, busy=True)
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertIn('<button type="submit" disabled>', text)

    # ---- the daily call count on the screens (item 191, piece B) ----

    BUDGET = {"used": 1234, "limit": 10000, "headroom": 200, "remaining": 8766, "usable": 8566}

    def budget_stub(self, **over):
        """Replace the count, so no screen test depends on how many real calls were made."""
        patcher = mock.patch.object(screen.sendjobs, "calls_budget",
                                    return_value={**self.BUDGET, **over})
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_the_send_page_shows_the_call_count(self):
        self.budget_stub()
        text = self.client_as("admin").get("/send/").get_data(as_text=True)
        self.assertIn("<strong>1234</strong>", text)
        self.assertIn("About 8566 more", text)

    def test_the_call_count_is_a_warning_when_nothing_is_usable(self):
        self.budget_stub(used=9900, remaining=100, usable=0)
        text = self.client_as("admin").get("/send/").get_data(as_text=True)
        self.assertIn('class="note warning">\n    Constant Contact calls', text)

    def test_the_call_count_is_not_a_warning_with_budget_left(self):
        self.budget_stub()
        text = self.client_as("admin").get("/send/").get_data(as_text=True)
        self.assertIn('class="note">\n    Constant Contact calls', text)

    def test_the_batch_page_shows_the_call_count_while_emails_are_queued(self):
        self.resume_stubs({"queued": 2})
        self.budget_stub()
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertIn("Constant Contact calls in the last 24 hours", text)

    def test_the_batch_page_leaves_the_call_count_out_when_nothing_is_queued(self):
        self.resume_stubs({"sent": 2})
        self.budget_stub()
        text = self.client_as("admin").get(f"/send/batch/{self.BATCH}").get_data(as_text=True)
        self.assertNotIn("Constant Contact calls in the last 24 hours", text)

    def test_confirm_warns_when_the_send_needs_more_calls_than_can_be_used(self):
        start = self.stubs()
        self.budget_stub(usable=5)
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertIn("needs about 20 Constant Contact calls", text)
        self.assertIn("only 5 can", text)
        self.assertIn("Send 2 emails now", text)                       # warn only
        self.assertNotIn('<button type="submit" disabled>Send 2', text)
        start.assert_not_called()

    def test_confirm_shows_the_call_count(self):
        self.stubs()
        self.budget_stub()
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertIn("<strong>1234</strong>", text)
        self.assertIn("About 8566 more", text)

    def test_confirm_does_not_warn_when_the_calls_fit(self):
        self.stubs()
        self.budget_stub(usable=8566)
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertNotIn("needs about", text)

    def test_confirm_does_not_warn_when_the_calls_exactly_fit(self):
        self.stubs()
        self.budget_stub(usable=20)                                    # 2 emails x 10 calls
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertNotIn("needs about", text)

    # ---- the description editor (item 202; the storage is always stubbed here) ----

    DESC_PATH = "/send/templates/123/description"

    def desc_stub(self, **kw):
        patcher = mock.patch.object(screen.templatestore, "set_description", **kw)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_description_needs_a_token(self):
        save = self.desc_stub()
        self.assertEqual(self.client_as("admin").post(self.DESC_PATH).status_code, 400)
        save.assert_not_called()

    def test_description_is_post_only(self):
        save = self.desc_stub()
        self.assertEqual(self.client_as("admin").get(self.DESC_PATH).status_code, 405)
        save.assert_not_called()

    def test_description_is_admin_only(self):
        save = self.desc_stub()
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post(self.DESC_PATH, role, description="x").status_code,
                                 403, role)
        save.assert_not_called()

    def test_description_is_saved_and_says_so(self):
        save = self.desc_stub()
        text = self.post(self.DESC_PATH, description="New note").get_data(as_text=True)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[1:], (123, "New note"))
        self.assertIn("Description saved.", text)

    def test_a_frozen_description_says_why_and_is_not_reported_as_saved(self):
        self.desc_stub(side_effect=screen.templatestore.DescriptionFrozen(
            "This template has been used in an email, so its description can no longer change."))
        text = self.post(self.DESC_PATH, description="x").get_data(as_text=True)
        self.assertIn("has been used in an email", text)
        self.assertNotIn("Description saved.", text)

    def test_a_too_long_description_says_so(self):
        self.desc_stub(side_effect=ValueError("The description is limited to 300 characters."))
        text = self.post(self.DESC_PATH, description="x").get_data(as_text=True)
        self.assertIn("limited to 300 characters", text)
        self.assertNotIn("Description saved.", text)

    def test_an_unknown_template_is_a_404(self):
        self.desc_stub(side_effect=screen.templatestore.TemplateNotFound(123))
        self.assertEqual(self.post(self.DESC_PATH, description="x").status_code, 404)

    def test_a_bad_day_is_a_400_and_nothing_is_saved(self):
        save = self.desc_stub()
        self.assertEqual(self.post(self.DESC_PATH, description="x", day="nope").status_code, 400)
        save.assert_not_called()

    def test_the_redirect_keeps_the_days_the_template_and_the_page(self):
        self.desc_stub()
        self.app.config["WTF_CSRF_ENABLED"] = False
        resp = self.client_as("admin").post(
            self.DESC_PATH, data=dict(description="x", day="2026-09-19", template="hail_alert",
                                      tpage="2"))
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["Location"]
        for part in ("day=2026-09-19", "template=hail_alert", "tpage=2"):
            self.assertIn(part, location)

    # ---- the template list on the send page (item 202; the list query is stubbed) ----

    def trow(self, name, template_id=1, **over):
        row = {"template_id": template_id, "template_name": name, "description": None,
               "subject": f"Subject of {name}",
               "body": "<p>MARKER-" + name + " {{ agent_first_name }}</p>[[trackingImage]]",
               "report_type": None, "report_text": None,
               "created_at": datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc),
               "emp_fname": "Ada", "emp_lname": "Lovelace", "versions": 1,
               "last_used": None, "sends": 0}
        row.update(over)
        return row

    def template_stubs(self, rows, page=1, pages=1, total=None, extra=()):
        """The list and the single lookup, replaced.  `extra` rows are found by name but are
        not on the page."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        listing = {"rows": rows, "page": page, "pages": pages,
                   "total": len(rows) if total is None else total}
        fetch_page = stack.enter_context(
            mock.patch.object(screen.templatelist, "fetch_page", return_value=listing))
        by_name = {r["template_name"]: r for r in [*rows, *extra]}
        stack.enter_context(mock.patch.object(
            screen.templatelist, "fetch_one", side_effect=lambda conn, name: by_name.get(name)))
        return fetch_page

    def send_page(self, query=""):
        return self.client_as("admin").get("/send/" + query).get_data(as_text=True)

    def test_every_template_is_a_row_with_a_radio_and_its_columns(self):
        self.template_stubs([
            self.trow("alpha", 1, description="Alpha note", versions=3, sends=2,
                      last_used=datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)),
            self.trow("beta", 2)])
        text = self.send_page()
        self.assertIn('name="template_pick" value="alpha"', text)
        self.assertIn('name="template_pick" value="beta"', text)
        self.assertIn("Alpha note", text)
        self.assertIn("v3", text)
        self.assertIn("Ada Lovelace", text)
        self.assertIn("2026-10-05", text)

    def test_the_built_in_is_chosen_when_nothing_else_is(self):
        self.template_stubs([self.trow("hail_alert"), self.trow("beta", 2)])
        text = self.send_page()
        self.assertIn("Chosen: <strong>hail_alert</strong>", text)
        self.assertNotIn("is not available", text)          # the default is not a fallback
        self.assertRegex(text, r'value="hail_alert"\s+id="tpl-1" checked')

    def test_a_radio_click_wins_over_the_carried_value(self):
        self.template_stubs([self.trow("hail_alert"), self.trow("beta", 2)])
        text = self.send_page("?template=hail_alert&template_pick=beta")
        self.assertIn("Chosen: <strong>beta</strong>", text)
        self.assertRegex(text, r'value="beta"\s+id="tpl-2" checked')

    def test_the_carried_value_is_used_when_nothing_is_clicked(self):
        self.template_stubs([self.trow("hail_alert"), self.trow("beta", 2)])
        self.assertIn("Chosen: <strong>beta</strong>", self.send_page("?template=beta"))

    def test_the_choice_is_kept_when_it_is_on_another_page(self):
        self.template_stubs([self.trow("hail_alert")], page=1, pages=3, total=25,
                            extra=[self.trow("zeta", 9)])
        text = self.send_page("?template=zeta")
        self.assertIn("Chosen: <strong>zeta</strong>", text)
        self.assertIn('<input type="hidden" name="template" value="zeta">', text)
        self.assertNotIn(" checked", text.split("<h3>Template</h3>")[1].split("</table>")[0])

    def test_an_unknown_template_falls_back_to_the_built_in_and_says_so(self):
        self.template_stubs([self.trow("hail_alert")])
        text = self.send_page("?template=nope")
        self.assertIn("is not available", text)
        self.assertIn("Chosen: <strong>hail_alert</strong>", text)

    def test_previous_and_next_carry_the_page_and_the_edges_are_disabled(self):
        self.template_stubs([self.trow("alpha")], page=2, pages=3, total=25)
        text = " ".join(self.send_page("?tpage=2").split())
        self.assertIn("Page 2 of 3 (25 templates)", text)
        self.assertIn('name="tpage" value="1" >Previous', text)
        self.assertIn('name="tpage" value="3" >Next', text)
        self.template_stubs([self.trow("alpha")], page=1, pages=3, total=25)
        self.assertIn('name="tpage" value="0" disabled>Previous',
                      " ".join(self.send_page().split()))

    def test_the_current_page_is_sent_before_the_buttons_so_a_button_wins(self):
        self.template_stubs([self.trow("alpha")], page=2, pages=3, total=25)
        text = self.send_page("?tpage=2")
        hidden = text.index('<input type="hidden" name="tpage" value="2">')
        button = text.index('<button type="submit" name="tpage"')
        self.assertLess(hidden, button)

    def test_one_page_has_no_paging_buttons(self):
        self.template_stubs([self.trow("alpha")])
        self.assertNotIn('<button type="submit" name="tpage"', self.send_page())

    def test_the_last_tpage_in_the_url_is_the_page_asked_for(self):
        fetch_page = self.template_stubs([self.trow("alpha")])
        self.send_page("?tpage=1&tpage=3")
        self.assertEqual(fetch_page.call_args.args[1], 3)

    def test_a_bad_page_number_means_page_one(self):
        fetch_page = self.template_stubs([self.trow("alpha")])
        self.send_page("?tpage=abc")
        self.assertEqual(fetch_page.call_args.args[1], 1)

    def test_only_an_unused_template_has_a_description_editor(self):
        self.template_stubs([self.trow("fresh", 7, sends=0), self.trow("used", 8, sends=2)])
        text = self.send_page()
        self.assertIn('<form id="desc-7"', text)
        self.assertIn('form="desc-7"', text)
        self.assertNotIn('id="desc-8"', text)
        self.assertNotIn('form="desc-8"', text)

    def test_the_description_form_carries_the_page_state(self):
        self.template_stubs([self.trow("fresh", 7)])
        text = self.send_page("?day=2026-09-19&template=fresh&tpage=1")
        form = text.split('<form id="desc-7"')[1].split("</form>")[0]
        self.assertIn('name="csrf_token"', form)
        self.assertIn('name="day" value="2026-09-19"', form)
        self.assertIn('name="template" value="fresh"', form)
        self.assertIn('name="tpage" value="1"', form)
        self.assertIn("/send/templates/7/description", form)

    def test_each_row_has_its_own_sample(self):
        self.template_stubs([self.trow("alpha", 1), self.trow("beta", 2)])
        text = self.send_page()
        self.assertIn("MARKER-alpha", text)
        self.assertIn("MARKER-beta", text)
        self.assertEqual(text.count("<summary>Show sample</summary>"), 2)

    def test_a_template_that_cannot_render_says_why_and_the_others_still_show(self):
        self.template_stubs([self.trow("alpha", 1, body="<p>{{ nope }}</p>[[trackingImage]]"),
                             self.trow("beta", 2)])
        text = self.send_page()
        self.assertIn("The sample could not be built", text)
        self.assertIn("MARKER-beta", text)

    def test_a_fresh_database_still_offers_the_built_in(self):
        self.template_stubs([], total=0)
        text = self.send_page()
        self.assertIn('name="template_pick" value="hail_alert"', text)
        self.assertIn("not saved yet", text)

    def test_the_built_in_is_flagged_when_its_files_have_changed(self):
        self.template_stubs([self.trow("hail_alert", 1, body="an older saved body")])
        self.assertIn("files changed since this version", self.send_page())

    def test_the_built_in_is_not_flagged_when_it_matches_its_files(self):
        subject, body = screen.templatestore.load_files()
        self.template_stubs([self.trow("hail_alert", 1, subject=subject, body=body)])
        self.assertNotIn("files changed since this version", self.send_page())

    def test_the_preview_sample_is_the_chosen_template_on_the_first_realtor(self):
        self.template_stubs([self.trow("hail_alert"), self.trow("beta", 2)])
        found = {"realtors": [{"realtor_id": 1, "email": "a@example.com",
                               "agent_name": "Pat Q", "events": screen.SAMPLE_MATCHES}],
                 "considered": {"matches": 1, "realtors": 1},
                 "excluded": {r: {"matches": 0, "realtors": 0}
                              for r in screen.sendlist.REASONS}}
        with mock.patch.object(screen.sendlist, "build_send_list", return_value=found):
            text = self.send_page("?day=2026-09-19&template=beta")
        sample = text.split("<h3>Sample (first on the list)</h3>")[1]
        self.assertIn("Subject: <strong>Subject of beta</strong>", sample)
        self.assertIn("MARKER-beta", sample)

    def test_the_review_form_carries_the_chosen_template(self):
        self.template_stubs([self.trow("hail_alert"), self.trow("beta", 2)])
        found = {"realtors": [{"realtor_id": 1, "email": "a@example.com",
                               "agent_name": "Pat Q", "events": screen.SAMPLE_MATCHES}],
                 "considered": {"matches": 1, "realtors": 1},
                 "excluded": {r: {"matches": 0, "realtors": 0}
                              for r in screen.sendlist.REASONS}}
        with mock.patch.object(screen.sendlist, "build_send_list", return_value=found):
            text = self.send_page("?day=2026-09-19&template=beta")
        review = text.split("<h3>Review a send</h3>")[1]
        self.assertIn('name="template" value="beta"', review)

    # ---- the choice through review, confirm and start ----

    def test_a_fresh_database_can_still_send_the_built_in(self):
        start = self.stubs()
        self.template_stubs([], total=0)                   # no saved row for any template
        self.post("/send/start", expected="2", **self.FORM)
        start.assert_called_once()

    def test_confirm_shows_the_chosen_template_and_carries_it(self):
        self.stubs()
        self.template_stubs([self.trow("beta", 2, description="Beta note")])
        text = self.post("/send/confirm", **{**self.FORM, "template": "beta"}).get_data(as_text=True)
        self.assertIn("Template: <strong>beta</strong> &mdash; Beta note", text)
        self.assertIn('name="template" value="beta"', text)

    def test_start_hands_the_chosen_template_to_the_engine(self):
        start = self.stubs()
        self.template_stubs([self.trow("beta", 2)])
        self.post("/send/start", expected="2", **{**self.FORM, "template": "beta"})
        self.assertEqual(start.call_args.kwargs["template_name"], "beta")

    def test_start_uses_the_built_in_when_no_template_is_sent(self):
        start = self.stubs()
        self.post("/send/start", expected="2", **self.FORM)
        self.assertEqual(start.call_args.kwargs["template_name"], "hail_alert")

    def test_an_unknown_template_is_refused_at_confirm_and_at_start(self):
        start = self.stubs()
        self.template_stubs([self.trow("beta", 2)])
        for path in ("/send/confirm", "/send/start"):
            text = self.post(path, expected="2", **{**self.FORM, "template": "nope"}
                             ).get_data(as_text=True)
            self.assertIn("That template is not available.", text, path)
        start.assert_not_called()

    def test_a_refused_form_sends_the_chosen_template_back_to_the_page(self):
        self.stubs()
        self.template_stubs([self.trow("beta", 2)])
        self.app.config["WTF_CSRF_ENABLED"] = False
        resp = self.client_as("admin").post(
            "/send/confirm", data={**self.FORM, "allowed": "", "template": "beta"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("template=beta", resp.headers["Location"])

    # ---- starting a drip campaign from the confirm screen (item 204, 6.2b) ----

    def campaign_stubs(self, followups=3, gap=14, names=("beta", "second")):
        """The drip limits set, and the template lookups replaced.  Returns the start stub."""
        start = self.stubs()
        rows = [self.trow(name, i + 2) for i, name in enumerate(names)]
        self.template_stubs(rows)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(screen.templatelist, "fetch_all", return_value=rows))
        real = fetch_settings
        stack.enter_context(mock.patch(
            "hailsys.web.auth.fetch_settings",
            side_effect=lambda conn: {**real(conn), "drip_max_followups": followups,
                                      "drip_max_gap_days": gap}))
        return start

    CAMPAIGN = {"campaign": "1", "campaign_name": "Storm test", "gap_1": "7",
                "template_1": "hail_alert", "gap_2": "14", "template_2": "beta"}

    def start_with(self, **over):
        data = {**self.FORM, **self.CAMPAIGN, **over}
        data = {k: v for k, v in data.items() if v is not None}
        return self.post("/send/start", expected="2", **data).get_data(as_text=True)

    def test_confirm_offers_one_row_per_allowed_follow_up(self):
        self.campaign_stubs(followups=3)
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertIn("Follow-ups (optional)", text)
        for n in (1, 2, 3):
            self.assertIn(f'name="gap_{n}"', text)
            self.assertIn(f'name="template_{n}"', text)
        self.assertNotIn('name="gap_4"', text)

    def test_confirm_limits_the_gap_box_to_the_admin_limit(self):
        self.campaign_stubs(gap=21)
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertIn('name="gap_1" min="1" max="21"', text)
        self.assertIn("up to 21 days", text)

    def test_confirm_names_the_campaign_after_the_storm_day(self):
        self.campaign_stubs()
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertIn('name="campaign_name" maxlength="100" size="40"\n'
                      '                   value="Storm 2026-09-19"', text)

    def test_confirm_names_a_campaign_over_several_days_by_its_first_and_last(self):
        self.campaign_stubs()
        text = self.post("/send/confirm", **{**self.FORM, "day": ["2026-09-22", "2026-09-19"]}
                         ).get_data(as_text=True)
        self.assertIn('value="Storm 2026-09-19 to 2026-09-22"', text)

    def test_confirm_lists_the_built_in_then_the_active_templates_in_every_row(self):
        self.campaign_stubs(followups=2)
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        row = text[text.index('name="template_1"'):text.index('name="template_2"')]
        self.assertLess(row.index(">hail_alert<"), row.index(">beta<"))
        self.assertLess(row.index(">beta<"), row.index(">second<"))

    def test_the_built_in_is_listed_once_even_when_it_has_a_saved_version(self):
        self.campaign_stubs(followups=1, names=("hail_alert", "beta"))
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        row = text[text.index('name="template_1"'):text.index("</select>", text.index('name="template_1"'))]
        self.assertEqual(row.count(">hail_alert<"), 1)
        self.assertEqual(row.count(">beta<"), 1)

    def test_the_template_list_is_not_read_when_campaigns_are_off(self):
        self.campaign_stubs(followups=0)
        with mock.patch.object(screen.templatelist, "fetch_all") as fetch_all:
            self.post("/send/confirm", **self.FORM)
        fetch_all.assert_not_called()

    def test_confirm_preselects_the_template_chosen_for_the_first_email(self):
        self.campaign_stubs()
        text = self.post("/send/confirm", **{**self.FORM, "template": "beta"}
                         ).get_data(as_text=True)
        self.assertIn('<option value="beta" selected>beta</option>', text)
        self.assertNotIn('<option value="second" selected>', text)

    def test_confirm_hides_the_part_when_campaigns_are_turned_off(self):
        self.campaign_stubs(followups=0)
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        self.assertNotIn("Follow-ups (optional)", text)
        self.assertNotIn('name="campaign"', text)

    def test_confirm_keeps_the_part_inside_the_start_form(self):
        self.campaign_stubs()
        text = self.post("/send/confirm", **self.FORM).get_data(as_text=True)
        form_start = text.index('action="/send/start"')
        form_end = text.index("</form>", form_start)
        self.assertGreater(text.index('name="campaign"'), form_start)
        self.assertLess(text.index('name="gap_1"'), form_end)

    def test_confirm_does_not_start_or_create_anything(self):
        start = self.campaign_stubs()
        before = self.counts()
        self.post("/send/confirm", **{**self.FORM, **self.CAMPAIGN})
        start.assert_not_called()
        self.assertEqual(self.counts(), before)

    def test_start_without_the_box_ticked_hands_the_engine_no_campaign(self):
        start = self.campaign_stubs()
        self.start_with(campaign=None)
        self.assertIsNone(start.call_args.kwargs["campaign"])

    def test_start_hands_the_engine_the_parsed_plan(self):
        start = self.campaign_stubs()
        self.start_with()
        self.assertEqual(start.call_args.kwargs["campaign"], {
            "name": "Storm test",
            "steps": [{"gap_days": 7, "template_name": "hail_alert"},
                      {"gap_days": 14, "template_name": "beta"}]})

    def test_start_still_hands_over_the_rest_of_the_send(self):
        start = self.campaign_stubs()
        self.start_with()
        kw = start.call_args.kwargs
        self.assertEqual((kw["allowed_emails"], kw["max_emails"], kw["template_name"]),
                         (["a@example.com", "b@example.com"], 5, "hail_alert"))

    def test_the_message_names_the_campaign_and_counts_its_follow_ups(self):
        self.campaign_stubs()
        text = self.start_with()
        self.assertIn("Campaign &#39;Storm test&#39; with 2 follow-ups is created with it.", text)

    def test_one_follow_up_reads_in_the_singular(self):
        self.campaign_stubs()
        text = self.start_with(gap_2=None, template_2=None)
        self.assertIn("with 1 follow-up is created", text)

    def test_no_campaign_message_without_a_campaign(self):
        self.campaign_stubs()
        self.assertNotIn("Campaign &#39;", self.start_with(campaign=None))

    def test_a_long_name_is_cut_in_the_message(self):
        self.campaign_stubs()
        text = self.start_with(campaign_name="N" * 90)
        self.assertIn("N" * 60, text)
        self.assertNotIn("N" * 61, text)

    def refused_at_start(self, expect, **over):
        start = self.campaign_stubs()
        text = self.start_with(**over)
        self.assertIn(expect, text)
        start.assert_not_called()

    def test_a_gap_over_the_admin_limit_starts_nothing(self):
        self.refused_at_start("follow-up 2 must be a whole number of days from 1 to 14",
                              gap_2="15")

    def test_follow_up_rows_out_of_order_start_nothing(self):
        self.refused_at_start("Fill the follow-ups in order", gap_1=None, template_1=None)

    def test_a_ticked_box_with_no_gaps_starts_nothing(self):
        self.refused_at_start("at least one follow-up", gap_1=None, gap_2=None)

    def test_a_retired_template_starts_nothing(self):
        self.refused_at_start("template for follow-up 2 is not available", template_2="retired")

    def test_a_missing_name_starts_nothing(self):
        self.refused_at_start("Give the campaign a name", campaign_name="")

    def test_a_campaign_while_campaigns_are_off_starts_nothing(self):
        start = self.campaign_stubs(followups=0)
        text = self.start_with()
        self.assertIn("turned off", text)
        start.assert_not_called()

    def test_a_refused_campaign_is_reported_before_the_list_count_check(self):
        start = self.campaign_stubs()
        text = self.post("/send/start", expected="99",
                         **{**self.FORM, **self.CAMPAIGN, "gap_1": "99"}).get_data(as_text=True)
        self.assertIn("whole number of days", text)
        start.assert_not_called()

    def test_a_raised_admin_limit_allows_the_longer_gap(self):
        start = self.campaign_stubs(gap=30)
        self.start_with(gap_2="30")
        self.assertEqual(start.call_args.kwargs["campaign"]["steps"][1]["gap_days"], 30)


if __name__ == "__main__":
    unittest.main()
