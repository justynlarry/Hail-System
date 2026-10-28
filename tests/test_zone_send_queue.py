"""Tests for the zone mode of hailsys/email/send.py and deliver.run_send (Phase 5c step 4, piece 4a).

Needs hail_admin;  everything runs in a transaction that is always rolled back, on made-up houses on a recent day.
Constant Contact is never called:  queue_batch calls nobody, and the run_send tests stop before anything is written.

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -v ./tests:/app/tests:ro web python -m unittest tests.test_zone_send_queue

What they pin:  a zone send writes one email per allowed realtor, rendered from the zone context with the evidence
sentence and no hail size;  each send_log row carries its own match's basis, level and set;  the built-in zone template
is saved under its own name as kind zone (item 264);  a report template cannot carry a zone send nor the other way
round;  no cap override, no ready-made list and no unpinned set;  the levels, the allow-list, Constant Contact's
unsubscribed state, the do-not-contact list, the cap and the observer-wording setting all hold in zone mode;  a zone
template with banned wording sends nothing;  a report send still records basis 'report';  and run_send refuses a zone
send with a campaign or a cap override before it syncs, and passes the zone to the list it builds.
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from hailsys.email import deliver, render, send, templatestore, zonewording
from hailsys.tuning import DISPLAY_TZ

NOW = datetime.now(timezone.utc)
SETTINGS = render.EmailSettings(
    logo_url="https://files.example.com/logo.png", badge_cra_url="https://files.example.com/cra.jpg",
    badge_bbb_url="https://files.example.com/bbb.png", contact_email="office@example.com",
    schedule_url="https://www.example.com/Orders/Create")


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZoneQueueTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('send_log', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert send_log rows (hail_admin)")
        self.emp = self.conn.execute("SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        self.set_id = self.conn.execute("SELECT max(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        self.day = NOW.astimezone(DISPLAY_TZ).date() - timedelta(days=3)
        self.sync = self.conn.execute(
            "INSERT INTO cc_sync_runs (started_at, finished_at, status, triggered_by) "
            "VALUES (%s, %s, 'ok', %s) RETURNING run_id",
            (NOW - timedelta(minutes=1), NOW, self.emp)).fetchone()["run_id"]
        self.n = 0
        self.houses = []

    # -- fixtures ---------------------------------------------------------------------------------

    def house(self, tag=None, *, basis="radar", level="agree", address=None):
        self.n += 1
        tag = tag or f"h{self.n}"
        realtor = self.conn.execute(
            "INSERT INTO realtors (agent_name, agent_email) VALUES (%s, %s) RETURNING realtor_id",
            (f"Agent {tag}", f"{tag}@example.test")).fetchone()["realtor_id"]
        rid = f"ZSQ-{uuid.uuid4().hex[:10]}"
        self.conn.execute("INSERT INTO properties (rentcast_id, property_address) VALUES (%s, %s)",
                          (rid, address or f"{self.n} Queue St, Calhan, CO 80808"))
        listing = self.conn.execute(
            "INSERT INTO listings (rentcast_id, list_status, list_date, list_last_seen, realtor_id) "
            "VALUES (%s, 'Active', now(), %s, %s) RETURNING listing_id", (rid, NOW, realtor)).fetchone()["listing_id"]
        match = self.conn.execute(
            "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
            "VALUES (%s, %s, %s, %s, 'core', %s) RETURNING match_id",
            (listing, self.day, basis, level, self.set_id)).fetchone()["match_id"]
        h = {"realtor": realtor, "listing": listing, "match": match, "email": f"{tag}@example.test",
             "basis": basis, "level": level}
        self.houses.append(h)
        return h

    def zone(self, **over):
        z = {"set_id": self.set_id, "levels": ["agree"]}
        z.update(over)
        return z

    def queue(self, *, zone="default", allowed=None, contacts=None, **over):
        allowed = [h["email"] for h in self.houses] if allowed is None else allowed
        args = dict(user_id=self.emp, storm_days=[self.day], email_settings=SETTINGS, now=NOW,
                    sync_run_id=self.sync, allowed_emails=allowed,
                    contacts={e: None for e in allowed} if contacts is None else contacts,
                    template_name=templatestore.ZONE_TEMPLATE_NAME,
                    zone=self.zone() if zone == "default" else zone)
        args.update(over)
        return send.queue_batch(self.conn, **args)

    def logged(self, batch_id):
        return self.conn.execute(
            "SELECT s.* FROM send_log s JOIN sent_emails e USING (email_id) WHERE e.batch_id = %s ORDER BY s.match_id",
            (batch_id,)).fetchall()

    def emails(self, batch_id):
        return self.conn.execute("SELECT * FROM sent_emails WHERE batch_id = %s ORDER BY email_id",
                                 (batch_id,)).fetchall()

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]

    def approve(self):
        self.conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(self.emp),))
        self.conn.execute("UPDATE settings SET zone_observer_wording_approved = true")

    def refused(self, words, **kw):
        before = (self.count("sent_emails"), self.count("send_log"))
        with self.assertRaises(send.SendError) as ctx:
            self.queue(**kw)
        self.assertIn(words, str(ctx.exception))
        self.assertEqual((self.count("sent_emails"), self.count("send_log")), before)

    # -- what is written --------------------------------------------------------------------------

    def test_one_email_per_realtor_from_the_zone_context(self):
        self.house("a")
        self.house("b")
        out = self.queue()
        self.assertEqual(out["emails"], 2)
        rows = self.emails(out["batch_id"])
        self.assertEqual(sorted(r["recipient_email"] for r in rows), ["a@example.test", "b@example.test"])
        s = self.conn.execute(
            "SELECT r.hail_threshold_in AS h, r.report_radius_km AS k FROM likelihood_parameter_sets s "
            "JOIN radar_parameter_sets r ON r.set_id = s.radar_set_id WHERE s.set_id = %s", (self.set_id,)).fetchone()
        sentence = zonewording.radar_sentence(s["h"], s["k"])
        for row in rows:
            self.assertIn(sentence, row["html_body"])
            self.assertIn("[[trackingImage]]", row["html_body"])
            self.assertNotIn(zonewording.ATTRIBUTION, row["html_body"])
            self.assertEqual(row["sync_run_id"], self.sync)

    def test_each_send_log_row_is_its_own_matchs_snapshot(self):
        self.approve()
        a = self.house("a", basis="radar", level="agree")
        b = self.house("b", basis="both", level="one_source")
        c = self.house("c", basis="observer", level="agree")
        out = self.queue(zone=self.zone(levels=["agree", "one_source"]))
        got = {r["match_id"]: (r["basis"], r["level"], r["zone_set_id"], r["send_status"]) for r in self.logged(out["batch_id"])}
        self.assertEqual(got, {h["match"]: (h["basis"], h["level"], self.set_id, "queued") for h in (a, b, c)})
        self.assertEqual(out["matches"], 3)

    def test_a_house_with_two_matches_logs_both_each_with_its_own_basis(self):
        self.approve()
        a = self.house("a", address="5 Pair St, Calhan, CO 80808", basis="radar")
        b = self.house("b", address="5 Pair St, Calhan, CO 80808", basis="observer")
        self.conn.execute("UPDATE listings SET realtor_id = %s WHERE listing_id = %s", (a["realtor"], b["listing"]))
        out = self.queue(allowed=["a@example.test"])
        got = {r["match_id"]: r["basis"] for r in self.logged(out["batch_id"])}
        self.assertEqual(got, {a["match"]: "radar", b["match"]: "observer"})
        self.assertEqual((out["emails"], out["matches"]), (1, 2))

    def test_the_pinned_set_is_used_not_the_newest(self):
        self.house("a")             # matched under the set in use until now
        newer = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "SELECT 'zone queue test', radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi "
            "FROM likelihood_parameter_sets WHERE set_id = %s RETURNING set_id", (self.set_id,)).fetchone()["set_id"]
        self.assertGreater(newer, self.set_id)
        self.assertEqual(self.queue()["emails"], 1)
        self.assertEqual(self.queue(zone=self.zone(set_id=newer))["emails"], 0)

    # match_kind on ensure_current is not tested here on purpose:  a built-in name fixes its own kind, and a
    # saved template already has an id, so the queue never reaches ensure_current with a kind it could get wrong.

    def test_the_built_in_zone_template_is_saved_under_its_own_name_as_zone(self):
        self.house("a")
        out = self.queue()
        (row,) = self.conn.execute(
            "SELECT DISTINCT t.template_name, t.match_kind FROM sent_emails e JOIN email_templates t USING (template_id) "
            "WHERE e.batch_id = %s", (out["batch_id"],)).fetchall()
        self.assertEqual((row["template_name"], row["match_kind"]), (templatestore.ZONE_TEMPLATE_NAME, "zone"))
        self.assertEqual(self.queue()["emails"], 0)       # and the house is not emailed twice

    def test_an_observer_email_carries_the_attribution_and_the_observer_sentence(self):
        self.approve()
        self.house("o", basis="observer")
        out = self.queue()
        (row,) = self.emails(out["batch_id"])
        self.assertIn(zonewording.OBSERVER_SENTENCE, row["html_body"])
        self.assertIn(zonewording.ATTRIBUTION[:30], row["html_body"])

    def test_a_report_send_records_basis_report_and_the_level_at_the_house(self):
        found = send.build_list(self.conn, [datetime(2026, 9, 22).date()], now=datetime(2026, 9, 30, 12, tzinfo=timezone.utc),
                                max_age_days=30, cap_days=14, freshness_days=14)
        if not found["realtors"]:
            self.skipTest("the 2026-09-22 storm data is not in this database")
        r = found["realtors"][0]
        sync = self.conn.execute(
            "INSERT INTO cc_sync_runs (started_at, finished_at, status, triggered_by) VALUES (%s, %s, 'ok', %s) "
            "RETURNING run_id", (datetime(2026, 9, 30, 11, 59, tzinfo=timezone.utc),
                                 datetime(2026, 9, 30, 12, tzinfo=timezone.utc), self.emp)).fetchone()["run_id"]
        out = send.queue_batch(self.conn, user_id=self.emp, storm_days=[datetime(2026, 9, 22).date()],
                               email_settings=SETTINGS, now=datetime(2026, 9, 30, 12, tzinfo=timezone.utc),
                               sync_run_id=sync, allowed_emails=[r["email"]], contacts={r["email"]: None})
        rows = self.logged(out["batch_id"])
        self.assertTrue(rows)
        # Item 266:  a report row records the level at its house under the set in use;  2026-09-22 is built.
        for x in rows:
            self.assertEqual(x["basis"], "report")
            self.assertIn(x["level"], ("agree", "one_source", "context", "none"))
            self.assertEqual(x["zone_set_id"], self.set_id)

    # -- the kinds stay apart -----------------------------------------------------------------------

    def test_a_report_template_cannot_carry_a_zone_send(self):
        self.house("a")
        self.refused("report template", template_name=templatestore.TEMPLATE_NAME)

    def test_a_zone_template_cannot_carry_a_report_send(self):
        self.house("a")
        self.refused("zone template", zone=None)

    def test_an_unknown_template_is_not_available(self):
        self.house("a")
        self.refused("not available", template_name=f"nope-{uuid.uuid4().hex[:8]}")

    def test_no_cap_override_no_ready_list_and_no_unpinned_set(self):
        self.house("a")
        self.refused("no cap override", override_cap=True)
        self.refused("no cap override", send_list={"realtors": [], "considered": {}, "excluded": {}})
        self.refused("pinned", zone=self.zone(set_id=None))

    def test_banned_wording_in_a_zone_template_sends_nothing(self):
        self.house("a")
        subject, body = templatestore.load_files(templatestore.ZONE_TEMPLATE_NAME)
        name = f"bad-{uuid.uuid4().hex[:8]}"
        # Inserted directly: ensure_current would refuse it at save, and the render must refuse it again.
        self.conn.execute("INSERT INTO email_templates (template_name, subject, body, created_by, match_kind) "
                          "VALUES (%s, %s, %s, %s, 'zone')", (name, subject, body + "<p>Guaranteed damage.</p>", self.emp))
        out = self.queue(template_name=name)
        self.assertEqual((out["emails"], out["batch_id"], len(out["skipped"]["unrenderable"])), (0, None, 1))

    # -- the rules hold in zone mode ------------------------------------------------------------------

    def test_only_the_chosen_levels_are_sent(self):
        self.house("agree", level="agree")
        self.house("one", level="one_source")
        out = self.queue()
        self.assertEqual([r["recipient_email"] for r in self.emails(out["batch_id"])], ["agree@example.test"])

    def test_the_allow_list_and_constant_contacts_unsubscribed_state_hold(self):
        self.house("a")
        self.house("b")
        self.house("c")
        contacts = {"a@example.test": None, "b@example.test": {"contact_id": "x", "permission": "unsubscribed"}}
        out = self.queue(allowed=["a@example.test", "b@example.test"], contacts=contacts)
        self.assertEqual([r["recipient_email"] for r in self.emails(out["batch_id"])], ["a@example.test"])
        self.assertEqual((out["skipped"]["not_allowed"], out["skipped"]["unsubscribed_in_cc"]), (1, 1))

    def test_a_recipient_not_looked_up_stops_the_send(self):
        self.house("a")
        self.refused("looked up", contacts={})

    def test_the_do_not_contact_list_holds(self):
        self.house("a")
        self.house("blocked")
        self.conn.execute("INSERT INTO dnc_list (email_raw, added_by, source) VALUES ('blocked@example.test', %s, 'manual')",
                          (self.emp,))
        out = self.queue()
        self.assertEqual([r["recipient_email"] for r in self.emails(out["batch_id"])], ["a@example.test"])
        self.assertEqual(out["excluded"]["on_dnc_list"]["matches"], 1)

    def test_the_cap_holds(self):
        a = self.house("a")
        self.queue()
        self.house("a2", address="77 Other St, Calhan, CO 80808")
        # A second house for the same realtor, emailed moments ago:  held by the cap.
        self.conn.execute("UPDATE listings SET realtor_id = %s WHERE listing_id = %s", (a["realtor"], self.houses[-1]["listing"]))
        out = self.queue(allowed=["a@example.test"])
        self.assertEqual(out["emails"], 0)
        self.assertGreaterEqual(out["excluded"]["within_cap"]["matches"], 1)

    def test_observer_matches_wait_for_the_setting(self):
        self.house("r", basis="radar")
        self.house("o", basis="observer")
        out = self.queue()
        self.assertEqual([r["recipient_email"] for r in self.emails(out["batch_id"])], ["r@example.test"])
        self.assertEqual(out["excluded"]["observer_wording_not_approved"]["matches"], 1)

    def test_a_stale_sync_stops_a_zone_send(self):
        self.house("a")
        old = self.conn.execute(      # a finished run cannot change, so an old one is made
            "INSERT INTO cc_sync_runs (started_at, finished_at, status, triggered_by) "
            "VALUES (%s, %s, 'ok', %s) RETURNING run_id",
            (NOW - timedelta(hours=1, minutes=1), NOW - timedelta(hours=1), self.emp)).fetchone()["run_id"]
        self.refused("sync", sync_run_id=old)


class RunSendZoneTest(unittest.TestCase):
    """run_send's zone checks, stopped before anything is written or sent."""

    def call(self, **kw):
        args = dict(user_id=1, storm_days=[NOW.date()], email_settings=SETTINGS, sender=object(),
                    allowed_emails=["a@example.test"], zone={"set_id": 1, "levels": ["agree"]},
                    sync=mock.Mock(side_effect=AssertionError("sync must not run")), cc=object(), now=NOW)
        args.update(kw)
        return deliver.run_send(**args)

    def test_a_campaign_or_a_cap_override_is_refused_before_the_sync(self):
        for extra in ({"campaign": {"name": "x", "steps": []}}, {"override_cap": True}):
            with self.subTest(extra=list(extra)), self.assertRaises(send.SendError):
                self.call(**extra)

    def test_the_zone_reaches_the_list_it_builds(self):
        class Stop(Exception):
            pass

        seen = {}

        def build(conn, days, **kw):
            seen.update(kw)
            raise Stop

        sync = mock.Mock(return_value={"run_id": 1})
        with mock.patch.object(send, "build_list", build), \
                mock.patch.object(deliver, "get_connection", mock.MagicMock()), self.assertRaises(Stop):
            self.call(sync=sync)
        self.assertEqual(seen["zone"], {"set_id": 1, "levels": ["agree"]})
        sync.assert_called_once()

    def test_the_zone_reaches_the_queue(self):
        class Stop(Exception):
            pass

        seen = {}

        def queue(conn, **kw):
            seen.update(kw)
            raise Stop

        empty = {"realtors": [], "considered": {}, "excluded": {}}
        with mock.patch.object(send, "build_list", mock.Mock(return_value=empty)), \
                mock.patch.object(send, "queue_batch", queue), \
                mock.patch.object(deliver, "lookup_contacts", mock.Mock(return_value={})), \
                mock.patch.object(deliver, "get_connection", mock.MagicMock()), self.assertRaises(Stop):
            self.call(sync=mock.Mock(return_value={"run_id": 1}))
        self.assertEqual(seen["zone"], {"set_id": 1, "levels": ["agree"]})


if __name__ == "__main__":
    unittest.main()
