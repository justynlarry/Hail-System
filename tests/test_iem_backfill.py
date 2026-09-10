"""Tests for scripts/iem_backfill.py -- the parts specific to a historical
range.  The shared machinery is covered in test_iem_common.py.

Run from the repo root:
    python3 -m unittest discover
"""

import argparse
import contextlib
import io
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))

from iem_backfill import (  # noqa: E402  (imports follow the sys.path edit above)
    ARCHIVE_FLOOR,
    iso_date,
    month_windows,
    parse_args,
)


def d(year, month, day=1):
    return datetime(year, month, day, tzinfo=timezone.utc)


class TestMonthWindows(unittest.TestCase):
    """One sub-window per calendar month, half-open and contiguous, now
    carrying the URL as a third element -- the (start, end, url) shape
    perform_run iterates.  Five years in one GET either finishes inside
    HTTP_TIMEOUT or loses everything; a month retries cheaply."""

    def spans(self, start, end):
        return [
            (s.date().isoformat(), e.date().isoformat())
            for s, e, _ in month_windows(start, end)
        ]

    def test_single_sub_month_window(self):
        w = list(month_windows(d(2021, 5, 8), d(2021, 5, 9)))
        self.assertEqual(len(w), 1)
        self.assertEqual((w[0][0], w[0][1]), (d(2021, 5, 8), d(2021, 5, 9)))

    def test_yields_start_end_and_url(self):
        (start, end, url), = month_windows(d(2021, 5, 8), d(2021, 5, 9))
        self.assertIsInstance(url, str)
        self.assertIn("sts=", url)
        self.assertIn("ets=", url)

    def test_splits_on_the_calendar_month(self):
        self.assertEqual(
            self.spans(d(2021, 1, 15), d(2021, 3, 10)),
            [
                ("2021-01-15", "2021-02-01"),
                ("2021-02-01", "2021-03-01"),
                ("2021-03-01", "2021-03-10"),
            ],
        )

    def test_december_to_january_rollover(self):
        self.assertEqual(
            self.spans(d(2021, 12, 1), d(2022, 1, 15)),
            [
                ("2021-12-01", "2022-01-01"),
                ("2022-01-01", "2022-01-15"),
            ],
        )

    def test_windows_are_contiguous_and_end_stays_exclusive(self):
        w = list(month_windows(d(2020, 11, 15), d(2021, 2, 1)))
        for (_, end, _), (nxt, _, _) in zip(w, w[1:]):
            self.assertEqual(end, nxt)  # no gap, no overlap
        self.assertEqual(w[0][0], d(2020, 11, 15))
        self.assertEqual(w[-1][1], d(2021, 2, 1))

    def test_whole_month_boundary_to_boundary_is_one_window(self):
        w = list(month_windows(d(2021, 6, 1), d(2021, 7, 1)))
        self.assertEqual(len(w), 1)


class TestIsoDate(unittest.TestCase):
    def test_returns_an_aware_utc_datetime(self):
        got = iso_date("2004-01-01")
        self.assertEqual(got, d(2004, 1, 1))
        self.assertIs(got.tzinfo, timezone.utc)

    def test_rejects_a_non_date(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            iso_date("not-a-date")

    def test_rejects_the_wrong_separator(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            iso_date("2004/01/01")


class TestParseArgs(unittest.TestCase):
    def test_end_after_start_is_accepted(self):
        args = parse_args(["--start", "2004-01-01", "--end", "2004-02-01"])
        self.assertEqual(args.mode, "backfill")

    def test_end_equal_to_start_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--start", "2004-01-01", "--end", "2004-01-01"])

    def test_end_before_start_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--start", "2004-02-01", "--end", "2004-01-01"])

    def test_a_start_below_the_archive_floor_is_not_an_arg_error(self):
        # The floor drives a WARNING inside main(), not arg validation -- an
        # earlier --start is still honoured (decision-log 2026-09-10).
        args = parse_args(["--start", "1999-01-01", "--end", "2000-01-01"])
        self.assertLess(args.start, ARCHIVE_FLOOR)

    def test_replay_mode_is_accepted(self):
        args = parse_args(
            ["--start", "2026-08-10", "--end", "2026-09-10", "--mode", "replay"]
        )
        self.assertEqual(args.mode, "replay")


if __name__ == "__main__":
    unittest.main()
