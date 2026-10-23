"""Tests for hailsys/cocorahs/fetch.py -- one request to the CoCoRaHS hail export (Phase 5c, step 1a).

No database and no network: the opener and the sleep are stubbed.

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_cocorahs_fetch

What they pin:  the URL asks for hail, csv, one state, a date type the export has and UTC times, with US style dates;
a bad argument is refused before any request;  the request names whoever runs it (the contact setting) in its User-Agent,
and refuses to go out with none;  a good answer is returned as text, after a pause;  a server error, a timeout or an
answer that is not the export is tried again a limited number of times and then raised, loudly;  a client error is not
tried again;  and the wait grows between tries.
"""

import sys
import unittest
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.cocorahs import fetch
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

HEADER = ("ObservationDate,ObservationTime,EntryDateTime,StationNumber,StationName,Latitude,Longitude,SmallestSize,"
          "AverageSize,LargestSize,DurationMinutes,DurationAccuracy,Timing,StoneConsistency,MoreRainThanHail,HailStarted,"
          "LargestHailStarted,MoreRainThanHail,Damage,AngleOfImpact,NumberOfStonesOnPad,DistanceBtwnStonesOnPad,"
          "DepthOnGround,DateTimeStamp")
GOOD = (HEADER + "\r\n2024-05-31, 03:00 AM, 2024-05-31 03:31 AM, CO-BO-285, Erie 1.7 WNW, 40.04, -105.07, 0.250, 0.375, "
        "0.500, 12, , , , False, , , False, , , , , , 2024-05-31 03:31 AM\r\n").encode()


class Response:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Stands in for urlopen:  each call takes the next scripted outcome, a body (bytes) or an exception to raise."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return Response(out)


