"""Every reader of storm_listing_matches keeps the inner join to iem_data (Phase 5c, steps 2b and 3).

The 2026-10-26 waiver adds match rows with no iem_id (basis 'radar' and 'observer').  Every reader inner-joins
iem_data on that column, so such a row is invisible to the match page, the send list, the drip follow-ups, the
activity feed, the exports and the work-state badges.  That is the safety:  nothing zone-only can reach an email
until step 4 is waived in.  These tests fail if a reader is changed to a left join, or a new reader is added without
the join.  They read source text and need no database:

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_match_readers

(The database half, a zone row that no reader returns, is in test_match_zone_rows once sql/057 is applied.)
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Where the table is only written, never read.
WRITERS = {"hailsys/matching/matcher.py", "hailsys/matching/zonematch.py"}

# The one reader that does not inner-join iem_data, on purpose:  the staff zones page counts the zone rows already written
# (iem_id IS NULL), by basis and level.  test_the_zone_row_counter_reads_only_counts pins what it may select.
# hailsys/queries/zonesend.py is the zone send list (step 4):  it reads zone matches (iem_id IS NULL) and, to avoid emailing a
# house twice, the send records of any match at the same address that storm day.  test_the_zone_send_list_reads_only_what_it_may
# pins both reads.
ZONE_ROW_READERS = {"hailsys/queries/zonepull.py", "hailsys/queries/zonesend.py"}

READ = re.compile(r"\b(?:FROM|JOIN)\s+storm_listing_matches\s+(\w+)", re.I)


def sources():
    out = []
    for path in sorted(ROOT.glob("hailsys/**/*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text()
        if "storm_listing_matches" in text:
            out.append((str(path.relative_to(ROOT)), text))
    return out


class MatchReaderTest(unittest.TestCase):
    def test_the_sweep_finds_the_readers(self):
        # A sweep that found nothing would pass every test below for the wrong reason.
        files = {name for name, _ in sources()}
        for expected in ("hailsys/queries/matches.py", "hailsys/queries/sendlist.py",
                         "hailsys/queries/dripfollowup.py", "hailsys/queries/activity.py",
                         "hailsys/queries/exports.py", "hailsys/queries/workstate.py", "hailsys/web/views.py"):
            self.assertIn(expected, files)
        reads = sum(len(READ.findall(text)) for name, text in sources())
        self.assertGreaterEqual(reads, 8)

    def test_every_read_of_the_matches_table_is_inner_joined_to_iem_data_on_iem_id(self):
        checked = 0
        for name, text in sources():
            if name in ZONE_ROW_READERS:
                continue
            for alias in READ.findall(text):
                with self.subTest(file=name, alias=alias):
                    inner = re.search(rf"\bJOIN\s+iem_data\s+\w+\s+ON\s+\w+\.iem_id\s*=\s*{alias}\.iem_id\b", text, re.I)
                    reverse = re.search(rf"\bJOIN\s+iem_data\s+\w+\s+ON\s+{alias}\.iem_id\s*=\s*\w+\.iem_id\b", text, re.I)
                    self.assertTrue(inner or reverse, f"{name}: storm_listing_matches {alias} is not inner-joined to iem_data")
                    checked += 1
        self.assertGreaterEqual(checked, 8)

    def test_the_zone_row_counter_reads_only_counts_of_zone_rows(self):
        # The exception above is only for this:  rows with no iem_id, as counts by basis and level, no listing columns.
        text = (ROOT / "hailsys/queries/zonepull.py").read_text()
        sql = re.search(r'_ROWS_SQL = """(.*?)"""', text, re.S).group(1)
        self.assertEqual(re.sub(r"\s+", " ", sql).strip(),
                         "SELECT basis, level, count(*) AS n FROM storm_listing_matches "
                         "WHERE storm_date = %(day)s AND iem_id IS NULL AND set_id = %(set_id)s "
                         "GROUP BY basis, level ORDER BY basis, level")
        self.assertEqual(len(READ.findall(text)), 1)
        # Exactly these two files are exempt, each with its own pin:  nothing else may read the table without the join.
        self.assertEqual(ZONE_ROW_READERS, {"hailsys/queries/zonepull.py", "hailsys/queries/zonesend.py"})

    def test_the_zone_send_list_reads_only_what_it_may(self):
        text = (ROOT / "hailsys/queries/zonesend.py").read_text()
        self.assertEqual(sorted(READ.findall(text)), ["m", "m", "m", "m2"])
        # The level at a report match's house (item 266):  report matches only, by id, and only the house's
        # location against the zones and the day's build under one set.
        lv = re.sub(r"\s+", " ", re.search(r'_REPORT_LEVELS_SQL = """(.*?)"""', text, re.S).group(1)).strip()
        self.assertIn("WHERE m.match_id = ANY(%(match_ids)s) AND m.iem_id IS NOT NULL", lv)
        self.assertIn("LEFT JOIN likelihood_builds b ON b.day = m.storm_date AND b.set_id = %(set_id)s", lv)
        self.assertIn("AND z.set_id = %(set_id)s AND ST_Intersects(z.geom, p.geom)", lv)
        self.assertRegex(lv, r"^SELECT m\.match_id, \(b\.day IS NOT NULL AND p\.geom IS NOT NULL\) AS looked, "
                             r"\(array_agg\(z\.level .*\)\[1\] AS best FROM ")
        # The day list for the send page (piece 4b-3):  counts of zone rows under one set, by day, nothing else.
        days = re.sub(r"\s+", " ", re.search(r'_DAYS_SQL = """(.*?)"""', text, re.S).group(1)).strip()
        self.assertEqual(
            days,
            "SELECT m.storm_date AS day, count(*) FILTER (WHERE m.level = 'agree') AS agree, "
            "count(*) FILTER (WHERE m.level = 'one_source') AS one_source FROM storm_listing_matches m "
            "WHERE m.iem_id IS NULL AND m.set_id = %(set_id)s AND m.level IN ('agree', 'one_source') "
            "AND m.storm_date >= %(oldest)s GROUP BY m.storm_date ORDER BY m.storm_date DESC")
        sql = re.search(r'_HITS_SQL = """(.*?)"""', text, re.S).group(1)
        flat = re.sub(r"\s+", " ", sql)
        # The main read is of zone matches only, and of one parameter set, the chosen days and levels.
        self.assertIn("FROM storm_listing_matches m JOIN listings l ON l.listing_id = m.listing_id", flat.replace("listings l ", "listings l "))
        self.assertIn("WHERE m.iem_id IS NULL AND m.set_id = %(set_id)s AND m.storm_date = ANY(%(days)s) AND m.level = ANY(%(levels)s)", flat)
        # The second read only asks whether a house was already emailed that day, through the send records.
        self.assertRegex(flat, r"JOIN storm_listing_matches m2 ON m2\.listing_id = l2\.listing_id JOIN send_log s2 "
                               r"ON s2\.match_id = m2\.match_id")
        self.assertIn("s2.send_status <> 'failed' AND m2.storm_date = m.storm_date", flat)

    def test_no_reader_left_joins_iem_data_or_the_matches_table(self):
        for name, text in sources():
            with self.subTest(file=name):
                self.assertIsNone(re.search(r"\bLEFT\s+(?:OUTER\s+)?JOIN\s+iem_data\b", text, re.I))
                self.assertIsNone(re.search(r"\bLEFT\s+(?:OUTER\s+)?JOIN\s+storm_listing_matches\b", text, re.I))
                self.assertIsNone(re.search(r"\b(?:RIGHT|FULL)\s+(?:OUTER\s+)?JOIN\s+(?:iem_data|storm_listing_matches)\b",
                                            text, re.I))

    def test_only_the_matchers_write_the_matches_table(self):
        for name, text in sources():
            if name in WRITERS:
                continue
            with self.subTest(file=name):
                self.assertIsNone(re.search(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+storm_listing_matches\b", text, re.I))

    def test_the_check_would_catch_a_left_join(self):
        # The guard itself.
        bad = "FROM storm_listing_matches m LEFT JOIN iem_data i ON i.iem_id = m.iem_id"
        self.assertIsNotNone(re.search(r"\bLEFT\s+(?:OUTER\s+)?JOIN\s+iem_data\b", bad, re.I))
        good = "FROM storm_listing_matches m\nJOIN iem_data i     ON i.iem_id = m.iem_id"
        self.assertEqual(READ.findall(good), ["m"])
        self.assertTrue(re.search(r"\bJOIN\s+iem_data\s+\w+\s+ON\s+\w+\.iem_id\s*=\s*m\.iem_id\b", good, re.I))
        self.assertIsNone(re.search(r"\bJOIN\s+iem_data\s+\w+\s+ON\s+\w+\.iem_id\s*=\s*m\.iem_id\b",
                                    "FROM storm_listing_matches m JOIN listings l ON l.listing_id = m.listing_id", re.I))


if __name__ == "__main__":
    unittest.main()
