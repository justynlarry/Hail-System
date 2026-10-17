"""Tests for hailsys/queries/dripfollowup.py -- who gets a drip follow-up.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_followup

The rules are pure and tested on fixtures.  The fetch functions run against a fake connection.  One
test runs the three queries against a real database (skips without PGHOST) to prove they parse.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.queries import dripfollowup as dfu
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None

HAVE_DB = bool(os.environ.get("PGHOST")) and psycopg is not None

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
STORM = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)


def hit(match_id, listing_id, realtor_id=1, **over):
    base = {"match_id": match_id, "listing_id": listing_id, "rentcast_id": f"rc-{listing_id}",
            "realtor_id": realtor_id, "list_last_seen": NOW, "address": f"{listing_id} Main St",
            "address_key": f"key-{listing_id}", "utc_datetime": STORM, "magnitude": 1.5,
            "distance_miles": 2.0, "agent_name": f"Agent {realtor_id}",
            "email": f"agent{realtor_id}@example.com", "send_status": "sent",
            "on_dnc": False, "in_cap": False}
    base.update(over)
    return base


def pick(hits, dead=(), verdicts=None):
    """Run the rules with every listing active unless the verdicts say otherwise."""
    if verdicts is None:
        verdicts = {h["listing_id"]: "active" for h in hits}
    return dfu.select_followup(hits, set(dead), verdicts)


def emailed(result):
    return {r["realtor_id"]: sorted(m for e in r["events"] for m in e["match_ids"])
            for r in result["realtors"]}


def stops(result):
    return {s["realtor_id"]: s["reason"] for s in result["to_stop"]}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RulesTest(unittest.TestCase):
    def test_an_active_listing_is_kept_with_its_match_and_name(self):
        result = pick([hit(10, 100)])
        self.assertEqual(emailed(result), {1: [10]})
        self.assertEqual(result["realtors"][0]["email"], "agent1@example.com")
        self.assertEqual(result["realtors"][0]["agent_name"], "Agent 1")
        self.assertEqual(result["to_stop"], [])

    def test_two_matches_for_one_house_and_day_are_one_event_with_both_matches(self):
        result = pick([hit(10, 100), hit(11, 100)])
        events = result["realtors"][0]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(sorted(events[0]["match_ids"]), [10, 11])

    def test_the_reasons_are_the_documented_ones_in_order(self):
        self.assertEqual(dfu.REASONS, ("recipient_bounced", "on_dnc_list", "within_cap",
                                       "listing_dead_earlier", "listing_inactive",
                                       "listing_not_found", "listing_unconfirmed"))

    def test_each_listing_verdict_has_its_own_reason(self):
        cases = (("inactive", "listing_inactive"), ("not_found", "listing_not_found"),
                 ("error", "listing_unconfirmed"), (None, "listing_unconfirmed"),
                 ("anything else", "listing_unconfirmed"))
        for outcome, reason in cases:
            with self.subTest(outcome=outcome):
                verdicts = {} if outcome is None else {100: outcome}
                result = pick([hit(10, 100)], verdicts=verdicts)
                self.assertEqual(result["realtors"], [])
                self.assertEqual(result["excluded"][reason], {"matches": 1, "realtors": 1})
                self.assertEqual(sum(v["matches"] for v in result["excluded"].values()), 1)

    def test_a_listing_found_dead_earlier_stays_out_whatever_this_step_says(self):
        result = pick([hit(10, 100)], dead={100}, verdicts={100: "active"})
        self.assertEqual(result["realtors"], [])
        self.assertEqual(result["excluded"]["listing_dead_earlier"], {"matches": 1, "realtors": 1})

    def test_recipient_rules_come_before_listing_rules_and_in_order(self):
        cases = (({"send_status": "bounced", "on_dnc": True, "in_cap": True}, "recipient_bounced"),
                 ({"on_dnc": True, "in_cap": True}, "on_dnc_list"),
                 ({"in_cap": True}, "within_cap"))
        for flags, reason in cases:
            with self.subTest(reason=reason):
                result = pick([hit(10, 100, **flags)], dead={100}, verdicts={100: "inactive"})
                self.assertEqual(result["excluded"][reason], {"matches": 1, "realtors": 1})
                self.assertEqual(sum(v["matches"] for v in result["excluded"].values()), 1)

    def test_one_bounced_row_holds_back_every_row_of_that_recipient(self):
        result = pick([hit(10, 100, send_status="bounced"), hit(11, 101)])
        self.assertEqual(result["realtors"], [])
        self.assertEqual(result["excluded"]["recipient_bounced"], {"matches": 2, "realtors": 1})

    def test_other_recipients_are_not_affected_by_one_being_held_back(self):
        result = pick([hit(10, 100, 1, on_dnc=True), hit(11, 101, 2)])
        self.assertEqual(emailed(result), {2: [11]})

    def test_a_recipient_with_one_dead_listing_still_gets_the_others(self):
        result = pick([hit(10, 100), hit(11, 101)], verdicts={100: "active", 101: "inactive"})
        self.assertEqual(emailed(result), {1: [10]})
        self.assertEqual(result["to_stop"], [])
        self.assertEqual(result["excluded"]["listing_inactive"], {"matches": 1, "realtors": 1})

    def test_considered_counts_every_match_and_recipient(self):
        result = pick([hit(10, 100, 1), hit(11, 101, 1), hit(12, 102, 2, on_dnc=True)])
        self.assertEqual(result["considered"], {"matches": 3, "realtors": 2})

    def test_realtors_are_sorted_and_no_hits_gives_an_empty_result(self):
        result = pick([hit(10, 100, 5), hit(11, 101, 2)])
        self.assertEqual([r["realtor_id"] for r in result["realtors"]], [2, 5])
        empty = pick([])
        self.assertEqual((empty["realtors"], empty["to_stop"]), ([], []))
        self.assertEqual(empty["considered"], {"matches": 0, "realtors": 0})
        self.assertEqual(set(empty), {"realtors", "considered", "excluded", "to_stop"})

    def test_excluded_has_every_reason_even_when_zero(self):
        self.assertEqual(set(pick([hit(10, 100)])["excluded"]), set(dfu.REASONS))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ToStopTest(unittest.TestCase):
    def test_bounced_and_unsubscribed_are_stopped_with_those_reasons(self):
        result = pick([hit(10, 100, 1, send_status="bounced"), hit(11, 101, 2, on_dnc=True)])
        self.assertEqual(stops(result), {1: "bounced", 2: "unsubscribed"})
        self.assertEqual(result["to_stop"][0]["email"], "agent1@example.com")

    def test_bounced_wins_over_dead_listings(self):
        result = pick([hit(10, 100, send_status="bounced")], verdicts={100: "inactive"})
        self.assertEqual(stops(result), {1: "bounced"})

    def test_all_listings_definitely_dead_stops_with_no_active_listings(self):
        hits = [hit(10, 100), hit(11, 101), hit(12, 102)]
        result = pick(hits, dead={100}, verdicts={100: "active", 101: "inactive", 102: "not_found"})
        self.assertEqual(stops(result), {1: "no_active_listings"})

    def test_one_unconfirmed_listing_means_not_stopped(self):
        for outcome in ("error", None):
            with self.subTest(outcome=outcome):
                verdicts = {100: "inactive"}
                if outcome:
                    verdicts[101] = outcome
                result = pick([hit(10, 100), hit(11, 101)], verdicts=verdicts)
                self.assertEqual(result["to_stop"], [])

    def test_only_unconfirmed_listings_is_not_a_stop(self):
        # Nothing was checked (say the job died): nobody is stopped for it.
        self.assertEqual(pick([hit(10, 100)], verdicts={})["to_stop"], [])

    def test_held_by_the_cap_is_skipped_not_stopped_even_if_every_listing_is_dead(self):
        result = pick([hit(10, 100, in_cap=True)], verdicts={100: "inactive"})
        self.assertEqual(result["to_stop"], [])
        self.assertEqual(result["realtors"], [])

    def test_a_recipient_who_gets_an_email_is_never_stopped(self):
        result = pick([hit(10, 100), hit(11, 101)], verdicts={100: "active", 101: "inactive"})
        self.assertEqual(result["to_stop"], [])

    def test_to_stop_is_sorted_by_realtor(self):
        result = pick([hit(10, 100, 9, on_dnc=True), hit(11, 101, 3, on_dnc=True)])
        self.assertEqual([s["realtor_id"] for s in result["to_stop"]], [3, 9])

    def test_the_stop_reasons_are_the_ones_the_table_allows(self):
        allowed = {"unsubscribed", "bounced", "no_active_listings", "manual", "replied"}
        result = pick([hit(10, 100, 1, send_status="bounced"), hit(11, 101, 2, on_dnc=True),
                       hit(12, 102, 3)], verdicts={102: "inactive"})
        self.assertEqual(len(result["to_stop"]), 3)
        self.assertLessEqual({s["reason"] for s in result["to_stop"]}, allowed)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ListingsToCheckTest(unittest.TestCase):
    def test_each_listing_once_with_its_rentcast_id_and_recipient(self):
        got = dfu.listings_to_check([hit(10, 101), hit(11, 100), hit(12, 100)], set())
        self.assertEqual(got, [{"listing_id": 100, "rentcast_id": "rc-100",
                                "email": "agent1@example.com"},
                               {"listing_id": 101, "rentcast_id": "rc-101",
                                "email": "agent1@example.com"}])

    def test_nobody_held_back_is_checked(self):
        hits = [hit(10, 100, 1, send_status="bounced"), hit(11, 101, 2, on_dnc=True),
                hit(12, 102, 3, in_cap=True), hit(13, 103, 4)]
        self.assertEqual([r["listing_id"] for r in dfu.listings_to_check(hits, set())], [103])

    def test_a_listing_found_dead_earlier_is_not_checked_again(self):
        got = dfu.listings_to_check([hit(10, 100), hit(11, 101)], {100})
        self.assertEqual([r["listing_id"] for r in got], [101])

    def test_nothing_to_check(self):
        self.assertEqual(dfu.listings_to_check([], set()), [])

    def test_the_count_matches_what_select_followup_would_need(self):
        hits = [hit(10, 100), hit(11, 101, 2, on_dnc=True)]
        self.assertEqual(len(dfu.listings_to_check(hits, set())), 1)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self.rows


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class FetchTest(unittest.TestCase):
    def test_the_cap_window_is_cap_days_before_now(self):
        conn = FakeConn([])
        dfu.fetch_hits(conn, 7, now=NOW)
        self.assertEqual(conn.calls[0][1], {"campaign_id": 7, "since": NOW - timedelta(days=14)})
        dfu.fetch_hits(conn, 7, now=NOW, cap_days=3)
        self.assertEqual(conn.calls[1][1]["since"], NOW - timedelta(days=3))

    def test_cap_days_none_turns_the_cap_off(self):
        conn = FakeConn([])
        dfu.fetch_hits(conn, 7, now=NOW, cap_days=None)
        self.assertIsNone(conn.calls[0][1]["since"])

    def test_dead_is_a_set_of_listing_ids_for_earlier_steps_only(self):
        conn = FakeConn([{"listing_id": 4}, {"listing_id": 9}])
        self.assertEqual(dfu.fetch_dead(conn, 7, 3), {4, 9})
        self.assertEqual(conn.calls[0][1], {"campaign_id": 7, "step_no": 3})
        sql = conn.calls[0][0]
        self.assertIn("step_no < %(step_no)s", sql)
        self.assertIn("outcome IN ('inactive', 'not_found')", sql)

    def test_verdicts_map_listing_to_its_latest_outcome_for_this_step(self):
        conn = FakeConn([{"listing_id": 4, "outcome": "active"}, {"listing_id": 9, "outcome": "error"}])
        self.assertEqual(dfu.fetch_verdicts(conn, 7, 3), {4: "active", 9: "error"})
        sql = conn.calls[0][0]
        self.assertIn("step_no = %(step_no)s", sql)
        self.assertIn("DISTINCT ON (listing_id)", sql)
        self.assertIn("checked_at DESC, check_id DESC", sql)

    def test_the_hits_query_reads_the_first_batch_of_live_recipients_only(self):
        sql = dfu._HITS_SQL
        for part in ("e.batch_id = c.first_batch_id", "dr.stopped_at IS NULL",
                     "s.send_status <> 'failed'", "d.removed_at IS NULL",
                     "e2.batch_id <> c.first_batch_id", "ds.batch_id = e2.batch_id",
                     "JOIN realtors r              ON r.realtor_id = e.realtor_id"):
            self.assertIn(part, sql)

    def test_build_uses_the_hits_the_dead_set_and_the_verdicts_together(self):
        class Conn(FakeConn):
            def execute(self, sql, params):
                self.rows = {"FROM drip_campaigns": [hit(10, 100), hit(11, 101)],
                             "step_no <": [{"listing_id": 101}],
                             "step_no =": [{"listing_id": 100, "outcome": "active"},
                                           {"listing_id": 101, "outcome": "active"}]}[
                    "FROM drip_campaigns" if "FROM drip_campaigns" in sql
                    else "step_no <" if "step_no <" in sql else "step_no ="]
                return super().execute(sql, params)
        result = dfu.build_followup_list(Conn([]), 7, 2, now=NOW)
        self.assertEqual(emailed(result), {1: [10]})
        self.assertEqual(result["excluded"]["listing_dead_earlier"]["matches"], 1)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(HAVE_DB, "no database (PGHOST unset)")
class QueriesParseTest(unittest.TestCase):
    def test_the_three_queries_run_and_a_missing_campaign_is_empty(self):
        with psycopg.connect(row_factory=dict_row) as conn:
            for cap in (14, None):
                self.assertEqual(dfu.fetch_hits(conn, -1, now=NOW, cap_days=cap), [])
            self.assertEqual(dfu.fetch_dead(conn, -1, 2), set())
            self.assertEqual(dfu.fetch_verdicts(conn, -1, 2), {})
            result = dfu.build_followup_list(conn, -1, 2, now=NOW)
        self.assertEqual(result["realtors"], [])


if __name__ == "__main__":
    unittest.main()
