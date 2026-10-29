"""Tests for scripts/radar_day.py's control flow -- no network, no database.

    python3 -m unittest tests.test_radar_day        (needs the app image's packages; see README)

The connection, NOAA, the radar builders and the likelihood run are stubbed, so these pin only what
the script decides:  a --refetch deletes the day and then runs one manual likelihood run for exactly
that day, after the radar work has committed (item 276); a failed likelihood run makes the exit
status 1; without --refetch there is no likelihood run.
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

MISSING = []
try:
    import radar_day
except ImportError as exc:      # psycopg and the radar decoder live in the app image
    MISSING.append(exc.name)

DAY = date(1999, 6, 8)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class RadarDayTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        conn = mock.MagicMock()
        conn.commit.side_effect = lambda: self.calls.append("commit")
        connect = mock.MagicMock()
        connect.return_value.__enter__.return_value = conn
        built = {"set_id": 1, "cells_over_threshold": 0, "patches": 0, "patches_anchored": 0,
                 "anchor_reports": 0, "areas": {}, "warnings_used": 0}
        for target, kw in (
                ("get_connection", {"new": connect}),
                ("load.start_run", {"return_value": 7}),
                ("load.finish_run", {}),
                ("load.crop_box", {}),
                ("load.delete_day", {"side_effect": lambda c, d: self.calls.append(("delete", d))}),
                ("load.load_day", {"return_value": "loaded"}),
                ("warnings.ensure_day", {"return_value": "held"}),
                ("zones.build_day", {"return_value": built})):
            patcher = mock.patch.object(radar_day, target, **kw) if "." not in target else \
                mock.patch(f"radar_day.{target}", **kw)
            patcher.start()
        self.run = mock.patch.object(radar_day.likelihood_ingest, "run", side_effect=self.likelihood).start()
        self.result = {"built": 1, "skipped": 0, "failed": [], "run_id": 1}
        self.addCleanup(mock.patch.stopall)

    def likelihood(self, conn, mode, days, tz, **kw):
        self.calls.append(("likelihood", mode, tuple(days)))
        return self.result

    def main(self, *flags):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            return radar_day.main([DAY.isoformat(), *flags])

    def test_a_refetch_rebuilds_the_likelihood_day_after_the_radar_commit(self):
        self.assertEqual(self.main("--refetch"), 0)
        self.assertEqual(self.calls[0], "commit")                      # the radar run row
        self.assertIn(("delete", DAY), self.calls)
        likelihood = self.calls.index(("likelihood", "manual", (DAY,)))
        self.assertEqual(self.calls[likelihood - 1], "commit")         # the radar work, before it
        self.assertEqual(self.run.call_count, 1)

    def test_a_failed_likelihood_rebuild_makes_the_exit_status_1(self):
        self.result = {"built": 0, "skipped": 0, "failed": [(DAY, "LikelihoodError: boom")], "run_id": 1}
        self.assertEqual(self.main("--refetch"), 1)

    def test_a_skipped_likelihood_day_is_not_a_failure(self):
        self.result = {"built": 0, "skipped": 1, "failed": [], "run_id": 1}
        self.assertEqual(self.main("--refetch"), 0)

    def test_without_refetch_there_is_no_likelihood_run(self):
        for flags in ((), ("--rebuild",)):
            with self.subTest(flags=flags):
                self.assertEqual(self.main(*flags), 0)
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
