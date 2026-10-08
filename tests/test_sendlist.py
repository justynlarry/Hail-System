"""Tests for hailsys/queries/sendlist.py.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_sendlist

The rules are tested on made-up rows (no database).  The query itself is run read-only
against the real tables: it must work on an empty day, and on the latest real storm day the
numbers must add up and no rule may have been bypassed.  Nothing is written.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.queries import sendlist as sl
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
NOW = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
TODAY = date(2026, 10, 8)
AUG15 = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)        # 14:00 in Denver, Sept 22


def hit(match_id, listing_id=1, realtor_id=10, when=AUG15, mag="1.00", dist="2.00",
        status="Active", sent=False, dnc=False, cap=False, email="a@x.invalid", address="1 St"):
    return {"match_id": match_id, "listing_id": listing_id, "realtor_id": realtor_id,
            "list_status": status, "address": address, "utc_datetime": when,
            "magnitude": None if mag is None else Decimal(mag),
            "distance_miles": Decimal(dist), "agent_name": "Jane Smith", "email": email,
            "already_sent": sent, "on_dnc": dnc, "in_cap": cap}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class CollapseTest(unittest.TestCase):
    def test_several_reports_one_day_become_one_event(self):
        events = sl.collapse([hit(1, mag="1.00", dist="3.00"),
                              hit(2, mag="1.75", dist="4.00"),
                              hit(3, mag="1.25", dist="0.50")])
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual((e["magnitude"], e["distance_miles"]), (Decimal("1.75"), Decimal("0.50")))
        self.assertEqual(e["match_ids"], [1, 2, 3])

    def test_the_earliest_report_gives_the_time(self):
        early = AUG15 - timedelta(hours=2)
        e = sl.collapse([hit(1), hit(2, when=early)])[0]
        self.assertEqual(e["utc_datetime"], early)

    def test_nan_and_missing_sizes_are_ignored(self):
        e = sl.collapse([hit(1, mag="NaN"), hit(2, mag=None), hit(3, mag="1.25")])[0]
        self.assertEqual(e["magnitude"], Decimal("1.25"))
        none = sl.collapse([hit(1, mag="NaN"), hit(2, mag=None)])[0]
        self.assertIsNone(none["magnitude"])

    def test_two_days_are_two_events(self):
        events = sl.collapse([hit(1), hit(2, when=AUG15 + timedelta(days=6))])
        self.assertEqual(len(events), 2)

    def test_the_day_is_the_denver_day(self):
        # 23:30 on Sept 22 in Denver is 05:30 UTC on Sept 23: still one Denver day with 20:00 UTC.
        late = datetime(2026, 9, 23, 5, 30, tzinfo=UTC)
        self.assertEqual(len(sl.collapse([hit(1), hit(2, when=late)])), 1)

    def test_two_listings_are_two_events(self):
        self.assertEqual(len(sl.collapse([hit(1, listing_id=1), hit(2, listing_id=2)])), 2)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SelectTest(unittest.TestCase):
    def counts(self, out, reason):
        return (out["excluded"][reason]["matches"], out["excluded"][reason]["realtors"])

    def test_a_clean_hit_is_kept(self):
        out = sl.select([hit(1)], today=TODAY)
        self.assertEqual([r["realtor_id"] for r in out["realtors"]], [10])
        self.assertEqual(out["realtors"][0]["email"], "a@x.invalid")
        self.assertEqual(out["considered"], {"matches": 1, "realtors": 1})

    def test_each_rule_drops_its_hits(self):
        hits = [hit(1, when=AUG15 - timedelta(days=60)),     # too old
                hit(2, status="Inactive"),
                hit(3, realtor_id=None, email=None),
                hit(4, sent=True),
                hit(5, dnc=True),
                hit(6, cap=True),
                hit(7, realtor_id=11)]                       # fine
        out = sl.select(hits, today=TODAY)
        self.assertEqual(self.counts(out, "too_old"), (1, 1))
        self.assertEqual(self.counts(out, "inactive_listing"), (1, 1))
        self.assertEqual(self.counts(out, "no_agent_email"), (1, 0))      # no realtor to count
        self.assertEqual(self.counts(out, "already_emailed"), (1, 1))
        self.assertEqual(self.counts(out, "on_dnc_list"), (1, 1))
        self.assertEqual(self.counts(out, "within_cap"), (1, 1))
        self.assertEqual([r["realtor_id"] for r in out["realtors"]], [11])

    def test_a_hit_is_counted_under_the_first_rule_only(self):
        out = sl.select([hit(1, status="Inactive", dnc=True, cap=True)], today=TODAY)
        self.assertEqual(self.counts(out, "inactive_listing"), (1, 1))
        self.assertEqual(self.counts(out, "on_dnc_list"), (0, 0))
        self.assertEqual(self.counts(out, "within_cap"), (0, 0))

    def test_the_age_boundary(self):
        edge = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)       # exactly 30 days before Oct 8: kept
        over = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)       # 31 days: dropped
        out = sl.select([hit(1, when=edge), hit(2, listing_id=2, when=over)], today=TODAY)
        self.assertEqual(self.counts(out, "too_old"), (1, 1))
        self.assertEqual(len(out["realtors"][0]["events"]), 1)

    def test_a_capped_realtor_is_dropped_whole(self):
        out = sl.select([hit(1, cap=True), hit(2, listing_id=2, cap=True)], today=TODAY)
        self.assertEqual(out["realtors"], [])
        self.assertEqual(self.counts(out, "within_cap"), (2, 1))

    def test_realtors_are_grouped_and_ordered(self):
        out = sl.select([hit(1, realtor_id=12, listing_id=3), hit(2, realtor_id=10, listing_id=1),
                         hit(3, realtor_id=10, listing_id=2)], today=TODAY)
        self.assertEqual([r["realtor_id"] for r in out["realtors"]], [10, 12])
        self.assertEqual(len(out["realtors"][0]["events"]), 2)

    def test_nothing_in_nothing_out(self):
        out = sl.select([], today=TODAY)
        self.assertEqual(out["realtors"], [])
        self.assertEqual(out["considered"], {"matches": 0, "realtors": 0})

    def test_the_numbers_add_up(self):
        hits = [hit(1), hit(2, sent=True), hit(3, dnc=True, realtor_id=11), hit(4, status="Inactive")]
        out = sl.select(hits, today=TODAY)
        kept = sum(len(e["match_ids"]) for r in out["realtors"] for e in r["events"])
        dropped = sum(d["matches"] for d in out["excluded"].values())
        self.assertEqual(kept + dropped, out["considered"]["matches"])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class QueryTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)

    def test_a_day_with_no_storms_returns_nothing(self):
        out = sl.build_send_list(self.conn, [date(1999, 1, 1)], now=NOW)
        self.assertEqual(out["realtors"], [])
        self.assertEqual(out["considered"], {"matches": 0, "realtors": 0})

    def test_the_latest_real_storm_day_obeys_every_rule(self):
        row = self.conn.execute(
            "SELECT max((i.utc_datetime AT TIME ZONE 'America/Denver')::date) AS d "
            "FROM iem_data i JOIN storm_listing_matches m USING (iem_id) "
            "WHERE i.report_text = 'HAIL'").fetchone()
        if row["d"] is None:
            self.skipTest("no hail matches in this database")
        now = datetime.combine(row["d"] + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        out = sl.build_send_list(self.conn, [row["d"]], now=now)

        kept_matches = [mid for r in out["realtors"] for e in r["events"] for mid in e["match_ids"]]
        self.assertEqual(len(kept_matches), len(set(kept_matches)))         # no match twice
        dropped = sum(d["matches"] for d in out["excluded"].values())
        self.assertEqual(len(kept_matches) + dropped, out["considered"]["matches"])

        emails = [r["email"] for r in out["realtors"]]
        on_dnc = self.conn.execute(
            "SELECT count(*) AS n FROM dnc_list WHERE removed_at IS NULL AND email_norm = ANY(%s)",
            (emails,)).fetchone()["n"]
        self.assertEqual(on_dnc, 0)                                          # nobody suppressed
        already = self.conn.execute(
            "SELECT count(*) AS n FROM send_log WHERE send_status <> 'failed' AND match_id = ANY(%s)",
            (kept_matches,)).fetchone()["n"]
        self.assertEqual(already, 0)                                         # nobody emailed twice
        for r in out["realtors"]:
            for e in r["events"]:
                self.assertTrue(e["address"])
                self.assertTrue(e["match_ids"])


if __name__ == "__main__":
    unittest.main()