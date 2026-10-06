"""Tests for hailsys/constantcontact/oauth.py -- no network, no database.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_oauth

What they pin: the token endpoint's answer never reaches a log line or an
exception, and a refreshed token is committed before it is handed back.
"""

import io
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.constantcontact import oauth, tokens
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
ENV = {"CC_API_KEY": "the-key", "CC_API_SECRET": "the-secret",
       "CC_REDIRECT_URI": "https://example.test/cc/callback"}


def grant(expires):
    return tokens.Grant(token_id=1, account_id="acct", scope="s",
                        access_expires_at=expires, created_at=NOW,
                        access_token="OLD-ACCESS", refresh_token="OLD-REFRESH")


def issued():
    return oauth.Issued(expires_at=NOW + timedelta(hours=24), scope="s",
                        access_token="NEW-ACCESS", refresh_token="NEW-REFRESH")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class UrlTest(unittest.TestCase):
    def test_authorize_url_carries_the_required_parameters(self):
        with mock.patch.dict(os.environ, ENV):
            url = oauth.authorize_url("STATE123")
        self.assertTrue(url.startswith(oauth.AUTHZ_URL + "?"))
        for part in ("client_id=the-key", "response_type=code", "state=STATE123",
                     "redirect_uri=https%3A%2F%2Fexample.test%2Fcc%2Fcallback",
                     "offline_access"):
            self.assertIn(part, url)
        self.assertNotIn("the-secret", url)

    def test_missing_setting_is_loud(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(oauth.OAuthError):
                oauth.authorize_url("s")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class LeakTest(unittest.TestCase):
    def test_error_body_never_reaches_exception_or_log(self):
        body = b'{"error":"invalid_grant","access_token":"LEAK","detail":"LEAK"}'
        err = urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(body))
        with mock.patch.dict(os.environ, ENV), \
             mock.patch("urllib.request.urlopen", side_effect=err), \
             self.assertLogs(oauth.logger, level="ERROR") as logs:
            with self.assertRaises(oauth.OAuthError) as cm:
                oauth.refresh_with("OLD-REFRESH")
        self.assertNotIn("LEAK", str(cm.exception))
        self.assertNotIn("OLD-REFRESH", str(cm.exception))
        self.assertEqual(cm.exception.code, "invalid_grant")
        self.assertNotIn("LEAK", "\n".join(logs.output))

    def test_odd_error_code_is_not_trusted(self):
        body = b'{"error":"Bad Thing; DROP TABLE"}'
        err = urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(body))
        self.assertEqual(oauth._error_code(err), "unknown")

    def test_okta_style_error_code_is_read(self):
        body = b'{"errorCode":"invalid_client","errorSummary":"x"}'
        err = urllib.error.HTTPError("u", 401, "Bad", {}, io.BytesIO(body))
        self.assertEqual(oauth._error_code(err), "invalid_client")

    def test_requests_carry_our_user_agent(self):
        seen = []

        def fake(req, timeout):
            seen.append(req)
            raise urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"{}"))

        with mock.patch.dict(os.environ, ENV), \
             mock.patch("urllib.request.urlopen", side_effect=fake):
            for call in (lambda: oauth.refresh_with("R"),
                         lambda: oauth.fetch_account_id("A")):
                with self.assertRaises(oauth.OAuthError):
                    call()
        self.assertEqual(len(seen), 2)
        for req in seen:
            self.assertEqual(req.get_header("User-agent"), oauth.USER_AGENT)

    def test_issued_repr_hides_tokens(self):
        self.assertNotIn("NEW-ACCESS", repr(issued()))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RefreshTest(unittest.TestCase):
    def run_refresh(self, current, **kw):
        events = []
        conn = mock.Mock()
        conn.execute.side_effect = lambda sql, *a: events.append("lock")
        conn.commit.side_effect = lambda: events.append("commit")
        conn.rollback.side_effect = lambda: events.append("rollback")
        patches = {
            "latest_token": mock.Mock(return_value=current),
            "save_token": mock.Mock(side_effect=lambda *a, **k: events.append("save")),
            "purge_old": mock.Mock(return_value=0),
        }
        with mock.patch.multiple(oauth.tokens, **patches), \
             mock.patch.object(oauth, "refresh_with",
                               side_effect=kw.get("refresh_side_effect",
                                                  lambda rt: (events.append("http"), issued())[1])) as http:
            try:
                result = oauth.get_access_token(
                    conn, force=kw.get("force", False), now=NOW)
            except oauth.OAuthError as exc:
                result = exc
        return result, events, http, patches

    def test_fresh_token_means_no_call(self):
        result, events, http, _ = self.run_refresh(grant(NOW + timedelta(hours=2)))
        self.assertEqual(result, "OLD-ACCESS")
        http.assert_not_called()
        self.assertEqual(events, [])

    def test_stale_token_is_saved_and_committed_before_it_is_returned(self):
        result, events, http, _ = self.run_refresh(grant(NOW + timedelta(minutes=1)))
        self.assertEqual(result, "NEW-ACCESS")
        self.assertEqual(events[:4], ["lock", "http", "save", "commit"])

    def test_force_refreshes_a_fresh_token(self):
        result, events, http, _ = self.run_refresh(
            grant(NOW + timedelta(hours=2)), force=True)
        self.assertEqual(result, "NEW-ACCESS")
        http.assert_called_once_with("OLD-REFRESH")

    def test_provider_failure_rolls_back_and_saves_nothing(self):
        boom = oauth.OAuthError("refused")
        result, events, _, patches = self.run_refresh(
            grant(NOW + timedelta(minutes=1)), refresh_side_effect=boom)
        self.assertIs(result, boom)
        patches["save_token"].assert_not_called()
        self.assertIn("rollback", events)

    def test_failed_save_is_critical_and_leaks_nothing(self):
        conn = mock.Mock()
        with mock.patch.object(oauth.tokens, "latest_token",
                               return_value=grant(NOW + timedelta(minutes=1))), \
             mock.patch.object(oauth, "refresh_with", return_value=issued()), \
             mock.patch.object(oauth.tokens, "save_token",
                               side_effect=RuntimeError("db down NEW-ACCESS")), \
             self.assertLogs(oauth.logger, level="CRITICAL") as logs:
            with self.assertRaises(oauth.OAuthError) as cm:
                oauth.get_access_token(conn, now=NOW)
        self.assertNotIn("NEW-ACCESS", str(cm.exception))
        self.assertNotIn("NEW-ACCESS", "\n".join(logs.output))
        conn.rollback.assert_called()


if __name__ == "__main__":
    unittest.main()