"""Tests for hailsys/cocorahs/parse.py -- the CoCoRaHS hail export, text in, reports and rejects out (Phase 5c, step 1a).

Pure: no database, no network.  Run with the default suite, or alone:

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_cocorahs_parse

The export is asked for with TimesInGMT=True, so every time in it is UTC (checked 2026-10-23: the 9 PM MDT hail of
2024-05-30 reads 2024-05-31, 03:00).  Times are 12-hour ("03:00 AM") whatever date format is asked for.

What they pin, each a trap or a rule from the design:  the header must be exactly the 24 columns the export has, two of
them named MoreRainThanHail, and a changed header is an error, not a guess;  fields are read by position, so the
duplicate name cannot shift Damage;  'NA' is a missing value, never 0 (the same trap as IEM's 'None');  'NaN' and
'Infinity' do not get through (Decimal accepts them);  a size outside 0 to 10 in, a bad position, a bad date or the
wrong number of fields is rejected with its line kept verbatim, and does not end the run;  12 AM is hour 0 and 12 PM is
hour 12;  every time comes back timezone aware in UTC;  and an accepted report keeps its source line as raw_row.
"""

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.cocorahs import parse
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

UTC = timezone.utc
HEADER = ("ObservationDate,ObservationTime,EntryDateTime,StationNumber,StationName,Latitude,Longitude,SmallestSize,"
          "AverageSize,LargestSize,DurationMinutes,DurationAccuracy,Timing,StoneConsistency,MoreRainThanHail,HailStarted,"
          "LargestHailStarted,MoreRainThanHail,Damage,AngleOfImpact,NumberOfStonesOnPad,DistanceBtwnStonesOnPad,"
          "DepthOnGround,DateTimeStamp")

# Real lines from the export of 2024-05-30 with TimesInGMT=True (2026-10-23).
REAL = [
    "2024-05-31, 03:00 AM, 2024-05-31 01:32 PM, CO-BO-560, Boulder 5.5 E, 40.041829, -105.14857, NA, NA, NA, 10, , "
    "Continuous, Hard, False, After rain, Same time as smaller hail, False, minor leaf damage, , , , , 2024-05-31 01:32 PM",
    "2024-05-31, 03:00 AM, 2024-05-31 03:31 AM, CO-BO-285, Erie 1.7 WNW, 40.0414439, -105.0729374, 0.250, 0.375, 0.500, "
    "12, , , , False, , , False, , , , , , 2024-05-31 03:31 AM",
    "2024-05-31, 03:44 AM, 2024-05-31 04:09 AM, CO-AD-100, Thornton 2.1 N, 39.922123, -104.955296, NA, 1.250, NA, 20, "
    "1min, , Mixed|Clear Ice|White Ice, False, After rain, After smaller hail, False, shredded leaves, 10-20, , , , "
    "2024-05-31 04:09 AM",
]


def line(date="2024-05-31", time="03:00 AM", entry="2024-05-31 03:31 AM", station="CO-BO-285", name="Erie 1.7 WNW",
         lat="40.0414439", lon="-105.0729374", small="0.250", avg="0.375", large="0.500", dur="12",
         damage="", stamp="2024-05-31 03:31 AM"):
    """One data line with the export's 24 fields, separated the way the export separates them (comma, space)."""
    fields = [date, time, entry, station, name, lat, lon, small, avg, large, dur, "", "", "", "False", "", "",
              "False", damage, "", "", "", "", stamp]
    return ", ".join(fields)