def http_error(code):
    return urllib.error.HTTPError("https://data.cocorahs.org/x", code, "x", {}, None)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class FetchTest(unittest.TestCase):
    def go(self, *outcomes, **kw):
        self.opener = Opener(*outcomes)
        self.sleeps = []
        args = dict(state="CO", date_type="reportdate", start=date(2024, 5, 1), end=date(2024, 5, 31),
                    contact="hail desk, 555-0100", opener=self.opener, sleep=self.sleeps.append)
        args.update(kw)
        return fetch.fetch_hail(**args)

    def query(self, index=0):
        req = self.opener.calls[index][0]
        return urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)

    # -- the request ------------------------------------------------------------------------

    def test_the_url_asks_for_hail_csv_one_state_and_utc_times(self):
        self.go(GOOD)
        req = self.opener.calls[0][0]
        self.assertTrue(req.full_url.startswith("https://data.cocorahs.org/cocorahs/export/exportreports.aspx?"))
        q = self.query()
        self.assertEqual(q["ReportType"], ["Hail"])
        self.assertEqual(q["Format"], ["csv"])
        self.assertEqual(q["State"], ["CO"])
        self.assertEqual(q["ReportDateType"], ["reportdate"])
        self.assertEqual(q["TimesInGMT"], ["True"])

    def test_dates_are_month_day_year_with_no_padding(self):
        self.go(GOOD, start=date(2024, 5, 1), end=date(2024, 12, 9))
        q = self.query()
        self.assertEqual((q["StartDate"], q["EndDate"]), (["5/1/2024"], ["12/9/2024"]))

    def test_the_timestamp_date_type_is_passed_through(self):
        self.go(GOOD, date_type="timestamp")
        self.assertEqual(self.query()["ReportDateType"], ["timestamp"])

    def test_a_one_day_window_is_allowed(self):
        self.go(GOOD, start=date(2024, 5, 30), end=date(2024, 5, 30))
        q = self.query()
        self.assertEqual(q["StartDate"], q["EndDate"])

    def test_bad_arguments_are_refused_before_any_request(self):
        bad = ({"state": "Colorado"}, {"state": "co"}, {"state": ""}, {"date_type": "entrydate"},
               {"start": date(2024, 6, 1), "end": date(2024, 5, 1)}, {"contact": ""}, {"contact": "   "})
        for kw in bad:
            with self.subTest(kw=kw):
                opener = Opener(GOOD)
                with self.assertRaises(ValueError):
                    fetch.fetch_hail(**{**dict(state="CO", date_type="reportdate", start=date(2024, 5, 1),
                                               end=date(2024, 5, 31), contact="c", opener=opener,
                                               sleep=lambda s: None), **kw})
                self.assertEqual(opener.calls, [])

    def test_the_user_agent_names_the_project_and_the_contact(self):
        self.go(GOOD)
        agent = self.opener.calls[0][0].get_header("User-agent")
        self.assertIn("hail-system", agent)
        self.assertIn("hail desk, 555-0100", agent)

    def test_the_request_has_a_timeout(self):
        self.go(GOOD)
        self.assertGreaterEqual(self.opener.calls[0][1], 30)

    # -- the answer -------------------------------------------------------------------------

    def test_a_good_answer_comes_back_as_text(self):
        text = self.go(GOOD)
        self.assertIsInstance(text, str)
        self.assertTrue(text.startswith("ObservationDate,"))
        self.assertIn("CO-BO-285", text)

    def test_a_byte_order_mark_does_not_hide_the_header(self):
        text = self.go(b"\xef\xbb\xbf" + GOOD)
        self.assertTrue(text.startswith("ObservationDate,"))

    def test_there_is_a_pause_after_a_good_answer(self):
        self.go(GOOD, pause=2.5)
        self.assertEqual(self.sleeps, [2.5])

    def test_an_answer_with_the_header_and_no_reports_is_fine(self):
        text = self.go((HEADER + "\r\n").encode())
        self.assertEqual(text.strip(), HEADER)

    # -- failures ---------------------------------------------------------------------------

    def test_a_server_error_is_tried_again_and_then_succeeds(self):
        text = self.go(http_error(503), GOOD)
        self.assertEqual(len(self.opener.calls), 2)
        self.assertIn("CO-BO-285", text)

    def test_a_timeout_or_network_error_is_tried_again(self):
        self.go(TimeoutError("slow"), urllib.error.URLError("reset"), GOOD)
        self.assertEqual(len(self.opener.calls), 3)

    def test_an_answer_that_is_not_the_export_is_tried_again(self):
        self.go(b"<html>Service unavailable</html>", GOOD)
        self.assertEqual(len(self.opener.calls), 2)

    def test_after_three_tries_it_raises_and_says_what_failed(self):
        with self.assertRaises(fetch.FetchError) as cm:
            self.go(http_error(500), http_error(502), http_error(503))
        self.assertEqual(len(self.opener.calls), 3)
        self.assertIn("CO", str(cm.exception))
        self.assertIn("5/1/2024", str(cm.exception))

    def test_an_answer_that_is_never_the_export_raises(self):
        junk = b"<html>nope</html>"
        with self.assertRaises(fetch.FetchError):
            self.go(junk, junk, junk)

    def test_a_client_error_is_not_tried_again(self):
        for code in (400, 403, 404):
            with self.subTest(code=code):
                with self.assertRaises(fetch.FetchError):
                    self.go(http_error(code), GOOD)
                self.assertEqual(len(self.opener.calls), 1)

    def test_the_wait_grows_between_tries_and_there_is_no_pause_after_a_failure(self):
        with self.assertRaises(fetch.FetchError):
            self.go(http_error(500), http_error(500), http_error(500), pause=1.0)
        self.assertEqual(len(self.sleeps), 2)                 # between the three tries, none after the last
        self.assertLess(self.sleeps[0], self.sleeps[1])
        self.assertGreaterEqual(self.sleeps[0], 1)

    def test_the_failure_is_chained_to_its_cause(self):
        with self.assertRaises(fetch.FetchError) as cm:
            self.go(http_error(500), http_error(500), http_error(500))
        self.assertIsNotNone(cm.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
