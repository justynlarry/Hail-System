"""The CoCoRaHS hail export, text in, reports and rejects out.

The export is asked for with TimesInGMT=True, so every time in it is UTC.  Times are 12-hour ("3:00 AM")
whatever date format is asked for.  Pure:  no database, no network.

Traps, checked 2026-10-23:
- The header has 24 columns and two are named MoreRainThanHail.  A dict reader keeps one and drops the other, so fields
  are read by position, and the header must be exactly EXPECTED_HEADER:  a changed export is an error, not a guess.
- 'NA' is a missing value.  Coercing it to 0 would invent measurements (the same trap as IEM's 'None').
- Decimal() accepts 'NaN' and 'Infinity', and Postgres NUMERIC accepts NaN, so a size is tested with is_finite() before
  any range check.
- An unquoted comma inside a field makes the wrong number of fields and the boundaries cannot be recovered:  the line is
  rejected whole and kept verbatim.
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

EXPECTED_HEADER = (
    "ObservationDate", "ObservationTime", "EntryDateTime", "StationNumber", "StationName", "Latitude", "Longitude",
    "SmallestSize", "AverageSize", "LargestSize", "DurationMinutes", "DurationAccuracy", "Timing", "StoneConsistency",
    "MoreRainThanHail", "HailStarted", "LargestHailStarted", "MoreRainThanHail", "Damage", "AngleOfImpact",
    "NumberOfStonesOnPad", "DistanceBtwnStonesOnPad", "DepthOnGround", "DateTimeStamp",
)

# Positions in a data line, from EXPECTED_HEADER.
DATE, TIME, ENTRY, STATION, NAME, LAT, LON = 0, 1, 2, 3, 4, 5, 6
SMALLEST, AVERAGE, LARGEST, DURATION, CONSISTENCY, DAMAGE, STAMP = 7, 8, 9, 10, 13, 18, 23

MAX_SIZE_IN = Decimal("10")
_TIME_FORMAT = "%I:%M %p"
_STAMP_FORMAT = "%Y-%m-%d %I:%M %p"


@dataclass
class ParseResult:
    reports: list = field(default_factory=list)
    rejected: list = field(default_factory=list)        # {"line": n, "reason": str, "raw": the line verbatim}


def _clean(text):
    """The text, or None when the form left it blank or the export wrote NA."""
    text = text.strip()
    return None if text in ("", "NA") else text


def _finite(text):
    """A finite Decimal, or None for blank, NA, NaN, Infinity and anything that is not a number."""
    text = _clean(text)
    if text is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _stamp(text):
    text = _clean(text)
    if text is None:
        return None
    try:
        return datetime.strptime(text, _STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _size(text):
    """(value, reason): (None, None) for a missing size, (Decimal, None) for a good one."""
    if _clean(text) is None:
        return None, None
    value = _finite(text)
    if value is None:
        return None, "bad_size"
    if not 0 <= value <= MAX_SIZE_IN:
        return None, "size_out_of_range"
    return value, None


def _parse_line(raw):
    """(report, None) or (None, reason)."""
    fields = next(csv.reader([raw], skipinitialspace=True), [])
    if len(fields) != len(EXPECTED_HEADER):
        return None, "field_count_mismatch"
    f = [x.strip() for x in fields]
    if not f[STATION]:
        return None, "bad_station"
    try:
        day = datetime.strptime(f[DATE], "%Y-%m-%d")
    except ValueError:
        return None, "bad_date"
    clock = _clean(f[TIME])
    if clock is None:
        observed_at = day.replace(tzinfo=timezone.utc)      # no time filed: the start of the date
    else:
        try:
            t = datetime.strptime(clock, _TIME_FORMAT)
        except ValueError:
            return None, "bad_time"
        observed_at = day.replace(hour=t.hour, minute=t.minute, tzinfo=timezone.utc)
    lat, lon = _finite(f[LAT]), _finite(f[LON])
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, "bad_position"
    sizes = []
    for index in (SMALLEST, AVERAGE, LARGEST):
        value, reason = _size(f[index])
        if reason:
            return None, reason
        sizes.append(value)
    try:
        duration = int(f[DURATION]) if _clean(f[DURATION]) else None
    except ValueError:
        duration = None
    if duration is not None and duration < 0:
        duration = None
    return {
        "station_number": f[STATION], "station_name": _clean(f[NAME]), "observed_at": observed_at,
        "latitude": lat, "longitude": lon,
        "smallest_in": sizes[0], "average_in": sizes[1], "largest_in": sizes[2],
        "duration_minutes": duration, "stone_consistency": _clean(f[CONSISTENCY]), "damage": _clean(f[DAMAGE]),
        "entered_at": _stamp(f[ENTRY]), "source_updated_at": _stamp(f[STAMP]), "raw_row": raw,
    }, None


def parse_hail(text):
    """Parse the export's text.  A header that is not EXPECTED_HEADER (or no text at all) raises ValueError.

    Every data line ends up in exactly one of 'reports' and 'rejected'.  Line numbers count the header as line 1.
    """
    lines = [line.rstrip("\r") for line in text.split("\n")]
    header = tuple(x.strip() for x in next(csv.reader(lines[:1], skipinitialspace=True), []))
    if header != EXPECTED_HEADER:
        raise ValueError("unexpected CoCoRaHS export header: " + repr(lines[0][:120]))
    result = ParseResult()
    for number, raw in enumerate(lines[1:], start=2):
        if not raw.strip():
            continue
        report, reason = _parse_line(raw)
        if report is None:
            result.rejected.append({"line": number, "reason": reason, "raw": raw})
        else:
            result.reports.append(report)
    return result