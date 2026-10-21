"""Tests for hailsys/radar/warnings.py:  NWS warning polygons from IEM.

The reader and URL tests need nothing.  The database tests need a role that can insert into the radar
and warning tables and update settings (not hail_app), see test_radar_zones.py:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_radar_warnings

Everything is built in a transaction that is always rolled back, on 1999 dates.  The network is
stubbed:  fetch.download writes a synthetic export zip (tests/radar_fixtures.py).

What they pin: the export URL carries the states, the explicit phenomena list and the storm-based flag;
the shapefile is read for what the zone builder needs and anything unexpected is refused loudly; a
fetch stores each polygon version once and records how many were new; a bow tie polygon is made valid;
a day is covered only by a fetch for the same states that spans it and ran LAG after it ended; and a
fetch never claims hours that have not happened.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.radar import fetch, warnings, zones
    from tests import radar_fixtures as fx
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

DENVER = ZoneInfo("America/Denver")
UTC = timezone.utc
DAY = date(1999, 6, 8)
START = datetime(1999, 6, 8, 6, tzinfo=UTC)
END = datetime(1999, 6, 9, 6, tzinfo=UTC)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class UrlTest(unittest.TestCase):
    def query(self, states="CO", start=START, end=END):
        from urllib.parse import parse_qs, urlsplit
        url = urlsplit(warnings.request_url(states, start, end))
        return f"{url.scheme}://{url.netloc}{url.path}", parse_qs(url.query)

    def test_asks_for_a_shapefile_of_storm_based_warnings_with_the_explicit_phenomena_list(self):
        base, q = self.query()
        self.assertEqual(base, "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py")
        self.assertEqual(q["accept"], ["shapefile"])
        self.assertEqual(q["limitps"], ["1"])
        self.assertEqual(q["phenomena"], ["SV,TO"])
        self.assertEqual(q["significance"], ["W,W"])
        self.assertEqual(q["limit1"], ["1"])
        self.assertEqual(q["location_group"], ["states"])

    def test_states_go_in_as_given(self):
        self.assertEqual(self.query("CO,WY")[1]["states"], ["CO,WY"])

    def test_times_are_utc_whatever_the_zone_of_the_datetime(self):
        _, q = self.query(start=datetime(1999, 6, 8, 0, tzinfo=DENVER), end=datetime(1999, 6, 9, 0, tzinfo=DENVER))
        self.assertEqual((q["sts"], q["ets"]), (["1999-06-08T06:00Z"], ["1999-06-09T06:00Z"]))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class StatesTest(unittest.TestCase):
    def test_normalized(self):
        self.assertEqual(warnings.normalize_states("co"), "CO")
        self.assertEqual(warnings.normalize_states(" wy , co,CO "), "CO,WY")

    def test_many_states_come_out_sorted(self):
        self.assertEqual(warnings.normalize_states("WY,UT,NM,CO,KS,NE,AZ,OK"), "AZ,CO,KS,NE,NM,OK,UT,WY")

    def test_refused(self):
        for bad in ("", None, " , ", "Colorado", "C", "CO;WY", "CO WY", "C1"):
            with self.subTest(bad=bad), self.assertRaises(warnings.WarningError):
                warnings.normalize_states(bad)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class ReadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def read(self, records):
        path = os.path.join(self.dir.name, "w.zip")
        fx.shapefile_zip(records, path)
        return warnings.read_shapefile(path)

    def test_reads_what_the_zone_builder_needs(self):
        got = self.read([fx.warning(wfo="PUB", phenom="TO", etn=34, year=2026, begin="202606092152",
                                    end="202606092211", issued="202606092100", expired="202606092230",
                                    product="202606092152-KPUB")])
        self.assertEqual(len(got), 1)
        w = got[0]
        self.assertEqual((w["wfo"], w["phenomena"], w["significance"], w["eventid"], w["vtec_year"]),
                         ("PUB", "TO", "W", 34, 2026))
        self.assertEqual(w["poly_begin"], datetime(2026, 6, 9, 21, 52, tzinfo=UTC))
        self.assertEqual(w["poly_end"], datetime(2026, 6, 9, 22, 11, tzinfo=UTC))
        self.assertEqual(w["issued"], datetime(2026, 6, 9, 21, 0, tzinfo=UTC))
        self.assertEqual(w["expires"], datetime(2026, 6, 9, 22, 30, tzinfo=UTC))
        self.assertEqual(w["product_id"], "202606092152-KPUB")
        self.assertEqual(w["ring"], fx.SQUARE)

    def test_an_empty_export_is_no_warnings(self):
        self.assertEqual(self.read([]), [])

    def test_empty_issue_and_product_are_none(self):
        w = self.read([fx.warning(issued="", expired="", product="")])[0]
        self.assertEqual((w["issued"], w["expires"], w["product_id"]), (None, None, None))

    def test_a_deleted_record_is_skipped_and_the_shapes_stay_lined_up(self):
        got = self.read([fx.warning(etn=1), fx.warning(etn=2, deleted=True, ring=fx.BOWTIE), fx.warning(etn=3)])
        self.assertEqual([w["eventid"] for w in got], [1, 3])
        self.assertEqual([w["ring"] for w in got], [fx.SQUARE, fx.SQUARE])

    def test_the_polygons_keep_their_own_shapes(self):
        other = [(-100.0, 35.0), (-100.0, 35.5), (-99.5, 35.5), (-100.0, 35.0)]
        got = self.read([fx.warning(etn=1), fx.warning(etn=2, ring=other)])
        self.assertEqual((got[0]["ring"], got[1]["ring"]), (fx.SQUARE, other))

    def test_refused(self):
        cases = {
            "a flash flood warning": fx.warning(phenom="FF"),
            "a watch": fx.warning(sig="A"),
            "a county based warning": fx.warning(gtype="C"),
            "a shape that is not a polygon": fx.warning(kind=3, ring=None),
            "a polygon with two parts": fx.warning(parts=2),
            "a ring that is not closed": fx.warning(ring=fx.SQUARE[:-1]),
            "no polygon begin": fx.warning(begin=""),
            "no polygon end": fx.warning(end=""),
        }
        for name, record in cases.items():
            with self.subTest(name), self.assertRaises(warnings.WarningError):
                self.read([fx.warning(), record])

    def test_two_parts_is_refused_for_that_reason(self):
        with self.assertRaisesRegex(warnings.WarningError, "2 parts"):
            self.read([fx.warning(parts=2)])

    def test_shapes_and_records_must_line_up(self):
        import io
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("a.shp", fx._shp([fx.warning(etn=1), fx.warning(etn=2)]))
            z.writestr("a.dbf", fx._dbf([fx.warning(etn=1)]))
        path = os.path.join(self.dir.name, "w.zip")
        with open(path, "wb") as f:
            f.write(buffer.getvalue())
        with self.assertRaisesRegex(warnings.WarningError, "2 shapes but 1 records"):
            warnings.read_shapefile(path)

    def test_not_a_zip_is_refused(self):
        path = os.path.join(self.dir.name, "w.zip")
        with open(path, "wb") as f:
            f.write(b"No data found for that request")
        with self.assertRaisesRegex(warnings.WarningError, "not a shapefile zip"):
            warnings.read_shapefile(path)


def _stub_download(data, urls=None):
    def download(url, dest, **kw):
        if urls is not None:
            urls.append((url, kw))
        if isinstance(data, Exception):
            raise data
        with open(dest, "wb") as f:
            f.write(data)
        return len(data)
    return download


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class StoreTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('warning_loads', 'UPDATE') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can write the warning tables (hail_admin)")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.run_id = self.conn.execute(
            "INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]

    def load(self, records, start=START, end=END, states="CO", urls=None, run_id="test", **kw):
        data = records if isinstance(records, (bytes, Exception)) else fx.shapefile_zip(records)
        with mock.patch.object(warnings.fetch, "download", _stub_download(data, urls)):
            return warnings.load_window(self.conn, start, end, states, self.dir.name,
                                        run_id=self.run_id if run_id == "test" else run_id, **kw)

    def count(self, table="warning_polygons"):
        return self.conn.execute(f"SELECT count(*) AS n FROM {table} WHERE "
                                 + ("vtec_year = 1999" if table == "warning_polygons" else "window_start < '2000-01-01'")
                                 ).fetchone()["n"]

    # -- storing --------------------------------------------------------------------------

    def test_stores_each_polygon_and_records_the_load(self):
        got = self.load([fx.warning(etn=1), fx.warning(etn=2, phenom="TO")])
        self.assertEqual((got["seen"], got["new"]), (2, 2))
        row = self.conn.execute("SELECT * FROM warning_loads WHERE load_id = %s", (got["load_id"],)).fetchone()
        self.assertEqual((row["states"], row["window_start"], row["window_end"], row["run_id"],
                          row["polygons_seen"], row["polygons_new"]), ("CO", START, END, self.run_id, 2, 2))
        self.assertEqual(self.count(), 2)

    def test_the_polygon_row_holds_the_times_and_the_shape(self):
        self.load([fx.warning(wfo="BOU", etn=7, begin="199901011200", end="199901011230", product="p7")])
        row = self.conn.execute(
            "SELECT *, ST_AsText(geom) AS wkt, ST_SRID(geom) AS srid, ST_IsValid(geom) AS ok, "
            "ST_Area(geom::geography) / 1e6 AS km2 FROM warning_polygons WHERE vtec_year = 1999").fetchone()
        self.assertEqual((row["wfo"], row["eventid"], row["product_id"], row["srid"], row["ok"]),
                         ("BOU", 7, "p7", 4326, True))
        self.assertEqual(row["poly_begin"], datetime(1999, 1, 1, 12, 0, tzinfo=UTC))
        self.assertEqual(row["poly_end"], datetime(1999, 1, 1, 12, 30, tzinfo=UTC))
        self.assertTrue(row["wkt"].startswith("MULTIPOLYGON"))
        self.assertAlmostEqual(row["km2"], 0.1 * 85.3 * 0.1 * 111.1, delta=8)       # 0.1 x 0.1 degrees at 40 N

    def test_a_bow_tie_is_made_valid(self):
        self.load([fx.warning(ring=fx.BOWTIE)])
        row = self.conn.execute("SELECT ST_IsValid(geom) AS ok, ST_Area(geom) AS a FROM warning_polygons "
                                "WHERE vtec_year = 1999").fetchone()
        self.assertTrue(row["ok"])
        self.assertGreater(row["a"], 0)

    def test_fetching_again_stores_nothing_new(self):
        self.load([fx.warning(etn=1), fx.warning(etn=2)])
        again = self.load([fx.warning(etn=1), fx.warning(etn=2), fx.warning(etn=3)])
        self.assertEqual((again["seen"], again["new"]), (3, 1))
        row = self.conn.execute("SELECT polygons_seen, polygons_new FROM warning_loads WHERE load_id = %s",
                                (again["load_id"],)).fetchone()
        self.assertEqual((row["polygons_seen"], row["polygons_new"]), (3, 1))
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.count("warning_loads"), 2)

    def test_a_new_polygon_version_of_the_same_warning_is_a_new_row(self):
        self.load([fx.warning(etn=1, begin="199901011200", end="199901011230")])
        got = self.load([fx.warning(etn=1, begin="199901011230", end="199901011300")])
        self.assertEqual(got["new"], 1)
        self.assertEqual(self.count(), 2)

    def test_the_same_key_twice_in_one_file_is_stored_once(self):
        got = self.load([fx.warning(etn=1), fx.warning(etn=1)])
        self.assertEqual((got["seen"], got["new"]), (2, 1))
        self.assertEqual(self.count(), 1)

    def test_an_empty_export_is_a_load_with_no_polygons(self):
        got = self.load([])
        self.assertEqual((got["seen"], got["new"]), (0, 0))
        self.assertEqual(self.count("warning_loads"), 1)

    def test_fetch_gets_the_url_and_the_options(self):
        urls = []
        self.load([], urls=urls, attempts=2)
        self.assertEqual(urls[0][0], warnings.request_url("CO", START, END))
        self.assertEqual(urls[0][1], {"attempts": 2})

    # -- failing --------------------------------------------------------------------------

    def test_a_failed_download_stores_nothing(self):
        with self.assertRaises(fetch.FetchError):
            self.load(fetch.FetchError("boom"))
        self.assertEqual((self.count(), self.count("warning_loads")), (0, 0))

    def test_a_refused_file_stores_nothing_and_the_download_is_deleted(self):
        with self.assertRaises(warnings.WarningError):
            self.load([fx.warning(), fx.warning(phenom="FF")])
        self.assertEqual((self.count(), self.count("warning_loads")), (0, 0))
        self.assertEqual(os.listdir(self.dir.name), [])

    def test_the_download_is_deleted_after_a_good_load(self):
        self.load([fx.warning()])
        self.assertEqual(os.listdir(self.dir.name), [])

    # -- the roles that run it ------------------------------------------------------------

    def as_role(self, role):
        self.conn.execute(f"SET LOCAL ROLE {role}")
        self.addCleanup(self.conn.execute, "RESET ROLE")

    def test_the_ingest_role_can_read_the_states_check_coverage_and_store(self):
        self.as_role("hail_ingest")
        self.assertEqual(warnings.current_states(self.conn), "CO")
        self.assertFalse(warnings.covered(self.conn, START, END, "CO"))
        got = self.load([fx.warning()], run_id=None)
        self.assertEqual(got["new"], 1)

    def test_the_ingest_role_can_ensure_a_day(self):
        self.as_role("hail_ingest")
        data = fx.shapefile_zip([fx.warning()])
        with mock.patch.object(warnings.fetch, "download", _stub_download(data)):
            self.assertEqual(warnings.ensure_day(self.conn, DAY, DENVER, self.dir.name, now=END + timedelta(days=3)),
                             "loaded")

    def test_the_app_role_can_read_the_states_and_check_coverage(self):
        self.as_role("hail_app")
        self.assertEqual(warnings.current_states(self.conn), "CO")
        self.assertFalse(warnings.covered(self.conn, START, END, "CO"))

    # -- coverage -------------------------------------------------------------------------

    def covered(self, **kw):
        return warnings.covered(self.conn, START, END, kw.get("states", "CO"))

    def add_load(self, start=START, end=END + warnings.LAG, states="CO"):
        self.conn.execute("INSERT INTO warning_loads (window_start, window_end, states, polygons_seen, polygons_new) "
                          "VALUES (%s, %s, %s, 0, 0)", (start, end, states))

    def test_a_day_with_no_fetch_is_not_covered(self):
        self.assertFalse(self.covered())

    def test_a_fetch_spanning_the_day_and_the_lag_covers_it(self):
        self.add_load()
        self.assertTrue(self.covered())

    def test_the_lag_is_two_hours(self):
        self.add_load(end=END + timedelta(hours=1, minutes=59))
        self.assertFalse(self.covered())
        self.add_load(end=END + timedelta(hours=2))
        self.assertTrue(self.covered())

    def test_a_fetch_that_ended_inside_the_lag_does_not(self):
        self.add_load(end=END + warnings.LAG - timedelta(minutes=1))
        self.assertFalse(self.covered())

    def test_a_fetch_that_started_after_the_day_began_does_not(self):
        self.add_load(start=START + timedelta(minutes=1))
        self.assertFalse(self.covered())

    def test_a_fetch_for_other_states_does_not(self):
        self.add_load(states="CO,WY")
        self.assertFalse(self.covered())
        self.assertTrue(self.covered(states="CO,WY"))

    # -- ensure_day -----------------------------------------------------------------------

    NOW = END + timedelta(days=3)

    def ensure(self, records=(), urls=None, now=None, **kw):
        data = fx.shapefile_zip(list(records))
        with mock.patch.object(warnings.fetch, "download", _stub_download(data, urls)):
            return warnings.ensure_day(self.conn, DAY, DENVER, self.dir.name, run_id=self.run_id,
                                       now=now or self.NOW, **kw)

    def test_fetches_the_day_padded_each_side(self):
        urls = []
        self.assertEqual(self.ensure(urls=urls), "loaded")
        self.assertEqual(urls[0][0], warnings.request_url("CO", START - warnings.PAD, END + warnings.PAD))
        row = self.conn.execute("SELECT window_start, window_end FROM warning_loads ORDER BY load_id DESC LIMIT 1").fetchone()
        self.assertEqual((row["window_start"], row["window_end"]), (START - warnings.PAD, END + warnings.PAD))

    def test_a_day_just_loaded_is_covered_and_not_fetched_again(self):
        self.ensure()
        urls = []
        self.assertEqual(self.ensure(urls=urls), "held")
        self.assertEqual(urls, [])

    def test_the_fetch_never_reaches_past_now(self):
        now = END + timedelta(hours=5)
        self.ensure(now=now)
        row = self.conn.execute("SELECT window_end FROM warning_loads ORDER BY load_id DESC LIMIT 1").fetchone()
        self.assertEqual(row["window_end"], now)
        self.assertTrue(warnings.covered(self.conn, START, END, "CO"))

    def test_a_day_that_ended_less_than_the_lag_ago_is_refused(self):
        with self.assertRaisesRegex(warnings.WarningError, "not all in yet"):
            self.ensure(now=END + warnings.LAG - timedelta(seconds=1))
        self.assertEqual(self.count("warning_loads"), 0)

    def test_the_lag_boundary_itself_is_allowed(self):
        self.assertEqual(self.ensure(now=END + warnings.LAG), "loaded")

    def test_states_default_to_the_setting(self):
        urls = []
        self.ensure(urls=urls)
        self.assertIn("states=CO&", urls[0][0])

    def test_the_states_setting_is_read_and_normalized(self):
        self.conn.execute("SELECT set_config('app.current_emp_id', "
                          "(SELECT emp_id::text FROM users ORDER BY emp_id LIMIT 1), true)")
        self.conn.execute("UPDATE settings SET storm_states = 'WY,CO'")
        self.assertEqual(warnings.current_states(self.conn), "CO,WY")
        urls = []
        self.ensure(urls=urls)
        self.assertIn("states=CO%2CWY&", urls[0][0].replace(",", "%2C"))

    def test_states_can_be_given(self):
        urls = []
        self.ensure(urls=urls, states="wy")
        self.assertIn("states=WY&", urls[0][0])
        self.assertTrue(warnings.covered(self.conn, START, END, "WY"))
        self.assertFalse(warnings.covered(self.conn, START, END, "CO"))


if __name__ == "__main__":
    unittest.main()
