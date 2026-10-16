"""Tests for hailsys/rentcast/dripcheck.py -- what a by-id answer means, and the log row for it.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_drip_check

What they pin: the four outcomes; the freshness boundary, which is the same Denver-date rule
sendlist uses; that anything unreadable is an 'error' (the listing is left out); that every lookup
writes one row and commits it at once; that only auth and malformed-request failures stop the job;
that nothing from a response body reaches the row or a log line.  The last test needs a database
and skips without one.
"""

import logging
import os
import re
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.rentcast import client, dripcheck
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

try:
    import psycopg
except ImportError:
    psycopg = None

HAVE_DB = bool(os.environ.get("PGHOST")) and psycopg is not None

TODAY = date(2026, 10, 16)
FRESH = 7                       # the stale line is 2026-10-09: that day is fresh, the 8th is not
SECRET = "SECRET-BODY-TEXT"
AGENT = "agent@example.com"        # the address the step-1 email went to


def listing(**extra):
    base = {"id": "x", "status": "Active", "lastSeenDate": "2026-10-16T10:51:59.131Z",
            "removedDate": None, "listingAgent": {"name": "A", "email": AGENT}}
    base.update(extra)
    return base


def judge(**extra):
    return dripcheck.classify(listing(**extra), today=TODAY, freshness_days=FRESH,
                              agent_email=AGENT)


class FakeConn:
    def __init__(self):
        self.executed = []
        self.commits = 0
        self.events = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        self.events.append("commit")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, values):
        self.conn.executed.append((sql, values))
        self.conn.events.append("insert")


