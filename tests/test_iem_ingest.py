"""Tests for scripts/iem_ingest.py -- the nightly's window computation and
argument handling.  Shared machinery is covered in test_iem_common.py.

Run from the repo root:
    python3 -m unittest discover
"""

import contextlib
import io
import sys
import types
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))

import iem_ingest  # noqa: E402  (import follows the sys.path edit above)
from iem_ingest import DEFAULT_HOURS, SECONDS_PER_HOUR, parse_args  # noqa: E402


class TestParseArgs(unittest.TestCase):
    def test_default_is_thirty_hours(self):
        self.assertEqual(parse_args([]).hours, DEFAULT_HOURS)
        self.assertEqual(DEFAULT_HOURS, 30)

    def test_custom_hours(self):
        self.assertEqual(parse_args(["--hours", "72"]).hours, 72)

    def test_zero_hours_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--hours", "0"])

    def test_negative_hours_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--hours", "-5"])


class TestWindow(unittest.TestCase):
    """recent= is what IEM actually filters on, computed from ITS clock; the
    two timestamps main() builds are only this run's best account of the window
    for ingest_runs.  They still have to be consistent with --hours, and there
    is no month chunking -- 30h of Colorado is a few hundred rows."""

    def capture(self, argv):
        seen = {}

        def fake_perform_run(mode, window_start, window_end, windows):
            seen.update(
                mode=mode, start=window_start, end=window_end,
                windows=list(windows),
            )
            return 0

        original = iem_ingest.perform_run
        iem_ingest.perform_run = fake_perform_run
        self.addCleanup(setattr, iem_ingest, "perform_run", original)
        iem_ingest.main(argv)
        return seen

    def test_window_span_matches_hours_and_mode_is_nightly(self):
        seen = self.capture(["--hours", "30"])
        self.assertEqual(seen["end"] - seen["start"], timedelta(hours=30))
        self.assertEqual(seen["mode"], "nightly")

    def test_one_window_with_a_recent_url_matching_the_span(self):
        seen = self.capture(["--hours", "30"])
        self.assertEqual(len(seen["windows"]), 1)
        chunk_start, chunk_end, url = seen["windows"][0]
        self.assertIn("recent=108000", url)
        self.assertEqual((chunk_start, chunk_end), (seen["start"], seen["end"]))

    def test_seconds_conversion(self):
        self.assertEqual(DEFAULT_HOURS * SECONDS_PER_HOUR, 108000)
        self.assertEqual(72 * SECONDS_PER_HOUR, 259200)


if __name__ == "__main__":
    unittest.main()
