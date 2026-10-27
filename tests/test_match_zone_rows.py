"""A match row that rests on the hail zones is invisible to every reader and cannot be sent (sql/057; Phase 5c, 3).

Needs a role that can insert into storm_listing_matches and send_log (hail_admin);  everything runs in a transaction
that is always rolled back:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_match_zone_rows

What they pin:  a zone row (basis radar, observer or both, no iem_id) added on a real storm day changes nothing the
match page, the CSV export, the work-state badges, the send list, the send days and the activity feed return;  the
database refuses wrong zone rows (an iem_id, a Context level, no set, a distance, an unknown basis, a second row for the
same house, day and set) and wrong report rows (a level);  an insert written the way the unchanged report matcher writes
gets its storm_date from the report and the basis 'report';  and send_log refuses a zone-only match while still taking a
report match.  The static half, that every reader keeps its inner join, is in test_match_readers.
"""

import os
import unittest
from datetime import datetime, timezone

from hailsys.queries import activity, exports, matches, sendlist, workstate
from hailsys.tuning import denver_day_bounds


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZoneMatchRowTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('storm_listing_matches', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert matches (hail_admin)")
        if not self.conn.execute("SELECT to_regclass('storm_listing_matches') IS NOT NULL AND EXISTS ("
                                 "SELECT 1 FROM information_schema.columns WHERE table_name = 'storm_listing_matches' "
                                 "AND column_name = 'basis') AS ok").fetchone()["ok"]:
            self.fail("sql/057_match_basis.sql is not applied")
        self.set_id = self.conn.execute("SELECT min(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        day = self.conn.execute("SELECT max(storm_date) AS d FROM storm_listing_matches").fetchone()["d"]
        if day is None:
            self.skipTest("no matches in this database")
        self.day = day
        self.window = denver_day_bounds(day)
        self.listing = self.conn.execute(
            "SELECT l.listing_id FROM listings l WHERE l.list_status = 'Active' AND l.realtor_id IS NOT NULL "
            "ORDER BY l.listing_id LIMIT 1").fetchone()["listing_id"]

    def zone_row(self, day=None, basis="radar", level="agree", layer="core", listing=None):
        return self.conn.execute(
            "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING match_id",
            (listing or self.listing, day or self.day, basis, level, layer, self.set_id)).fetchone()["match_id"]

    def insert_refused(self, sql, args=()):
        import psycopg
        with self.assertRaises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation,
                                psycopg.errors.NotNullViolation)):
            self.conn.execute(sql, args)
        self.conn.rollback()
        self.setUp()

    # -- the readers --------------------------------------------------------------------------

    def readers(self):
        """What every reader returns for the busiest recent day, as comparable values."""
        start, end = self.window
        kw = dict(window_start=start, window_end=end, report_text="HAIL", radius_miles=5.0)
        now = datetime.now(timezone.utc)
        today = now.astimezone(sendlist.DISPLAY_TZ).date()
        return {
            "match page": [dict(r) for r in matches.fetch_match_detail(self.conn, **kw)],
            "export": [dict(r) for r in exports.fetch_matches(self.conn, dnc_exclude=False, **kw)],
            "export count": exports.count_matches(self.conn, dnc_exclude=False, **kw),
            "export count (dnc)": exports.count_matches(self.conn, dnc_exclude=True, **kw),
            "work state": workstate.fetch_work_state(self.conn, window_start=start, window_end=end,
                                                      today=today, now=now),
            "send days": [dict(r) for r in sendlist.fetch_send_days(self.conn, today=today, max_age_days=4000)],
            "send list": repr(sendlist.build_send_list(self.conn, [self.day], now=now, max_age_days=4000,
                                                       cap_days=None, freshness_days=4000)),
            "feed": repr(activity.build_feed(self.conn, since=datetime(2000, 1, 1, tzinfo=timezone.utc), today=today,
                                             radius_m=8046.72)),
        }

    def test_a_zone_row_changes_nothing_any_reader_returns(self):
        before = self.readers()
        # One zone row per basis, each for a different house on the day, so the unique index is not in the way.
        others = [r["listing_id"] for r in self.conn.execute(
            "SELECT l.listing_id FROM listings l WHERE l.list_status = 'Active' AND l.realtor_id IS NOT NULL "
            "ORDER BY l.listing_id LIMIT 3").fetchall()]
        for basis, listing in zip(("radar", "observer", "both"), others):
            self.zone_row(basis=basis, listing=listing)
        after = self.readers()
        for name in before:
            with self.subTest(reader=name):
                self.assertEqual(before[name], after[name])

    def test_the_zone_row_is_really_there_and_only_the_readers_cannot_see_it(self):
        match_id = self.zone_row()
        row = self.conn.execute("SELECT * FROM storm_listing_matches WHERE match_id = %s", (match_id,)).fetchone()
        self.assertIsNone(row["iem_id"])
        self.assertEqual((row["basis"], row["level"], row["layer"], row["set_id"]),
                         ("radar", "agree", "core", self.set_id))
        seen = self.conn.execute(
            "SELECT count(*) AS n FROM storm_listing_matches m JOIN iem_data i ON i.iem_id = m.iem_id "
            "WHERE m.match_id = %s", (match_id,)).fetchone()["n"]
        self.assertEqual(seen, 0)

    def test_a_day_with_only_a_zone_row_stays_unworked_in_the_work_state(self):
        day = self.conn.execute(
            "SELECT d.day FROM likelihood_builds d WHERE NOT EXISTS (SELECT 1 FROM storm_listing_matches m "
            "WHERE m.storm_date = d.day) AND NOT EXISTS (SELECT 1 FROM api_pulls p WHERE p.storm_date = d.day) "
            "ORDER BY d.day DESC LIMIT 1").fetchone()
        if day is None:
            self.skipTest("no idle built day")
        start, end = denver_day_bounds(day["day"])
        now = datetime.now(timezone.utc)
        today = now.astimezone(sendlist.DISPLAY_TZ).date()
        before = workstate.fetch_work_state(self.conn, window_start=start, window_end=end, today=today, now=now)
        self.zone_row(day=day["day"])
        after = workstate.fetch_work_state(self.conn, window_start=start, window_end=end, today=today, now=now)
        self.assertEqual(before, after)

    # -- the constraints ----------------------------------------------------------------------

    def test_wrong_zone_rows_are_refused(self):
        s, l, d = self.set_id, self.listing, self.day
        iem = self.conn.execute("SELECT iem_id, distance_miles FROM storm_listing_matches ORDER BY match_id LIMIT 1").fetchone()
        for label, sql, args in (
            ("an iem_id", "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, storm_date, "
                          "basis, level, layer, set_id) VALUES (%s, %s, %s, 98, %s, 'radar', 'agree', 'core', %s)",
             (iem["iem_id"], l, iem["distance_miles"], d, s)),
            ("a Context level", "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
                                "VALUES (%s, %s, 'radar', 'context', 'outer', %s)", (l, d, s)),
            ("no set", "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer) "
                       "VALUES (%s, %s, 'radar', 'agree', 'core')", (l, d)),
            ("a distance", "INSERT INTO storm_listing_matches (listing_id, distance_miles, storm_date, basis, level, layer, "
                           "set_id) VALUES (%s, 1, %s, 'radar', 'agree', 'core', %s)", (l, d, s)),
            ("an unknown basis", "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
                                 "VALUES (%s, %s, 'guess', 'agree', 'core', %s)", (l, d, s)),
            ("an unknown layer", "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
                                 "VALUES (%s, %s, 'radar', 'agree', 'sky', %s)", (l, d, s)),
            ("no level", "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, layer, set_id) "
                         "VALUES (%s, %s, 'radar', 'core', %s)", (l, d, s)),
        ):
            with self.subTest(wrong=label):
                self.insert_refused(sql, args)

    def test_the_same_house_day_and_set_cannot_have_two_zone_rows(self):
        self.zone_row()
        self.insert_refused(
            "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
            "VALUES (%s, %s, 'observer', 'one_source', 'none', %s)", (self.listing, self.day, self.set_id))

    def test_a_report_row_cannot_carry_a_level(self):
        row = self.conn.execute("SELECT iem_id, listing_id, distance_miles FROM storm_listing_matches "
                                "ORDER BY match_id LIMIT 1").fetchone()
        self.insert_refused(
            "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, level) "
            "VALUES (%s, %s, %s, 97, 'agree')", (row["iem_id"], row["listing_id"], row["distance_miles"]))

    def test_an_insert_written_like_the_report_matcher_gets_its_date_and_basis(self):
        row = self.conn.execute("SELECT iem_id, listing_id, distance_miles FROM storm_listing_matches "
                                "ORDER BY match_id LIMIT 1").fetchone()
        new = self.conn.execute(
            "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used) "
            "VALUES (%s, %s, %s, 99) RETURNING storm_date, basis, level, layer, set_id",
            (row["iem_id"], row["listing_id"], row["distance_miles"])).fetchone()
        want = self.conn.execute(
            "SELECT (utc_datetime AT TIME ZONE 'America/Denver')::date AS d FROM iem_data WHERE iem_id = %s",
            (row["iem_id"],)).fetchone()["d"]
        self.assertEqual((new["storm_date"], new["basis"]), (want, "report"))
        self.assertEqual((new["level"], new["layer"], new["set_id"]), (None, None, None))

    # -- send_log ------------------------------------------------------------------------------

    def a_send_row(self):
        return self.conn.execute("SELECT * FROM send_log ORDER BY send_id LIMIT 1").fetchone()

    def insert_send(self, old, match_id):
        return self.conn.execute(
            "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by, email_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (old["realtor_id"], old["recipient_email"], match_id, old["template_id"], old["sent_by"], old["email_id"]))

    def test_send_log_refuses_a_zone_only_match(self):
        import psycopg
        old = self.a_send_row()
        if old is None:
            self.skipTest("no send_log row to copy")
        match_id = self.zone_row()
        with self.assertRaises(psycopg.errors.RestrictViolation) as ctx:
            self.insert_send(old, match_id)
        self.assertIn("zone-kind template", str(ctx.exception))

    def test_send_log_refuses_every_zone_basis(self):
        import psycopg
        old = self.a_send_row()
        if old is None:
            self.skipTest("no send_log row to copy")
        others = [r["listing_id"] for r in self.conn.execute(
            "SELECT l.listing_id FROM listings l WHERE l.list_status = 'Active' ORDER BY l.listing_id LIMIT 3").fetchall()]
        for basis, listing in zip(("radar", "observer", "both"), others):
            with self.subTest(basis=basis):
                match_id = self.zone_row(basis=basis, listing=listing)
                with self.assertRaises(psycopg.errors.RestrictViolation):
                    self.insert_send(old, match_id)
                self.conn.rollback()
                self.setUp()

    def test_send_log_still_takes_a_report_match(self):
        old = self.a_send_row()
        if old is None:
            self.skipTest("no send_log row to copy")
        self.insert_send(old, old["match_id"])


if __name__ == "__main__":
    unittest.main()
