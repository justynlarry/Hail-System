"""Tests for scripts/iem_parse.py.

Run from the repo root:
    python3 -m unittest discover

Each case here is a trap from docs/data-sources.md or a contract the ingest
scripts depend on.  Isolated to local machine, quick run.

Requires tests/__init.py__ to exist and be empty.
"""

import csv
import io
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from iem_parse import (  # noqa: E402  (import follows the sys.path edit above)
    QUALIFIER_DOMAIN,
    REASON_BAD_COORDINATE,
    REASON_BAD_MAGNITUDE,
    REASON_BAD_TIMESTAMP,
    REASON_FIELD_COUNT,
    REASON_UNKNOWN_TYPE,
    RESTKEY,
    QualifierDomainError,
    parse_row,
)

HEADER = (
    "VALID,VALID2,LAT,LON,MAG,WFO,TYPECODE,TYPETEXT,CITY,COUNTY,STATE,"
    "SOURCE,REMARK,UGC,UGCNAME,QUALIFIER"
)

VALID_TYPES = {
    ("H", "HAIL"),
    ("S", "SNOW"),
    ("S", "HEAVY SNOW"),
    ("N", "NON-TSTM WND GST"),
}

CLEAN_HAIL = (
    "202105081930,2021/05/08 19:30,39.74,-104.99,1.75,BOU,H,HAIL,DENVER,"
    "DENVER,CO,TRAINED SPOTTER,QUARTER SIZE HAIL,COC031,Denver,E"
)

MALFORMED_2018 = (
    "201802111600,2018/02/11 16:00,39.76,-107.36,2.0,GJT,S,SNOW,BISON LAKE,"
    " GLENWOOD 15,GARFIELD,CO,MESONET,MESONET STATION BLSC2_ BISON LAKE_"
    " GLENWOOD 15N.,COC045,Garfield,M"
)

VALID, LAT, LON, MAG, WFO, TYPECODE, TYPETEXT, UGC, QUALIFIER = (
    0, 2, 3, 4, 5, 6, 7, 13, 15
)

def row_from(line, header=HEADER):
    """Turn one CSV line into the dict the ingest scripts hand to parser.

    restkey is what makes a 17-field row detectable.  restval is deliberately
    not passed -> an absent key defaulting to None differentiates a short row
    from a field that's present but empty.
    """

    reader = csv.DictReader(io.StringIO(header + "\n" + line), restkey=RESTKEY)
    return next(reader)

def with_field(line,index, value):
    """Return 'line' with the field at 'index' replaced.
    """

    fields = line.split(",")
    fields[index] = value
    return ",".join(fields)

