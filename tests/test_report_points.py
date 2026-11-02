"""Tests for the Territory map's report points (item 33).

The database tests insert reports, which hail_app cannot, so run them as the admin role (under hail_app they skip):

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose --profile test run --rm -T --no-deps -e PGUSER=hail_admin -e PGPASSWORD="$PW" test \
        python -m unittest tests.test_report_points

Everything is in a transaction that is always rolled back, on 1999 dates no real report uses.  What they pin: past
the limit the newest reports are kept, newest first, whatever order they were inserted in; the total counts every
report, not only those sent; a report near several coverage zips is one point; and the route hands the total and the
limit to the map.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.queries import storms
    from hailsys.web import create_app, views
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
START = datetime(1999, 6, 1, tzinfo=UTC)
END = datetime(1999, 7, 1, tzinfo=UTC)
RADIUS_M = 5 * 1609.344


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class ReportPointsTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('iem_data', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert iem_data rows (hail_admin)")
        # A point inside an active coverage zip, so the report reaches the map.
        self.spot = self.conn.execute(
            "SELECT ST_X(z.centroid) AS lon, ST_Y(z.centroid) AS lat FROM coverage_zips c "
            "JOIN zcta_boundaries z USING (zcta5) WHERE c.removed_at IS NULL ORDER BY c.zcta5 LIMIT 1").fetchone()
        if self.spot is None:
            self.skipTest("no active coverage zips")

    def add_report(self, day):
        return self.conn.execute(
            "INSERT INTO iem_data (utc_datetime, latitude, longitude, magnitude, nws_issuer, report_type, report_text, "
            "ingested_at) VALUES (%s, %s, %s, 1.5, 'BOU', 'H', 'HAIL', now()) RETURNING iem_id",
            (datetime(1999, 6, day, 20, 0, tzinfo=UTC), self.spot["lat"], self.spot["lon"])).fetchone()["iem_id"]

    def points(self, limit):
        return storms.fetch_report_points(self.conn, radius_m=RADIUS_M, window_start=START, window_end=END,
                                          report_text="HAIL", actionable_only=False, limit=limit)

    def test_past_the_limit_the_newest_are_kept_newest_first(self):
        ids = {day: self.add_report(day) for day in (8, 9, 10, 11)}    # in time order, as the ingest inserts them
        self.assertEqual([r["iem_id"] for r in self.points(limit=2)], [ids[11], ids[10]])

    def test_the_order_is_by_time_not_by_insert(self):
        ids = {day: self.add_report(day) for day in (11, 8, 10, 9)}
        self.assertEqual([r["iem_id"] for r in self.points(limit=2)], [ids[11], ids[10]])

    def test_the_total_counts_every_report_not_only_those_sent(self):
        for day in (8, 9, 10):
            self.add_report(day)
        self.assertEqual({r["total"] for r in self.points(limit=2)}, {3})
        self.assertEqual(self.points(limit=2000)[0]["total"], 3)

    def test_a_report_near_several_zips_is_one_point(self):
        iem_id = self.add_report(8)
        zips = self.conn.execute(
            "SELECT count(*) AS n FROM report_zip_distances d JOIN coverage_zips c USING (zcta5) "
            "WHERE d.iem_id = %s AND d.distance_m <= %s AND c.removed_at IS NULL", (iem_id, RADIUS_M)).fetchone()["n"]
        if zips < 2:
            self.skipTest("the test spot has only one coverage zip within the radius")
        rows = self.points(limit=2000)
        self.assertEqual([r["iem_id"] for r in rows], [iem_id])
        self.assertEqual(rows[0]["total"], 1)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class PointsRouteTest(unittest.TestCase):
    ROW = {"iem_id": 1, "utc_datetime": datetime(1999, 6, 8, 20, 0, tzinfo=UTC), "latitude": Decimal("39.7"),
           "longitude": Decimal("-104.9"), "local_time": datetime(1999, 6, 8, 14, 0), "report_text": "HAIL",
           "magnitude": Decimal("1.50"), "mag_unit": "inches", "report_source": "Public",
           "report_source_norm": "PUBLIC", "total": 5}

    @classmethod
    def setUpClass(cls):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            cls.app = create_app()

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(row_factory=dict_row) as conn:
            self.user = conn.execute("SELECT emp_id, role FROM users WHERE is_active ORDER BY emp_id LIMIT 1").fetchone()
        if not self.user:
            self.skipTest("no active user")

    def get(self, rows):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.user["emp_id"], self.user["role"], "test"
            s["issued_at"] = datetime.now(UTC).isoformat()
        with mock.patch.object(storms, "fetch_report_points", return_value=rows):
            return client.get("/map/points.geojson").get_json()

    def test_the_map_is_told_the_total_and_the_limit(self):
        data = self.get([self.ROW, dict(self.ROW, iem_id=2)])
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["limit"], views.POINTS_SQL_LIMIT)
        self.assertEqual(len(data["features"]), 2)

    def test_no_reports_is_a_total_of_zero(self):
        data = self.get([])
        self.assertEqual((data["total"], data["features"]), (0, []))


if __name__ == "__main__":
    unittest.main()