class Collector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ClassifyTest(unittest.TestCase):
    def test_a_recently_seen_active_listing_is_active(self):
        verdict = judge()
        self.assertEqual(verdict["outcome"], "active")
        self.assertEqual(verdict["rc_status"], "Active")
        self.assertIsNone(verdict["detail"])
        self.assertEqual(verdict["last_seen"],
                         datetime(2026, 10, 16, 10, 51, 59, 131000, tzinfo=timezone.utc))

    def test_any_other_status_is_inactive_and_keeps_what_rentcast_said(self):
        for status in ("Inactive", "Sold", "inactive", "active", "ACTIVE", "Pending"):
            with self.subTest(status=status):
                verdict = judge(status=status)
                self.assertEqual(verdict["outcome"], "inactive")
                self.assertEqual(verdict["rc_status"], status)

    def test_a_removal_date_makes_an_active_listing_inactive(self):
        verdict = judge(removedDate="2026-10-15T00:00:00.000Z")
        self.assertEqual(verdict["outcome"], "inactive")
        self.assertEqual(verdict["rc_status"], "Active")
        self.assertEqual(verdict["removed_at"],
                         datetime(2026, 10, 15, tzinfo=timezone.utc))

    def test_the_probe_answer_for_a_removed_listing(self):
        verdict = judge(status="Inactive", removedDate="2026-09-17T00:00:00.000Z",
                        lastSeenDate="2026-09-16T10:00:00.000Z")
        self.assertEqual(verdict["outcome"], "inactive")

    # ---- the freshness boundary: the same Denver-date rule as sendlist ----

    def test_the_last_fresh_day_is_active_and_the_day_before_is_stale(self):
        cases = (("2026-10-09T18:00:00.000Z", "active"),     # Oct 9 noon in Denver: the last fresh day
                 ("2026-10-08T18:00:00.000Z", "inactive"))   # Oct 8: stale
        for seen, outcome in cases:
            with self.subTest(seen=seen):
                self.assertEqual(judge(lastSeenDate=seen)["outcome"], outcome)

    def test_the_boundary_is_the_denver_date_not_the_utc_date(self):
        # 2026-10-10 02:00 UTC is still Oct 9 in Denver (MDT, UTC-6): fresh.
        self.assertEqual(judge(lastSeenDate="2026-10-10T02:00:00.000Z")["outcome"], "active")
        # 2026-10-09 05:00 UTC is Oct 8 in Denver: stale, though its UTC date is the 9th.
        self.assertEqual(judge(lastSeenDate="2026-10-09T05:00:00.000Z")["outcome"], "inactive")

    def test_a_stale_listing_says_why_and_stays_active_in_rc_status(self):
        verdict = judge(lastSeenDate="2026-09-01T12:00:00.000Z")
        self.assertEqual((verdict["outcome"], verdict["rc_status"]), ("inactive", "Active"))
        self.assertIn("2026-09-01", verdict["detail"])

    def test_the_window_is_the_one_passed_in(self):
        seen = "2026-10-12T18:00:00.000Z"
        for days, outcome in ((4, "active"), (3, "inactive")):
            with self.subTest(days=days):
                got = dripcheck.classify(listing(lastSeenDate=seen), today=TODAY,
                                         freshness_days=days, agent_email=AGENT)
                self.assertEqual(got["outcome"], outcome)

    def test_a_future_last_seen_date_is_not_stale(self):
        self.assertEqual(judge(lastSeenDate="2026-10-20T00:00:00.000Z")["outcome"], "active")

    # ---- the listing agent must still be the one emailed ----

    def test_the_same_agent_with_other_case_or_spaces_is_active(self):
        for written in ("AGENT@Example.COM", "  agent@example.com  ", "Agent@example.com\n"):
            with self.subTest(written=written):
                self.assertEqual(judge(listingAgent={"email": written})["outcome"], "active")

    def test_another_agent_makes_the_listing_inactive_and_says_so_without_an_address(self):
        verdict = judge(listingAgent={"email": "someone.else@example.com"})
        self.assertEqual((verdict["outcome"], verdict["rc_status"]), ("inactive", "Active"))
        self.assertIn("agent", verdict["detail"])
        self.assertNotIn("@", verdict["detail"])
        self.assertNotIn("example.com", verdict["detail"])

    def test_an_answer_that_does_not_name_the_agent_is_an_error(self):
        for agent in (None, {}, {"email": None}, {"email": ""}, {"email": "  "},
                      {"email": 5}, {"name": "A"}, "agent@example.com", ["agent@example.com"]):
            with self.subTest(agent=agent):
                self.assertEqual(judge(listingAgent=agent)["outcome"], "error")
        no_agent = listing()
        del no_agent["listingAgent"]
        self.assertEqual(dripcheck.classify(no_agent, today=TODAY, freshness_days=FRESH,
                                            agent_email=AGENT)["outcome"], "error")

    def test_without_an_address_to_compare_with_nothing_is_active(self):
        for blank in (None, "", "   ", 5):
            with self.subTest(blank=blank):
                got = dripcheck.classify(listing(), today=TODAY, freshness_days=FRESH,
                                         agent_email=blank)
                self.assertEqual(got["outcome"], "error")

    def test_a_removed_or_stale_listing_gives_that_reason_before_the_agent(self):
        other = {"email": "someone.else@example.com"}
        removed = judge(status="Inactive", listingAgent=other)
        self.assertIn("not active", removed["detail"])
        stale = judge(lastSeenDate="2026-09-01T12:00:00.000Z", listingAgent=other)
        self.assertIn("not seen since", stale["detail"])
        self.assertEqual(judge(status="Inactive", listingAgent=None)["outcome"], "inactive")

    # ---- anything unreadable is an error ----

    def test_a_missing_or_blank_status_is_an_error(self):
        for status in (None, "", "   ", 5, ["Active"]):
            with self.subTest(status=status):
                verdict = judge(status=status)
                self.assertEqual(verdict["outcome"], "error")
                self.assertIsNone(verdict["rc_status"])
        self.assertEqual(dripcheck.classify({}, today=TODAY, freshness_days=FRESH,
                                            agent_email=AGENT)["outcome"],
                         "error")

    def test_active_with_no_last_seen_date_is_an_error(self):
        self.assertEqual(judge(lastSeenDate=None)["outcome"], "error")
        self.assertEqual(judge(lastSeenDate=None, removedDate=None)["rc_status"], "Active")

    def test_a_date_that_cannot_be_read_is_an_error(self):
        for field in ("lastSeenDate", "removedDate"):
            for bad in ("yesterday", "", "2026-13-45T00:00:00Z", 20261016, ["x"],
                        "2026-10-16T10:00:00"):          # no time zone
                with self.subTest(field=field, bad=bad):
                    verdict = judge(**{field: bad})
                    self.assertEqual(verdict["outcome"], "error")
                    self.assertEqual(verdict["rc_status"], "Active")
                    self.assertIn("date", verdict["detail"])

    def test_an_unreadable_date_wins_over_an_inactive_status(self):
        # Not worth guessing what a garbled answer means.
        self.assertEqual(judge(status="Inactive", removedDate="garbage")["outcome"], "error")

    def test_the_status_is_trimmed_and_cut_for_the_column_but_only_exact_active_counts(self):
        verdict = judge(status=" Active ")
        self.assertEqual(verdict["outcome"], "inactive")
        self.assertEqual(verdict["rc_status"], "Active")
        self.assertEqual(len(judge(status="x" * 100)["rc_status"]), 40)

    def test_every_verdict_has_the_same_keys(self):
        keys = {"outcome", "rc_status", "last_seen", "removed_at", "http_status", "calls_made",
                "detail"}
        for verdict in (judge(), judge(status="Inactive"), judge(status=None),
                        dripcheck.classify_failure(client.RentCastNotFound("x", status=404))):
            self.assertEqual(set(verdict), keys)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ClassifyFailureTest(unittest.TestCase):
    def test_a_404_is_not_found_and_not_an_error(self):
        verdict = dripcheck.classify_failure(client.RentCastNotFound("x", attempts=1, status=404))
        self.assertEqual(verdict["outcome"], "not_found")
        self.assertEqual((verdict["http_status"], verdict["calls_made"]), (404, 1))
        self.assertIsNone(verdict["detail"])

    def test_every_other_failure_is_an_error_with_the_class_and_status_only(self):
        for cls, status in ((client.RentCastAuthError, 401), (client.RentCastValidationError, 400),
                            (client.RentCastServerError, 503), (client.RentCastConnectionError, None),
                            (client.RentCastResponseError, 200), (client.RentCastError, 418)):
            with self.subTest(cls=cls.__name__):
                verdict = dripcheck.classify_failure(cls(SECRET, attempts=3, status=status))
                self.assertEqual(verdict["outcome"], "error")
                self.assertEqual((verdict["http_status"], verdict["calls_made"]), (status, 3))
                self.assertEqual(verdict["detail"], f"{cls.__name__} status={status}")
                self.assertNotIn(SECRET, str(verdict))