class TestCleanRow(unittest.TestCase):
    """A well-formed row parses and every field lands where it should."""

    def setUp(self):
        self.record, self.reject = parse_row(row_from(CLEAN_HAIL), VALID_TYPES)

    def test_no_reject(self):
        self.assertIsNone(self.reject)
        self.assertIsNotNone(self.record)

    def test_timestamp_is_aware_utc(self):
        self.assertEqual(
            self.record["utc_datetime"],
            datetime(2021, 5, 8, 19, 30, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(self.record["utc_datetime"].tzinfo)

    def test_numerics_are_decimal_not_float(self):
        for key in ("latitude", "longitude", "magnitude"):
            self.assertIsInstance(self.record[key], Decimal)

    def test_field_mapping(self):
        self.assertEqual(self.record["magnitude"], Decimal("1.75"))
        self.assertEqual(self.record["latitude"], Decimal("39.74"))
        self.assertEqual(self.record["longitude"], Decimal("-104.99"))
        self.assertEqual(self.record["report_type"], "H")
        self.assertEqual(self.record["report_text"], "HAIL")
        self.assertEqual(self.record["nws_issuer"], "BOU")
        self.assertEqual(self.record["report_source"], "TRAINED SPOTTER")
        self.assertEqual(self.record["report_qualifier"], "E")
        self.assertEqual(self.record["county"], "DENVER")
        self.assertEqual(self.record["state"], "CO")
        self.assertEqual(self.record["nws_geo_code"], "COC031")
        self.assertEqual(self.record["remark"], "QUARTER SIZE HAIL")

    def test_discarded_fields_absent(self):
        for key in ("VALID2", "CITY", "UGCNAME", "valid2", "city", "ugc", "name"):
            self.assertNotIn(key, self.record)

    def test_generated_columns_absent(self):
        self.assertNotIn("geom", self.record)
        self.assertNotIn("report_source_norm", self.record)

    def test_ingested_at_is_the_callers_job(self):
        self.assertNotIn("ingested_at", self.record)

class TestMagnitudeNullMarker(unittest.TestCase):
    """IEM's null marker for MAG is the literal string None, not empty field."""

    def test_none_marker_becomes_none(self):
        line = with_field(CLEAN_HAIL, MAG, "None")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(reject)
        self.assertIsNone(record["magnitude"])

    def test_none_marker_is_not_zero(self):
        # Why this trap is documented: coercing it produces 549 magnitude-zero
        # tornadoes and 629 magnitude-zero flash floods.
        line = with_field(CLEAN_HAIL, MAG, "None")
        record, _ = parse_row(row_from(line), VALID_TYPES)
        self.assertNotEqual(record["magnitude"], 0)
        self.assertNotEqual(record["magnitude"], Decimal("0"))

    def test_none_marker_is_not_the_string(self):
        line = with_field(CLEAN_HAIL, MAG, "None")
        record, _ = parse_row(row_from(line), VALID_TYPES)
        self.assertNotIsInstance(record["magnitude"], str)

    def test_empty_magnitude_becomes_none(self):
        line = with_field(CLEAN_HAIL, MAG, "")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(reject)
        self.assertIsNone(record["magnitude"])

    def test_zero_magnitude_is_preserved(self):
        # A real zero is not the null marker. Guards against a future
        # `if not magnitude` that would conflate them.
        line = with_field(CLEAN_HAIL, MAG, "0.00")
        record, _ = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNotNone(record["magnitude"])
        self.assertEqual(record["magnitude"], Decimal("0.00"))

    def test_unparseable_magnitude_rejects(self):
        line = with_field(CLEAN_HAIL, MAG, "LARGE")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_BAD_MAGNITUDE)

    def test_non_finite_magnitude_rejects(self):
        # Decimal("Infinity") and Decimal("nan") construct without raising.
        # NUMERIC(6,2) would refuse them at insert and take the whole
        # transaction with it. One row, one reject, is the better failure.
        for value in ("Infinity", "-Infinity", "nan"):
            with self.subTest(value=value):
                line = with_field(CLEAN_HAIL, MAG, value)
                record, reject = parse_row(row_from(line), VALID_TYPES)
                self.assertIsNone(record)
                self.assertEqual(reject["reason"], REASON_BAD_MAGNITUDE)


class TestFieldCount(unittest.TestCase):
    """16 fields exactly. 17 is the 2018 CITY comma; fewer is a short row."""

    def test_long_row_rejects(self):
        record, reject = parse_row(row_from(MALFORMED_2018), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_FIELD_COUNT)

    def test_long_row_detail_names_the_count(self):
        _, reject = parse_row(row_from(MALFORMED_2018), VALID_TYPES)
        self.assertIn("17", reject["detail"])

    def test_short_row_rejects(self):
        line = ",".join(CLEAN_HAIL.split(",")[:12])
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_FIELD_COUNT)

    def test_short_row_detail_names_the_missing_fields(self):
        line = ",".join(CLEAN_HAIL.split(",")[:12])
        _, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIn("QUALIFIER", reject["detail"])

    def test_field_count_checked_before_anything_else(self):
        # A shifted row has garbage in several fields at once, including
        # QUALIFIER -- which would otherwise raise QualifierDomainError and
        # end the run over a row that is merely malformed. The field-count
        # check running first is what keeps the 76 archive rows as rejects
        # rather than as 76 aborted backfills.
        _, reject = parse_row(row_from(MALFORMED_2018), VALID_TYPES)
        self.assertEqual(reject["reason"], REASON_FIELD_COUNT)


class TestReportTypePair(unittest.TestCase):
    """TYPECODE is not unique. The key is (TYPECODE, TYPETEXT)."""

    def test_known_pair_accepted(self):
        line = with_field(with_field(CLEAN_HAIL, TYPECODE, "S"),
                          TYPETEXT, "HEAVY SNOW")
        _, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(reject)

    def test_unknown_pair_rejects(self):
        line = with_field(with_field(CLEAN_HAIL, TYPECODE, "Z"),
                          TYPETEXT, "LANDSLIDE")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_UNKNOWN_TYPE)

    def test_right_code_wrong_text_rejects(self):
        # H is a known code and DUST STORM is a real IEM type, but the pair is
        # not one. Matching on the code alone would let this through.
        line = with_field(CLEAN_HAIL, TYPETEXT, "DUST STORM")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_UNKNOWN_TYPE)

    def test_reject_detail_names_both_halves(self):
        line = with_field(CLEAN_HAIL, TYPETEXT, "DUST STORM")
        _, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIn("DUST STORM", reject["detail"])
        self.assertIn("H", reject["detail"])


