"""Tests for hailsys/rentcast/client.py's zip search (search_sale_listings and _get) -- no network, no database.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_rentcast_zip

Parking-lot item 228: the zip search had no unit tests; it was verified by real pulls only.  What they pin:  the
query sent (zip, status, a 500 page, offset, daysOld only when given, the key in a header);  paging (a short page
ends it, a full page asks for the next, an empty page after a full one ends it);  a 404 is an empty zip, not an
error;  429/500/503/504 and an unreachable host are retried with doubling back-off up to MAX_RETRIES, then raise;
401/403 and 400/405 raise at once;  an unexpected status raises;  an answer that is not a list, or cannot be read,
raises and is not retried;  every error carries the calls already made, earlier pages included, because
api_call_log records them and they were billed;  and a missing key makes no request.
"""

import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.rentcast import client
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

ZIP = "80014"
KEY = "KEY-VALUE"
PAGE = 500


def ok(body, status=200):
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


def page(n, start=0):
    return [{"id": f"L{start + i}"} for i in range(n)]


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b'{"message": "no"}'))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ZipSearchTest(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        mock.patch.object(client, "_throttle").start()
        mock.patch.object(client.time, "sleep", side_effect=self.sleeps.append).start()
        mock.patch.dict(os.environ, {"RENTCAST_KEY": KEY}).start()
        self.urlopen = mock.patch.object(client.urllib.request, "urlopen").start()
        self.addCleanup(mock.patch.stopall)

    def answers(self, *outcomes):
        self.urlopen.side_effect = list(outcomes)

    def queries(self):
        """The query string of every request made, in order."""
        out = []
        for call in self.urlopen.call_args_list:
            url = call.args[0].full_url
            self.assertTrue(url.startswith(client.BASE_URL + "?"), url)
            out.append(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)))
        return out

    def fails(self, exc_type, **kw):
        with self.assertRaises(exc_type) as caught:
            client.search_sale_listings(ZIP, **kw)
        return caught.exception

    # ---- the request ----

    def test_one_short_page_is_one_call_with_the_expected_query(self):
        self.answers(ok(page(3)))
        listings, calls = client.search_sale_listings(ZIP)
        self.assertEqual((len(listings), calls), (3, 1))
        self.assertEqual(self.queries(), [{"zipCode": ZIP, "status": "Active", "limit": "500", "offset": "0"}])
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-api-key"), KEY)
        self.assertEqual(self.sleeps, [])

    def test_days_old_and_status_are_sent_when_given(self):
        self.answers(ok(page(1)))
        client.search_sale_listings(ZIP, status="Inactive", days_old=7)
        self.assertEqual(self.queries()[0]["daysOld"], "7")
        self.assertEqual(self.queries()[0]["status"], "Inactive")

    def test_a_missing_key_makes_no_request(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.fails(client.RentCastAuthError)
        self.assertEqual(self.urlopen.call_count, 0)

    # ---- paging ----

    def test_a_full_page_asks_for_the_next_and_a_short_one_ends_it(self):
        self.answers(ok(page(PAGE)), ok(page(31, PAGE)))
        listings, calls = client.search_sale_listings(ZIP)
        self.assertEqual((len(listings), calls), (PAGE + 31, 2))
        self.assertEqual([q["offset"] for q in self.queries()], ["0", "500"])
        self.assertEqual(listings[PAGE]["id"], f"L{PAGE}")

    def test_an_empty_page_after_a_full_one_ends_it(self):
        self.answers(ok(page(PAGE)), ok([]))
        listings, calls = client.search_sale_listings(ZIP)
        self.assertEqual((len(listings), calls), (PAGE, 2))

    def test_a_404_is_an_empty_zip_not_an_error(self):
        self.answers(http_error(404))
        self.assertEqual(client.search_sale_listings(ZIP), ([], 1))

    # ---- retries ----

    def test_a_retryable_status_is_retried_and_counted(self):
        for code in sorted(client.RETRYABLE_STATUSES):
            with self.subTest(code=code):
                self.sleeps.clear()
                self.answers(http_error(code), ok(page(2)))
                listings, calls = client.search_sale_listings(ZIP)
                self.assertEqual((len(listings), calls), (2, 2))
                self.assertEqual(self.sleeps, [client.RETRY_BACKOFF_BASE])

    def test_retries_stop_after_max_retries_with_doubling_back_off(self):
        self.answers(*[http_error(503)] * (client.MAX_RETRIES + 1))
        exc = self.fails(client.RentCastServerError)
        self.assertEqual((exc.attempts, exc.status), (client.MAX_RETRIES + 1, 503))
        self.assertEqual(self.sleeps, [client.RETRY_BACKOFF_BASE * 2 ** i for i in range(client.MAX_RETRIES)])
        self.assertIn("RentCast is temporarily unavailable", exc.user_message)

    def test_an_unreachable_host_is_retried_then_raises(self):
        self.answers(*[urllib.error.URLError("refused")] * (client.MAX_RETRIES + 1))
        exc = self.fails(client.RentCastConnectionError)
        self.assertEqual(exc.attempts, client.MAX_RETRIES + 1)
        self.assertIsNone(exc.status)

    # ---- no retry ----

    def test_auth_and_validation_errors_raise_at_once(self):
        for code, exc_type in ((401, client.RentCastAuthError), (403, client.RentCastAuthError),
                               (400, client.RentCastValidationError), (405, client.RentCastValidationError)):
            with self.subTest(code=code):
                self.urlopen.reset_mock()
                self.answers(http_error(code))
                exc = self.fails(exc_type)
                self.assertEqual((exc.attempts, exc.status, self.urlopen.call_count), (1, code, 1))
        self.assertEqual(self.sleeps, [])

    def test_an_unexpected_status_raises_the_base_error(self):
        self.answers(http_error(418))
        exc = self.fails(client.RentCastError)
        self.assertIs(type(exc), client.RentCastError)
        self.assertEqual((exc.attempts, exc.status), (1, 418))

    def test_an_answer_that_is_not_a_list_raises_with_its_status(self):
        self.answers(ok({"listings": []}))
        exc = self.fails(client.RentCastResponseError)
        self.assertEqual((exc.attempts, exc.status), (1, 200))

    def test_an_unreadable_answer_raises_without_a_status_and_is_not_retried(self):
        self.answers(ok(b"{not json"))
        exc = self.fails(client.RentCastResponseError)
        self.assertEqual((exc.attempts, exc.status, self.urlopen.call_count), (1, None, 1))

    # ---- calls already made ----

    def test_an_error_on_a_later_page_counts_the_earlier_calls(self):
        self.answers(ok(page(PAGE)), http_error(429), http_error(401))
        exc = self.fails(client.RentCastAuthError)
        self.assertEqual(exc.attempts, 3)          # page 1, then a retried page 2 that ended in 401


if __name__ == "__main__":
    unittest.main()
