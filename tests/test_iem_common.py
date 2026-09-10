"""Tests for scripts/iem_common.py -- the machinery both ingest scripts share.

Run from the repo root:
    python3 -m unittest discover

iem_common imports psycopg at module load, but nothing tested here touches a
database, so psycopg is stubbed rather than required.  perform_run and
load_lines end to end are exercised by running the scripts, per the module's
own docstring; what is covered here is the pure surface plus the failure path,
which an integration run does not reach.

Each case is a contract the nightly/backfill depend on or a trap from
docs/decision-log.md.
"""

import logging
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))

from iem_common import (  # noqa: E402  (imports follow the sys.path edit above)
    IEM_STATE,
    Counters,
    archive_url,
    assert_single_state,
    build_url,
    load_lines,
    log_event,
    recent_url,
)
from iem_parse import RESTKEY  # noqa: E402


HEADER = (
    "VALID,VALID2,LAT,LON,MAG,WFO,TYPECODE,TYPETEXT,CITY,COUNTY,STATE,"
    "SOURCE,REMARK,UGC,UGCNAME,QUALIFIER"
)

# A well-formed Colorado hail row, and a well-formed New Jersey one.  The NJ row
# is what an ignored filter parameter (?stat=CO instead of ?state=CO) returns
# thousands of -- HTTP 200, no warning.
CO_ROW = (
    "202105081930,2021/05/08 19:30,39.74,-104.99,1.75,BOU,H,HAIL,DENVER,"
    "DENVER,CO,TRAINED SPOTTER,QUARTER SIZE HAIL,COC031,Denver,E"
)
NJ_ROW = (
    "202105081930,2021/05/08 19:30,40.73,-74.17,1.00,PHI,H,HAIL,NEWARK,"
    "ESSEX,NJ,TRAINED SPOTTER,PENNY SIZE,NJC013,Essex,E"
)
# The 2018 archive malformation: an unquoted comma inside CITY shifts every
# later column one place right, so COUNTY 'GARFIELD' lands in the STATE field.
# 17 fields, so csv.DictReader populates restkey -- which is how the state
# check knows to skip it rather than abort the run on each of the 76 such rows.
MALFORMED_ROW = (
    "201802111600,2018/02/11 16:00,39.76,-107.36,2.0,GJT,S,SNOW,BISON LAKE,"
    " GLENWOOD 15,GARFIELD,CO,MESONET,MESONET STATION,COC045,Garfield,M"
)


def body(*rows):
    """Header line plus data rows, as the list of strings the functions see."""
    return [HEADER, *rows]


def params(url):
    """Decoded query string of url as a flat dict."""
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


class TestUrlBuilders(unittest.TestCase):
    """archive_url and recent_url are the one thing the two scripts pass
    differently into the shared run.  Split out of a single build_url in the
    refactor, so their output is worth pinning."""

    def test_recent_url_is_in_seconds(self):
        # recent= is SECONDS; hours= returns HTTP 422.  30h default -> 108000.
        self.assertEqual(params(recent_url(108000))["recent"], "108000")
        self.assertEqual(params(recent_url(72 * 3600))["recent"], "259200")

    def test_recent_url_carries_the_constant_filter_only(self):
        p = params(recent_url(108000))
        self.assertEqual(p["state"], IEM_STATE)
        self.assertEqual(p["fmt"], "csv")
        self.assertNotIn("sts", p)

    def test_archive_url_format_and_half_open_range(self):
        p = params(archive_url(
            datetime(2021, 5, 8, tzinfo=timezone.utc),
            datetime(2021, 5, 9, tzinfo=timezone.utc),
        ))
        self.assertEqual(p["sts"], "2021-05-08T00:00Z")
        self.assertEqual(p["ets"], "2021-05-09T00:00Z")
        self.assertEqual(p["state"], IEM_STATE)
        self.assertEqual(p["fmt"], "csv")
        self.assertNotIn("recent", p)

    def test_build_url_encodes_values(self):
        # urlencode, not string concatenation: a space survives as a space.
        self.assertEqual(params(build_url(x="a b"))["x"], "a b")