class TestQualifierDomain(unittest.TestCase):
    """An out-of-domain QUALIFIER ends the run. It is not a reject.

    Decision log 2026-09-06. A reject would discard an entire storm report
    over a field that tracks reporter training rather than measurement, and
    SOURCE is the confidence signal anyway. A fourth value is not a bad row --
    it means the upstream domain changed, which is a human decision requiring
    a migration of the CHECK on iem_data.report_qualifier.
    """

    def test_each_domain_value_accepted(self):
        for value in sorted(QUALIFIER_DOMAIN):
            with self.subTest(qualifier=value):
                line = with_field(CLEAN_HAIL, QUALIFIER, value)
                record, reject = parse_row(row_from(line), VALID_TYPES)
                self.assertIsNone(reject)
                self.assertEqual(record["report_qualifier"], value)

    def test_out_of_domain_raises(self):
        line = with_field(CLEAN_HAIL, QUALIFIER, "X")
        with self.assertRaises(QualifierDomainError):
            parse_row(row_from(line), VALID_TYPES)

    def test_out_of_domain_does_not_return_a_reject(self):
        # Explicitly asserting the shape of the decision: if this ever starts
        # returning a reject instead of raising, the 09-06 entry has been
        # reversed and that needs a reversal entry, not a silent change.
        line = with_field(CLEAN_HAIL, QUALIFIER, "X")
        try:
            record, reject = parse_row(row_from(line), VALID_TYPES)
        except QualifierDomainError:
            return
        self.fail(
            f"expected QualifierDomainError, got record={record!r} "
            f"reject={reject!r}"
        )

    def test_error_message_locates_the_row(self):
        # The run is ending, so the message is the only diagnostic. It has to
        # be enough to find the report in the archive by hand.
        line = with_field(CLEAN_HAIL, QUALIFIER, "X")
        with self.assertRaises(QualifierDomainError) as ctx:
            parse_row(row_from(line), VALID_TYPES)
        message = str(ctx.exception)
        self.assertIn("X", message)
        self.assertIn("202105081930", message)   # VALID
        self.assertIn("BOU", message)            # WFO

    def test_empty_qualifier_is_none_not_an_error(self):
        # Nullable column, and an absent qualifier is normal. Only a non-empty
        # out-of-domain value means the contract changed.
        line = with_field(CLEAN_HAIL, QUALIFIER, "")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(reject)
        self.assertIsNone(record["report_qualifier"])

    def test_whitespace_qualifier_is_none_not_an_error(self):
        line = with_field(CLEAN_HAIL, QUALIFIER, "   ")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(reject)
        self.assertIsNone(record["report_qualifier"])

    def test_domain_matches_the_database_check(self):
        # CHECK (report_qualifier IN ('M','E','U')) on iem_data. Drift between
        # this frozenset and that constraint is an IntegrityError mid-batch.
        self.assertEqual(QUALIFIER_DOMAIN, frozenset({"M", "E", "U"}))


class TestTimestamp(unittest.TestCase):
    def test_unparseable_rejects(self):
        line = with_field(CLEAN_HAIL, VALID, "NOTADATE")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_BAD_TIMESTAMP)

    def test_valid2_format_is_not_accepted(self):
        # VALID2 is the human-readable duplicate. Feeding its format to the
        # VALID parser must fail rather than half-parse.
        line = with_field(CLEAN_HAIL, VALID, "2021/05/08 19:30")
        _, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertEqual(reject["reason"], REASON_BAD_TIMESTAMP)

    def test_utc_evening_storm_crosses_midnight(self):
        # A Front Range evening storm lands on the following UTC date. Correct,
        # and not to be "fixed" -- grouping by local date is a display concern.
        line = with_field(CLEAN_HAIL, VALID, "202105090230")
        record, _ = parse_row(row_from(line), VALID_TYPES)
        self.assertEqual(record["utc_datetime"].date().isoformat(), "2021-05-09")