def text(*lines, header=HEADER):
    return header + "\r\n" + "\r\n".join(lines) + "\r\n"


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class HeaderTest(unittest.TestCase):
    def test_the_header_is_the_24_columns_with_the_duplicate_name(self):
        self.assertEqual(",".join(parse.EXPECTED_HEADER), HEADER)
        self.assertEqual(parse.EXPECTED_HEADER.count("MoreRainThanHail"), 2)

    def test_a_changed_header_is_an_error(self):
        for bad in (HEADER.replace("LargestSize", "BiggestSize"),
                    HEADER.rsplit(",", 1)[0],                       # a column missing
                    HEADER + ",Extra",                              # a column added
                    "<html>Service unavailable</html>"):
            with self.subTest(header=bad[:40]):
                with self.assertRaises(ValueError):
                    parse.parse_hail(text(*REAL, header=bad))

    def test_an_empty_answer_is_an_error_not_no_reports(self):
        with self.assertRaises(ValueError):
            parse.parse_hail("")

    def test_a_header_with_no_reports_is_no_reports(self):
        got = parse.parse_hail(HEADER + "\r\n")
        self.assertEqual((got.reports, got.rejected), ([], []))


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class ReportTest(unittest.TestCase):
    def one(self, ln, **kw):
        got = parse.parse_hail(text(ln))
        self.assertEqual(got.rejected, [], got.rejected)
        self.assertEqual(len(got.reports), 1)
        return got.reports[0]

    def test_a_real_line_with_every_size(self):
        r = self.one(REAL[1])
        self.assertEqual(r["station_number"], "CO-BO-285")
        self.assertEqual(r["station_name"], "Erie 1.7 WNW")
        self.assertEqual((r["latitude"], r["longitude"]), (Decimal("40.0414439"), Decimal("-105.0729374")))
        self.assertEqual((r["smallest_in"], r["average_in"], r["largest_in"]),
                         (Decimal("0.250"), Decimal("0.375"), Decimal("0.500")))
        self.assertEqual(r["duration_minutes"], 12)
        self.assertEqual(r["observed_at"], datetime(2024, 5, 31, 3, 0, tzinfo=UTC))
        self.assertEqual(r["entered_at"], datetime(2024, 5, 31, 3, 31, tzinfo=UTC))
        self.assertEqual(r["source_updated_at"], datetime(2024, 5, 31, 3, 31, tzinfo=UTC))

    def test_NA_is_a_missing_size_never_zero(self):
        r = self.one(REAL[0])
        self.assertEqual((r["smallest_in"], r["average_in"], r["largest_in"]), (None, None, None))
        r = self.one(REAL[2])
        self.assertEqual((r["smallest_in"], r["average_in"], r["largest_in"]), (None, Decimal("1.250"), None))

    def test_damage_is_read_by_position_not_name(self):
        # two columns are called MoreRainThanHail;  by name the second would overwrite the first and a dict reader
        # would also lose track of which column Damage follows.  By position Damage is column 19 either way.
        self.assertEqual(self.one(REAL[0])["damage"], "minor leaf damage")
        self.assertEqual(self.one(REAL[2])["damage"], "shredded leaves")
        self.assertEqual(self.one(REAL[1])["damage"], None)

    def test_stone_consistency_is_kept(self):
        self.assertEqual(self.one(REAL[2])["stone_consistency"], "Mixed|Clear Ice|White Ice")
        self.assertIsNone(self.one(REAL[1])["stone_consistency"])

    def test_a_station_name_with_parentheses_and_a_damage_note_with_a_dot_survive(self):
        r = self.one(line(name="Denver 0.2 NNW (Botanic Gardens)", damage="shredded leaves.car dents"))
        self.assertEqual(r["station_name"], "Denver 0.2 NNW (Botanic Gardens)")
        self.assertEqual(r["damage"], "shredded leaves.car dents")

    def test_missing_duration_is_none(self):
        self.assertIsNone(self.one(line(dur="NA"))["duration_minutes"])
        self.assertIsNone(self.one(line(dur=""))["duration_minutes"])

    def test_twelve_oclock_is_read_correctly(self):
        self.assertEqual(self.one(line(time="12:00 AM"))["observed_at"], datetime(2024, 5, 31, 0, 0, tzinfo=UTC))
        self.assertEqual(self.one(line(time="12:30 PM"))["observed_at"], datetime(2024, 5, 31, 12, 30, tzinfo=UTC))
        self.assertEqual(self.one(line(time="01:32 PM"))["observed_at"], datetime(2024, 5, 31, 13, 32, tzinfo=UTC))
        self.assertEqual(self.one(line(time="11:59 PM"))["observed_at"], datetime(2024, 5, 31, 23, 59, tzinfo=UTC))

    def test_every_time_is_timezone_aware_utc(self):
        r = self.one(REAL[1])
        for key in ("observed_at", "entered_at", "source_updated_at"):
            self.assertEqual(r[key].utcoffset().total_seconds(), 0, key)

    def test_a_blank_observation_time_means_the_start_of_the_date(self):
        self.assertEqual(self.one(line(time=""))["observed_at"], datetime(2024, 5, 31, 0, 0, tzinfo=UTC))

    def test_a_missing_entry_or_update_time_is_none_not_an_error(self):
        r = self.one(line(entry="", stamp=""))
        self.assertEqual((r["entered_at"], r["source_updated_at"]), (None, None))

    def test_raw_row_is_the_source_line_verbatim(self):
        self.assertEqual(self.one(REAL[0])["raw_row"], REAL[0])

    def test_a_size_of_exactly_zero_or_ten_is_kept(self):
        self.assertEqual(self.one(line(large="0.000"))["largest_in"], Decimal("0.000"))
        self.assertEqual(self.one(line(large="10.000"))["largest_in"], Decimal("10.000"))

    def test_leading_spaces_are_stripped_everywhere(self):
        r = self.one(REAL[1])
        self.assertEqual(r["station_number"], "CO-BO-285")
        self.assertFalse(r["station_name"].startswith(" "))

    def test_several_lines_in_order_and_a_blank_line_is_ignored(self):
        got = parse.parse_hail(text(REAL[0], "", REAL[1], REAL[2]))
        self.assertEqual([r["station_number"] for r in got.reports], ["CO-BO-560", "CO-BO-285", "CO-AD-100"])
        self.assertEqual(got.rejected, [])

    def test_a_repeated_report_is_returned_twice_for_the_load_to_settle(self):
        got = parse.parse_hail(text(REAL[1], REAL[1]))
        self.assertEqual(len(got.reports), 2)


