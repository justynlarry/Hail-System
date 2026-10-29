"""Tests for hailsys/queries/zonesend.py -- the zone send list (Phase 5c step 4, piece 3a).

Needs a role that can insert listings, matches and send records (hail_admin);  everything runs in a transaction that is
always rolled back, on made-up houses on a recent day, so nothing real is touched and nothing is sent:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -v ./tests:/app/tests:ro web python -m unittest tests.test_zonesend

What they pin:  a clean zone match is on the list as one event per house and day with each match's own basis, level and set;
every eligibility rule drops what it should and is counted under its own name (too old, inactive, stale, no agent email,
already emailed, duplicate address, do-not-contact, the cap), a failed send counts for nothing and a lifted do-not-contact
entry is not one;  a house emailed for the same storm day by a REPORT send is not emailed again;  observer and both
matches are left out under their own reason until the setting is true;  only the chosen levels and the chosen set are
offered;  report matches never appear;  and the call writes nothing.
"""

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from hailsys.queries import sendlist, zonesend
from hailsys.tuning import DISPLAY_TZ

NOW = datetime.now(timezone.utc)


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZoneSendListTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('send_log', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert send_log rows (hail_admin)")
        if self.conn.execute("SELECT to_regclass('pull_zips') IS NULL OR NOT EXISTS (SELECT 1 FROM information_schema.columns "
                             "WHERE table_name = 'email_templates' AND column_name = 'match_kind') AS no").fetchone()["no"]:
            self.fail("sql/059 and sql/060 are not applied")
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]
        self.set_id = self.conn.execute("SELECT max(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        self.sync = self.conn.execute("SELECT min(run_id) AS r FROM cc_sync_runs").fetchone()["r"]
        self.day = NOW.astimezone(DISPLAY_TZ).date() - timedelta(days=3)
        self.zone_tpl = self.conn.execute(
            "INSERT INTO email_templates (template_name, subject, body, created_by, match_kind) "
            "VALUES ('zone list test', 's', 'b', %s, 'zone') RETURNING template_id", (self.emp,)).fetchone()["template_id"]
        self.report_tpl = self.conn.execute(
            "SELECT template_id FROM email_templates WHERE match_kind = 'report' ORDER BY template_id LIMIT 1").fetchone()["template_id"]
        self.n = 0

    # -- fixtures ---------------------------------------------------------------------------------

    def house(self, tag=None, *, address=None, basis="radar", level="agree", day=None, status="Active",
              seen=None, email="default", set_id=None, realtor_id=None, no_realtor=False):
        self.n += 1
        tag = tag or f"h{self.n}"
        if no_realtor:
            realtor = None
        elif realtor_id is not None:
            realtor = realtor_id
        else:
            address_email = f"{tag}@example.test" if email == "default" else email
            realtor = self.conn.execute(
                "INSERT INTO realtors (agent_name, agent_email) VALUES (%s, %s) RETURNING realtor_id",
                (f"Agent {tag}", address_email)).fetchone()["realtor_id"]
        rid = f"ZSL-{uuid.uuid4().hex[:10]}"
        self.conn.execute("INSERT INTO properties (rentcast_id, property_address) VALUES (%s, %s)",
                          (rid, address or f"{self.n} Test St, Calhan, CO 80808"))
        listing = self.conn.execute(
            "INSERT INTO listings (rentcast_id, list_status, list_date, list_last_seen, realtor_id) "
            "VALUES (%s, %s, now(), %s, %s) RETURNING listing_id",
            (rid, status, seen or NOW, realtor)).fetchone()["listing_id"]
        match = self.conn.execute(
            "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
            "VALUES (%s, %s, %s, %s, 'core', %s) RETURNING match_id",
            (listing, day or self.day, basis, level, set_id or self.set_id)).fetchone()["match_id"]
        return {"realtor": realtor, "listing": listing, "match": match, "email": f"{tag}@example.test"}

    def record_send(self, house, *, status="sent", created=None, kind="zone"):
        """A send record for this house's match, as the engine would leave it."""
        email_id = self.conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, sync_run_id, permission_asserted, "
            "created_by, subject, html_body, created_at) VALUES (%s, %s, %s, %s, %s, 'none', %s, 's', 'b', %s) "
            "RETURNING email_id", (uuid.uuid4(), house["realtor"], house["email"],
                                   self.zone_tpl if kind == "zone" else self.report_tpl, self.sync, self.emp,
                                   created or NOW)).fetchone()["email_id"]
        basis = "radar" if kind == "zone" else "report"
        self.conn.execute(
            "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by, email_id, send_status, "
            "sent_at, basis, level, zone_set_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (house["realtor"], house["email"], house["match"], self.zone_tpl if kind == "zone" else self.report_tpl,
             self.emp, email_id, status, None if status in ("queued", "failed") else NOW, basis,
             "agree" if kind == "zone" else None, self.set_id if kind == "zone" else None))
        return email_id

    def report_match(self, house_of_listing):
        """A report match (an IEM report within the radius) on the same storm day for this listing."""
        iem = self.conn.execute("SELECT iem_id FROM iem_data WHERE report_text = 'HAIL' LIMIT 1").fetchone()["iem_id"]
        match = self.conn.execute(
            "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, storm_date) "
            "VALUES (%s, %s, 1, 5, %s) RETURNING match_id", (iem, house_of_listing, self.day)).fetchone()["match_id"]
        return match

    def approve(self):
        self.conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(self.emp),))
        self.conn.execute("UPDATE settings SET zone_observer_wording_approved = true")

    def listed(self, **kw):
        kw.setdefault("days", [self.day])
        days = kw.pop("days")
        return zonesend.build_zone_send_list(self.conn, days, now=NOW, **kw)

    def emails(self, out):
        return {r["email"] for r in out["realtors"]}

    def dropped(self, out, reason):
        return out["excluded"][reason]["matches"]

    # -- a clean match ----------------------------------------------------------------------------

    def test_a_clean_zone_match_is_on_the_list_as_one_event(self):
        h = self.house("clean")
        out = self.listed()
        self.assertEqual(self.emails(out), {"clean@example.test"})
        (realtor,) = out["realtors"]
        self.assertEqual((realtor["realtor_id"], realtor["agent_name"]), (h["realtor"], "Agent clean"))
        (event,) = realtor["events"]
        self.assertEqual((event["listing_id"], event["match_ids"], event["bases"]), (h["listing"], [h["match"]], ["radar"]))
        self.assertEqual(event["matches"], [{"match_id": h["match"], "basis": "radar", "level": "agree", "set_id": self.set_id}])
        self.assertEqual(event["utc_datetime"].astimezone(DISPLAY_TZ).date(), self.day)
        self.assertEqual(event["utc_datetime"].astimezone(DISPLAY_TZ).hour, 12)

    def test_an_event_has_no_size_and_no_distance(self):
        self.house("clean")
        (event,) = self.listed()["realtors"][0]["events"]
        self.assertNotIn("magnitude", event)
        self.assertNotIn("distance_miles", event)

    def test_the_result_says_which_set_it_used_and_the_radar_numbers_for_its_sentence(self):
        self.house("clean")
        out = self.listed()
        want = self.conn.execute(
            "SELECT r.hail_threshold_in AS h, r.report_radius_km AS k FROM likelihood_parameter_sets s "
            "JOIN radar_parameter_sets r ON r.set_id = s.radar_set_id WHERE s.set_id = %s", (self.set_id,)).fetchone()
        self.assertEqual(out["set"]["set_id"], self.set_id)
        self.assertEqual((out["set"]["hail_in"], out["set"]["radius_km"]), (want["h"], want["k"]))
        self.assertEqual(out["levels"], ["agree"])

    def test_a_day_with_no_zone_matches_gives_an_empty_list(self):
        out = self.listed(days=[self.day - timedelta(days=1)])
        self.assertEqual(out["realtors"], [])
        self.assertEqual(out["considered"], {"matches": 0, "realtors": 0})

    # -- the eligibility rules ---------------------------------------------------------------------

    def test_a_storm_older_than_the_age_limit_is_too_old(self):
        old = self.day - timedelta(days=40)
        self.house("old", day=old)
        out = self.listed(days=[old], max_age_days=30)
        self.assertEqual((self.emails(out), self.dropped(out, "too_old")), (set(), 1))
        self.assertEqual(self.emails(self.listed(days=[old], max_age_days=60)), {"old@example.test"})

    def test_an_inactive_listing_is_dropped(self):
        self.house("inactive", status="Inactive")
        out = self.listed()
        self.assertEqual((self.emails(out), self.dropped(out, "inactive_listing")), (set(), 1))

    def test_a_listing_not_seen_lately_is_stale(self):
        self.house("stale", seen=NOW - timedelta(days=10))
        out = self.listed(freshness_days=7)
        self.assertEqual((self.emails(out), self.dropped(out, "stale_listing")), (set(), 1))
        self.assertEqual(self.emails(self.listed(freshness_days=14)), {"stale@example.test"})

    def test_no_realtor_or_no_email_is_dropped(self):
        self.house("none", no_realtor=True)
        self.house("blank", email=None)
        out = self.listed()
        self.assertEqual((self.emails(out), self.dropped(out, "no_agent_email")), (set(), 2))

    def test_a_match_already_sent_is_dropped_but_a_failed_send_is_not_a_send(self):
        done = self.house("done")
        self.record_send(done, status="sent")
        failed = self.house("failed")
        self.record_send(failed, status="failed")
        out = self.listed(cap_days=None)
        self.assertEqual(self.emails(out), {"failed@example.test"})
        self.assertEqual(self.dropped(out, "already_emailed"), 1)

    def test_a_queued_send_also_counts_as_sent(self):
        queued = self.house("queued")
        self.record_send(queued, status="queued")
        out = self.listed(cap_days=None)
        self.assertEqual((self.emails(out), self.dropped(out, "already_emailed")), (set(), 1))

    def test_another_listing_of_the_same_house_already_emailed_is_a_duplicate_address(self):
        first = self.house("first", address="9 Shared Ln, Calhan, CO 80808")
        self.house("second", address="9 Shared Ln, Calhan, CO 80808")
        self.record_send(first)
        out = self.listed(cap_days=None)
        self.assertEqual(self.emails(out), set())
        self.assertEqual((self.dropped(out, "already_emailed"), self.dropped(out, "duplicate_address")), (1, 1))

    def test_a_failed_send_to_the_same_house_does_not_block_it(self):
        # Had the failed send counted, the second listing would be a duplicate and nobody would be emailed.  Two agents on
        # one house is still one email, to the better ranked listing (the more recently seen), the other a duplicate.
        first = self.house("first", address="9 Shared Ln, Calhan, CO 80808", seen=NOW - timedelta(hours=3))
        self.house("second", address="9 Shared Ln, Calhan, CO 80808", seen=NOW)
        self.record_send(first, status="failed")
        out = self.listed(cap_days=None)
        self.assertEqual(self.emails(out), {"second@example.test"})
        self.assertEqual((self.dropped(out, "already_emailed"), self.dropped(out, "duplicate_address")), (0, 1))

    def test_a_house_emailed_for_a_report_the_same_day_is_not_emailed_again(self):
        report_side = self.house("reportside", address="12 Cross Kind Rd, Calhan, CO 80808")
        report_match = self.report_match(report_side["listing"])
        report_house = dict(report_side, match=report_match)
        # The report send, as the report path leaves it.
        email_id = self.conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, sync_run_id, permission_asserted, "
            "created_by, subject, html_body) VALUES (%s, %s, %s, %s, %s, 'none', %s, 's', 'b') RETURNING email_id",
            (uuid.uuid4(), report_side["realtor"], "x@example.test", self.report_tpl, self.sync, self.emp)).fetchone()["email_id"]
        self.conn.execute(
            "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by, email_id, send_status, sent_at) "
            "VALUES (%s, 'x@example.test', %s, %s, %s, %s, 'sent', now())",
            (report_side["realtor"], report_match, self.report_tpl, self.emp, email_id))
        self.house("zoneside", address="12 Cross Kind Rd, Calhan, CO 80808")
        out = self.listed(cap_days=None)
        # Both zone matches at that house are held back:  the report side's own and the other listing's.
        self.assertEqual(self.dropped(out, "duplicate_address"), 2)
        self.assertEqual(self.emails(out), set())

    def test_a_different_storm_day_at_the_same_house_is_not_a_duplicate(self):
        earlier = self.day - timedelta(days=5)
        first = self.house("first", address="9 Shared Ln, Calhan, CO 80808", day=earlier)
        self.record_send(first)
        self.house("second", address="9 Shared Ln, Calhan, CO 80808")
        out = self.listed(cap_days=None)
        self.assertEqual(self.dropped(out, "duplicate_address"), 0)
        self.assertEqual(self.emails(out), {"second@example.test"})

    def test_an_address_on_the_do_not_contact_list_is_dropped_and_a_lifted_entry_is_not(self):
        self.house("blocked")
        self.house("lifted")
        self.conn.execute("INSERT INTO dnc_list (email_raw, added_by, source) VALUES ('blocked@example.test', %s, 'manual')",
                          (self.emp,))
        self.conn.execute("INSERT INTO dnc_list (email_raw, added_by, source, removed_at, removed_by) "
                          "VALUES ('lifted@example.test', %s, 'manual', now(), %s)", (self.emp, self.emp))
        out = self.listed()
        self.assertEqual(self.emails(out), {"lifted@example.test"})
        self.assertEqual(self.dropped(out, "on_dnc_list"), 1)

    def test_the_do_not_contact_match_ignores_case_and_spaces(self):
        self.house("shout", email="Shout@Example.Test ")
        self.conn.execute("INSERT INTO dnc_list (email_raw, added_by, source) VALUES ('shout@example.test', %s, 'manual')",
                          (self.emp,))
        out = self.listed()
        self.assertEqual((self.emails(out), self.dropped(out, "on_dnc_list")), (set(), 1))

    def test_a_realtor_emailed_within_the_cap_is_held_and_the_cap_can_be_off_or_expired(self):
        agent = self.house("capped")
        self.record_send(agent, created=NOW - timedelta(days=3))
        later = self.house("later", realtor_id=agent["realtor"], address="77 Other Way, Calhan, CO 80808")
        out = self.listed(cap_days=14)
        self.assertEqual(self.dropped(out, "within_cap"), 1)
        self.assertNotIn("capped@example.test", self.emails(out))
        self.assertEqual(self.dropped(self.listed(cap_days=None), "within_cap"), 0)
        self.assertEqual(self.dropped(self.listed(cap_days=2), "within_cap"), 0)       # 3 days ago is past a 2-day cap
        self.assertIsNotNone(later)

    def test_a_failed_send_does_not_start_the_cap(self):
        agent = self.house("capped")
        self.record_send(agent, status="failed", created=NOW - timedelta(days=1))
        self.house("later", realtor_id=agent["realtor"], address="77 Other Way, Calhan, CO 80808")
        self.assertEqual(self.dropped(self.listed(cap_days=14), "within_cap"), 0)

    def test_each_hit_is_counted_under_the_first_rule_that_drops_it(self):
        self.house("both", status="Inactive", seen=NOW - timedelta(days=30))
        out = self.listed()
        self.assertEqual((self.dropped(out, "inactive_listing"), self.dropped(out, "stale_listing")), (1, 0))

    def test_the_counts_add_up(self):
        self.house("keep")
        self.house("inactive", status="Inactive")
        self.house("stale", seen=NOW - timedelta(days=20))
        self.house("none", no_realtor=True)
        out = self.listed(cap_days=None)
        kept = sum(len(e["match_ids"]) for r in out["realtors"] for e in r["events"])
        dropped = sum(v["matches"] for v in out["excluded"].values())
        self.assertEqual(out["considered"]["matches"], kept + dropped)
        self.assertEqual(set(out["excluded"]), set(sendlist.REASONS) | {zonesend.BLOCKED})

    # -- the observer-wording gate -------------------------------------------------------------------

    def test_observer_and_both_matches_are_left_out_under_their_own_reason_until_approved(self):
        self.house("radar", basis="radar")
        self.house("observer", basis="observer")
        self.house("both", basis="both")
        out = self.listed(cap_days=None)
        self.assertEqual(self.emails(out), {"radar@example.test"})
        self.assertEqual(out["excluded"][zonesend.BLOCKED], {"matches": 2, "realtors": 2})
        self.assertEqual(out["considered"], {"matches": 3, "realtors": 3})
        self.assertEqual(zonesend.BLOCKED, "observer_wording_not_approved")

    def test_once_approved_they_are_offered_with_their_bases(self):
        self.house("radar", basis="radar")
        self.house("observer", basis="observer")
        self.house("both", basis="both")
        self.approve()
        out = self.listed(cap_days=None)
        self.assertEqual(self.emails(out), {"radar@example.test", "observer@example.test", "both@example.test"})
        self.assertEqual(out["excluded"][zonesend.BLOCKED], {"matches": 0, "realtors": 0})
        bases = {r["email"]: r["events"][0]["bases"] for r in out["realtors"]}
        self.assertEqual(bases, {"radar@example.test": ["radar"], "observer@example.test": ["observer"],
                                 "both@example.test": ["both"]})

    def test_a_blocked_match_does_not_hide_the_houses_other_matches(self):
        # One house, two listings, two matches:  the radar one is offered, the observer one is not.
        self.house("a", address="5 Pair St, Calhan, CO 80808", basis="radar")
        self.house("b", address="5 Pair St, Calhan, CO 80808", basis="observer")
        out = self.listed(cap_days=None)
        offered = [m["basis"] for r in out["realtors"] for e in r["events"] for m in e["matches"]]
        self.assertEqual(offered, ["radar"])
        self.assertEqual(out["excluded"][zonesend.BLOCKED]["matches"], 1)

    # -- levels, sets, kinds ---------------------------------------------------------------------------

    def test_only_the_chosen_levels_are_offered(self):
        self.house("agree", level="agree")
        self.house("one", level="one_source")
        self.assertEqual(self.emails(self.listed(cap_days=None)), {"agree@example.test"})
        self.assertEqual(self.emails(self.listed(cap_days=None, levels=("one_source",))), {"one@example.test"})
        both = self.listed(cap_days=None, levels=("agree", "one_source"))
        self.assertEqual(self.emails(both), {"agree@example.test", "one@example.test"})
        self.assertEqual(both["levels"], ["agree", "one_source"])

    def test_context_and_unknown_levels_are_refused(self):
        for bad in ((), ("context",), ("agree", "none"), ("AGREE",)):
            with self.subTest(levels=bad):
                with self.assertRaises(ValueError):
                    self.listed(levels=bad)

    def test_only_the_chosen_set_is_offered(self):
        self.house("mine")
        other = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "SELECT 'another set', radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi "
            "FROM likelihood_parameter_sets WHERE set_id = %s RETURNING set_id", (self.set_id,)).fetchone()["set_id"]
        self.house("theirs", set_id=other)
        self.assertEqual(self.emails(self.listed(set_id=self.set_id, cap_days=None)), {"mine@example.test"})
        picked = self.listed(set_id=other, cap_days=None)
        self.assertEqual(self.emails(picked), {"theirs@example.test"})
        self.assertEqual(picked["set"]["set_id"], other)
        # With none named, the newest set is used:  the one just made.
        self.assertEqual(self.listed(cap_days=None)["set"]["set_id"], other)

    def test_an_unknown_set_is_refused(self):
        with self.assertRaises(ValueError):
            self.listed(set_id=987654321)

    def test_report_matches_never_appear(self):
        # The test deletes the match it made; hail_app has no DELETE on storm_listing_matches (item 241).
        if not self.conn.execute("SELECT has_table_privilege('storm_listing_matches', 'DELETE') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can delete matches (hail_admin)")
        h = self.house("zone")
        reportish = self.house("report")
        self.report_match(reportish["listing"])
        self.conn.execute("DELETE FROM storm_listing_matches WHERE match_id = %s", (reportish["match"],))
        out = self.listed(cap_days=None)
        self.assertEqual(self.emails(out), {"zone@example.test"})
        self.assertEqual(out["considered"]["matches"], 1)
        self.assertIsNotNone(h)

    # -- grouping ----------------------------------------------------------------------------------

    def test_one_house_through_two_listings_is_one_event_and_each_match_keeps_its_own_record(self):
        self.approve()
        # The same agent carries both listings of the house (with two agents only the better ranked is emailed).
        a = self.house("a", address="5 Pair St, Calhan, CO 80808", basis="radar", seen=NOW - timedelta(hours=2))
        b = self.house("b", address="5 Pair St, Calhan, CO 80808", basis="observer", realtor_id=a["realtor"])
        out = self.listed(cap_days=None)
        self.assertEqual(len(out["realtors"]), 1)
        (event,) = out["realtors"][0]["events"]
        self.assertEqual(sorted(m["match_id"] for m in event["matches"]), sorted([a["match"], b["match"]]))
        self.assertEqual(sorted(event["match_ids"]), sorted([a["match"], b["match"]]))
        self.assertEqual(event["bases"], ["observer", "radar"])
        by_match = {m["match_id"]: m for m in event["matches"]}
        self.assertEqual((by_match[a["match"]]["basis"], by_match[b["match"]]["basis"]), ("radar", "observer"))
        self.assertTrue(all(m["set_id"] == self.set_id for m in event["matches"]))

    def test_one_agent_with_two_houses_gets_one_entry_with_two_events_in_order(self):
        first = self.house("one", address="1 Row St, Calhan, CO 80808")
        self.house("two", realtor_id=first["realtor"], address="2 Row St, Calhan, CO 80808")
        out = self.listed(cap_days=None)
        self.assertEqual(len(out["realtors"]), 1)
        self.assertEqual(len(out["realtors"][0]["events"]), 2)
        keys = [(e["utc_datetime"], e["listing_id"]) for e in out["realtors"][0]["events"]]
        self.assertEqual(keys, sorted(keys))

    def test_two_storm_days_are_two_events(self):
        first = self.house("one", address="1 Row St, Calhan, CO 80808")
        other_day = self.day - timedelta(days=1)
        self.conn.execute("INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
                          "VALUES (%s, %s, 'radar', 'agree', 'core', %s)", (first["listing"], other_day, self.set_id))
        out = self.listed(days=[self.day, other_day], cap_days=None)
        self.assertEqual(len(out["realtors"][0]["events"]), 2)

    def test_the_internal_rank_is_not_left_in_the_event(self):
        self.house("a")
        (event,) = self.listed()["realtors"][0]["events"]
        self.assertNotIn("rank", event)

    # -- it writes nothing -----------------------------------------------------------------------------

    def test_listing_writes_nothing(self):
        self.house("a")
        self.house("b", basis="observer")
        tables = ("storm_listing_matches", "send_log", "sent_emails", "listings", "properties", "realtors", "settings_history")
        before = {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in tables}
        self.listed(cap_days=None)
        self.listed(cap_days=14, levels=("agree", "one_source"))
        after = {t: self.conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in tables}
        self.assertEqual(before, after)