class TestCoordinates(unittest.TestCase):
    def test_non_numeric_rejects_as_not_a_number(self):
        line = with_field(CLEAN_HAIL, LAT, "NORTH")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_BAD_COORDINATE)
        # Both coordinate failures share a reason, so only the detail tells
        # them apart. These assertions exist because those two messages were
        # once swapped: valid code, correct reason, and a sentence that would
        # mislead whoever read it months later.
        self.assertIn("not a number", reject["detail"])

    def test_out_of_range_rejects_as_out_of_range(self):
        # Swapped lat/lon: a Denver report would otherwise land in Kazakhstan
        # with no error anywhere.
        line = with_field(CLEAN_HAIL, LAT, "-104.99")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_BAD_COORDINATE)
        self.assertIn("outside", reject["detail"])

    def test_nan_rejects_rather_than_raising(self):
        # Decimal("nan") does not raise in the constructor -- NaN is a
        # legitimate Decimal. And unlike float, COMPARING a Decimal NaN raises
        # InvalidOperation instead of returning False, so the bounds check
        # alone is not a defence: `-90 <= Decimal("nan") <= 90` raises.
        #
        # An escaping exception ends the run, and the caller's loop has no
        # try/except by design. A guard on is_finite() must come before the
        # bounds comparison.
        line = with_field(CLEAN_HAIL, LAT, "nan")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_BAD_COORDINATE)

    def test_infinity_rejects(self):
        line = with_field(CLEAN_HAIL, LON, "Infinity")
        record, reject = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record)
        self.assertEqual(reject["reason"], REASON_BAD_COORDINATE)

    def test_longitude_range_is_wider_than_latitude(self):
        # -104.99 is a valid longitude and an invalid latitude. The limits are
        # per-field, not shared.
        _, reject = parse_row(row_from(CLEAN_HAIL), VALID_TYPES)
        self.assertIsNone(reject)


class TestEmptyFields(unittest.TestCase):
    """Empty string and NULL are different facts."""

    def test_empty_ugc_becomes_none(self):
        # UGC is empty for every report before July 2022 -- roughly the first
        # half of the archive. Storing '' would make those rows look populated.
        line = with_field(CLEAN_HAIL, UGC, "")
        record, _ = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record["nws_geo_code"])
        self.assertNotEqual(record["nws_geo_code"], "")

    def test_whitespace_only_becomes_none(self):
        line = with_field(CLEAN_HAIL, UGC, "   ")
        record, _ = parse_row(row_from(line), VALID_TYPES)
        self.assertIsNone(record["nws_geo_code"])


class TestContract(unittest.TestCase):
    """The guarantee both ingest scripts are written against."""

    def test_exactly_one_of_record_and_reject_is_none(self):
        lines = [
            CLEAN_HAIL,
            MALFORMED_2018,
            with_field(CLEAN_HAIL, MAG, "None"),
            with_field(CLEAN_HAIL, VALID, "NOTADATE"),
            with_field(CLEAN_HAIL, LAT, "NORTH"),
            with_field(CLEAN_HAIL, TYPETEXT, "DUST STORM"),
            ",".join(CLEAN_HAIL.split(",")[:12]),
        ]
        for line in lines:
            with self.subTest(line=line[:40]):
                record, reject = parse_row(row_from(line), VALID_TYPES)
                self.assertEqual(
                    (record is None, reject is None).count(True),
                    1,
                    "exactly one of record and reject must be None",
                )

    def test_reject_always_carries_reason_and_detail(self):
        _, reject = parse_row(row_from(MALFORMED_2018), VALID_TYPES)
        self.assertIn("reason", reject)
        self.assertIn("detail", reject)
        self.assertTrue(reject["detail"])

    def test_every_reason_is_in_the_check_constraint_enum(self):
        # Drift between this list and the CHECK on iem_ingest_rejects.reason
        # is an IntegrityError at insert -- during the backfill, after the
        # fetch has already happened. Note invalid_qualifier is deliberately
        # NOT here: an out-of-domain qualifier raises, it does not reject.
        allowed = {
            "field_count_mismatch",
            "unknown_report_type",
            "unparseable_timestamp",
            "unparseable_coordinate",
            "unparseable_magnitude",
        }
        for reason in (REASON_FIELD_COUNT, REASON_UNKNOWN_TYPE,
                       REASON_BAD_TIMESTAMP, REASON_BAD_COORDINATE,
                       REASON_BAD_MAGNITUDE):
            self.assertIn(reason, allowed)

    def test_only_qualifier_domain_escapes_as_an_exception(self):
        # Everything else the parser can encounter must come back as a reject.
        # This is what lets the caller's row loop have no try/except at all.
        garbage = with_field(
            with_field(with_field(CLEAN_HAIL, VALID, "!!"), LAT, "??"),
            MAG, "@@",
        )
        record, reject = parse_row(row_from(garbage), VALID_TYPES)
        self.assertIsNone(record)
        self.assertIsNotNone(reject)


if __name__ == "__main__":
    unittest.main()