def failing(cls, status=None, attempts=1):
    return cls(SECRET, attempts=attempts, status=status)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class CheckListingTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConn()
        self.get = mock.patch.object(client, "get_sale_listing").start()
        self.addCleanup(mock.patch.stopall)

    def run_check(self, **over):
        args = dict(campaign_id=7, step_no=2, listing_id=41, rentcast_id="x", today=TODAY,
                    freshness_days=FRESH, agent_email=AGENT)
        args.update(over)
        return dripcheck.check_listing(self.conn, **args)

    def row(self):
        self.assertEqual(len(self.conn.executed), 1)
        return self.conn.executed[0][1]

    def test_an_active_answer_is_logged_with_the_attempts_and_a_200(self):
        self.get.return_value = (listing(), 2)
        verdict = self.run_check()
        row = self.row()
        self.assertEqual(verdict["outcome"], "active")
        self.assertEqual(row["outcome"], "active")
        self.assertEqual((row["campaign_id"], row["step_no"], row["listing_id"]), (7, 2, 41))
        self.assertEqual((row["calls_made"], row["http_status"]), (2, 200))
        self.assertEqual(self.conn.commits, 1)

    def test_the_lookup_uses_the_rentcast_id_and_nothing_else(self):
        self.get.return_value = (listing(), 1)
        self.run_check(rentcast_id="the-rc-id", listing_id=99)
        self.get.assert_called_once_with("the-rc-id")

    def test_an_inactive_answer_is_logged_as_inactive(self):
        self.get.return_value = (listing(status="Inactive"), 1)
        self.assertEqual(self.run_check()["outcome"], "inactive")
        self.assertEqual(self.row()["rc_status"], "Inactive")

    def test_an_unusable_answer_is_logged_as_an_error_but_still_costs_the_call(self):
        self.get.return_value = ({"id": "x"}, 1)
        verdict = self.run_check()
        self.assertEqual(verdict["outcome"], "error")
        self.assertEqual((self.row()["calls_made"], self.row()["http_status"]), (1, 200))

    def test_another_agent_is_logged_as_inactive(self):
        self.get.return_value = (listing(listingAgent={"email": "x@example.com"}), 1)
        self.assertEqual(self.run_check()["outcome"], "inactive")
        self.assertEqual(self.row()["outcome"], "inactive")
        self.assertNotIn("x@example.com", str(self.conn.executed))

    def test_the_agent_email_given_is_the_one_compared(self):
        self.get.return_value = (listing(listingAgent={"email": "x@example.com"}), 1)
        self.assertEqual(self.run_check(agent_email="x@example.com")["outcome"], "active")

    def test_a_404_is_logged_as_not_found(self):
        self.get.side_effect = failing(client.RentCastNotFound, 404)
        verdict = self.run_check()
        self.assertEqual(verdict["outcome"], "not_found")
        self.assertEqual((self.row()["outcome"], self.row()["http_status"]), ("not_found", 404))
        self.assertEqual(self.conn.commits, 1)

    def test_a_failure_that_will_not_repeat_is_logged_and_returned(self):
        for cls, status in ((client.RentCastServerError, 503), (client.RentCastConnectionError, None),
                            (client.RentCastResponseError, 200), (client.RentCastError, 418)):
            with self.subTest(cls=cls.__name__):
                self.conn = FakeConn()
                self.get.side_effect = failing(cls, status, attempts=3)
                verdict = self.run_check()
                self.assertEqual(verdict["outcome"], "error")
                self.assertEqual(self.row()["calls_made"], 3)
                self.assertEqual(self.conn.commits, 1)

    def test_auth_and_malformed_request_are_logged_then_raised(self):
        for cls, status in ((client.RentCastAuthError, 401), (client.RentCastAuthError, 403),
                            (client.RentCastValidationError, 400)):
            with self.subTest(cls=cls.__name__, status=status):
                self.conn = FakeConn()
                self.get.side_effect = failing(cls, status)
                with self.assertRaises(cls):
                    self.run_check()
                self.assertEqual(self.row()["outcome"], "error")
                self.assertEqual(self.row()["http_status"], status)
                self.assertEqual(self.conn.commits, 1)

    def test_a_fatal_error_is_logged_before_it_is_raised(self):
        self.get.side_effect = failing(client.RentCastAuthError, 401)
        with self.assertRaises(client.RentCastAuthError):
            self.run_check()
        self.assertEqual(self.conn.events, ["insert", "commit"])

    def test_the_fatal_set_is_exactly_auth_and_validation(self):
        self.assertEqual(set(dripcheck.FATAL),
                         {client.RentCastAuthError, client.RentCastValidationError})

    def test_a_listing_with_no_rentcast_id_is_an_error_row_and_no_request(self):
        self.get.side_effect = ValueError("a listing id is required")
        verdict = self.run_check(rentcast_id=None)
        self.assertEqual(verdict["outcome"], "error")
        self.assertEqual(self.row()["calls_made"], 0)
        self.assertIsNone(self.row()["http_status"])
        self.assertEqual(self.conn.commits, 1)

    def test_a_row_is_written_and_committed_every_time(self):
        outcomes = ((listing(), 1), failing(client.RentCastNotFound, 404),
                    failing(client.RentCastServerError, 503))
        for result in outcomes:
            self.conn = FakeConn()
            if isinstance(result, Exception):
                self.get.side_effect = result
            else:
                self.get.side_effect = None
                self.get.return_value = result
            self.run_check()
            self.assertEqual((len(self.conn.executed), self.conn.commits), (1, 1))
            self.assertEqual(self.conn.events, ["insert", "commit"])

    def test_a_long_detail_is_cut_to_the_column_limit(self):
        verdict = dict(dripcheck._verdict("error", detail="d" * 500))
        dripcheck.record_check(self.conn, campaign_id=1, step_no=1, listing_id=1, verdict=verdict)
        self.assertEqual(len(self.row()["detail"]), 300)

    def test_no_detail_is_stored_as_null(self):
        for blank in (None, ""):
            self.conn = FakeConn()
            dripcheck.record_check(self.conn, campaign_id=1, step_no=1, listing_id=1,
                                   verdict=dripcheck._verdict("active", detail=blank))
            self.assertIsNone(self.row()["detail"])

    def test_nothing_from_a_response_body_reaches_the_row_or_a_log_line(self):
        handler = Collector()
        dripcheck.logger.addHandler(handler)
        self.addCleanup(dripcheck.logger.removeHandler, handler)
        for cls, status in ((client.RentCastNotFound, 404), (client.RentCastServerError, 503),
                            (client.RentCastResponseError, 200), (client.RentCastAuthError, 401)):
            self.conn = FakeConn()
            self.get.side_effect = failing(cls, status)
            try:
                self.run_check()
            except client.RentCastError:
                pass
            self.assertNotIn(SECRET, str(self.conn.executed))
        self.assertTrue(handler.lines)
        self.assertNotIn(SECRET, " ".join(handler.lines))

    def test_the_failure_log_line_names_the_campaign_step_and_listing(self):
        handler = Collector()
        dripcheck.logger.addHandler(handler)
        self.addCleanup(dripcheck.logger.removeHandler, handler)
        self.get.side_effect = failing(client.RentCastServerError, 503)
        self.run_check()
        self.assertEqual(len(handler.lines), 1)
        for part in ("campaign=7", "step=2", "listing=41", "outcome=error", "status=503"):
            self.assertIn(part, handler.lines[0])

    def test_an_answer_that_is_not_a_failure_logs_nothing(self):
        handler = Collector()
        dripcheck.logger.addHandler(handler)
        self.addCleanup(dripcheck.logger.removeHandler, handler)
        self.get.return_value = (listing(), 1)
        self.run_check()
        self.assertEqual(handler.lines, [])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(HAVE_DB, "no database (PGHOST unset)")
class InsertMatchesTableTest(unittest.TestCase):
    def test_every_column_the_insert_names_exists_and_every_required_one_is_named(self):
        named = set(re.findall(r"\w+", re.search(r"\((.*?)\)\s*VALUES", dripcheck._INSERT_SQL,
                                                 re.S).group(1)))
        with psycopg.connect() as conn:
            rows = conn.execute(
                "SELECT column_name, is_nullable, column_default, is_identity "
                "FROM information_schema.columns WHERE table_name = 'drip_listing_checks'"
            ).fetchall()
        columns = {r[0] for r in rows}
        self.assertEqual(named - columns, set())
        required = {r[0] for r in rows if r[1] == "NO" and r[2] is None and r[3] == "NO"}
        self.assertEqual(required - named, set())
        placeholders = set(re.findall(r"%\((\w+)\)s", dripcheck._INSERT_SQL))
        self.assertEqual(placeholders, named)
        self.assertEqual(set(dripcheck._verdict("active")) | {"campaign_id", "step_no", "listing_id"},
                         placeholders)


if __name__ == "__main__":
    unittest.main()
