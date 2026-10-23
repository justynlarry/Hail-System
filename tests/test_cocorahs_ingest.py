"""Tests for hailsys/cocorahs/ingest.py -- which windows a job reads, and the run around them (Phase 5c, step 1a).

Two parts.  The window arithmetic needs no database.  The run needs one, as a role that can insert CoCoRaHS rows:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_cocorahs_ingest

The run commits after each window, so the tests put a stand-in around the connection:  its commit is a savepoint and
its rollback goes back to the last one, and nothing a test does is left in the database.  The network is a stub.

What they pin:  nightly reads the last three days by Report Timestamp (late entries and edits), weekly the last thirty
days by Report Date, backfill reads monthly chunks by Report Date from the first CoCoRaHS day to today and never past
today;  the states come from settings.storm_states;  every state is fetched for every window;  a window that fails
(the fetch, or an export that is not the export) is named and the others still run;  the run is closed 'complete' or
'failed' with the counts of what did get stored;  a second run over the same text stores nothing new.
"""

import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.cocorahs import fetch, ingest
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

HEADER = ("ObservationDate,ObservationTime,EntryDateTime,StationNumber,StationName,Latitude,Longitude,SmallestSize,"
          "AverageSize,LargestSize,DurationMinutes,DurationAccuracy,Timing,StoneConsistency,MoreRainThanHail,HailStarted,"
          "LargestHailStarted,MoreRainThanHail,Damage,AngleOfImpact,NumberOfStonesOnPad,DistanceBtwnStonesOnPad,"
          "DepthOnGround,DateTimeStamp")


def line(station, day="1999-06-01"):
    return (f"{day}, 03:00 AM, {day} 03:31 AM, {station}, Test, 40.04, -105.07, 0.25, 0.375, 0.5, 12, , , , False, , , "
            f"False, , , , , , {day} 03:31 AM")


def export(*stations):
    return "\r\n".join([HEADER, *[line(s) for s in stations]]) + "\r\n"


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class WindowsTest(unittest.TestCase):
    TODAY = date(2026, 10, 23)

    def test_nightly_is_the_last_three_days_by_timestamp(self):
        self.assertEqual(ingest.windows("nightly", self.TODAY), [("timestamp", date(2026, 10, 20), self.TODAY)])

    def test_weekly_is_the_last_thirty_days_by_report_date(self):
        self.assertEqual(ingest.windows("weekly", self.TODAY), [("reportdate", date(2026, 9, 23), self.TODAY)])

    def test_backfill_starts_at_the_first_cocorahs_day_and_ends_today(self):
        wins = ingest.windows("backfill", self.TODAY)
        self.assertEqual(wins[0][1], date(2020, 10, 15))
        self.assertEqual(wins[-1][2], self.TODAY)
        self.assertTrue(all(w[0] == "reportdate" for w in wins))

    def test_backfill_chunks_are_months_that_touch_but_never_overlap(self):
        wins = ingest.windows("backfill", self.TODAY, first=date(2024, 1, 15))
        self.assertEqual(wins[0], ("reportdate", date(2024, 1, 15), date(2024, 1, 31)))
        self.assertEqual(wins[1], ("reportdate", date(2024, 2, 1), date(2024, 2, 29)))      # a leap year
        for before, after in zip(wins, wins[1:]):
            self.assertEqual((after[1] - before[2]).days, 1)

    def test_backfill_a_window_inside_one_month_is_one_chunk(self):
        wins = ingest.windows("backfill", date(2024, 3, 20), first=date(2024, 3, 5))
        self.assertEqual(wins, [("reportdate", date(2024, 3, 5), date(2024, 3, 20))])

    def test_backfill_from_after_today_is_nothing(self):
        self.assertEqual(ingest.windows("backfill", date(2024, 3, 20), first=date(2024, 4, 1)), [])

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            ingest.windows("hourly", self.TODAY)


