"""Tests for hailsys/cocorahs/load.py -- parsed CoCoRaHS reports into the database (Phase 5c, step 1a).

Needs a role that can insert into the CoCoRaHS tables (hail_admin or hail_ingest, not hail_app); everything runs in a
transaction that is always rolled back:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_cocorahs_load

What they pin:  a new report is inserted;  the same report again changes nothing, not even updated_at or the load it
points at (a nightly job that re-reads three days must not look like 3 days of edits);  an edited report updates the
row, keeps first_seen_at and points at the load that changed it;  a coordinate Postgres rounds is not an edit;  a
report missing from a later fetch is never deleted;  two lines for one report in one fetch settle on the later one;
missing sizes stay NULL;  the load row's counts include the rejects, which are logged with their line number and
reason;  and a run and a load are opened at zero and closed with their counts.
"""

import os
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.cocorahs import load, parse
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
T1 = datetime(2024, 5, 31, 3, 0, tzinfo=UTC)
T2 = datetime(2024, 5, 31, 4, 0, tzinfo=UTC)


def report(station="CO-TEST-1", observed_at=T1, **kw):
    """A parsed report, the keys parse.parse_hail returns."""
    base = {"station_number": station, "station_name": "Erie 1.7 WNW", "observed_at": observed_at,
            "latitude": Decimal("40.0414439"), "longitude": Decimal("-105.0729374"),
            "smallest_in": Decimal("0.250"), "average_in": Decimal("0.375"), "largest_in": Decimal("0.500"),
            "duration_minutes": 12, "stone_consistency": None, "damage": None,
            "entered_at": datetime(2024, 5, 31, 3, 31, tzinfo=UTC),
            "source_updated_at": datetime(2024, 5, 31, 3, 31, tzinfo=UTC), "raw_row": f"raw {station} {observed_at}"}
    base.update(kw)
    return base


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class LoadTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('cocorahs_hail_reports', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert CoCoRaHS rows (hail_admin or hail_ingest)")
        self.run_id = load.start_run(self.conn, "manual")
        self.load_id = load.start_load(self.conn, self.run_id, "CO", "reportdate", date(1999, 6, 1), date(1999, 6, 2))

    def second_load(self):
        return load.start_load(self.conn, self.run_id, "CO", "timestamp", date(1999, 6, 2), date(1999, 6, 3))

    def row(self, station="CO-TEST-1", observed_at=T1):
        return self.conn.execute("SELECT * FROM cocorahs_hail_reports WHERE station_number = %s AND observed_at = %s",
                                 (station, observed_at)).fetchone()

    def count(self):
        return self.conn.execute("SELECT count(*) AS n FROM cocorahs_hail_reports WHERE station_number LIKE 'CO-TEST-%%'"
                                 ).fetchone()["n"]

    # -- runs and loads ---------------------------------------------------------------------

    def test_a_run_opens_running_and_closes_with_its_counts(self):
        row = self.conn.execute("SELECT * FROM cocorahs_runs WHERE run_id = %s", (self.run_id,)).fetchone()
        self.assertEqual((row["run_mode"], row["run_status"], row["finished_at"]), ("manual", "running", None))
        load.finish_run(self.conn, self.run_id, status="complete", loads=2, seen=10, new=6, changed=1, rejected=3)
        row = self.conn.execute("SELECT * FROM cocorahs_runs WHERE run_id = %s", (self.run_id,)).fetchone()
        self.assertEqual((row["run_status"], row["loads"], row["reports_seen"], row["reports_new"],
                          row["reports_changed"], row["reports_rejected"]), ("complete", 2, 10, 6, 1, 3))
        self.assertIsNotNone(row["finished_at"])
        self.assertIsNone(row["error_detail"])

    def test_a_failed_run_keeps_its_error(self):
        load.finish_run(self.conn, self.run_id, status="failed", loads=1, seen=0, new=0, changed=0, rejected=0,
                        error="CO reportdate 5/1/2024 to 5/31/2024: HTTP 500")
        row = self.conn.execute("SELECT run_status, error_detail FROM cocorahs_runs WHERE run_id = %s",
                                (self.run_id,)).fetchone()
        self.assertEqual(row["run_status"], "failed")
        self.assertIn("HTTP 500", row["error_detail"])

    def test_a_load_opens_at_zero_and_closes_with_its_counts(self):
        row = self.conn.execute("SELECT * FROM cocorahs_loads WHERE load_id = %s", (self.load_id,)).fetchone()
        self.assertEqual((row["rows_seen"], row["rows_new"], row["rows_changed"], row["rows_rejected"]), (0, 0, 0, 0))
        self.assertEqual((row["state"], row["date_type"], row["run_id"]), ("CO", "reportdate", self.run_id))
        load.finish_load(self.conn, self.load_id, seen=5, new=3, changed=1, rejected=2)
        row = self.conn.execute("SELECT * FROM cocorahs_loads WHERE load_id = %s", (self.load_id,)).fetchone()
        self.assertEqual((row["rows_seen"], row["rows_new"], row["rows_changed"], row["rows_rejected"]), (5, 3, 1, 2))

    # -- saving reports ---------------------------------------------------------------------

    def test_new_reports_are_inserted_with_every_field(self):
        got = load.save_reports(self.conn, self.load_id, [report(), report("CO-TEST-2", damage="shredded leaves")])
        self.assertEqual(got, {"new": 2, "changed": 0, "unchanged": 0})
        r = self.row()
        self.assertEqual((r["station_name"], r["observed_at"], r["duration_minutes"]), ("Erie 1.7 WNW", T1, 12))
        self.assertEqual((r["smallest_in"], r["average_in"], r["largest_in"]),
                         (Decimal("0.250"), Decimal("0.375"), Decimal("0.500")))
        self.assertEqual(r["load_id"], self.load_id)
        self.assertEqual(r["raw_row"], f"raw CO-TEST-1 {T1}")
        self.assertEqual(self.row("CO-TEST-2")["damage"], "shredded leaves")
        self.assertIsNotNone(r["first_seen_at"])

    def test_the_same_report_again_changes_nothing(self):
        load.save_reports(self.conn, self.load_id, [report(), report("CO-TEST-2")])
        before = self.row()
        later = self.second_load()
        got = load.save_reports(self.conn, later, [report(), report("CO-TEST-2")])
        self.assertEqual(got, {"new": 0, "changed": 0, "unchanged": 2})
        after = self.row()
        self.assertEqual(after, before, "not even updated_at or load_id moves for a report that did not change")
        self.assertEqual(after["load_id"], self.load_id)
        self.assertEqual(self.count(), 2)

    def test_a_coordinate_postgres_rounds_is_not_an_edit(self):
        # the table keeps 6 decimals;  40.0414439 is stored as 40.041444 and must not look different next night
        load.save_reports(self.conn, self.load_id, [report(latitude=Decimal("40.0414439"), longitude=Decimal("-105.0729374"))])
        got = load.save_reports(self.conn, self.second_load(), [report(latitude=Decimal("40.0414439"),
                                                                       longitude=Decimal("-105.0729374"))])
        self.assertEqual(got["unchanged"], 1)

    def test_an_edited_report_updates_the_row_and_points_at_the_load_that_changed_it(self):
        load.save_reports(self.conn, self.load_id, [report()])
        before = self.row()
        later = self.second_load()
        edited = report(largest_in=Decimal("1.250"), damage="car dents",
                        source_updated_at=datetime(2024, 6, 1, 9, 0, tzinfo=UTC), raw_row="edited line")
        got = load.save_reports(self.conn, later, [edited])
        self.assertEqual(got, {"new": 0, "changed": 1, "unchanged": 0})
        after = self.row()
        self.assertEqual((after["largest_in"], after["damage"], after["raw_row"]),
                         (Decimal("1.250"), "car dents", "edited line"))
        self.assertEqual(after["source_updated_at"], datetime(2024, 6, 1, 9, 0, tzinfo=UTC))
        self.assertEqual(after["load_id"], later)
        self.assertEqual(after["first_seen_at"], before["first_seen_at"])
        self.assertEqual(after["report_id"], before["report_id"])
        self.assertEqual(self.count(), 1)

    def test_each_field_that_can_change_counts_as_a_change(self):
        load.save_reports(self.conn, self.load_id, [report()])
        changes = {"station_name": "Erie 2 W", "smallest_in": Decimal("0.100"), "average_in": Decimal("0.500"),
                   "largest_in": Decimal("0.750"), "duration_minutes": 20, "stone_consistency": "Hard",
                   "damage": "dents", "entered_at": datetime(2024, 5, 31, 5, 0, tzinfo=UTC),
                   "source_updated_at": datetime(2024, 6, 2, 5, 0, tzinfo=UTC), "raw_row": "other",
                   "latitude": Decimal("40.05"), "longitude": Decimal("-105.08")}
        for key, value in changes.items():
            with self.subTest(field=key):
                got = load.save_reports(self.conn, self.second_load(), [report(**{key: value})])
                self.assertEqual(got["changed"], 1, key)
                load.save_reports(self.conn, self.second_load(), [report()])      # back, for the next field

    def test_a_missing_size_stays_null_and_a_size_that_appears_is_a_change(self):
        load.save_reports(self.conn, self.load_id, [report(smallest_in=None, average_in=Decimal("1.250"), largest_in=None)])
        r = self.row()
        self.assertEqual((r["smallest_in"], r["largest_in"]), (None, None))
        again = load.save_reports(self.conn, self.second_load(),
                                  [report(smallest_in=None, average_in=Decimal("1.250"), largest_in=None)])
        self.assertEqual(again["unchanged"], 1, "NULL against NULL is not a change")
        filled = load.save_reports(self.conn, self.second_load(),
                                   [report(smallest_in=None, average_in=Decimal("1.250"), largest_in=Decimal("1.500"))])
        self.assertEqual(filled["changed"], 1)

    def test_a_report_missing_from_a_later_fetch_is_never_deleted(self):
        load.save_reports(self.conn, self.load_id, [report(), report("CO-TEST-2")])
        load.save_reports(self.conn, self.second_load(), [report("CO-TEST-3")])
        self.assertEqual(self.count(), 3)

    def test_the_same_station_at_another_time_is_another_report(self):
        got = load.save_reports(self.conn, self.load_id, [report(), report(observed_at=T2)])
        self.assertEqual(got["new"], 2)
        self.assertEqual(self.count(), 2)

    def test_two_lines_for_one_report_in_one_fetch_settle_on_the_later_one(self):
        got = load.save_reports(self.conn, self.load_id, [report(largest_in=Decimal("0.500")),
                                                          report(largest_in=Decimal("1.000"))])
        self.assertEqual(got, {"new": 1, "changed": 1, "unchanged": 0})
        self.assertEqual(self.row()["largest_in"], Decimal("1.000"))

    def test_an_empty_batch_is_fine(self):
        self.assertEqual(load.save_reports(self.conn, self.load_id, []), {"new": 0, "changed": 0, "unchanged": 0})

    # -- one window, start to finish --------------------------------------------------------

    def parsed(self, reports, rejected=()):
        return parse.ParseResult(reports=list(reports), rejected=list(rejected))

    def test_store_window_opens_a_load_saves_the_reports_and_closes_it_with_counts(self):
        rej = [{"line": 7, "reason": "bad_size", "raw": "bad line"}, {"line": 9, "reason": "bad_date", "raw": "worse"}]
        got = load.store_window(self.conn, self.run_id, "CO", "reportdate", date(1999, 6, 1), date(1999, 6, 2),
                                self.parsed([report(), report("CO-TEST-2")], rej))
        self.assertEqual({k: got[k] for k in ("seen", "new", "changed", "unchanged", "rejected")},
                         {"seen": 2, "new": 2, "changed": 0, "unchanged": 0, "rejected": 2})
        row = self.conn.execute("SELECT * FROM cocorahs_loads WHERE load_id = %s", (got["load_id"],)).fetchone()
        self.assertEqual((row["rows_seen"], row["rows_new"], row["rows_changed"], row["rows_rejected"]), (2, 2, 0, 2))
        self.assertEqual((row["window_start"], row["window_end"], row["date_type"], row["state"]),
                         (date(1999, 6, 1), date(1999, 6, 2), "reportdate", "CO"))
        self.assertEqual(self.row()["load_id"], got["load_id"])

    def test_store_window_logs_each_reject_with_its_line_and_reason(self):
        rej = [{"line": 7, "reason": "bad_size", "raw": "bad line"}]
        with self.assertLogs("hailsys.cocorahs.load", level="WARNING") as logs:
            load.store_window(self.conn, self.run_id, "CO", "reportdate", date(1999, 6, 1), date(1999, 6, 2),
                              self.parsed([report()], rej))
        text = "\n".join(logs.output)
        self.assertIn("line 7", text)
        self.assertIn("bad_size", text)
        self.assertIn("bad line", text)

    def test_store_window_with_nothing_still_leaves_a_load_row(self):
        # "no reports" has to be told apart from "never fetched"
        got = load.store_window(self.conn, self.run_id, "CO", "reportdate", date(1999, 6, 1), date(1999, 6, 1),
                                self.parsed([]))
        row = self.conn.execute("SELECT rows_seen FROM cocorahs_loads WHERE load_id = %s", (got["load_id"],)).fetchone()
        self.assertEqual(row["rows_seen"], 0)

    def test_store_window_counts_an_edit_as_changed(self):
        load.store_window(self.conn, self.run_id, "CO", "reportdate", date(1999, 6, 1), date(1999, 6, 2),
                          self.parsed([report()]))
        got = load.store_window(self.conn, self.run_id, "CO", "timestamp", date(1999, 6, 2), date(1999, 6, 3),
                                self.parsed([report(largest_in=Decimal("2.000"))]))
        self.assertEqual((got["new"], got["changed"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