class TestAssertSingleState(unittest.TestCase):
    """?stat=CO (one transposed character) returns HTTP 200 and every LSR in
    the country; verified 2026-09-09.  Nothing upstream flags it, so the
    response body has to be checked.  The refactor puts this on the nightly
    path too, which the old backfill-only code never had."""

    def test_clean_colorado_passes(self):
        assert_single_state(body(CO_ROW, CO_ROW))

    def test_header_only_passes(self):
        # A quiet day is a header line and no data rows -- a normal run.
        assert_single_state(body())

    def test_whitespace_padded_state_passes(self):
        # row["STATE"].strip() -- ' CO ' is not a wrong state.
        assert_single_state(body(CO_ROW.replace(",CO,", ", CO ,")))

    def test_out_of_state_row_raises_and_names_the_state(self):
        with self.assertRaises(ValueError) as ctx:
            assert_single_state(body(CO_ROW, NJ_ROW))
        self.assertIn("NJ", str(ctx.exception))

    def test_malformed_only_does_not_raise(self):
        # The shifted row reads STATE='GARFIELD'.  Asserting on it would abort
        # the run on every one of the 76 known-malformed archive rows -- the
        # exact thing the field-count reject exists to prevent.
        assert_single_state(body(MALFORMED_ROW))

    def test_a_leak_beside_a_malformed_row_still_raises(self):
        with self.assertRaises(ValueError) as ctx:
            assert_single_state(body(MALFORMED_ROW, NJ_ROW, CO_ROW))
        self.assertIn("NJ", str(ctx.exception))


class TestCounters(unittest.TestCase):
    """Attempted totals, and totals as of the last commit.  The gap between
    them is what a failed run reports; it must never claim rows the rollback
    threw away."""

    def test_defaults_are_zero(self):
        c = Counters()
        self.assertEqual(
            (c.seen, c.inserted, c.skipped,
             c.done_seen, c.done_inserted, c.done_skipped),
            (0, 0, 0, 0, 0, 0),
        )

    def test_checkpoint_copies_attempted_into_committed(self):
        c = Counters()
        c.seen, c.inserted, c.skipped = 10, 7, 3
        c.checkpoint()
        self.assertEqual(
            (c.done_seen, c.done_inserted, c.done_skipped), (10, 7, 3)
        )

    def test_work_after_a_checkpoint_does_not_move_committed(self):
        c = Counters()
        c.seen, c.inserted, c.skipped = 10, 7, 3
        c.checkpoint()
        c.seen, c.inserted, c.skipped = 15, 9, 5  # a chunk that then rolls back
        self.assertEqual(
            (c.done_seen, c.done_inserted, c.done_skipped), (10, 7, 3)
        )
        self.assertEqual((c.seen, c.inserted, c.skipped), (15, 9, 5))


class TestLogEvent(unittest.TestCase):
    """logfmt to stdout: run_id on every line so one run can be pulled out of
    an interleaved journal, and values containing spaces quoted so the split
    stays unambiguous."""

    def line(self, *args, **kwargs):
        with self.assertLogs(level=logging.DEBUG) as caught:
            log_event(*args, **kwargs)
        self.assertEqual(len(caught.records), 1)
        return caught.records[0].getMessage()

    def test_run_id_comes_first_when_given(self):
        msg = self.line("start", 41, mode="nightly")
        self.assertTrue(msg.startswith("run_id=41 event=start "))

    def test_run_id_is_omitted_when_none(self):
        msg = self.line("below_archive_floor", start="2003-01-01")
        self.assertTrue(msg.startswith("event=below_archive_floor "))
        self.assertNotIn("run_id=", msg)

    def test_value_with_a_space_is_quoted(self):
        msg = self.line("failed", 41, error="connection refused")
        self.assertIn("error='connection refused'", msg)

    def test_value_without_a_space_is_bare(self):
        msg = self.line("complete", 41, seen=118, inserted=12)
        self.assertIn("seen=118", msg)
        self.assertIn("inserted=12", msg)


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class FakeConn:
    def __init__(self, rowcount=1):
        self.cur = FakeCursor()
        self.cur.rowcount = rowcount
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