class FakeConn:
    """Wraps a real connection:  commit is a savepoint and rollback goes back to the last one."""

    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("SAVEPOINT stub")

    def execute(self, *args, **kw):
        return self.conn.execute(*args, **kw)

    def commit(self):
        self.conn.execute("RELEASE SAVEPOINT stub")
        self.conn.execute("SAVEPOINT stub")

    def rollback(self):
        self.conn.execute("ROLLBACK TO SAVEPOINT stub")


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class RunTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(raw.close)
        self.addCleanup(raw.rollback)
        if not raw.execute("SELECT has_table_privilege('cocorahs_hail_reports', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert CoCoRaHS rows (hail_admin or hail_ingest)")
        self.raw = raw
        self.conn = FakeConn(raw)
        self.calls = []

    def fetcher(self, answers):
        """A stand-in for fetch_hail.  answers: {(state, start): text or an exception}; anything else is an empty export."""
        def fetch_one(state, date_type, start, end):
            self.calls.append((state, date_type, start, end))
            out = answers.get((state, start), HEADER + "\r\n")
            if isinstance(out, Exception):
                raise out
            return out
        return fetch_one

    def run_it(self, answers, states=("CO", "WY"), wins=None, mode="manual"):
        wins = wins or [("reportdate", date(1999, 6, 1), date(1999, 6, 2))]
        return ingest.run(self.conn, mode, states, wins, self.fetcher(answers))

    def stored(self, like="ZZ-%"):
        return self.raw.execute("SELECT count(*) AS n FROM cocorahs_hail_reports WHERE station_number LIKE %s",
                                (like,)).fetchone()["n"]

    def test_the_states_come_from_settings(self):
        states = ingest.states(self.conn)
        self.assertTrue(states)
        self.assertTrue(all(len(s) == 2 and s.isupper() for s in states))
        self.assertEqual(states, sorted(set(states)))

    def test_every_state_is_fetched_for_every_window(self):
        wins = [("reportdate", date(1999, 6, 1), date(1999, 6, 30)), ("reportdate", date(1999, 7, 1), date(1999, 7, 31))]
        out = self.run_it({}, wins=wins)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(out["loads"], 4)
        self.assertEqual(out["failed"], [])

    def test_a_good_run_stores_the_reports_and_closes_complete(self):
        out = self.run_it({("CO", date(1999, 6, 1)): export("ZZ-1", "ZZ-2"), ("WY", date(1999, 6, 1)): export("ZZ-3")})
        self.assertEqual((out["seen"], out["new"], out["changed"], out["rejected"]), (3, 3, 0, 0))
        self.assertEqual(self.stored(), 3)
        row = self.raw.execute("SELECT * FROM cocorahs_runs WHERE run_id = %s", (out["run_id"],)).fetchone()
        self.assertEqual(row["run_status"], "complete")
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual((row["loads"], row["reports_seen"], row["reports_new"]), (2, 3, 3))

    def test_the_run_remembers_its_mode(self):
        out = self.run_it({}, mode="weekly")
        mode = self.raw.execute("SELECT run_mode FROM cocorahs_runs WHERE run_id = %s", (out["run_id"],)).fetchone()
        self.assertEqual(mode["run_mode"], "weekly")

    def test_a_failed_fetch_is_named_and_the_other_states_still_run(self):
        out = self.run_it({("CO", date(1999, 6, 1)): fetch.FetchError("CoCoRaHS export failed after 3 tries (CO)"),
                           ("WY", date(1999, 6, 1)): export("ZZ-3")})
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("CO", out["failed"][0][0])
        self.assertIn("3 tries", out["failed"][0][1])
        self.assertEqual(out["loads"], 1)
        self.assertEqual(self.stored(), 1)

    def test_a_failed_run_is_closed_failed_with_the_reason(self):
        out = self.run_it({("CO", date(1999, 6, 1)): fetch.FetchError("boom")})
        row = self.raw.execute("SELECT * FROM cocorahs_runs WHERE run_id = %s", (out["run_id"],)).fetchone()
        self.assertEqual(row["run_status"], "failed")
        self.assertIsNotNone(row["finished_at"])
        self.assertIn("boom", row["error_detail"])

    def test_an_export_with_a_changed_header_fails_that_window_only(self):
        out = self.run_it({("CO", date(1999, 6, 1)): "Surprise,Columns\r\n1,2\r\n",
                           ("WY", date(1999, 6, 1)): export("ZZ-3")})
        self.assertEqual(len(out["failed"]), 1)
        self.assertEqual(self.stored(), 1)

    def test_a_window_that_fails_leaves_no_half_stored_load(self):
        bad = fetch.FetchError("boom")
        out = self.run_it({("CO", date(1999, 6, 1)): bad})
        n = self.raw.execute("SELECT count(*) AS n FROM cocorahs_loads WHERE run_id = %s", (out["run_id"],)).fetchone()["n"]
        self.assertEqual(n, 1)                                  # only WY's, which was fine and empty

    def test_the_same_text_again_stores_nothing_new(self):
        answers = {("CO", date(1999, 6, 1)): export("ZZ-1", "ZZ-2")}
        self.run_it(answers)
        again = self.run_it(answers)
        self.assertEqual((again["seen"], again["new"], again["changed"]), (2, 0, 0))
        self.assertEqual(self.stored(), 2)


if __name__ == "__main__":
    unittest.main()
