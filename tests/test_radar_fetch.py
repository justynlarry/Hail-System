"""Tests for hailsys.radar.fetch:  which file covers a day, and the retrying download."""

import io
import os
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from hailsys.radar import fetch

DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc


class TestStamp(unittest.TestCase):
    def test_summer_day_ends_at_0600_utc_next_day(self):
        self.assertEqual(fetch.day_stamp(date(2026, 6, 8), DENVER),
                         datetime(2026, 6, 9, 6, 0, tzinfo=UTC))

    def test_winter_day_ends_at_0700_utc_next_day(self):
        self.assertEqual(fetch.day_stamp(date(2026, 1, 15), DENVER),
                         datetime(2026, 1, 16, 7, 0, tzinfo=UTC))

    def test_day_that_ends_across_a_month_and_year(self):
        self.assertEqual(fetch.day_stamp(date(2025, 12, 31), DENVER),
                         datetime(2026, 1, 1, 7, 0, tzinfo=UTC))

    def test_the_spring_forward_day_is_23_hours_and_ends_at_0600(self):
        # 2026-03-08, clocks go forward at 02:00 local, midnight ending the day is MDT
        self.assertEqual(fetch.day_stamp(date(2026, 3, 8), DENVER),
                         datetime(2026, 3, 9, 6, 0, tzinfo=UTC))

    def test_the_zone_is_an_argument(self):
        self.assertEqual(fetch.day_stamp(date(2026, 6, 8), ZoneInfo("America/Chicago")),
                         datetime(2026, 6, 9, 5, 0, tzinfo=UTC))
        self.assertEqual(fetch.day_stamp(date(2026, 6, 8), UTC),
                         datetime(2026, 6, 9, 0, 0, tzinfo=UTC))


class TestKey(unittest.TestCase):
    def test_key_matches_noaa_layout(self):
        self.assertEqual(
            fetch.source_key(datetime(2026, 6, 9, 6, 0, tzinfo=UTC)),
            "CONUS/MESH_Max_1440min_00.50/20260609/MRMS_MESH_Max_1440min_00.50_20260609-060000.grib2.gz")


class _Response(io.BytesIO):
    def __init__(self, body, length="same"):
        super().__init__(body)
        self.headers = {} if length is None else {
            "Content-Length": str(len(body) if length == "same" else length)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    """Gives the listed outcomes in order: a response, or an exception to raise."""
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _http(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.dest = os.path.join(self.dir.name, "f.gz")
        self.sleeps = []

    def run_download(self, *outcomes, **kw):
        opener = _Opener(*outcomes)
        kw.setdefault("attempts", 3)
        size = fetch.download("http://x/f", self.dest, opener=opener,
                              sleep=self.sleeps.append, **kw)
        return size, opener

    def test_saves_the_file_and_leaves_no_part_file(self):
        size, _ = self.run_download(_Response(b"abc"))
        self.assertEqual(size, 3)
        self.assertEqual(open(self.dest, "rb").read(), b"abc")
        self.assertEqual(os.listdir(self.dir.name), ["f.gz"])

    def test_retries_a_connection_reset_then_succeeds(self):
        size, opener = self.run_download(ConnectionResetError("reset"), _Response(b"abc"))
        self.assertEqual((size, opener.calls), (3, 2))
        self.assertEqual(self.sleeps, [fetch.HTTP_BACKOFF])

    def test_backoff_grows_with_each_attempt(self):
        self.run_download(ConnectionResetError(), ConnectionResetError(), _Response(b"a"),
                          backoff=2)
        self.assertEqual(self.sleeps, [2, 4])

    def test_retries_a_server_error(self):
        _, opener = self.run_download(_http(503), _Response(b"abc"))
        self.assertEqual(opener.calls, 2)

    def test_a_short_body_is_retried_and_never_kept(self):
        _, opener = self.run_download(_Response(b"ab", length=3), _Response(b"abc"))
        self.assertEqual(opener.calls, 2)
        self.assertEqual(open(self.dest, "rb").read(), b"abc")

    def test_gives_up_after_the_last_attempt(self):
        with self.assertRaisesRegex(fetch.FetchError, "gave up after 3 attempts"):
            self.run_download(*[ConnectionResetError("reset")] * 3)
        self.assertEqual(len(self.sleeps), 2)         # no sleep after the last try
        self.assertFalse(os.path.exists(self.dest))

    def test_404_is_not_available_and_not_retried(self):
        with self.assertRaises(fetch.NotAvailable):
            self.run_download(_http(404), _Response(b"abc"))
        self.assertEqual(self.sleeps, [])

    def test_other_4xx_is_an_error_and_not_retried(self):
        with self.assertRaisesRegex(fetch.FetchError, "HTTP 403"):
            self.run_download(_http(403), _Response(b"abc"))
        self.assertEqual(self.sleeps, [])

    def test_no_content_length_is_accepted(self):
        size, _ = self.run_download(_Response(b"abc", length=None))
        self.assertEqual(size, 3)


class TestFetchDay(unittest.TestCase):
    def test_downloads_the_stamped_file_into_the_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            seen = []

            def opener(url, timeout=None):
                seen.append(url)
                return _Response(b"abc")

            path, stamp, key = fetch.fetch_day(date(2026, 6, 8), DENVER, folder, opener=opener)
            self.assertEqual(stamp, datetime(2026, 6, 9, 6, 0, tzinfo=UTC))
            self.assertEqual(seen, [f"{fetch.BUCKET}/{key}"])
            self.assertEqual(path, os.path.join(
                folder, "MRMS_MESH_Max_1440min_00.50_20260609-060000.grib2.gz"))
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
