"""Tests for sql/053_cocorahs.sql -- the CoCoRaHS tables (Phase 5c, step 1a).

Needs a role that can insert into them (hail_admin), the same as the radar tests; everything runs in a transaction that
is always rolled back:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_cocorahs_schema

What they pin:  a report needs its station, time, position and the load that brought it;  one report per station and
time;  sizes are NULL or 0 to 10 in and NaN cannot get in (Postgres NUMERIC accepts it);  a position is on the earth;
geom follows the coordinates;  a run that has finished has a finish time and a running one has none;  a load's window
is ordered, its state is two capitals and its date type is one the export has;  and the grants:  the app reads, the
ingest role reads, inserts and updates (never deletes), may read the contact setting for the User-Agent and no other
setting, and the app role cannot write.
"""

import os
import unittest
from datetime import date, datetime, timezone

UTC = timezone.utc
WHEN = datetime(2024, 5, 31, 3, 0, tzinfo=UTC)


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class CocorahsSchemaTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT to_regclass('cocorahs_runs') IS NOT NULL AS ok").fetchone()["ok"]:
            self.fail("sql/053_cocorahs.sql is not applied")
        if not self.conn.execute("SELECT has_table_privilege('cocorahs_hail_reports', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert CoCoRaHS rows (hail_admin)")
        self.run_id = self.conn.execute(
            "INSERT INTO cocorahs_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.load_id = self.conn.execute(
            "INSERT INTO cocorahs_loads (run_id, state, date_type, window_start, window_end, rows_seen, rows_new, "
            "rows_changed, rows_rejected) VALUES (%s, 'CO', 'reportdate', '1999-06-01', '1999-06-02', 0, 0, 0, 0) "
            "RETURNING load_id", (self.run_id,)).fetchone()["load_id"]

    def report(self, **kw):
        row = dict(station_number="CO-TEST-1", observed_at=WHEN, latitude=40.04, longitude=-105.07,
                   smallest_in=0.25, average_in=0.375, largest_in=0.5, load_id=self.load_id, raw_row="raw line")
        row.update(kw)
        cols = ", ".join(row)
        marks = ", ".join(f"%({k})s" for k in row)
        return self.conn.execute(f"INSERT INTO cocorahs_hail_reports ({cols}) VALUES ({marks}) RETURNING report_id", row)

    def refused(self, sql, args=()):
        import psycopg
        with self.assertRaises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation,
                                psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation)):
            self.conn.execute(sql, args)
        self.conn.rollback()
        self.setUp()          # a refused statement aborts the transaction;  start a clean one

    # -- reports ----------------------------------------------------------------------------

    def test_a_minimal_report_is_accepted_and_timestamps_default(self):
        rid = self.report().fetchone()["report_id"]
        row = self.conn.execute("SELECT * FROM cocorahs_hail_reports WHERE report_id = %s", (rid,)).fetchone()
        self.assertEqual(row["observed_at"], WHEN)
        self.assertIsNotNone(row["first_seen_at"])
        self.assertIsNotNone(row["updated_at"])

    def test_sizes_and_the_optional_columns_may_be_null(self):
        self.report(smallest_in=None, average_in=None, largest_in=None, entered_at=None, source_updated_at=None,
                    duration_minutes=None, damage=None, stone_consistency=None, station_name=None)

    def test_geom_follows_the_coordinates(self):
        rid = self.report(latitude=40.0414439, longitude=-105.0729374).fetchone()["report_id"]
        row = self.conn.execute("SELECT ST_X(geom) AS x, ST_Y(geom) AS y, ST_SRID(geom) AS srid "
                                "FROM cocorahs_hail_reports WHERE report_id = %s", (rid,)).fetchone()
        self.assertAlmostEqual(row["x"], -105.0729374, places=6)
        self.assertAlmostEqual(row["y"], 40.0414439, places=6)
        self.assertEqual(row["srid"], 4326)

    def test_one_report_per_station_and_time(self):
        self.report()
        self.refused("INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, load_id, raw_row) "
                     "VALUES ('CO-TEST-1', %s, 40.0, -105.0, %s, 'x')", (WHEN, self.load_id))

    def test_the_same_station_at_another_time_and_another_station_at_the_same_time_are_fine(self):
        self.report()
        self.report(observed_at=datetime(2024, 5, 31, 4, 0, tzinfo=UTC))
        self.report(station_number="CO-TEST-2")

    def test_a_report_needs_its_station_time_position_load_and_raw_row(self):
        for col in ("station_number", "observed_at", "latitude", "longitude", "load_id", "raw_row"):
            with self.subTest(col=col):
                self.refused(f"INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, "
                             f"load_id, raw_row) SELECT "
                             + ", ".join("NULL" if c == col else v for c, v in
                                         (("station_number", "'S'"), ("observed_at", "now()"), ("latitude", "40"),
                                          ("longitude", "-105"), ("load_id", str(self.load_id)), ("raw_row", "'x'"))))

    def test_a_report_must_belong_to_a_real_load(self):
        self.refused("INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, load_id, raw_row) "
                     "VALUES ('S', now(), 40, -105, -1, 'x')")

    def test_sizes_outside_zero_to_ten_are_refused(self):
        for col in ("smallest_in", "average_in", "largest_in"):
            for bad in ("-0.01", "10.01", "99"):
                with self.subTest(col=col, bad=bad):
                    self.refused(f"INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, "
                                 f"load_id, raw_row, {col}) VALUES ('S', now(), 40, -105, {self.load_id}, 'x', {bad})")

    def test_a_size_of_zero_and_of_ten_are_accepted(self):
        self.report(largest_in=0)
        self.report(station_number="CO-TEST-2", largest_in=10)

    def test_NaN_cannot_get_in_as_a_size(self):
        # Postgres NUMERIC accepts 'NaN' (CLAUDE.md trap) and it sorts above every number, so a range check refuses it
        for col in ("smallest_in", "average_in", "largest_in"):
            with self.subTest(col=col):
                self.refused(f"INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, "
                             f"load_id, raw_row, {col}) VALUES ('S', now(), 40, -105, {self.load_id}, 'x', 'NaN'::numeric)")

    def test_a_position_must_be_on_the_earth(self):
        for lat, lon in ((91, -105), (-91, -105), (40, 181), (40, -181)):
            with self.subTest(lat=lat, lon=lon):
                self.refused("INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, load_id, "
                             "raw_row) VALUES ('S', now(), %s, %s, %s, 'x')", (lat, lon, self.load_id))

    def test_a_negative_duration_is_refused(self):
        self.refused("INSERT INTO cocorahs_hail_reports (station_number, observed_at, latitude, longitude, load_id, raw_row, "
                     "duration_minutes) VALUES ('S', now(), 40, -105, %s, 'x', -1)", (self.load_id,))

    # -- runs and loads ---------------------------------------------------------------------

    def test_a_new_run_is_running_with_no_finish_time(self):
        row = self.conn.execute("SELECT run_status, finished_at, started_at FROM cocorahs_runs WHERE run_id = %s",
                                (self.run_id,)).fetchone()
        self.assertEqual(row["run_status"], "running")
        self.assertIsNone(row["finished_at"])
        self.assertIsNotNone(row["started_at"])

    def test_a_finished_run_needs_a_finish_time_and_a_running_one_must_not_have_one(self):
        self.conn.execute("UPDATE cocorahs_runs SET run_status = 'complete', finished_at = now() WHERE run_id = %s",
                          (self.run_id,))
        self.refused("INSERT INTO cocorahs_runs (run_mode, run_status) VALUES ('manual', 'complete')")
        self.refused("INSERT INTO cocorahs_runs (run_mode, run_status, finished_at) VALUES ('manual', 'running', now())")

    def test_the_run_modes_and_statuses_are_the_known_ones(self):
        for mode in ("nightly", "weekly", "backfill", "manual"):
            self.conn.execute("INSERT INTO cocorahs_runs (run_mode) VALUES (%s)", (mode,))
        self.refused("INSERT INTO cocorahs_runs (run_mode) VALUES ('hourly')")
        self.refused("INSERT INTO cocorahs_runs (run_mode, run_status, finished_at) VALUES ('manual', 'done', now())")

    def test_run_counts_cannot_be_negative(self):
        for col in ("loads", "reports_seen", "reports_new", "reports_changed", "reports_rejected"):
            with self.subTest(col=col):
                self.refused(f"INSERT INTO cocorahs_runs (run_mode, {col}) VALUES ('manual', -1)")

    def test_a_load_has_an_ordered_window_a_state_and_a_known_date_type(self):
        base = "INSERT INTO cocorahs_loads (run_id, state, date_type, window_start, window_end, rows_seen, rows_new, " \
               "rows_changed, rows_rejected) VALUES (%s, %s, %s, %s, %s, 0, 0, 0, 0)"
        self.conn.execute(base, (self.run_id, "WY", "timestamp", date(1999, 6, 1), date(1999, 6, 1)))   # a one day window
        self.refused(base, (self.run_id, "CO", "reportdate", date(1999, 6, 2), date(1999, 6, 1)))
        self.refused(base, (self.run_id, "Colorado", "reportdate", date(1999, 6, 1), date(1999, 6, 2)))
        self.refused(base, (self.run_id, "co", "reportdate", date(1999, 6, 1), date(1999, 6, 2)))
        self.refused(base, (self.run_id, "CO", "entrydate", date(1999, 6, 1), date(1999, 6, 2)))

    def test_load_counts_cannot_be_negative_and_new_and_changed_cannot_exceed_seen(self):
        base = "INSERT INTO cocorahs_loads (run_id, state, date_type, window_start, window_end, rows_seen, rows_new, " \
               "rows_changed, rows_rejected) VALUES (%s, 'CO', 'reportdate', '1999-06-01', '1999-06-02', %s, %s, %s, %s)"
        self.conn.execute(base, (self.run_id, 5, 3, 2, 1))
        self.refused(base, (self.run_id, -1, 0, 0, 0))
        self.refused(base, (self.run_id, 2, 3, 0, 0))                 # more new than seen
        self.refused(base, (self.run_id, 2, 1, 2, 0))                 # new plus changed more than seen

    def test_a_load_may_not_point_at_a_run_that_does_not_exist(self):
        self.refused("INSERT INTO cocorahs_loads (run_id, state, date_type, window_start, window_end, rows_seen, rows_new, "
                     "rows_changed, rows_rejected) VALUES (-1, 'CO', 'reportdate', '1999-06-01', '1999-06-02', 0, 0, 0, 0)")

    # -- grants -----------------------------------------------------------------------------

    def priv(self, role, table, priv):
        return self.conn.execute("SELECT has_table_privilege(%s, %s, %s) AS ok", (role, table, priv)).fetchone()["ok"]

    def test_the_app_role_reads_and_cannot_write(self):
        for table in ("cocorahs_runs", "cocorahs_loads", "cocorahs_hail_reports"):
            self.assertTrue(self.priv("hail_app", table, "SELECT"), table)
            for p in ("INSERT", "UPDATE", "DELETE"):
                self.assertFalse(self.priv("hail_app", table, p), f"{table} {p}")

    def test_the_ingest_role_reads_inserts_and_updates_but_never_deletes(self):
        for table in ("cocorahs_runs", "cocorahs_loads", "cocorahs_hail_reports"):
            for p in ("SELECT", "INSERT"):
                self.assertTrue(self.priv("hail_ingest", table, p), f"{table} {p}")
            self.assertFalse(self.priv("hail_ingest", table, "DELETE"), f"{table} DELETE")
            self.assertFalse(self.priv("hail_ingest", table, "TRUNCATE"), f"{table} TRUNCATE")
        # a report needs its load before the load's counts are known, so the loads are updated too
        for table in ("cocorahs_runs", "cocorahs_loads", "cocorahs_hail_reports"):
            self.assertTrue(self.priv("hail_ingest", table, "UPDATE"), f"{table} UPDATE")

    def test_the_ingest_role_may_read_the_contact_and_the_states_and_no_other_setting(self):
        def col(c):
            return self.conn.execute("SELECT has_column_privilege('hail_ingest', 'settings', %s, 'SELECT') AS ok",
                                     (c,)).fetchone()["ok"]
        self.assertTrue(col("contact_email"))
        self.assertTrue(col("storm_states"))
        for other in ("rentcast_monthly_quota", "from_email", "schedule_url", "global_sessions_invalidated_at"):
            self.assertFalse(col(other), other)


if __name__ == "__main__":
    unittest.main()
