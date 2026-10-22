"""Tests for scripts/radar_ingest.py -- the arguments, the days each mode picks, and the exit status.

The orchestration is in test_radar_ingest.py; here the database and the job are stubbed.

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro -v ./scripts:/app/scripts:ro web \
        python -m unittest tests.test_radar_ingest_cli
"""

import contextlib
import io
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

try:
    import radar_ingest
    from hailsys.radar import ingest
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc
NOW = datetime(2026, 6, 9, 10, 30, tzinfo=UTC)          # the nightly time, latest finished day is 2026-06-08
LATEST = date(2026, 6, 8)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    """Answers the script's queries from canned rows, by a word in the SQL."""
    def __init__(self, held=(), first_report=None, years=()):
        self.held, self.first_report, self.years = set(held), first_report, years
        self.queries, self.commits, self.rollbacks = [], 0, 0

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if "FROM radar_days" in sql:
            return FakeResult([{"day": d} for d in sorted(self.held) if params["first"] <= d <= params["last"]])
        if "min(utc_datetime)" in sql:
            return FakeResult([{"first_report": self.first_report}])
        if "warning_polygons" in sql:
            return FakeResult([{"vtec_year": y, "n": n} for y, n in self.years])
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class ParseTest(unittest.TestCase):
    def test_nightly_defaults(self):
        a = radar_ingest.parse_args(["nightly"])
        self.assertEqual((a.mode, a.days, a.pause), ("nightly", 3, 0.5))

    def test_rebuild_defaults(self):
        a = radar_ingest.parse_args(["rebuild"])
        self.assertEqual((a.mode, a.days), ("rebuild", 30))
        self.assertFalse(hasattr(a, "pause"))

    def test_backfill_defaults(self):
        a = radar_ingest.parse_args(["backfill"])
        self.assertEqual((a.first, a.last, a.limit, a.pause), (None, None, None, 0.5))

    def test_backfill_options(self):
        a = radar_ingest.parse_args(["backfill", "--from", "2024-01-02", "--to", "2024-02-03", "--limit", "7", "--pause", "0"])
        self.assertEqual((a.first, a.last, a.limit, a.pause), (date(2024, 1, 2), date(2024, 2, 3), 7, 0.0))

    def test_warnings_options(self):
        a = radar_ingest.parse_args(["warnings", "--from", "2010-01-01", "--to", "2012-01-01"])
        self.assertEqual((a.mode, a.first, a.last), ("warnings", date(2010, 1, 1), date(2012, 1, 1)))

    def test_refused(self):
        for argv in ([], ["hourly"], ["nightly", "--days", "0"], ["rebuild", "--days", "-1"],
                     ["backfill", "--limit", "0"], ["backfill", "--from", "June 1"], ["backfill", "--to", "2024-13-01"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                radar_ingest.parse_args(argv)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class PickDaysTest(unittest.TestCase):
    def pick(self, argv, held=(), now=NOW):
        conn = FakeConn(held=held)
        return radar_ingest.pick_days(conn, radar_ingest.parse_args(argv), now), conn

    def test_nightly_is_the_last_three_finished_days(self):
        days, conn = self.pick(["nightly"])
        self.assertEqual(days, [LATEST, LATEST - timedelta(days=1), LATEST - timedelta(days=2)])
        self.assertEqual(conn.queries, [])

    def test_nightly_days_can_be_widened(self):
        self.assertEqual(len(self.pick(["nightly", "--days", "7"])[0]), 7)

    def test_rebuild_is_the_last_thirty(self):
        days, _ = self.pick(["rebuild"])
        self.assertEqual((len(days), days[0], days[-1]), (30, LATEST, LATEST - timedelta(days=29)))

    def test_backfill_is_every_day_from_the_archive_start_to_the_last_finished_day_newest_first(self):
        days, _ = self.pick(["backfill"])
        self.assertEqual((days[0], days[-1]), (LATEST, ingest.FIRST_RADAR_DAY))
        self.assertEqual(len(days), (LATEST - ingest.FIRST_RADAR_DAY).days + 1)
        self.assertEqual(days, sorted(days, reverse=True))

    def test_backfill_skips_the_days_held(self):
        held = {LATEST, LATEST - timedelta(days=2), ingest.FIRST_RADAR_DAY}
        days, _ = self.pick(["backfill"], held=held)
        self.assertTrue(held.isdisjoint(days))
        self.assertEqual(days[0], LATEST - timedelta(days=1))
        self.assertEqual(len(days), (LATEST - ingest.FIRST_RADAR_DAY).days + 1 - 3)

    def test_backfill_limit_takes_the_newest_not_yet_held(self):
        days, _ = self.pick(["backfill", "--limit", "2"], held={LATEST})
        self.assertEqual(days, [LATEST - timedelta(days=1), LATEST - timedelta(days=2)])

    def test_backfill_range(self):
        days, _ = self.pick(["backfill", "--from", "2024-05-18", "--to", "2024-05-20"])
        self.assertEqual(days, [date(2024, 5, 20), date(2024, 5, 19), date(2024, 5, 18)])

    def test_backfill_cannot_go_past_the_last_finished_day(self):
        days, _ = self.pick(["backfill", "--from", "2026-06-07", "--to", "2026-06-30"])
        self.assertEqual(days, [LATEST, date(2026, 6, 7)])

    def test_backfill_cannot_start_before_the_archive(self):
        days, _ = self.pick(["backfill", "--from", "2019-01-01", "--to", "2020-10-15"])
        self.assertEqual(days, [date(2020, 10, 15), ingest.FIRST_RADAR_DAY])

    def test_backfill_with_everything_held_is_nothing(self):
        held = {date(2024, 5, 18), date(2024, 5, 19)}
        self.assertEqual(self.pick(["backfill", "--from", "2024-05-18", "--to", "2024-05-19"], held=held)[0], [])

    def test_backfill_with_no_days_in_range_asks_nothing(self):
        days, conn = self.pick(["backfill", "--from", "2026-06-09", "--to", "2026-06-09"])
        self.assertEqual((days, conn.queries), ([], []))

    def test_the_held_query_covers_exactly_the_range(self):
        _, conn = self.pick(["backfill", "--from", "2024-05-18", "--to", "2024-05-20"])
        self.assertEqual(conn.queries[0][1], {"first": date(2024, 5, 18), "last": date(2024, 5, 20)})


@contextlib.contextmanager
def _connection(conn):
    yield conn


RESULT = {"run_id": 9, "loaded": 2, "missing": 1, "held": 3, "absent": 0, "built": 5, "failed": []}


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class MainTest(unittest.TestCase):
    def main(self, argv, result=RESULT, conn=None, now=NOW):
        conn = conn or FakeConn()
        out, err = io.StringIO(), io.StringIO()
        real_datetime = datetime

        class Clock(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with mock.patch.object(radar_ingest, "get_connection", lambda: _connection(conn)), \
                mock.patch.object(radar_ingest, "datetime", Clock), \
                mock.patch.object(radar_ingest.ingest, "run", return_value=result) as run, \
                mock.patch.object(radar_ingest, "configure_logging"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = radar_ingest.main(argv)
        return status, out.getvalue(), err.getvalue(), run

    def test_nightly_runs_the_last_three_days_with_downloads_and_a_pause(self):
        status, out, _, run = self.main(["nightly"])
        (conn, mode, days, tz, folder), kw = run.call_args
        self.assertEqual((mode, len(days), tz), ("nightly", 3, DENVER))
        self.assertEqual((kw["fetch_new"], kw["pause"], kw["now"]), (True, 0.5, NOW))
        self.assertEqual(status, 0)

    def test_rebuild_downloads_nothing(self):
        _, _, _, run = self.main(["rebuild"])
        (conn, mode, days, tz, folder), kw = run.call_args
        self.assertEqual((mode, len(days), kw["fetch_new"], kw["pause"]), ("rebuild", 30, False, 0.0))

    def test_backfill_is_run_as_a_backfill_with_the_days_not_held(self):
        _, _, _, run = self.main(["backfill", "--limit", "3"], conn=FakeConn(held={LATEST}))
        (conn, mode, days, tz, folder), kw = run.call_args
        self.assertEqual(mode, "backfill")
        self.assertEqual(days, [LATEST - timedelta(days=i) for i in (1, 2, 3)])

    def test_the_summary_line(self):
        _, out, _, _ = self.main(["nightly"])
        self.assertEqual(out.strip(), "run 9: 3 days: loaded 2, missing 1, held 3, absent 0, built 5, failed 0")

    def test_failed_days_make_the_exit_status_one_and_are_named_on_stderr(self):
        result = {**RESULT, "failed": [(date(2026, 6, 7), "FetchError: boom")]}
        status, out, err, _ = self.main(["nightly"], result=result)
        self.assertEqual(status, 1)
        self.assertIn("failed 1", out)
        self.assertIn("failed 2026-06-07: FetchError: boom", err)

    def test_nothing_to_do_is_a_clean_run(self):
        status, out, _, run = self.main(["backfill", "--from", "2026-06-09", "--to", "2026-06-09"],
                                        result={**RESULT, "loaded": 0, "held": 0, "built": 0, "missing": 0})
        self.assertEqual((status, run.call_args[0][2]), (0, []))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class WarningsModeTest(unittest.TestCase):
    def run_it(self, argv, conn, preload=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(radar_ingest, "get_connection", lambda: _connection(conn)), \
                mock.patch.object(radar_ingest.ingest, "preload_warnings", preload or mock.Mock(return_value=4)) as pre, \
                mock.patch.object(radar_ingest.load, "start_run", return_value=7) as start, \
                mock.patch.object(radar_ingest.load, "finish_run") as finish, \
                mock.patch.object(radar_ingest, "configure_logging"), \
                mock.patch.object(radar_ingest, "datetime", mock.Mock(now=lambda tz=None: NOW)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                status = radar_ingest.main(argv)
            except Exception as exc:
                status = exc
        return status, out.getvalue(), err.getvalue(), pre, start, finish

    def test_default_range_is_the_first_stored_report_to_the_day_before_the_radar(self):
        conn = FakeConn(first_report=datetime(2004, 1, 26, 0, 52, tzinfo=UTC), years=[(2024, 127), (2026, 107)])
        status, out, _, pre, start, finish = self.run_it(["warnings"], conn)
        (c, first, last, tz, folder), kw = pre.call_args
        self.assertEqual((first, last, tz), (date(2004, 1, 25), date(2020, 10, 13), DENVER))     # 00:52 UTC is the 25th in Denver
        self.assertEqual((kw["run_id"], kw["now"]), (7, NOW))
        start.assert_called_once_with(conn, "backfill")
        finish.assert_called_once_with(conn, 7, loaded=0, missing=0)
        self.assertEqual(status, 0)
        self.assertIn("4 requests", out)
        self.assertIn("2024     127 polygons", out)

    def test_range_can_be_given(self):
        status, _, _, pre, _, _ = self.run_it(["warnings", "--from", "2010-01-01", "--to", "2012-01-01"], FakeConn())
        self.assertEqual(pre.call_args[0][1:3], (date(2010, 1, 1), date(2012, 1, 1)))

    def test_it_cannot_go_past_the_last_finished_day(self):
        _, _, _, pre, _, _ = self.run_it(["warnings", "--from", "2026-01-01", "--to", "2026-12-31"], FakeConn())
        self.assertEqual(pre.call_args[0][2], LATEST)

    def test_no_reports_and_no_from_is_an_error_and_starts_no_run(self):
        status, _, err, pre, start, _ = self.run_it(["warnings"], FakeConn(first_report=None))
        self.assertEqual(status, 2)
        self.assertIn("no storm reports", err)
        start.assert_not_called()
        pre.assert_not_called()

    def test_a_failure_marks_the_run_failed_and_is_raised(self):
        boom = mock.Mock(side_effect=RuntimeError("iem down"))
        conn = FakeConn(first_report=datetime(2004, 1, 26, tzinfo=UTC))
        status, _, _, _, _, finish = self.run_it(["warnings"], conn, preload=boom)
        self.assertIsInstance(status, RuntimeError)
        finish.assert_called_once_with(conn, 7, loaded=0, missing=0, error="RuntimeError: iem down")
        self.assertEqual(conn.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