class TestLoadLines(unittest.TestCase):
    """The row loop: one parse_row per data line, the reject branch versus the
    insert branch, inserted taken from cursor.rowcount rather than a Python
    tally, and a tail commit+checkpoint so the last partial chunk is not
    silently rolled back on the way out."""

    def run_it(self, *rows, rowcount=1):
        conn = FakeConn(rowcount=rowcount)
        counters = Counters()
        load_lines(
            conn, 1, body(*rows),
            {("H", "HAIL")},
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            counters,
        )
        return conn, counters

    def test_clean_row_takes_the_insert_branch(self):
        conn, c = self.run_it(CO_ROW)
        self.assertEqual((c.seen, c.inserted, c.skipped), (1, 1, 0))
        self.assertEqual(c.done_seen, 1)          # tail checkpoint ran
        self.assertGreaterEqual(conn.commits, 1)  # tail commit ran

    def test_inserted_comes_from_rowcount_not_a_tally(self):
        # ON CONFLICT DO NOTHING makes rowcount 0 for a row already held.  A
        # Python counter would report seen == inserted and hide the overlap.
        _, c = self.run_it(CO_ROW, CO_ROW, rowcount=0)
        self.assertEqual(c.seen, 2)
        self.assertEqual(c.inserted, 0)

    def test_malformed_row_takes_the_reject_branch(self):
        _, c = self.run_it(MALFORMED_ROW)
        self.assertEqual((c.seen, c.inserted, c.skipped), (1, 0, 1))

    def test_no_data_rows_still_commits_the_tail(self):
        conn, c = self.run_it()
        self.assertEqual(c.seen, 0)
        self.assertGreaterEqual(conn.commits, 1)

    def test_commit_and_checkpoint_on_the_chunk_boundary(self):
        import iem_common
        original = iem_common.COMMIT_CHUNK
        iem_common.COMMIT_CHUNK = 2
        self.addCleanup(setattr, iem_common, "COMMIT_CHUNK", original)
        with self.assertLogs(level=logging.INFO):  # swallow the progress line
            conn, c = self.run_it(CO_ROW, CO_ROW, CO_ROW)
        self.assertGreaterEqual(conn.commits, 2)   # boundary commit + tail
        self.assertEqual(c.done_seen, 3)           # tail checkpoint is last


class TestPerformRunFailurePath(unittest.TestCase):
    """On any exception: roll back, write a 'failed' row carrying the COMMITTED
    counts (never the attempted ones -- the rollback discarded those), name the
    gap in error_detail, then re-raise so the process exits non-zero.  An
    integration run never reaches this."""

    def test_failed_row_carries_committed_counts_then_reraises(self):
        import iem_common

        calls = []

        class Cur:
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                calls.append((sql, params))

            def fetchone(self):
                return (99,)

            def fetchall(self):
                return []

        class Conn:
            def cursor(self):
                return Cur()

            def commit(self):
                pass

            def rollback(self):
                calls.append(("ROLLBACK", None))

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self.addCleanup(setattr, iem_common, "psycopg", iem_common.psycopg)
        iem_common.psycopg = types.SimpleNamespace(connect=lambda *a, **k: Conn())

        self.addCleanup(setattr, iem_common, "fetch", iem_common.fetch)
        iem_common.fetch = lambda url, run_id: (_ for _ in ()).throw(
            RuntimeError("fetch exploded")
        )

        window = (
            datetime(2021, 1, 1, tzinfo=timezone.utc),
            datetime(2021, 2, 1, tzinfo=timezone.utc),
            "http://example/x",
        )
        with self.assertLogs(level=logging.ERROR), self.assertRaises(RuntimeError):
            iem_common.perform_run(
                "backfill", window[0], window[1], [window]
            )

        self.assertIn(("ROLLBACK", None), calls)
        failed = [p for _, p in calls if p and p[0] == "failed"]
        self.assertEqual(len(failed), 1, "one FINISH_RUN row with status failed")
        _status, seen, inserted, skipped, detail, run_id = failed[0]
        self.assertEqual((seen, inserted, skipped), (0, 0, 0))
        self.assertEqual(run_id, 99)
        self.assertIn("RuntimeError", detail)
        self.assertIn("fetch exploded", detail)
        self.assertIn("committed seen=0", detail)


if __name__ == "__main__":
    unittest.main()