class SelectUnchangedTest(unittest.TestCase):
    """sendlist.select with no collapse_fn is exactly what it was:  the report path is untouched."""

    def test_the_default_collapse_is_still_the_reports_one(self):
        import inspect
        sig = inspect.signature(sendlist.select)
        self.assertIsNone(sig.parameters["collapse_fn"].default)
        hit = {"match_id": 1, "listing_id": 1, "realtor_id": 1, "list_status": "Active", "list_last_seen": NOW,
               "address": "1 A St", "address_key": "1astcalhan", "utc_datetime": NOW - timedelta(days=1),
               "magnitude": 1, "distance_miles": 2, "agent_name": "A B", "email": "a@example.test", "already_sent": False,
               "on_dnc": False, "in_cap": False, "key_emailed": False}
        out = sendlist.select([hit], today=NOW.astimezone(DISPLAY_TZ).date())
        (event,) = out["realtors"][0]["events"]
        self.assertEqual((event["magnitude"], event["distance_miles"]), (1, 2))
        self.assertNotIn("matches", event)

    def test_a_given_collapse_function_is_used(self):
        hit = {"match_id": 1, "listing_id": 1, "realtor_id": 1, "list_status": "Active", "list_last_seen": NOW,
               "address": "1 A St", "address_key": None, "utc_datetime": NOW - timedelta(days=1),
               "magnitude": 1, "distance_miles": 2, "agent_name": "A B", "email": "a@example.test", "already_sent": False,
               "on_dnc": False, "in_cap": False, "key_emailed": False}
        out = sendlist.select([hit], today=NOW.astimezone(DISPLAY_TZ).date(), collapse_fn=lambda rows: ["mine", len(rows)])
        self.assertEqual(out["realtors"][0]["events"], ["mine", 1])


if __name__ == "__main__":
    unittest.main()
