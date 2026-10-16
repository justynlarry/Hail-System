"""Tests for hailsys/rentcast/client.py's get_sale_listing (lookup by id) -- no network, no database.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_rentcast_by_id

What they pin: one request per attempt and at most MAX_ATTEMPTS_BY_ID of them; a 404 is an error
and never an empty result; a blank id makes no request at all (the address without an id is the
search endpoint); no response body or key reaches a log line or an exception message; the answer
must be the listing that was asked for.
"""

import io
import json
import logging
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.rentcast import client
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

LISTING_ID = "4707-Justeagen-Dr,-Colorado-Springs,-CO-80911"
SECRET_BODY = "SECRET-BODY-TEXT"
KEY = "KEY-VALUE"


def ok(body, status=200):
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


def listing(**extra):
    return {"id": LISTING_ID, "status": "Active", "lastSeenDate": "2026-10-16T10:51:59.131Z",
            "removedDate": None, **extra}


class Collector(logging.Handler):
    """Keeps the text of every log line, so a test can look for what must never be in one."""
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def http_error(code):
    body = json.dumps({"message": SECRET_BODY}).encode()
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class GetSaleListingTest(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self.throttle = mock.patch.object(client, "_throttle").start()
        mock.patch.object(client.time, "sleep", side_effect=self.sleeps.append).start()
        mock.patch.dict(os.environ, {"RENTCAST_KEY": KEY}).start()
        self.urlopen = mock.patch.object(client.urllib.request, "urlopen").start()
        self.addCleanup(mock.patch.stopall)

    def answers(self, *outcomes):
        self.urlopen.side_effect = list(outcomes)

    def fails(self, exc_type, listing_id=LISTING_ID):
        with self.assertRaises(exc_type) as caught:
            client.get_sale_listing(listing_id)
        return caught.exception

    # ---- the good answer ----

    def test_a_good_answer_comes_back_with_one_attempt(self):
        self.answers(ok(listing()))
        found, attempts = client.get_sale_listing(LISTING_ID)
        self.assertEqual(found, listing())
        self.assertEqual(attempts, 1)
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_the_request_goes_to_the_id_with_the_key(self):
        self.answers(ok(listing()))
        client.get_sale_listing(LISTING_ID)
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{client.BASE_URL}/{LISTING_ID}")
        self.assertEqual(request.get_header("X-api-key"), KEY)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], client.TIMEOUT)

    def test_an_id_cannot_change_the_path_or_the_query(self):
        weird = "A B/C#1?x=y"
        self.answers(ok(listing(id=weird)))
        client.get_sale_listing(weird)
        url = self.urlopen.call_args.args[0].full_url
        self.assertEqual(url, f"{client.BASE_URL}/A%20B%2FC%231%3Fx%3Dy")

    def test_each_attempt_is_throttled(self):
        self.answers(http_error(503), http_error(503), ok(listing()))
        client.get_sale_listing(LISTING_ID)
        self.assertEqual(self.throttle.call_count, 3)

    # ---- no request at all ----

    def test_a_blank_id_makes_no_request(self):
        for blank in ("", "   ", "\n", None, 5, ["x"]):
            with self.subTest(blank=blank):
                with self.assertRaises(ValueError):
                    client.get_sale_listing(blank)
        self.urlopen.assert_not_called()

    def test_a_missing_key_makes_no_request(self):
        with mock.patch.dict(os.environ, clear=True):
            self.fails(client.RentCastAuthError)
        self.urlopen.assert_not_called()

    # ---- statuses that are never retried ----

    def test_a_404_is_its_own_error_and_is_not_retried(self):
        self.answers(http_error(404))
        error = self.fails(client.RentCastNotFound)
        self.assertEqual((error.status, error.attempts), (404, 1))
        self.assertEqual(self.urlopen.call_count, 1)

    def test_a_404_is_an_error_so_a_broad_handler_fails_closed(self):
        self.assertTrue(issubclass(client.RentCastNotFound, client.RentCastError))

    def test_auth_statuses_raise_at_once(self):
        for code in (401, 403):
            with self.subTest(code=code):
                self.urlopen.reset_mock()
                self.answers(http_error(code))
                error = self.fails(client.RentCastAuthError)
                self.assertEqual((error.status, error.attempts), (code, 1))
                self.assertEqual(self.urlopen.call_count, 1)

    def test_malformed_request_statuses_raise_at_once(self):
        for code in (400, 405):
            with self.subTest(code=code):
                self.urlopen.reset_mock()
                self.answers(http_error(code))
                error = self.fails(client.RentCastValidationError)
                self.assertEqual((error.status, error.attempts), (code, 1))
                self.assertEqual(self.urlopen.call_count, 1)

    def test_an_unlisted_status_is_a_plain_error_and_is_not_retried(self):
        self.answers(http_error(418))
        error = self.fails(client.RentCastError)
        self.assertIs(type(error), client.RentCastError)
        self.assertEqual((error.status, error.attempts), (418, 1))
        self.assertEqual(self.urlopen.call_count, 1)

    # ---- retries ----

    def test_a_retryable_status_is_tried_again(self):
        for code in sorted(client.RETRYABLE_STATUSES):
            with self.subTest(code=code):
                self.urlopen.reset_mock()
                self.sleeps.clear()
                self.answers(http_error(code), ok(listing()))
                found, attempts = client.get_sale_listing(LISTING_ID)
                self.assertEqual(attempts, 2)
                self.assertEqual(self.sleeps, [client.RETRY_BACKOFF_BASE])

    def test_retries_stop_at_the_cap_and_report_every_attempt(self):
        self.answers(*[http_error(429) for _ in range(5)])
        error = self.fails(client.RentCastServerError)
        self.assertEqual(client.MAX_ATTEMPTS_BY_ID, 3)
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual((error.status, error.attempts), (429, 3))
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.assertIn("rate-limiting", error.user_message)

    def test_the_cap_is_the_constant(self):
        with mock.patch.object(client, "MAX_ATTEMPTS_BY_ID", 2):
            self.answers(*[http_error(500) for _ in range(5)])
            error = self.fails(client.RentCastServerError)
        self.assertEqual(self.urlopen.call_count, 2)
        self.assertEqual(error.attempts, 2)

    def test_an_unreachable_server_is_tried_again_then_reported(self):
        self.answers(urllib.error.URLError("down"), urllib.error.URLError("down"), ok(listing()))
        found, attempts = client.get_sale_listing(LISTING_ID)
        self.assertEqual(attempts, 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.urlopen.reset_mock()
        self.answers(*[urllib.error.URLError("down") for _ in range(5)])
        error = self.fails(client.RentCastConnectionError)
        self.assertEqual((error.attempts, self.urlopen.call_count), (3, 3))

    # ---- an answer that cannot be used ----

    def test_an_unreadable_answer_is_not_retried(self):
        for label, outcome in (("not json", ok(b"not json")),
                               ("empty", ok(b"")),
                               ("timeout while reading", TimeoutError("slow"))):
            with self.subTest(label=label):
                self.urlopen.reset_mock()
                self.answers(outcome)
                error = self.fails(client.RentCastResponseError)
                self.assertEqual(error.attempts, 1)
                self.assertEqual(self.urlopen.call_count, 1)

    def test_the_answer_must_be_the_listing_asked_for(self):
        for label, body in (("a list", [listing()]), ("a string", "x"), ("null", None),
                            ("another id", listing(id="some-other-house")),
                            ("no id", {"status": "Active"}),
                            ("a different case", listing(id=LISTING_ID.lower()))):
            with self.subTest(label=label):
                self.urlopen.reset_mock()
                self.answers(ok(body))
                error = self.fails(client.RentCastResponseError)
                self.assertEqual((error.attempts, self.urlopen.call_count), (1, 1))

    def test_an_inactive_listing_is_a_normal_answer(self):
        # The probe of 2026-10-16: a removed listing is a 200 saying Inactive, not an error.
        gone = listing(status="Inactive", removedDate="2026-09-17T00:00:00.000Z")
        self.answers(ok(gone))
        found, attempts = client.get_sale_listing(LISTING_ID)
        self.assertEqual(found["status"], "Inactive")

    # ---- what is never written down ----

    def collect_logs(self):
        handler = Collector()
        client.logger.addHandler(handler)
        self.addCleanup(client.logger.removeHandler, handler)
        return handler.lines

    def test_no_response_body_or_key_reaches_a_log_line_or_a_message(self):
        lines = self.collect_logs()
        for code in (400, 401, 403, 404, 418, 503):
            with self.subTest(code=code):
                self.urlopen.reset_mock()
                self.answers(*[http_error(code) for _ in range(5)])
                error = self.fails(client.RentCastError)
                text = " ".join(lines) + f" {error} {error.user_message}"
                self.assertNotIn(SECRET_BODY, text)
                self.assertNotIn(KEY, text)
        self.assertTrue(lines)                      # the check above did look at something

    def test_an_unreadable_answer_leaks_nothing_either(self):
        lines = self.collect_logs()
        self.answers(ok(f"{SECRET_BODY} but not json".encode()))
        error = self.fails(client.RentCastResponseError)
        text = " ".join(lines) + f" {error} {error.user_message}"
        self.assertNotIn(SECRET_BODY, text)
        self.assertNotIn(KEY, text)
        self.assertTrue(lines)


if __name__ == "__main__":
    unittest.main()
