"""Tests for hailsys/matching/zonematch.py -- zone matching (Phase 5c, step 3).

Needs a role that can insert matches and send_log rows (hail_admin);  everything runs in a transaction that is always
rolled back, on real days (the function takes 'today', so a test can run on a day older than the storm age limit):

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_zonematch

What they pin:  the counts add up (houses in the zones = report matches + outside the crop box + no evidence + written +
already there);  every row written has no iem_id, a basis of radar, observer or both, a level of Agree or One source, a
layer, the set in use and the clicker;  each row's house lies inside the stored zone of its level, is an Active listing that
is not new construction or land, and has no HAIL report match that day;  the basis equals an independent derivation (radar
from the layer, observer from a CoCoRaHS filing at or above the floor within the third distance edge);  a re-run writes
nothing;  a day whose houses already have report matches writes nothing;  the day-age, future and not-built refusals write
nothing;  the temporary tables are gone;  the stored zones and every reader are unchanged;  and send_log refuses a
written row.
"""

import os
import unittest
from datetime import date, datetime, timedelta, timezone

from hailsys.matching import zonematch
from hailsys.queries import exports, sendlist
from hailsys.tuning import DISPLAY_TZ, denver_day_bounds

MAX_AGE = 30


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZoneMatchTest(unittest.TestCase):
    candidate_days = None
    first_with_rows = None

    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('storm_listing_matches', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert matches (hail_admin)")
        self.set = self.conn.execute(
            "SELECT * FROM likelihood_parameter_sets ORDER BY set_id DESC LIMIT 1").fetchone()
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]

    def match(self, day, **kw):
        kw.setdefault("today", day + timedelta(days=1))
        kw.setdefault("max_age_days", MAX_AGE)
        return zonematch.match_zones(self.conn, day, DISPLAY_TZ, emp_id=self.emp, **kw)

    def days_with_zones(self, limit=12):
        rows = self.conn.execute(
            "SELECT DISTINCT z.day FROM likelihood_zones z JOIN likelihood_builds b ON b.day = z.day AND b.set_id = z.set_id "
            "WHERE z.set_id = %s AND z.level IN ('agree', 'one_source') ORDER BY z.day DESC LIMIT %s",
            (self.set["set_id"], limit)).fetchall()
        return [r["day"] for r in rows]

    def a_day_with_rows(self):
        """The newest day where zone matching would write something;  found once per run."""
        if ZoneMatchTest.first_with_rows is None:
            ZoneMatchTest.first_with_rows = False
            for day in self.days_with_zones():
                out = self.match(day)
                self.conn.rollback()
                if out["written"] > 0:
                    ZoneMatchTest.first_with_rows = day
                    break
        if not ZoneMatchTest.first_with_rows:
            self.skipTest("no recent day where zone matching writes rows")
        return ZoneMatchTest.first_with_rows

    def written(self, day):
        return self.conn.execute(
            "SELECT m.*, p.geom AS house FROM storm_listing_matches m JOIN listings l ON l.listing_id = m.listing_id "
            "JOIN properties p ON p.rentcast_id = l.rentcast_id "
            "WHERE m.storm_date = %s AND m.iem_id IS NULL AND m.set_id = %s", (day, self.set["set_id"])).fetchall()

    def table_counts(self):
        return {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in ("storm_listing_matches", "likelihood_zones", "likelihood_builds", "api_pulls")}

    # -- what it writes -------------------------------------------------------------------------

    def test_the_counts_add_up(self):
        day = self.a_day_with_rows()
        out = self.match(day)
        self.assertEqual(out["in_zones"], out["has_report"] + out["outside_crop"] + out["no_evidence"]
                         + out["written"] + out["already"])
        self.assertEqual(out["written"], sum(out["by_basis"].values()))
        self.assertEqual(out["written"], sum(out["by_level"].values()))
        self.assertEqual(len(self.written(day)), out["written"])
        self.assertEqual((out["day"], out["set_id"]), (day, self.set["set_id"]))
        self.assertIsNotNone(out["built_at"])

    def test_every_row_is_a_zone_row_for_the_set_in_use_by_the_clicker(self):
        day = self.a_day_with_rows()
        self.match(day)
        for r in self.written(day):
            self.assertIsNone(r["iem_id"])
            self.assertIsNone(r["distance_miles"])
            self.assertIsNone(r["radius_used"])
            self.assertIn(r["basis"], ("radar", "observer", "both"))
            self.assertIn(r["level"], ("agree", "one_source"))
            self.assertIn(r["layer"], ("core", "margin", "radar_indicated", "outer", "none"))
            self.assertEqual((r["set_id"], r["emp_id"], r["storm_date"]), (self.set["set_id"], self.emp, day))

    def test_every_house_is_inside_the_stored_zone_of_its_level_and_is_a_real_target(self):
        day = self.a_day_with_rows()
        self.match(day)
        bad = self.conn.execute(
            "SELECT count(*) AS n FROM storm_listing_matches m JOIN listings l ON l.listing_id = m.listing_id "
            "JOIN properties p ON p.rentcast_id = l.rentcast_id "
            "WHERE m.storm_date = %(d)s AND m.iem_id IS NULL AND NOT EXISTS ("
            "  SELECT 1 FROM likelihood_zones z WHERE z.day = m.storm_date AND z.set_id = m.set_id "
            "  AND z.level = m.level AND ST_Intersects(z.geom, p.geom))", {"d": day}).fetchone()["n"]
        self.assertEqual(bad, 0)
        wrong_kind = self.conn.execute(
            "SELECT count(*) AS n FROM storm_listing_matches m JOIN listings l ON l.listing_id = m.listing_id "
            "JOIN properties p ON p.rentcast_id = l.rentcast_id "
            "WHERE m.storm_date = %(d)s AND m.iem_id IS NULL AND (l.list_status <> 'Active' "
            "OR l.list_type = 'New Construction' OR p.property_type = 'Land')", {"d": day}).fetchone()["n"]
        self.assertEqual(wrong_kind, 0)

    def test_no_house_with_a_hail_report_match_that_day_gets_a_zone_row(self):
        day = self.a_day_with_rows()
        self.match(day)
        both = self.conn.execute(
            "SELECT count(*) AS n FROM storm_listing_matches z JOIN storm_listing_matches r "
            "ON r.listing_id = z.listing_id AND r.storm_date = z.storm_date AND r.iem_id IS NOT NULL "
            "JOIN iem_data i ON i.iem_id = r.iem_id AND i.report_text = 'HAIL' "
            "WHERE z.storm_date = %s AND z.iem_id IS NULL", (day,)).fetchone()["n"]
        self.assertEqual(both, 0)
        one_each = self.conn.execute(
            "SELECT count(*) - count(DISTINCT listing_id) AS dup FROM storm_listing_matches "
            "WHERE storm_date = %s AND iem_id IS NULL", (day,)).fetchone()["dup"]
        self.assertEqual(one_each, 0)

    def test_the_basis_equals_an_independent_derivation(self):
        # Radar from the layer;  observer from a CoCoRaHS filing at or above the floor within the third edge.
        checked = 0
        for day in self.days_with_zones(8):
            start, end = denver_day_bounds(day)
            out = self.match(day)
            rows = self.conn.execute(
                "SELECT m.match_id, m.basis, m.layer, "
                "EXISTS (SELECT 1 FROM cocorahs_hail_reports c WHERE c.observed_at >= %(s)s AND c.observed_at < %(e)s "
                "AND coalesce(c.largest_in, c.average_in) >= %(floor)s "
                "AND ST_DWithin(c.geom::geography, p.geom::geography, %(b3)s)) AS observer "
                "FROM storm_listing_matches m JOIN listings l ON l.listing_id = m.listing_id "
                "JOIN properties p ON p.rentcast_id = l.rentcast_id "
                "WHERE m.storm_date = %(d)s AND m.iem_id IS NULL",
                {"s": start, "e": end, "d": day, "floor": self.set["cocorahs_min_in"],
                 "b3": float(self.set["band_3_mi"]) * 1609.344}).fetchall()
            self.assertEqual(len(rows), out["written"])
            for r in rows:
                radar = r["layer"] in ("core", "margin", "radar_indicated")
                want = "both" if radar and r["observer"] else "radar" if radar else "observer"
                self.assertTrue(radar or r["observer"], f"{day}: a row with no evidence")
                self.assertEqual(r["basis"], want, f"{day} match {r['match_id']}")
                checked += 1
            self.conn.rollback()
        if not checked:
            self.skipTest("no rows written on any recent day")

    # -- re-runs, skips, refusals ----------------------------------------------------------------

    def test_a_second_run_writes_nothing_and_changes_no_row(self):
        day = self.a_day_with_rows()
        first = self.match(day)
        before = [(r["match_id"], r["basis"], r["level"], r["layer"]) for r in self.written(day)]
        second = self.match(day)
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["already"], first["written"])
        self.assertEqual(before, [(r["match_id"], r["basis"], r["level"], r["layer"]) for r in self.written(day)])

    def test_among_lookalikes_at_the_same_spot_only_the_real_target_is_written(self):
        # Take a house the matcher really writes, then put lookalike listings at its exact coordinates, so the
        # evidence is identical and only the kind of listing differs.  Real data has no inactive listings at all,
        # so the fixtures are the only way to test these.
        day = self.a_day_with_rows()
        self.match(day)
        src = self.conn.execute(
            "SELECT m.basis, m.level, m.layer, p.list_latitude, p.list_longitude FROM storm_listing_matches m "
            "JOIN listings l ON l.listing_id = m.listing_id JOIN properties p ON p.rentcast_id = l.rentcast_id "
            "WHERE m.storm_date = %s AND m.iem_id IS NULL ORDER BY m.match_id LIMIT 1", (day,)).fetchone()
        self.conn.rollback()
        self.setUp()
        report = self.conn.execute("SELECT iem_id FROM iem_data WHERE report_text = 'HAIL' LIMIT 1").fetchone()["iem_id"]

        def house(tag, status="Active", list_type="Standard", property_type="Single Family"):
            rid = f"ZMTEST-{tag}"
            self.conn.execute(
                "INSERT INTO properties (rentcast_id, property_address, property_type, list_latitude, list_longitude) "
                "VALUES (%s, %s, %s, %s, %s)", (rid, f"{tag} test street", property_type, src["list_latitude"],
                                                src["list_longitude"]))
            return self.conn.execute(
                "INSERT INTO listings (rentcast_id, list_status, list_type, list_date) VALUES (%s, %s, %s, now()) "
                "RETURNING listing_id", (rid, status, list_type)).fetchone()["listing_id"]

        control = house("control")
        lookalikes = {"inactive": house("inactive", status="Inactive"),
                      "new construction": house("newc", list_type="New Construction"),
                      "land": house("land", property_type="Land"),
                      "report-matched": house("matched")}
        self.conn.execute(
            "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, storm_date) "
            "VALUES (%s, %s, 1, 5, %s)", (report, lookalikes["report-matched"], day))
        # Matched for wind that day, not hail:  only a HAIL report match skips a house, so this one is written.
        wind = self.conn.execute("SELECT iem_id FROM iem_data WHERE report_text <> 'HAIL' LIMIT 1").fetchone()["iem_id"]
        wind_house = house("wind")
        self.conn.execute(
            "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, storm_date) "
            "VALUES (%s, %s, 1, 5, %s)", (wind, wind_house, day))
        self.match(day)
        rows = {r["listing_id"]: r for r in self.written(day)}
        self.assertIn(control, rows, "the control house, same spot, was not written")
        self.assertIn(wind_house, rows, "a house matched only for wind was skipped")
        self.assertEqual((rows[control]["basis"], rows[control]["level"], rows[control]["layer"]),
                         (src["basis"], src["level"], src["layer"]))
        for name, listing in lookalikes.items():
            with self.subTest(lookalike=name):
                self.assertNotIn(listing, rows)

    def test_a_day_in_the_future_is_refused_and_writes_nothing(self):
        day = self.a_day_with_rows()
        before = self.table_counts()
        with self.assertRaises(zonematch.ZoneMatchError) as ctx:
            self.match(day, today=day - timedelta(days=1))
        self.assertIn("not happened yet", str(ctx.exception))
        self.assertEqual(self.table_counts(), before)

    def test_a_day_past_the_storm_age_limit_is_refused_and_writes_nothing(self):
        day = self.a_day_with_rows()
        before = self.table_counts()
        for age, ok in ((MAX_AGE, True), (MAX_AGE + 1, False)):
            with self.subTest(age=age):
                if ok:
                    self.match(day, today=day + timedelta(days=age))
                    self.conn.rollback()
                else:
                    with self.assertRaises(zonematch.ZoneMatchError) as ctx:
                        self.match(day, today=day + timedelta(days=age))
                    self.assertIn("storm age limit", str(ctx.exception))
        self.assertEqual(self.table_counts(), before)

    def test_a_day_with_no_radar_file_is_refused(self):
        with self.assertRaises(zonematch.ZoneMatchError) as ctx:
            self.match(date(1999, 1, 1))
        self.assertIn("no radar file", str(ctx.exception))

    def test_a_day_with_radar_but_no_build_for_the_set_in_use_is_refused(self):
        run = self.conn.execute("INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box) VALUES ('1999-06-10', %s, 'loaded', now(), 'k', "
            "41.5, 36.5, -109.7, -101.4, 12.7, 1000)", (run,))
        before = self.table_counts()
        with self.assertRaises(zonematch.ZoneMatchError) as ctx:
            self.match(date(1999, 6, 10), today=date(1999, 6, 11))
        self.assertIn("has not been built", str(ctx.exception))
        self.assertEqual(self.table_counts(), before)

    # -- what it leaves alone ---------------------------------------------------------------------

    def test_the_temporary_tables_are_gone_and_the_zones_are_untouched(self):
        day = self.a_day_with_rows()
        zones = self.conn.execute("SELECT level, area_km2 FROM likelihood_zones WHERE day = %s AND set_id = %s "
                                  "ORDER BY level", (day, self.set["set_id"])).fetchall()
        self.match(day)
        for table in ("zm_candidates", "lk_anchors", "lk_layers", "lk_bands", "radar_work", "radar_anchor_reports"):
            self.assertIsNone(self.conn.execute("SELECT to_regclass(%s) AS t", (table,)).fetchone()["t"], table)
        self.assertEqual(zones, self.conn.execute(
            "SELECT level, area_km2 FROM likelihood_zones WHERE day = %s AND set_id = %s ORDER BY level",
            (day, self.set["set_id"])).fetchall())

    def test_no_reader_sees_the_new_rows(self):
        day = self.a_day_with_rows()
        start, end = denver_day_bounds(day)
        kw = dict(window_start=start, window_end=end, report_text="HAIL", radius_miles=5.0, dnc_exclude=False)
        now = datetime.now(timezone.utc)
        today = now.astimezone(DISPLAY_TZ).date()
        before = (exports.count_matches(self.conn, **kw),
                  [dict(r) for r in sendlist.fetch_send_days(self.conn, today=today, max_age_days=4000)])
        out = self.match(day)
        self.assertGreater(out["written"], 0)
        after = (exports.count_matches(self.conn, **kw),
                 [dict(r) for r in sendlist.fetch_send_days(self.conn, today=today, max_age_days=4000)])
        self.assertEqual(before, after)

    def test_send_log_refuses_a_written_row(self):
        import psycopg
        old = self.conn.execute("SELECT * FROM send_log ORDER BY send_id LIMIT 1").fetchone()
        if old is None:
            self.skipTest("no send_log row to copy")
        day = self.a_day_with_rows()
        self.match(day)
        match_id = self.written(day)[0]["match_id"]
        with self.assertRaises(psycopg.errors.RestrictViolation):
            self.conn.execute(
                "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by, email_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (old["realtor_id"], old["recipient_email"], match_id, old["template_id"], old["sent_by"],
                 old["email_id"]))


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZoneMatchBusiestDayTest(ZoneMatchTest):
    """The same tests on the busiest storm day on record.

    The recent days are small, and every house in their zones either has a report match or has radar or an
    observer behind it, so a matcher that wrote houses with no evidence, or new-construction listings, would pass.
    The busiest old day, never matched by anyone, has thousands of houses of every kind in its zones.  The function
    takes 'today', so the storm age limit does not stop the test.
    """
    busiest = None

    def a_day_with_rows(self):
        if ZoneMatchBusiestDayTest.busiest is None:
            row = self.conn.execute(
                "SELECT z.day FROM likelihood_zones z JOIN likelihood_builds b ON b.day = z.day AND b.set_id = z.set_id "
                "WHERE z.set_id = %s AND z.level IN ('agree', 'one_source') "
                "GROUP BY z.day ORDER BY sum(z.area_km2) DESC LIMIT 1", (self.set["set_id"],)).fetchone()
            ZoneMatchBusiestDayTest.busiest = row["day"] if row else False
        if not ZoneMatchBusiestDayTest.busiest:
            self.skipTest("no built day with zones")
        return ZoneMatchBusiestDayTest.busiest

    def days_with_zones(self, limit=12):
        return [self.a_day_with_rows()]

    def test_the_busy_day_has_every_basis_and_houses_with_no_evidence(self):
        out = self.match(self.a_day_with_rows())
        self.assertGreater(out["written"], 100)
        self.assertGreater(out["no_evidence"], 0)
        self.assertEqual(set(out["by_basis"]), {"radar", "observer", "both"})


if __name__ == "__main__":
    unittest.main()
