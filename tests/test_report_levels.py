"""Tests for item 266:  a report send records the level at each house (zonesend.report_levels and the report branch
of send.queue_batch), and the by-level report splits report emails by it.

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -v ./tests:/app/tests:ro web python -m unittest tests.test_report_levels

Everything runs in one transaction that is always rolled back.  Each test makes a new parameter set (so it is the
set in use and nothing real is built under it), builds a real radar day under it, and draws its own zones around
made-up houses.  Nothing is sent:  queue_batch writes rows and calls nobody.

What they pin:  the best zone at the house on the match's storm day under the set (Agree before One source before
Context);  'none' when the day is built and the house is in no zone;  nothing (None, None) when the day is not built
under the set or the house has no location;  zone matches and an empty list give nothing;  a report send writes the
level and the set in use beside basis 'report', and the pairing trigger accepts it;  a zone send is unchanged;  and the
by-level report reads report levels back.
"""

import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

try:
    from hailsys.email import render, send, templatestore
    from hailsys.queries import levelreport, zonesend
    MISSING = None
except ImportError as exc:      # psycopg and jinja2 live in the app image; the host run skips (item 273)
    MISSING = str(exc)

DAY = date(2026, 9, 22)                 # a real radar day;  built here under the test's own set only
NOW = datetime.now(timezone.utc)
SETTINGS = None if MISSING else render.EmailSettings(
    logo_url="https://files.example.com/logo.png", badge_cra_url="https://files.example.com/cra.jpg",
    badge_bbb_url="https://files.example.com/bbb.png", contact_email="office@example.com",
    schedule_url="https://www.example.com/Orders/Create")


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ReportLevelsTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('likelihood_zones', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs hail_admin")
        self.emp = self.conn.execute("SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.set_id = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "SELECT 'item 266 test', radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi "
            "FROM likelihood_parameter_sets ORDER BY set_id DESC LIMIT 1 RETURNING set_id").fetchone()["set_id"]
        self.iem = self.conn.execute("SELECT iem_id FROM iem_data WHERE report_text = 'HAIL' LIMIT 1").fetchone()["iem_id"]
        self.n = 0

    # -- fixtures ---------------------------------------------------------------------------------

    def build(self, day=DAY):
        self.conn.execute(
            "INSERT INTO likelihood_builds (day, set_id, window_start, window_end, iem_reports, cocorahs_reports, "
            "duplicates_dropped, patches, patches_anchored) VALUES (%s, %s, now() - interval '1 day', now(), 0, 0, 0, 0, 0)",
            (day, self.set_id))

    def zone(self, level, lat, lon, radius_m=2000, day=DAY):
        self.conn.execute(
            "INSERT INTO likelihood_zones (day, set_id, level, geom, area_km2) "
            "SELECT %s, %s, %s, g, ST_Area(g::geography) / 1e6 FROM "
            "(SELECT ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)::geometry) AS g) x",
            (day, self.set_id, level, lon, lat, radius_m))

    def house(self, lat=39.0, lon=-105.0, *, located=True, day=DAY, zone_match=False, email=None):
        """A listing with a report match on 'day' (or a zone match), and its realtor."""
        self.n += 1
        rid = f"RL-{uuid.uuid4().hex[:10]}"
        email = email or f"rl{self.n}-{uuid.uuid4().hex[:6]}@example.test"
        realtor = self.conn.execute("INSERT INTO realtors (agent_name, agent_email) VALUES ('Pat Jones', %s) "
                                    "RETURNING realtor_id", (email,)).fetchone()["realtor_id"]
        self.conn.execute("INSERT INTO properties (rentcast_id, property_address, list_latitude, list_longitude) "
                          "VALUES (%s, %s, %s, %s)", (rid, f"{self.n} Level St, Denver, CO 80202",
                                                      lat if located else None, lon if located else None))
        listing = self.conn.execute(
            "INSERT INTO listings (rentcast_id, list_status, list_date, list_last_seen, realtor_id) "
            "VALUES (%s, 'Active', now(), now(), %s) RETURNING listing_id", (rid, realtor)).fetchone()["listing_id"]
        if zone_match:
            match = self.conn.execute(
                "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
                "VALUES (%s, %s, 'radar', 'agree', 'core', %s) RETURNING match_id",
                (listing, day, self.set_id)).fetchone()["match_id"]
        else:
            match = self.conn.execute(
                "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, storm_date) "
                "VALUES (%s, %s, 1, 5, %s) RETURNING match_id", (self.iem, listing, day)).fetchone()["match_id"]
        return {"match": match, "listing": listing, "realtor": realtor, "email": email}

    def levels(self, *houses, set_id="mine"):
        return zonesend.report_levels(self.conn, [h["match"] for h in houses],
                                      self.set_id if set_id == "mine" else set_id)

    # -- the lookup -------------------------------------------------------------------------------

    def test_a_house_in_a_zone_gets_that_zones_level_and_the_set(self):
        self.build()
        for level, lat in (("agree", 39.0), ("one_source", 39.5), ("context", 40.0)):
            with self.subTest(level=level):
                self.zone(level, lat, -105.0)
                h = self.house(lat, -105.0)
                self.assertEqual(self.levels(h), {h["match"]: (level, self.set_id)})

    def test_a_house_in_two_zones_gets_the_better_level(self):
        self.build()
        self.zone("context", 39.0, -105.0, 5000)
        self.zone("one_source", 39.0, -105.0, 3000)
        self.zone("agree", 39.0, -105.0, 1000)
        inner, middle, outer = self.house(39.0, -105.0), self.house(39.02, -105.0), self.house(39.035, -105.0)
        self.assertEqual(self.levels(inner, middle, outer),
                         {inner["match"]: ("agree", self.set_id), middle["match"]: ("one_source", self.set_id),
                          outer["match"]: ("context", self.set_id)})

    def test_a_built_day_with_the_house_in_no_zone_is_none(self):
        self.build()
        self.zone("agree", 39.0, -105.0)
        far = self.house(41.0, -108.0)
        self.assertEqual(self.levels(far), {far["match"]: ("none", self.set_id)})

    def test_a_day_with_no_zones_at_all_but_built_is_none(self):
        self.build()
        h = self.house()
        self.assertEqual(self.levels(h), {h["match"]: ("none", self.set_id)})

    def test_a_day_not_built_under_the_set_records_nothing(self):
        h = self.house()
        self.assertEqual(self.levels(h), {h["match"]: (None, None)})

    def test_a_house_with_no_location_records_nothing(self):
        self.build()
        self.zone("agree", 39.0, -105.0)
        h = self.house(located=False)
        self.assertEqual(self.levels(h), {h["match"]: (None, None)})

    def test_the_zones_of_another_set_or_another_day_are_not_read(self):
        self.build()
        other_day = self.conn.execute("SELECT max(day) AS d FROM radar_days WHERE day < %s", (DAY,)).fetchone()["d"]
        self.build(other_day)
        self.zone("agree", 39.0, -105.0, day=other_day)
        h = self.house()
        self.assertEqual(self.levels(h), {h["match"]: ("none", self.set_id)})
        # The real set is built for this day, so under it the house gets that set's own answer, not this test's zone.
        older = self.conn.execute("SELECT min(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        self.assertTrue(self.conn.execute("SELECT 1 FROM likelihood_builds WHERE day = %s AND set_id = %s",
                                          (DAY, older)).fetchone())
        level, set_id = self.levels(h, set_id=older)[h["match"]]
        self.assertEqual(set_id, older)
        self.assertIn(level, ("agree", "one_source", "context", "none"))

    def test_a_zone_under_another_set_on_the_same_day_is_not_read(self):
        first = self.set_id
        self.build()
        self.zone("agree", 39.0, -105.0)            # under the first test set
        self.set_id = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "SELECT 'item 266 test 2', radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi "
            "FROM likelihood_parameter_sets WHERE set_id = %s RETURNING set_id", (first,)).fetchone()["set_id"]
        self.build()                                # the second set is built for the day too, with no zones
        h = self.house()
        self.assertEqual(self.levels(h), {h["match"]: ("none", self.set_id)})
        self.assertEqual(self.levels(h, set_id=first), {h["match"]: ("agree", first)})

    def test_zone_matches_an_empty_list_and_no_set_give_nothing(self):
        self.build()
        z = self.house(zone_match=True)
        self.assertEqual(self.levels(z), {})
        self.assertEqual(zonesend.report_levels(self.conn, [], self.set_id), {})
        h = self.house()
        self.assertEqual(self.levels(h, set_id=None), {})

    # -- the engine ---------------------------------------------------------------------------------

    def queue(self, houses):
        """A report send of these houses through queue_batch's ready-made list path (the drip path uses it too)."""
        sync = self.conn.execute(
            "INSERT INTO cc_sync_runs (started_at, finished_at, status, triggered_by) "
            "VALUES (%s, %s, 'ok', %s) RETURNING run_id", (NOW - timedelta(minutes=1), NOW, self.emp)).fetchone()["run_id"]
        realtors = []
        for h in houses:
            realtors.append({"realtor_id": h["realtor"], "email": h["email"], "agent_name": "Pat Jones",
                             "events": [{"listing_id": h["listing"], "address": "1 Level St, Denver, CO 80202",
                                         "utc_datetime": datetime(2026, 9, 22, 20, tzinfo=timezone.utc),
                                         "magnitude": Decimal("1.75"), "distance_miles": Decimal("1.0"),
                                         "match_ids": [h["match"]]}]})
        found = {"realtors": realtors, "considered": {}, "excluded": {}}
        return send.queue_batch(self.conn, user_id=self.emp, email_settings=SETTINGS, now=NOW, sync_run_id=sync,
                                allowed_emails=[h["email"] for h in houses],
                                contacts={h["email"]: None for h in houses}, send_list=found,
                                template_name=templatestore.TEMPLATE_NAME)

    def logged(self, batch_id):
        return {r["match_id"]: (r["basis"], r["level"], r["zone_set_id"]) for r in self.conn.execute(
            "SELECT s.* FROM send_log s JOIN sent_emails e USING (email_id) WHERE e.batch_id = %s", (batch_id,))}

    def test_a_report_send_records_the_level_and_the_set_in_use(self):
        self.build()
        self.zone("agree", 39.0, -105.0)
        inside, outside = self.house(39.0, -105.0), self.house(41.0, -108.0)
        self.assertEqual(zonesend.current_set(self.conn)["set_id"], self.set_id)
        out = self.queue([inside, outside])
        self.assertEqual(out["emails"], 2)
        self.assertEqual(self.logged(out["batch_id"]),
                         {inside["match"]: ("report", "agree", self.set_id),
                          outside["match"]: ("report", "none", self.set_id)})

    def test_a_report_send_on_a_day_not_built_records_nothing_beside_basis_report(self):
        h = self.house()
        out = self.queue([h])
        self.assertEqual(self.logged(out["batch_id"]), {h["match"]: ("report", None, None)})

    def test_the_by_level_report_reads_report_levels_back(self):
        self.build()
        self.zone("one_source", 39.0, -105.0)
        self.zone("context", 40.0, -105.0)
        inside, outside, context = self.house(39.0, -105.0), self.house(41.0, -108.0), self.house(40.0, -105.0)
        out = self.queue([inside, outside, context])
        self.conn.execute("UPDATE send_log SET send_status = 'sent', sent_at = now() WHERE email_id = ANY(%s)",
                          (out["email_ids"],))
        rows = [r for r in levelreport.fetch_emails(self.conn) if r["email_id"] in out["email_ids"]]
        self.assertEqual(sorted((r["kind"], r["level"], r["set_id"]) for r in rows),
                         [("report", "context", self.set_id), ("report", "none", self.set_id),
                          ("report", "one_source", self.set_id)])
        summary = levelreport.summarize(rows)
        self.assertEqual([(r["set_id"], r["level"], r["emails"]) for r in summary["report_by_level"]],
                         [(self.set_id, "one_source", 1), (self.set_id, "context", 1), (self.set_id, "none", 1)])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SummarizeReportLevelsTest(unittest.TestCase):
    def row(self, set_id, level):
        return {"kind": "report", "set_id": set_id, "level": level, "bases": ["report"],
                "bounced": False, "complained": False, "unsubscribed": False}

    def test_report_emails_are_split_newest_set_first_best_level_first_and_not_recorded_last(self):
        got = levelreport.summarize([self.row(None, None), self.row(1, "none"), self.row(2, "context"),
                                     self.row(2, "agree"), self.row(1, "agree"), self.row(2, "none"),
                                     self.row(2, "one_source"), self.row(None, None)])
        self.assertEqual([(r["set_id"], r["level"], r["emails"]) for r in got["report_by_level"]],
                         [(2, "agree", 1), (2, "one_source", 1), (2, "context", 1), (2, "none", 1),
                          (1, "agree", 1), (1, "none", 1), (None, None, 2)])
        self.assertEqual(got["report"]["emails"], 8)
        self.assertEqual((got["by_level"], got["by_basis"]), ([], []))


if __name__ == "__main__":
    unittest.main()
