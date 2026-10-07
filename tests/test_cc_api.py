"""Tests for hailsys/constantcontact/api.py -- no network, no database.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_api

What they pin: no response body or token reaches a log line or exception, a POST
is never retried after an ambiguous failure, paging links cannot leave
/contacts, and a bulk activity is polled, not assumed.
"""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.constantcontact import api, oauth
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


def ok(body, status=200):
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(body).encode() if body is not None else b""
    resp.__enter__.return_value = resp
    return resp


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RequestTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(api, "_token", return_value="TOKEN-VALUE"),
                        mock.patch.object(api.time, "sleep"),
                        mock.patch.object(api, "MIN_INTERVAL", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_success_sends_agent_and_bearer_and_returns_parsed(self):
        with mock.patch("urllib.request.urlopen", return_value=ok({"a": 1})) as uo:
            status, doc = api.request("GET", "/contacts", query={"status": "all"})
        req = uo.call_args[0][0]
        self.assertEqual((status, doc), (200, {"a": 1}))
        self.assertEqual(req.get_header("User-agent"), oauth.USER_AGENT)
        self.assertEqual(req.get_header("Authorization"), "Bearer TOKEN-VALUE")
        self.assertTrue(req.full_url.endswith("/v3/contacts?status=all"))

    def test_an_expected_error_status_is_returned_not_raised(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(409)):
            status, doc = api.request("POST", "/contacts", body={},
                                      expect=(201, 409))
        self.assertEqual((status, doc), (409, None))

    def test_error_body_never_reaches_exception_or_log(self):
        body = (b'[{"error_key":"contacts.api.conflict",'
                b'"error_message":"LEAK TOKEN-VALUE"}]')
        with mock.patch("urllib.request.urlopen", side_effect=http_error(409, body)), \
             self.assertLogs(api.logger, level="ERROR") as logs:
            with self.assertRaises(api.ApiError) as cm:
                api.request("POST", "/contacts", body={})
        self.assertEqual(cm.exception.key, "contacts.api.conflict")
        for text in (str(cm.exception), "\n".join(logs.output)):
            self.assertNotIn("LEAK", text)
            self.assertNotIn("TOKEN-VALUE", text)

    def test_odd_error_key_is_not_trusted(self):
        body = b'{"error_key":"bad key; DROP"}'
        self.assertEqual(api._error_key(http_error(400, body)), "unknown")

    def test_throttled_is_retried_for_any_method(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[http_error(429, b'{"error_key":"throttled"}'),
                                     ok({})]) as uo:
            api.request("POST", "/emails", body={})
        self.assertEqual(uo.call_count, 2)

    def test_quota_exceeded_is_not_retried(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[http_error(429, b'{"error_key":"quota_exceeded"}')]) as uo:
            with self.assertRaises(api.ApiError):
                api.request("GET", "/contacts")
        self.assertEqual(uo.call_count, 1)

    def test_a_post_is_not_retried_after_a_5xx(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(503)]) as uo:
            with self.assertRaises(api.ApiError):
                api.request("POST", "/emails", body={})
        self.assertEqual(uo.call_count, 1)

    def test_a_get_is_retried_after_a_5xx(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[http_error(503), ok({})]) as uo:
            api.request("GET", "/contacts")
        self.assertEqual(uo.call_count, 2)

    def test_a_post_is_not_retried_after_a_network_error(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[urllib.error.URLError("boom")]) as uo:
            with self.assertRaises(api.ApiError):
                api.request("POST", "/emails", body={})
        self.assertEqual(uo.call_count, 1)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ActivityTest(unittest.TestCase):
    def test_polls_until_completed(self):
        answers = [(200, {"state": "initialized"}),
                   (200, {"state": "completed", "activity_errors": []})]
        with mock.patch.object(api, "request", side_effect=answers) as req:
            doc = api.wait_for_activity("act", sleep=lambda s: None)
        self.assertEqual(doc["state"], "completed")
        self.assertEqual(req.call_count, 2)

    def test_gives_up_when_it_does_not_finish(self):
        with mock.patch.object(api, "request",
                               return_value=(200, {"state": "initialized"})):
            with self.assertRaises(api.ApiError):
                api.wait_for_activity("act", timeout=0, sleep=lambda s: None)

    def test_activity_errors_raise(self):
        answer = (200, {"state": "completed", "activity_errors": [{"x": 1}]})
        with mock.patch.object(api, "request", return_value=answer):
            with self.assertRaises(api.ApiError):
                api.wait_for_activity("act", sleep=lambda s: None)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class PagingTest(unittest.TestCase):
    def test_follows_the_next_link(self):
        answers = [
            (200, {"contacts": [{"n": 1}],
                   "_links": {"next": {"href": "/v3/contacts?cursor=abc"}}}),
            (200, {"contacts": [{"n": 2}], "_links": None}),
        ]
        with mock.patch.object(api, "request", side_effect=answers) as req:
            got = list(api.iter_unsubscribed(updated_after="2026-10-07T00:00:00Z"))
        self.assertEqual(got, [{"n": 1}, {"n": 2}])
        self.assertEqual(req.call_args_list[1],
                         mock.call("GET", "/contacts?cursor=abc", query=None))

    def test_the_first_request_carries_the_filters(self):
        with mock.patch.object(api, "request",
                               return_value=(200, {"contacts": []})) as req:
            list(api.iter_unsubscribed(updated_after="T", limit=5))
        self.assertEqual(req.call_args.kwargs["query"],
                         {"status": "unsubscribed", "limit": 5, "updated_after": "T"})

    def test_a_link_outside_contacts_is_refused(self):
        for href in ("/v3/account/summary", "https://evil.example/contacts"):
            answer = (200, {"contacts": [],
                            "_links": {"next": {"href": href}}})
            with mock.patch.object(api, "request", return_value=answer):
                with self.assertRaises(api.ApiError):
                    list(api.iter_unsubscribed())


if __name__ == "__main__":
    unittest.main()