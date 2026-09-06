""" Parse one IEM Local Storm Report Row into an iem_data record.

Both ingest scripts import iem_parse.py.  The backfill uses it to
review years of data, the nightly runs it unattended against a few
rows.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# ----------------------------------------------------------------
# Reject Reasons:
#
# - Must match the CHECK constraint on iem_ingest_rejects.reason
#    Adding a reason here without migrating there will produce
#    an IntegrityError at insert
# - The closed list is what prevents skip-and-continue from 
#   drifting into losing the problem altogether
#

# ----------------------------------------------------------------

REASON_FIELD_COUNT = "field_count_mismatch"
REASON_UNKNOWN_TYPE = "unknown_report_type"
REASON_BAD_TIMESTAMP = "unparseable_timestamp"
REASON_BAD_COORDINATE = "unparseable_coordinate"
REASON_BAD_MAGNITUDE = "unparseable_magnitude"


EXPECTED_FIELDS = (
    "VALID", "VALID2", "LAT", "LON", "MAG", "WFO", "TYPECODE", "TYPETEXT",
    "CITY", "COUNTY", "STATE", "SOURCE", "REMARK", "UGC", "UGCNAME",
    "QUALIFIER",
)

# Fields deliberately not mapped:
#   VALID2    human-readable duplicate of VALID
#   CITY      not actually city -- a position relative to a landmark
#   UGCNAME   IEM-computed county name


RESTKEY = "_extra"
IEM_NULL_MARKER = "None"
VALID_FORMAT = "%Y%m%d%H%M"

# iem_data.report_qualifier carries CHECK (report_qualifier IN ('M','E','U')),
# kept deliberately -- there are only three codes (decision log, 2026-09-03).
#
# A fourth value is therefore not a bad row, it is a changed upstream domain,
# and it ends the run.  That is already the rule: skip-and-continue covers the
# five enumerated reasons and nothing else.  This check exists only so the run
# ends on a sentence naming the field and the value, instead of on an
# IntegrityError raised from the middle of a batch INSERT with no clue which
# of the rows carried it.
#
# NOT a reject reason on purpose.  A sixth reason would discard an entire storm
# report over this field, and QUALIFIER tracks reporter training rather than
# instrument measurement -- SOURCE is the confidence signal.  Silently nulling
# it would be worse still: it would convert a changed upstream contract into no
# signal at all.
QUALIFIER_DOMAIN = frozenset({"M", "E", "U"})


class QualifierDomainError(ValueError):
    """IEM sent a QUALIFIER outside ('M','E','U').

    Ends the run by design.  Fixing it means confirming the new code with IEM
    and migrating the CHECK on iem_data.report_qualifier -- a human decision,
    not something the parser should absorb.
    """

def _clean(value):
    """ Strip whitespace; return None for an empty result.

    NULL and empty string are different facts, and only one of them is
    correct about a field IEM didn't populate.  UGC arrives empty for
    every report prior to July 2022.

    """


    if value is None:
        return None
    value = value.strip()
    return value or None

def parse_row(row, valid_types):
    """Convert one CSV row into an iem_data record, or explain

    Args:
        row:	a dict from csv.DictReader constructed with
                restkey=RESTKEY.  Do NOT pass restval -- the short-row
                check below tests for None, so a missing key that
                defaults to anything else defeats that detection.
        valid_types: a set of (report_type, report_text) tuples, loaded once
                 from report_types at the start of a run

    Returns:
        (record, reject) where exactly one is None.

        record:  dict of iem_data column names to values.  NOT complete:
                 iem_data.ingested_at is NOT NULL with no DEFAULT, and is
                 not set here because this function has no clock and no
                 opinion about when the run started.  The caller must add
                 it or the INSERT fails.
        reject:  dict with 'reason' and 'detail'. The caller adds raw_row
                 and run_id -> this function never sees the original line.

    Raises:
        QualifierDomainError if IEM sends a QUALIFIER outside ('M','E','U').
        This is deliberate and is the one exception: it means the upstream
        domain changed, which is not a bad row and not something to skip.

        Otherwise nothing, by design.  An expected problem comes back as a
        reject.  The caller's row loop therefore needs no try/except, and
        any other exception that escapes is a bug that should end the run.
    """

    overflow = row.get(RESTKEY)
    if overflow:
        return None, {
            "reason": REASON_FIELD_COUNT,
            "detail": (
                f"{len(EXPECTED_FIELDS) + len(overflow)} fields, expected "
                f"{len(EXPECTED_FIELDS)}; overflow {overflow!r}"
            ),
        }

    missing = [name for name in EXPECTED_FIELDS if row.get(name) is None]
    if missing:
        return None, {
            "reason": REASON_FIELD_COUNT,
            "detail": f"short row, missing {', '.join(missing)}",
        }

    # ------ TimeStamp ------

    raw_valid = row["VALID"].strip()
    try:
        utc_datetime = datetime.strptime(raw_valid, VALID_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        return None, {
            "reason": REASON_BAD_TIMESTAMP,
            "detail": f"VALID={raw_valid!r}: {exc}",
        }

    # ------ Coordinates ------

    coordinates = {}
    for field, column, limit in (
        ("LAT", "latitude", 90),
        ("LON", "longitude", 180),
    ):
        raw = row[field].strip()
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return None, {
                "reason": REASON_BAD_COORDINATE,
                "detail": f"{field}={raw!r} is not a number",
            }
        if not -limit <= value <= limit:
            return None, {
                "reason": REASON_BAD_COORDINATE,
                "detail": f"{field}={raw!r} outside +/-{limit}",
            }
        coordinates[column] = value

    # ------ Magnitude ------
    raw_mag = row["MAG"].strip()
    if raw_mag in ("", IEM_NULL_MARKER):
        magnitude = None
    else:
        try:
            magnitude = Decimal(raw_mag)
        except InvalidOperation:
            return None, {
                "reason": REASON_BAD_MAGNITUDE,
                "detail": f"MAG={raw_mag!r} is neither a number nor "
                          f"{IEM_NULL_MARKER!r}",
            }

    # ------ Report Type ------
    report_type = row["TYPECODE"].strip()
    report_text = row["TYPETEXT"].strip()
    if (report_type, report_text) not in valid_types:
        return None, {
            "reason": REASON_UNKNOWN_TYPE,
            "detail": f"({report_type!r}, {report_text!r}) not in report_types",
        }

    # ------ Qualifier ------
    qualifier = _clean(row["QUALIFIER"])
    if qualifier is not None and qualifier not in QUALIFIER_DOMAIN:
        raise QualifierDomainError(
            f"QUALIFIER={qualifier!r} outside {sorted(QUALIFIER_DOMAIN)}; "
            f"VALID={row['VALID'].strip()!r} "
            f"TYPECODE={report_type!r} WFO={row['WFO'].strip()!r}"
        )

    # ------ Build Record ------
    record = {
        "utc_datetime": utc_datetime,
        "latitude": coordinates["latitude"],
        "longitude": coordinates["longitude"],
        "magnitude": magnitude,
        "report_type": report_type,
        "report_text": report_text,
        "nws_issuer": _clean(row["WFO"]),
        "report_source": _clean(row["SOURCE"]),
        "report_qualifier": qualifier,
        "county": _clean(row["COUNTY"]),
        "state": _clean(row["STATE"]),
        "nws_geo_code": _clean(row["UGC"]),
        "remark": _clean(row["REMARK"]),
    }
    return record, None




