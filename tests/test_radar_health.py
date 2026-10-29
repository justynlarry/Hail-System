"""Tests for the radar health line on the Storm Days page:  hailsys/queries/radar.py and the block in
hailsys/web/templates/_ingest_health.html.

    docker compose run --rm -T --no-deps -e PGHOST -e FLASK_SECRET_KEY -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_radar_health

The query is checked with canned rows and, read only, against the database.  The template is rendered
by the real app with made up health dicts.  What they pin: healthy means a nightly completed inside the
stale window (not a manual run, not a failed one); a system that has never run a nightly says so and
is not called stale; the detail table shows counts, the date range and a failed run's note; and the
IEM block is untouched.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.queries import radar
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
DENVER = ZoneInfo("America/Denver")
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(hours=30)


class FakeConn:
    """cursor() context whose execute() remembers the SQL and whose fetch* answer by what was asked."""
    def __init__(self, last_ok=None, started=False, runs=(), days=None):
        self.last_ok, self.started, self.runs = last_ok, started, list(runs)
        self.days = days or {"held": 0, "missing": 0, "suspect": 0, "oldest_day": None, "newest_day": None, "built": 0}
        self.sql = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.sql.append(sql)
        self.current = sql

    def fetchone(self):
        if "max(finished_at)" in self.current:
            return {"last_ok": self.last_ok}
        if "count(*) > 0" in self.current:
            return {"started": self.started}
        return self.days

    def fetchall(self):
        return self.runs


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class QueryTest(unittest.TestCase):
    def health(self, **kw):
        return radar.fetch_health(FakeConn(**kw), now=NOW, stale_after=STALE_AFTER)

    def test_a_nightly_inside_the_window_is_healthy(self):
        got = self.health(last_ok=NOW - timedelta(hours=29, minutes=59), started=True)
        self.assertTrue(got["healthy"])
        self.assertEqual(got["age"], timedelta(hours=29, minutes=59))

    def test_a_nightly_at_the_window_edge_is_stale(self):
        self.assertFalse(self.health(last_ok=NOW - STALE_AFTER, started=True)["healthy"])

    def test_no_nightly_ever_is_not_healthy_and_has_no_age(self):
        got = self.health(last_ok=None, started=False)
        self.assertEqual((got["healthy"], got["started"], got["age"], got["last_ok"]), (False, False, None, None))

    def test_a_started_nightly_that_never_completed_is_stale_not_unstarted(self):
        got = self.health(last_ok=None, started=True)
        self.assertEqual((got["healthy"], got["started"]), (False, True))

    def test_the_counts_and_runs_are_passed_through(self):
        days = {"held": 7, "missing": 2, "suspect": 3, "oldest_day": date(2024, 5, 20), "newest_day": date(2026, 6, 8), "built": 5}
        runs = [{"run_id": 3}]
        got = self.health(days=days, runs=runs)
        self.assertEqual((got["held"], got["missing"], got["suspect"], got["built"], got["oldest_day"], got["newest_day"], got["runs"]),
                         (7, 2, 3, 5, date(2024, 5, 20), date(2026, 6, 8), runs))
        self.assertEqual(got["stale_after"], STALE_AFTER)

    def test_only_a_completed_nightly_counts_as_the_last_ok(self):
        conn = FakeConn()
        radar.fetch_health(conn, now=NOW, stale_after=STALE_AFTER)
        sql = " ".join(conn.sql)
        self.assertIn("run_mode = 'nightly'", sql)
        self.assertIn("run_status = 'complete'", sql)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class DatabaseTest(unittest.TestCase):
    def test_the_real_queries_run_and_the_shape_is_right(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(row_factory=dict_row) as conn:
            got = radar.fetch_health(conn, now=datetime.now(UTC), stale_after=STALE_AFTER)
        self.assertEqual(set(got), {"healthy", "started", "last_ok", "age", "stale_after", "runs", "held", "missing",
                                    "suspect", "built", "oldest_day", "newest_day"})
        self.assertIsInstance(got["held"], int)
        self.assertLessEqual(len(got["runs"]), 5)
        if got["runs"]:
            self.assertEqual(got["runs"][0]["run_id"], max(r["run_id"] for r in got["runs"]))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class BuiltRowsTest(unittest.TestCase):
    """The SQL against rows made in a transaction that is always rolled back (needs hail_admin)."""

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        # radar_runs, not radar_parameter_sets: sql/056 lets hail_app add parameter sets (item 241).
        if not self.conn.execute("SELECT has_table_privilege('radar_runs', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert radar_runs rows (hail_admin)")
        # Real nightly runs may exist; set them aside inside this transaction (rolled back) so the tests see none.
        self.conn.execute("UPDATE radar_runs SET run_mode = 'manual' WHERE run_mode = 'nightly'")
        self.before = self.health()

    def health(self):
        return radar.fetch_health(self.conn, now=datetime.now(UTC), stale_after=STALE_AFTER)

    def add_run(self, mode, status, hours_ago=None):
        finished = None if hours_ago is None else datetime.now(UTC) - timedelta(hours=hours_ago)
        return self.conn.execute(
            "INSERT INTO radar_runs (run_mode, run_status, finished_at) VALUES (%s, %s, %s) RETURNING run_id",
            (mode, status, finished)).fetchone()["run_id"]

    def add_day(self, day, run_id, status="loaded"):
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, box_lon_min, "
            "box_lon_max, floor_mm, cells_in_box) VALUES (%s, %s, %s, now(), 'k', 2, 1, 1, 2, 12.7, %s)",
            (day, run_id, status, 5 if status == "loaded" else None))

    def test_last_ok_is_a_completed_nightly_only(self):
        self.add_run("manual", "complete", hours_ago=1)
        self.add_run("rebuild", "complete", hours_ago=1)
        self.add_run("backfill", "complete", hours_ago=1)
        self.add_run("nightly", "failed", hours_ago=1)
        self.add_run("nightly", "running")
        self.assertEqual(self.health()["last_ok"], self.before["last_ok"])         # none of those counts
        self.assertEqual(self.health()["healthy"], self.before["healthy"])
        ok = self.add_run("nightly", "complete", hours_ago=5)
        got = self.health()
        self.assertTrue(got["healthy"])
        self.assertTrue(got["started"])
        self.assertAlmostEqual(got["age"].total_seconds(), 5 * 3600, delta=60)

    def test_the_newest_completed_nightly_wins(self):
        self.add_run("nightly", "complete", hours_ago=50)
        self.add_run("nightly", "complete", hours_ago=2)
        self.assertAlmostEqual(self.health()["age"].total_seconds(), 2 * 3600, delta=60)

    def test_a_nightly_older_than_the_window_is_stale_but_started(self):
        self.add_run("nightly", "complete", hours_ago=31)
        got = self.health()
        self.assertEqual((got["healthy"], got["started"]), (False, True))

    def test_only_a_nightly_makes_it_started(self):
        self.assertFalse(self.before["started"])
        self.add_run("manual", "complete", hours_ago=1)
        self.assertFalse(self.health()["started"])
        self.add_run("nightly", "running")
        self.assertTrue(self.health()["started"])

    def test_the_five_newest_runs_newest_first(self):
        ids = [self.add_run("manual", "complete", hours_ago=h) for h in range(1, 9)]
        runs = self.health()["runs"]
        self.assertEqual([r["run_id"] for r in runs], sorted(ids, reverse=True)[:5])

    def test_suspect_days_are_counted_apart_from_held_and_missing_and_stay_out_of_the_range(self):
        run = self.add_run("manual", "complete", hours_ago=1)
        self.add_day(date(1999, 6, 8), run)
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, box_lon_min, "
            "box_lon_max, floor_mm, cells_in_box, cells_stored, max_mm) VALUES ('2099-01-01', %s, 'suspect', now(), 'k', "
            "2, 1, 1, 2, 12.7, 5, 0, 1007.7)", (run,))
        got = self.health()
        self.assertEqual(got["suspect"], self.before["suspect"] + 1)
        self.assertEqual(got["held"], self.before["held"] + 1)
        self.assertEqual(got["missing"], self.before["missing"])
        self.assertEqual(got["newest_day"], self.before["newest_day"])           # the 2099 suspect day is not in the range

    def test_days_held_missing_range_and_built(self):
        run = self.add_run("manual", "complete", hours_ago=1)
        self.add_day(date(1999, 6, 8), run)
        self.add_day(date(1999, 6, 9), run)
        self.add_day(date(2099, 12, 31), run, status="missing")        # outside the loaded range on both sides,
        self.add_day(date(1998, 1, 1), run, status="missing")          # so the range must ignore missing days
        second_set = self.conn.execute(
            "INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km) "
            "VALUES ('health-test', 2.0, 3.0, 5.0) RETURNING set_id").fetchone()["set_id"]
        first_set = self.conn.execute("SELECT min(set_id) AS s FROM radar_parameter_sets").fetchone()["s"]
        for set_id in (first_set, second_set):                 # two sets built on the same day count that day once
            self.conn.execute(
                "INSERT INTO radar_zone_builds (day, set_id, window_start, window_end, cells_over_threshold, patches, "
                "patches_anchored, anchor_reports) VALUES ('1999-06-08', %s, '1999-06-08 06:00+00', '1999-06-09 06:00+00', 0, 0, 0, 0)",
                (set_id,))
        got = self.health()
        self.assertEqual(got["held"], self.before["held"] + 2)
        self.assertEqual(got["missing"], self.before["missing"] + 2)
        self.assertEqual(got["suspect"], self.before["suspect"])
        self.assertEqual(got["built"], self.before["built"] + 1)
        self.assertEqual(got["oldest_day"], min(self.before["oldest_day"] or date(1999, 6, 8), date(1999, 6, 8)))
        self.assertEqual(got["newest_day"], max(self.before["newest_day"] or date(1999, 6, 9), date(1999, 6, 9)))


def iem_health(**over):
    base = {"healthy": True, "last_ok": NOW - timedelta(hours=5), "age": timedelta(hours=5),
            "stale_after": STALE_AFTER, "runs": [], "report_rows": 178279,
            "newest_denver": datetime(2026, 10, 16, 0, 0), "rejects": []}
    base.update(over)
    return base


def radar_health(**over):
    base = {"healthy": True, "started": True, "last_ok": NOW - timedelta(hours=3), "age": timedelta(hours=3),
            "stale_after": STALE_AFTER, "runs": [], "held": 2198, "missing": 4, "suspect": 0, "built": 2190,
            "oldest_day": date(2020, 10, 14), "newest_day": date(2026, 10, 20)}
    base.update(over)
    return base


def run_row(**over):
    base = {"run_id": 12, "run_mode": "nightly", "run_status": "complete", "days_loaded": 1, "days_missing": 0,
            "days_built": 3, "days_failed": 0, "finished_at": datetime(2026, 10, 22, 10, 31, tzinfo=UTC),
            "error_detail": None}
    base.update(over)
    return base


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hailsys.web import create_app
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            cls.app = create_app()

    def render(self, health=None, radar=None):
        template = self.app.jinja_env.get_template("_ingest_health.html")
        return template.render(health=health or iem_health(), radar_health=radar, display_tz=DENVER)

    def test_without_radar_health_the_block_is_absent_and_the_iem_block_is_there(self):
        text = self.render()
        self.assertNotIn("Radar", text)
        self.assertIn("Ingest OK", text)

    def test_healthy(self):
        text = self.render(radar=radar_health())
        self.assertIn("Radar layers OK — last nightly completed", text)
        self.assertIn("3 hours ago", text)
        self.assertNotIn("health-stale", text)

    def test_not_started_is_neither_ok_nor_stale(self):
        text = self.render(radar=radar_health(healthy=False, started=False, last_ok=None, age=None, held=4))
        self.assertIn("the nightly job has not run yet", text)
        self.assertIn("4 days loaded", text)
        self.assertNotIn("Radar layers STALE", text)
        self.assertNotIn("Radar layers OK", text)

    def test_one_day_is_singular(self):
        text = self.render(radar=radar_health(healthy=False, started=False, last_ok=None, age=None, held=1))
        self.assertIn("1 day loaded", text)

    def test_stale_says_how_long(self):
        text = self.render(radar=radar_health(healthy=False, last_ok=NOW - timedelta(hours=40), age=timedelta(hours=40)))
        self.assertIn("Radar layers STALE — no nightly has completed in", text)
        self.assertIn("40 hours", text)
        self.assertIn("health-stale", text)

    def test_started_but_never_completed_is_stale(self):
        text = self.render(radar=radar_health(healthy=False, last_ok=None, age=None))
        self.assertIn("no nightly run has ever completed", text)

    def test_the_detail_shows_counts_the_range_and_the_runs(self):
        text = self.render(radar=radar_health(runs=[run_row(), run_row(run_id=11, run_mode="rebuild")]))
        self.assertIn("2,198 days of radar loaded", text)
        self.assertIn("(2020-10-14 to 2026-10-20)", text)
        self.assertIn("4 missing from NOAA", text)
        self.assertIn("zones built for 2,190", text)
        self.assertIn("<td>rebuild</td>", text)
        self.assertIn("2026-10-22 04:31", text)               # 10:31 UTC in Denver, shown in Denver time

    def test_suspect_days_are_named_only_when_there_are_some(self):
        none = self.render(radar=radar_health())
        self.assertNotIn("suspect", none)
        some = self.render(radar=radar_health(suspect=2))
        self.assertIn("2 set aside as suspect (impossible values in NOAA's file)", some)

    def test_a_failed_run_shows_its_note_cut_to_160_characters(self):
        note = "5 of 5 days failed: " + "x" * 400
        text = self.render(radar=radar_health(runs=[run_row(run_status="failed", days_failed=5, error_detail=note)]))
        self.assertIn(note[:160], text)
        self.assertNotIn(note[:161], text)
        self.assertIn("<td>failed</td>", text)
        self.assertIn("<td>5</td>", text)                    # days failed

    def test_a_run_still_going_has_no_finish_time(self):
        text = self.render(radar=radar_health(runs=[run_row(run_status="running", finished_at=None)]))
        self.assertIn("<td>—</td>", text)

    def test_with_no_days_loaded_there_is_no_range(self):
        text = self.render(radar=radar_health(held=0, oldest_day=None, newest_day=None, healthy=False, started=False,
                                              last_ok=None, age=None, missing=0, built=0))
        self.assertIn("0 days of radar loaded", text)
        self.assertNotIn(" to None", text)

    def test_a_stale_iem_block_is_unchanged_by_radar(self):
        without = self.render(health=iem_health(healthy=False))
        with_radar = self.render(health=iem_health(healthy=False), radar=radar_health())
        self.assertIn(without.rstrip().rsplit("</div>", 1)[0].strip()[:200], with_radar)

    # -- the real page --------------------------------------------------------------------

    def test_the_storm_days_page_shows_the_radar_line(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(row_factory=dict_row) as conn:
            user = conn.execute("SELECT emp_id, role FROM users WHERE is_active ORDER BY emp_id LIMIT 1").fetchone()
        if not user:
            self.skipTest("no active user")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = user["emp_id"], user["role"], "test"
            s["issued_at"] = datetime.now(UTC).isoformat()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        self.assertIn("Radar layers", text)
        self.assertIn("Radar detail", text)
        self.assertIn("Ingest detail", text)


if __name__ == "__main__":
    unittest.main()
