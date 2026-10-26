"""Tests for hailsys/queries/zonepull.py -- the zones view of a storm day (Phase 5c, step 2a).

Needs a role that can insert the likelihood tables (hail_admin);  everything runs in a transaction that is always
rolled back:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_zonepull

What they pin:  a day not built with the set in use gives no rows;  on a day of its own (1999, a zone laid over three
real coverage zips so the numbers are known) the zips come back with their best level, the area overlapped and the share
of the zip, Agree before One source before Context, and Context only when asked for;  a zip that only touches a zone
along an edge is left out;  the report-based list is always inside the union, and the counts add up;  and on a real
day with a hail report the same holds.
"""

import os
import unittest
from datetime import date

from hailsys.queries import zonepull
from hailsys.tuning import denver_day_bounds, miles_to_metres

DAY = date(1999, 6, 9)


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZonePullTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('likelihood_zones', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert likelihood rows (hail_admin)")
        self.set_id = self.conn.execute("SELECT max(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        zips = self.conn.execute(
            "SELECT c.zcta5 FROM coverage_zips c WHERE c.removed_at IS NULL ORDER BY c.zcta5 LIMIT 4").fetchall()
        self.a, self.b, self.c, self.d = [z["zcta5"] for z in zips]

    def view(self, day=DAY, context=False, report_text="HAIL"):
        start, end = denver_day_bounds(day)
        return zonepull.zone_zips(self.conn, day=day, include_context=context, radius_m=miles_to_metres(5.0),
                                  window_start=start, window_end=end, report_text=report_text,
                                  actionable_only=True)

    def build_day(self, zones):
        """A 1999 day with a build for the set in use and these zones: {level: SQL for the geometry}."""
        run = self.conn.execute("INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box) VALUES (%s, %s, 'loaded', now(), 'k', "
            "41.5, 36.5, -109.7, -101.4, 12.7, 1000)", (DAY, run))
        start, end = denver_day_bounds(DAY)
        self.conn.execute(
            "INSERT INTO likelihood_builds (day, set_id, window_start, window_end, iem_reports, cocorahs_reports, "
            "duplicates_dropped, patches, patches_anchored) VALUES (%s, %s, %s, %s, 0, 0, 0, 0, 0)",
            (DAY, self.set_id, start, end))
        for level, sql in zones.items():
            self.conn.execute(
                f"INSERT INTO likelihood_zones (day, set_id, level, geom, area_km2) "
                f"SELECT %s, %s, %s, g, ST_Area(g::geography) / 1000000.0 "
                f"FROM (SELECT ST_Multi(ST_CollectionExtract(ST_MakeValid(({sql})), 3)) AS g) q",
                (DAY, self.set_id, level))

    ZIP = "SELECT geom FROM zcta_boundaries WHERE zcta5 = '{}'"

    # -- not built --------------------------------------------------------------------------

    def test_a_day_with_no_build_for_the_set_in_use_gives_no_rows(self):
        out = self.view(date(1999, 1, 1))
        self.assertFalse(out["built"])
        self.assertEqual((out["rows"], out["zips"], out["counts"]), ([], [], {}))
        self.assertEqual(out["set"]["set_id"], self.set_id)

    def test_a_build_for_another_set_does_not_count(self):
        self.build_day({})
        older = self.conn.execute(
            "SELECT min(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        if older == self.set_id:
            self.skipTest("only one parameter set")
        self.conn.execute("DELETE FROM likelihood_builds WHERE day = %s AND set_id = %s", (DAY, self.set_id))
        start, end = denver_day_bounds(DAY)
        self.conn.execute(
            "INSERT INTO likelihood_builds (day, set_id, window_start, window_end, iem_reports, cocorahs_reports, "
            "duplicates_dropped, patches, patches_anchored) VALUES (%s, %s, %s, %s, 0, 0, 0, 0, 0)",
            (DAY, older, start, end))
        self.assertFalse(self.view()["built"])

    def test_a_built_day_with_no_zones_is_built_with_no_rows(self):
        self.build_day({})
        out = self.view()
        self.assertTrue(out["built"])
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["counts"]["union"], 0)

    # -- a zone laid over known zips ----------------------------------------------------------

    def three_levels(self):
        self.build_day({"agree": self.ZIP.format(self.a), "one_source": self.ZIP.format(self.b),
                        "context": self.ZIP.format(self.c)})

    def test_each_zip_comes_back_with_its_best_level_and_the_whole_zip_as_overlap(self):
        self.three_levels()
        out = self.view(context=True)
        by = {r["zcta5"]: r for r in out["rows"]}
        self.assertEqual(set(by), {self.a, self.b, self.c} | set(r["zcta5"] for r in out["rows"]
                                                                if r["basis"] == "reports"))
        for zcta5, level in ((self.a, "agree"), (self.b, "one_source"), (self.c, "context")):
            with self.subTest(zip=zcta5):
                row = by[zcta5]
                self.assertEqual(row["best_level"], level)
                self.assertEqual(row["basis"], "zones")
                self.assertAlmostEqual(row["share"], 1.0, places=3)
                self.assertAlmostEqual(row[f"{level}_km2"], row["zip_km2"], delta=row["zip_km2"] * 0.001)

    def test_agree_comes_before_one_source_before_context(self):
        self.three_levels()
        rows = [r for r in self.view(context=True)["rows"] if r["basis"] == "zones"]
        self.assertEqual([r["best_level"] for r in rows], ["agree", "one_source", "context"])

    def test_context_is_left_out_unless_asked_for(self):
        self.three_levels()
        without = self.view(context=False)
        self.assertNotIn(self.c, without["zips"])
        self.assertEqual(without["counts"]["context"], 0)
        with_ctx = self.view(context=True)
        self.assertIn(self.c, with_ctx["zips"])
        self.assertTrue(set(without["zips"]) <= set(with_ctx["zips"]))

    def test_a_zip_in_two_levels_takes_the_best_and_adds_the_overlap(self):
        # A is all Agree and also all One source (the levels never overlap in a real build;  the view must still add up).
        self.build_day({"agree": self.ZIP.format(self.a), "one_source": self.ZIP.format(self.a)})
        row = next(r for r in self.view()["rows"] if r["zcta5"] == self.a)
        self.assertEqual(row["best_level"], "agree")
        self.assertAlmostEqual(row["share"], 2.0, places=2)

    def test_a_zip_that_only_touches_a_zone_along_an_edge_is_left_out(self):
        # Two coverage zips that share a border and nothing else:  the zone is all of the first, so the second
        # intersects it with no area.  (Not every pair of neighbours is clean in the Census boundaries.)
        pair = self.conn.execute(
            "SELECT p.zcta5 AS inside, q.zcta5 AS beside FROM coverage_zips p "
            "JOIN zcta_boundaries a ON a.zcta5 = p.zcta5 "
            "JOIN zcta_boundaries b ON b.zcta5 <> a.zcta5 AND ST_Touches(a.geom, b.geom) "
            "JOIN coverage_zips q ON q.zcta5 = b.zcta5 AND q.removed_at IS NULL "
            "WHERE p.removed_at IS NULL AND ST_Area(ST_Intersection(a.geom, b.geom)) = 0 "
            "ORDER BY p.zcta5, q.zcta5 LIMIT 1").fetchone()
        if pair is None:
            self.skipTest("no two coverage zips share only a border")
        self.build_day({"one_source": self.ZIP.format(pair["inside"])})
        zips = [r["zcta5"] for r in self.view()["rows"] if r["basis"] != "reports"]
        self.assertIn(pair["inside"], zips)
        self.assertNotIn(pair["beside"], zips)

    # -- the report-based list, and the counts --------------------------------------------------

    def test_the_counts_add_up_on_a_zone_day(self):
        self.three_levels()
        c = self.view(context=True)["counts"]
        self.assertEqual(c["union"], c["both"] + c["zones_only"] + c["reports_only"])
        self.assertEqual(c["union"], c["agree"] + c["one_source"] + c["context"] + c["reports_only"])
        self.assertEqual(c["reports"], c["both"] + c["reports_only"])

    def real_day_with_a_report(self):
        row = self.conn.execute(
            "SELECT max((i.utc_datetime AT TIME ZONE 'America/Denver')::date) AS d FROM iem_data i "
            "JOIN likelihood_builds b ON b.day = (i.utc_datetime AT TIME ZONE 'America/Denver')::date "
            "AND b.set_id = (SELECT max(set_id) FROM likelihood_parameter_sets) "
            "WHERE i.report_text = 'HAIL' AND i.magnitude >= 1 "
            "AND i.report_source_norm IS DISTINCT FROM 'COCORAHS'").fetchone()
        if row["d"] is None:
            self.skipTest("no built day with a hail report")
        return row["d"]

    def test_on_a_real_day_the_report_zips_are_always_inside_the_union(self):
        from hailsys.queries import storms
        day = self.real_day_with_a_report()
        start, end = denver_day_bounds(day)
        reports = {r["zcta5"] for r in storms.fetch_zips(
            self.conn, radius_m=miles_to_metres(5.0), window_start=start, window_end=end,
            report_text="HAIL", actionable_only=True)}
        for context in (False, True):
            with self.subTest(context=context):
                out = self.view(day, context=context)
                self.assertTrue(out["built"])
                self.assertTrue(reports <= set(out["zips"]))
                self.assertEqual(out["counts"]["reports"], len(reports))
                self.assertEqual(out["zips"], sorted(out["zips"]))
                self.assertEqual(len(out["zips"]), len(set(out["zips"])))

    def test_every_zone_zip_is_an_active_coverage_zip(self):
        day = self.real_day_with_a_report()
        active = {r["zcta5"] for r in self.conn.execute("SELECT zcta5 FROM coverage_zips WHERE removed_at IS NULL")}
        out = self.view(day, context=True)
        for row in out["rows"]:
            if row["basis"] != "reports":
                self.assertIn(row["zcta5"], active)
                self.assertGreater(row["share"], 0)

    def test_a_reports_only_zip_has_no_level_and_no_overlap(self):
        day = self.real_day_with_a_report()
        for row in self.view(day)["rows"]:
            if row["basis"] == "reports":
                self.assertIsNone(row["best_level"])
                self.assertEqual((row["agree_km2"], row["one_source_km2"], row["context_km2"], row["share"]),
                                 (0.0, 0.0, 0.0, 0.0))

    def test_the_view_writes_nothing(self):
        self.three_levels()
        before = [self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                  for t in ("likelihood_zones", "likelihood_builds", "api_pulls", "storm_listing_matches")]
        self.view(context=True)
        after = [self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                 for t in ("likelihood_zones", "likelihood_builds", "api_pulls", "storm_listing_matches")]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