@unittest.skipIf(MISSING, f"imports unavailable: {MISSING}")
class RejectTest(unittest.TestCase):
    def rejected(self, ln, reason):
        got = parse.parse_hail(text(REAL[1], ln, REAL[0]))
        self.assertEqual(len(got.reports), 2, "the good lines around a bad one are still read")
        self.assertEqual(len(got.rejected), 1)
        rej = got.rejected[0]
        self.assertEqual(rej["reason"], reason)
        self.assertEqual(rej["raw"], ln, "the bad line is kept verbatim")
        self.assertEqual(rej["line"], 3, "line numbers count the header as line 1")

    def test_NaN_and_Infinity_sizes_are_rejected(self):
        for bad in ("NaN", "Infinity", "-Infinity", "nan"):
            with self.subTest(size=bad):
                self.rejected(line(large=bad), "bad_size")

    def test_a_size_that_is_not_a_number_is_rejected(self):
        self.rejected(line(avg="big"), "bad_size")

    def test_a_negative_or_impossible_size_is_rejected(self):
        self.rejected(line(large="-0.5"), "size_out_of_range")
        self.rejected(line(small="10.01"), "size_out_of_range")
        self.rejected(line(avg="99"), "size_out_of_range")

    def test_a_bad_position_is_rejected(self):
        for kw in ({"lat": "NaN"}, {"lat": ""}, {"lat": "NA"}, {"lon": "Infinity"}, {"lat": "91"}, {"lon": "-181"},
                   {"lat": "north"}):
            with self.subTest(kw=kw):
                self.rejected(line(**kw), "bad_position")

    def test_a_bad_date_or_time_is_rejected(self):
        self.rejected(line(date="2024-13-45"), "bad_date")
        self.rejected(line(date=""), "bad_date")
        self.rejected(line(time="25:00 AM"), "bad_time")
        self.rejected(line(time="noon"), "bad_time")

    def test_a_missing_station_is_rejected(self):
        self.rejected(line(station=""), "bad_station")

    def test_the_wrong_number_of_fields_is_rejected_and_kept(self):
        # an unquoted comma inside a field makes 25 fields and the boundaries are unrecoverable (the IEM CITY trap)
        self.rejected(line(name="Denver, 0.2 NNW"), "field_count_mismatch")
        self.rejected("2024-05-31, 03:00 AM, truncated", "field_count_mismatch")

    def test_a_quoted_comma_in_a_name_is_not_a_mismatch(self):
        got = parse.parse_hail(text(line(name='"Denver, 0.2 NNW"')))
        self.assertEqual((len(got.reports), len(got.rejected)), (1, 0))
        self.assertEqual(got.reports[0]["station_name"], "Denver, 0.2 NNW")

    def test_nothing_rejected_is_silently_dropped(self):
        bad = [line(large="NaN"), line(lat=""), line(date="x"), "short"]
        got = parse.parse_hail(text(REAL[1], *bad))
        self.assertEqual(len(got.reports) + len(got.rejected), 1 + len(bad))


if __name__ == "__main__":
    unittest.main()
