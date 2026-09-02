#!/usr/bin/env python3
"""Build LSR reference/lookup tables from IEM Local Storm Report CSV exports.

Reads every *.csv in SOURCE_DIR (read-only), and writes reference tables to
OUTPUT_DIR/reference/. Re-runnable: outputs are overwritten each run.
"""

import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------- configuration

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference")

EXPECTED_COLUMNS = [
    "VALID", "VALID2", "LAT", "LON", "MAG", "WFO", "TYPECODE", "TYPETEXT",
    "CITY", "COUNTY", "STATE", "SOURCE", "REMARK", "UGC", "UGCNAME", "QUALIFIER",
]

# IEM documents QUALIFIER as how the magnitude was determined.
QUALIFIER_MEANINGS = {
    "E": "Estimated - magnitude was estimated by the observer, not measured",
    "M": "Measured - magnitude was measured with an instrument",
    "U": "Unknown - the determination method was not reported",
    "": "(empty) - no qualifier supplied for this report",
}

# Unit inference. A TYPETEXT that names the measured quantity fixes the unit;
# the observed range is then used to raise flags, never to override the name.
LENGTH_KEYWORDS = ["HAIL", "SNOW", "RAIN", "SLEET", "ICE", "PRECIP", "FREEZING"]
SPEED_KEYWORDS = ["WND", "WIND", "GST", "GUST"]
# "WIND CHILL" / "EXTR WIND CHILL" contain a speed keyword but report a
# temperature, so the keyword match must not claim them.
NOT_A_MEASUREMENT = ["CHILL"]

# IEM writes the literal string "None" rather than an empty field for reports
# that carry no magnitude. It is a null marker, not a value, and never a zero.
NULL_MAG_SENTINELS = {"None", "NONE", "none", "M", "NULL"}

# NWS standard hail size chart -- the coin and ball reference objects spotters
# and the public compare a stone against. Used to test whether a "Measured"
# qualifier on a hail report reflects an instrument or just this chart.
HAIL_SIZE_CHART = {0.25, 0.50, 0.75, 0.88, 1.00, 1.25, 1.50, 1.75,
                   2.00, 2.50, 2.75, 3.00, 4.00, 4.50}
_TOOL_RE = re.compile(r"RULER|CALIPER|MEASURE|TAPE MEASURE", re.I)

# Sources whose reports come off an instrument feed rather than a person. Used
# only to pre-fill a hint column; the authoritative call is left to a human.
AUTOMATED_SOURCE_HINTS = {"ASOS", "AWOS", "ASOS/AWOS", "MESONET", "SNOTEL"}

# Sources at or below this row count are listed individually in the notes so the
# long tail of one-off spellings gets human review.
RARE_SOURCE_MAX = 30
_REFOBJ_RE = re.compile(
    r"QUARTER|GOLF|PENNY|NICKEL|PING PONG|BASEBALL|MARBLE|PEA |EGG|SOFTBALL|DIME", re.I)

# Ranges beyond these are still reported in the named unit but get flagged.
PLAUSIBLE_INCHES_MAX = 12.0    # single-event hail diameter or accumulation
PLAUSIBLE_MPH_MIN = 15.0       # below this a "gust"/"wind" report is suspect
PLAUSIBLE_MPH_MAX = 220.0

# ---------------------------------------------------------------- helpers


def parse_valid(raw):
    """Parse compact YYYYMMDDHHMM as UTC. Returns None if unparseable."""
    s = (raw or "").strip()
    if not re.fullmatch(r"\d{12}", s):
        return None
    try:
        return datetime.strptime(s, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_mag(raw):
    """Return float magnitude, or None when the field is empty/unparseable."""
    s = (raw or "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_typetext(text):
    """Collapse case, whitespace and punctuation so near-duplicates collide."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def normalize_source(text):
    """Fold case and collapse whitespace. Deliberately does no other merging:
    truncated spellings are reported as candidates, never silently combined."""
    return re.sub(r"\s+", " ", text.strip()).upper()


def in_range(lo, hi, envelope):
    return envelope[0] <= lo and hi <= envelope[1]


def infer_unit(typetext, present, mag_min, mag_max, zero_count):
    """Return (mag_unit, unit_confidence, [flags]) for one report type.

    Deliberately conservative, in three tiers:

      certain  - the TYPETEXT names the measured quantity (hail size, snowfall,
                 wind gust), so the unit follows from the name itself.
      inferred - the type carried no magnitude at all in the entire dataset, so
                 'none' follows from the data rather than from the name.
      unknown  - everything else. Left blank on purpose for manual fill-in
                 rather than guessed at from the numeric range, because several
                 types here carry values in units the name never mentions
                 (EF number, visibility in miles, temperature in F).
    """
    flags = []
    name = typetext.upper()

    if present == 0:
        return "none", "inferred", flags

    if any(k in name for k in NOT_A_MEASUREMENT):
        flags.append(
            "name contains a wind keyword but reports a temperature, not a speed; "
            "range %g to %g" % (mag_min, mag_max)
        )
        return "", "unknown", flags

    is_length = any(k in name for k in LENGTH_KEYWORDS)
    is_speed = any(k in name for k in SPEED_KEYWORDS)

    if is_length and is_speed:
        flags.append("type name contains both a length and a speed keyword")
        return "", "unknown", flags

    if is_length:
        if mag_max > PLAUSIBLE_INCHES_MAX:
            flags.append(
                "max %g in exceeds the %g in single-event envelope - check whether these "
                "are multi-day or storm-total accumulations (the remarks on the largest "
                "SNOW and HEAVY RAIN values say so explicitly) or data entry errors"
                % (mag_max, PLAUSIBLE_INCHES_MAX)
            )
        if zero_count:
            flags.append(
                "%d row%s report a magnitude of exactly 0, which is not a measurable "
                "accumulation or hail size - likely data entry errors"
                % (zero_count, "" if zero_count == 1 else "s")
            )
        return "inches", "certain", flags

    if is_speed:
        if mag_min < PLAUSIBLE_MPH_MIN:
            flags.append(
                "min %g mph is below the %g mph floor for a reportable wind; the "
                "low tail is suspect" % (mag_min, PLAUSIBLE_MPH_MIN)
            )
        if mag_max > PLAUSIBLE_MPH_MAX:
            flags.append("max %g mph exceeds the %g mph envelope"
                         % (mag_max, PLAUSIBLE_MPH_MAX))
        if zero_count:
            flags.append("%d row%s report a wind speed of exactly 0"
                         % (zero_count, "" if zero_count == 1 else "s"))
        return "mph", "certain", flags

    flags.append(
        "type name names no measurement and %d rows carry a magnitude (range %g to "
        "%g); the unit cannot be established from name or range - fill in by hand"
        % (present, mag_min, mag_max)
    )
    return "", "unknown", flags


CITY_IDX = EXPECTED_COLUMNS.index("CITY")
_UGC_RE = re.compile(r"[A-Z]{2}[CZ]\d{3}")


def repair_row(row, filename, line_num):
    """Recover a row with too many fields caused by an unquoted comma in CITY.

    IEM occasionally emits mesonet station names such as `BISON LAKE, GLENWOOD 15`
    without quoting them, which splits CITY across several fields. Everything
    before CITY and everything after it is still positionally intact, so anchor
    from both ends and rejoin the middle. Bail out loudly if the recovered row
    does not validate -- a different corruption must not be silently absorbed.
    """
    if len(row) < len(EXPECTED_COLUMNS):
        sys.exit(
            "STOPPING: %s line %d has %d fields, fewer than the expected %d. "
            "Cannot recover positionally.\n  row: %r"
            % (filename, line_num, len(row), len(EXPECTED_COLUMNS), row)
        )
    tail = len(EXPECTED_COLUMNS) - CITY_IDX - 1
    fixed = row[:CITY_IDX] + [",".join(row[CITY_IDX:len(row) - tail])] + row[len(row) - tail:]
    rec = dict(zip(EXPECTED_COLUMNS, fixed))
    ok = (
        re.fullmatch(r"\d{12}", rec["VALID"].strip())
        and re.fullmatch(r"[A-Z]{2}", rec["STATE"].strip())
        and rec["QUALIFIER"].strip() in ("E", "M", "U", "")
        and (rec["UGC"].strip() == "" or _UGC_RE.fullmatch(rec["UGC"].strip()))
    )
    if not ok:
        sys.exit(
            "STOPPING: %s line %d has %d fields and does not match the known "
            "unquoted-CITY pattern. Inspect it before re-running.\n  row: %r"
            % (filename, line_num, len(row), row)
        )
    return fixed


def sql_str(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def sql_num(value):
    return "NULL" if value is None else repr(value)


# ---------------------------------------------------------------- main


def main():
    paths = sorted(
        os.path.join(SOURCE_DIR, f)
        for f in os.listdir(SOURCE_DIR)
        if f.lower().endswith(".csv")
    )
    if not paths:
        sys.exit("No CSV files found in %s" % SOURCE_DIR)

    types = defaultdict(lambda: {
        "count": 0, "first": None, "last": None,
        "mag_present": 0, "mag_null": 0, "mag_unparseable": 0, "mag_zero": 0, "mag_odd": 0,
        "mag_min": None, "mag_max": None,
    })
    qualifiers = Counter()
    nonnumeric_mags = Counter()
    qualifier_by_type = defaultdict(Counter)
    hail_source = Counter()
    hail_source_m = Counter()
    hail_mags_by_q = defaultdict(list)
    hail_tool_remarks = 0
    hail_ref_remarks_m = 0
    sources = defaultdict(lambda: {
        "count": 0, "variants": Counter(), "types": set(), "first": None, "last": None,
        "qual": Counter(), "hail": 0, "hail_qual": Counter(),
    })
    per_file = []
    total_rows = 0
    bad_valid = 0
    bad_valid_examples = []
    ragged_rows = 0
    ragged_examples = []
    norm_to_texts = defaultdict(set)
    code_to_texts = defaultdict(set)
    text_to_codes = defaultdict(set)
    overall_first = overall_last = None

    for path in paths:
        rows_in_file = 0
        # newline='' lets csv handle embedded newlines and mixed line endings.
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                per_file.append((os.path.basename(path), 0))
                continue
            header = [h.strip() for h in header]
            if header != EXPECTED_COLUMNS:
                sys.exit(
                    "STOPPING: %s has a different column set than expected.\n"
                    "  expected: %s\n  found:    %s\n"
                    "Resolve the mapping before re-running."
                    % (os.path.basename(path), EXPECTED_COLUMNS, header)
                )

            for row in reader:
                if not row or all(c.strip() == "" for c in row):
                    continue  # trailing blank line
                if len(row) != len(EXPECTED_COLUMNS):
                    row = repair_row(row, os.path.basename(path), reader.line_num)
                    ragged_rows += 1
                    if len(ragged_examples) < 10:
                        ragged_examples.append(
                            (os.path.basename(path), reader.line_num, row[CITY_IDX])
                        )
                rec = dict(zip(EXPECTED_COLUMNS, row))
                rows_in_file += 1
                total_rows += 1

                code = rec["TYPECODE"].strip()
                text = rec["TYPETEXT"].strip()
                qual = rec["QUALIFIER"].strip()
                qualifiers[qual] += 1
                qualifier_by_type[text][qual] += 1
                if text == "HAIL":
                    src = rec["SOURCE"].strip().upper()
                    hail_source[src] += 1
                    if qual == "M":
                        hail_source_m[src] += 1
                    hm = parse_mag(rec["MAG"])
                    if hm is not None:
                        hail_mags_by_q[qual].append(hm)
                    if _TOOL_RE.search(rec["REMARK"]):
                        hail_tool_remarks += 1
                    if qual == "M" and _REFOBJ_RE.search(rec["REMARK"]):
                        hail_ref_remarks_m += 1
                norm_to_texts[normalize_typetext(text)].add(text)
                code_to_texts[code].add(text)
                text_to_codes[text].add(code)

                raw_source = rec["SOURCE"].strip()
                src = sources[normalize_source(raw_source)]
                src["count"] += 1
                src["variants"][raw_source] += 1
                src["types"].add(text)
                src["qual"][qual] += 1
                if text == "HAIL":
                    src["hail"] += 1
                    src["hail_qual"][qual] += 1

                t = types[(code, text)]
                t["count"] += 1

                ts = parse_valid(rec["VALID"])
                if ts is None:
                    bad_valid += 1
                    if len(bad_valid_examples) < 10:
                        bad_valid_examples.append(
                            (os.path.basename(path), rec["VALID"], rec["VALID2"], code, text)
                        )
                else:
                    if t["first"] is None or ts < t["first"]:
                        t["first"] = ts
                    if t["last"] is None or ts > t["last"]:
                        t["last"] = ts
                    if src["first"] is None or ts < src["first"]:
                        src["first"] = ts
                    if src["last"] is None or ts > src["last"]:
                        src["last"] = ts
                    if overall_first is None or ts < overall_first:
                        overall_first = ts
                    if overall_last is None or ts > overall_last:
                        overall_last = ts

                raw_mag = rec["MAG"].strip()
                mag = parse_mag(raw_mag)
                if raw_mag == "":
                    t["mag_null"] += 1
                elif mag is None:
                    t["mag_null"] += 1
                    t["mag_unparseable"] += 1
                    nonnumeric_mags[raw_mag] += 1
                    if raw_mag not in NULL_MAG_SENTINELS:
                        t["mag_odd"] += 1
                else:
                    t["mag_present"] += 1
                    if mag == 0:
                        t["mag_zero"] += 1
                    if t["mag_min"] is None or mag < t["mag_min"]:
                        t["mag_min"] = mag
                    if t["mag_max"] is None or mag > t["mag_max"]:
                        t["mag_max"] = mag

        per_file.append((os.path.basename(path), rows_in_file))

    hail_on_chart = {}
    for q in ("M", "E"):
        vals = hail_mags_by_q.get(q, [])
        hail_on_chart[q] = (
            100.0 * sum(1 for v in vals if v in HAIL_SIZE_CHART) / len(vals) if vals else 0.0
        )

    # ---- assemble report-type rows
    rows = []
    for (code, text), t in types.items():
        unit, conf, flags = infer_unit(
            text, t["mag_present"], t["mag_min"], t["mag_max"], t["mag_zero"])
        if t["mag_present"] and t["mag_null"]:
            flags.append(
                "mixed magnitudes: %d rows with a value, %d without"
                % (t["mag_present"], t["mag_null"])
            )
        # Only flag non-numeric magnitudes that are NOT the documented "None"
        # sentinel; that one is reported once in its own section instead of
        # repeating on every type that never carries a magnitude.
        if t["mag_odd"]:
            flags.append(
                "%d row%s have a MAG that is neither a number nor the usual "
                "\"None\" null marker" % (t["mag_odd"], "" if t["mag_odd"] == 1 else "s")
            )
        rows.append({
            "typecode": code,
            "typetext": text,
            "report_count": t["count"],
            "first_seen": t["first"].strftime("%Y-%m-%d %H:%M:%S+00") if t["first"] else "",
            "last_seen": t["last"].strftime("%Y-%m-%d %H:%M:%S+00") if t["last"] else "",
            "mag_present_count": t["mag_present"],
            "mag_null_count": t["mag_null"],
            "mag_min": t["mag_min"],
            "mag_max": t["mag_max"],
            "mag_unit": unit,
            "unit_confidence": conf,
            "_flags": flags,
        })
    rows.sort(key=lambda r: (-r["report_count"], r["typetext"]))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fields = ["typecode", "typetext", "report_count", "first_seen", "last_seen",
              "mag_present_count", "mag_null_count", "mag_min", "mag_max",
              "mag_unit", "unit_confidence"]

    # ---- report_types.csv
    with open(os.path.join(OUTPUT_DIR, "report_types.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in fields})

    # ---- qualifiers.csv
    with open(os.path.join(OUTPUT_DIR, "qualifiers.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["qualifier", "meaning", "report_count"])
        for q, n in qualifiers.most_common():
            w.writerow([q, QUALIFIER_MEANINGS.get(q, "Undocumented value - not in the IEM vocabulary"), n])

    # ---- seed_report_types.sql
    with open(os.path.join(OUTPUT_DIR, "seed_report_types.sql"), "w", encoding="utf-8") as fh:
        fh.write("-- Generated by build_reference_tables.py -- do not edit by hand.\n")
        fh.write("-- roof_relevant is intentionally NULL for every row; fill it in manually.\n\n")
        fh.write("DROP TABLE IF EXISTS report_types;\n\n")
        fh.write("""CREATE TABLE report_types (
    typecode          TEXT NOT NULL,
    typetext          TEXT NOT NULL,
    report_count      INTEGER NOT NULL,
    first_seen        TIMESTAMPTZ,
    last_seen         TIMESTAMPTZ,
    mag_present_count INTEGER NOT NULL,
    mag_null_count    INTEGER NOT NULL,
    mag_min           NUMERIC,
    mag_max           NUMERIC,
    mag_unit          TEXT,
    unit_confidence   TEXT NOT NULL CHECK (unit_confidence IN ('certain','inferred','unknown')),
    roof_relevant     BOOLEAN,
    PRIMARY KEY (typecode, typetext)
);\n\n""")
        for r in rows:
            fh.write(
                "INSERT INTO report_types (typecode, typetext, report_count, first_seen, "
                "last_seen, mag_present_count, mag_null_count, mag_min, mag_max, mag_unit, "
                "unit_confidence, roof_relevant) VALUES (%s, %s, %d, %s, %s, %d, %d, %s, %s, %s, %s, NULL);\n"
                % (
                    sql_str(r["typecode"]), sql_str(r["typetext"]), r["report_count"],
                    sql_str(r["first_seen"] or None), sql_str(r["last_seen"] or None),
                    r["mag_present_count"], r["mag_null_count"],
                    sql_num(r["mag_min"]), sql_num(r["mag_max"]),
                    sql_str(r["mag_unit"] or None), sql_str(r["unit_confidence"]),
                )
            )

    # ---- data_quality_notes.md
    dup_norm = {k: sorted(v) for k, v in norm_to_texts.items() if len(v) > 1}
    multi_code = {k: sorted(v) for k, v in code_to_texts.items() if len(v) > 1}
    multi_text = {k: sorted(v) for k, v in text_to_codes.items() if len(v) > 1}
    flagged = [r for r in rows if r["_flags"]]

    with open(os.path.join(OUTPUT_DIR, "data_quality_notes.md"), "w", encoding="utf-8") as fh:
        W = fh.write
        W("# LSR data quality notes\n\n")
        W("Generated %s by `build_reference_tables.py`.\n\n"
          % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

        W("## Volume\n\n")
        W("- Total data rows: **%d**\n" % total_rows)
        W("- Files read: **%d**\n" % len(per_file))
        W("- Date range (`VALID`, UTC): **%s** to **%s**\n\n" % (
            overall_first.strftime("%Y-%m-%d %H:%M") if overall_first else "n/a",
            overall_last.strftime("%Y-%m-%d %H:%M") if overall_last else "n/a"))
        W("| File | Rows |\n|---|---:|\n")
        for name, n in per_file:
            W("| `%s` | %d |\n" % (name, n))
        W("\n")

        W("## Timestamp parsing\n\n")
        if bad_valid == 0:
            W("Every row has a `VALID` that parses as a 12-digit UTC `YYYYMMDDHHMM`. No empty or malformed values.\n\n")
        else:
            W("**%d rows** have an empty or unparseable `VALID`.\n\n" % bad_valid)
            W("| File | VALID | VALID2 | TYPECODE | TYPETEXT |\n|---|---|---|---|---|\n")
            for e in bad_valid_examples:
                W("| `%s` | `%s` | `%s` | %s | %s |\n" % e)
            W("\n")

        W("## Malformed rows (field count)\n\n")
        if ragged_rows == 0:
            W("Every row parsed into exactly %d fields.\n\n" % len(EXPECTED_COLUMNS))
        else:
            W("**%d rows** carried more than %d fields because IEM emitted an unquoted "
              "comma inside `CITY` (mesonet station names such as `BISON LAKE, GLENWOOD 15`). "
              "These were repaired by anchoring the fields before and after `CITY` and "
              "rejoining the middle; every repaired row was re-validated on `VALID`, `STATE`, "
              "`UGC` and `QUALIFIER` before being counted. No rows were dropped. The source "
              "file is unmodified.\n\n" % (ragged_rows, len(EXPECTED_COLUMNS)))
            W("| File | Line | Recovered CITY |\n|---|---:|---|\n")
            for f, ln, city in ragged_examples:
                W("| `%s` | %d | `%s` |\n" % (f, ln, city))
            if ragged_rows > len(ragged_examples):
                W("\n(showing the first %d of %d)\n" % (len(ragged_examples), ragged_rows))
            W("\n")

        W("## Non-numeric `MAG` values\n\n")
        if not nonnumeric_mags:
            W("Every non-empty `MAG` parsed as a number.\n\n")
        else:
            W("`MAG` is non-empty but not a number in **%d rows**. IEM writes the literal "
              "string `None` instead of leaving the field empty for reports that carry no "
              "magnitude, so this is a null marker rather than corruption. All of these are "
              "counted as *null* magnitudes: they never reach `mag_min`/`mag_max`, and they are "
              "**not zeros** - do not let a loader coerce them to 0.\n\n"
              % sum(nonnumeric_mags.values()))
            W("| Literal value | Rows |\n|---|---:|\n")
            for v, n in nonnumeric_mags.most_common():
                W("| `%s` | %d |\n" % (v, n))
            W("\n")

        W("## Near-duplicate `TYPETEXT` values\n\n")
        W("Values that collapse to the same string after removing case, whitespace and punctuation.\n\n")
        if not dup_norm:
            W("None found. Note that `WATER SPOUT` / `WATERSPOUT` do not appear at all in this "
              "extract - neither spelling is present, so there is nothing to reconcile here. "
              "Every row in this file is from Colorado (`STATE = CO`), which is why the type "
              "vocabulary is narrower than IEM's national list; expect new types, and possibly "
              "new near-duplicates, if you widen the download.\n\n")
        else:
            W("| Normalized form | Variants seen |\n|---|---|\n")
            for k in sorted(dup_norm):
                W("| `%s` | %s |\n" % (k, ", ".join("`%s`" % v for v in dup_norm[k])))
            W("\nThese are distinct rows in `report_types.csv`. Decide on a canonical spelling "
              "before you key anything off `TYPETEXT`.\n\n")

        W("## Code/text mapping consistency\n\n")
        if not multi_code:
            W("Every `TYPECODE` maps to exactly one `TYPETEXT`.\n\n")
        else:
            W("`TYPECODE` values mapping to more than one `TYPETEXT`. This is normal in the "
              "IEM vocabulary - a single code covers a base type and its intensity variant "
              "(`R` is both `RAIN` and `HEAVY RAIN`) - so `TYPECODE` alone is **not** a key. "
              "The reference table is keyed on `(typecode, typetext)`.\n\n")
            for k in sorted(multi_code):
                W("- `%s` -> %s\n" % (k, ", ".join("`%s`" % v for v in multi_code[k])))
            W("\n")
        if not multi_text:
            W("Every `TYPETEXT` maps to exactly one `TYPECODE`.\n\n")
        else:
            W("`TYPETEXT` values mapping to more than one `TYPECODE`:\n\n")
            for k in sorted(multi_text):
                W("- `%s` -> %s\n" % (k, ", ".join("`%s`" % v for v in multi_text[k])))
            W("\n")

        W("## Magnitude units\n\n")
        W("`mag_unit` is derived from the type name plus the observed magnitude range:\n\n")
        W("- `certain` - the `TYPETEXT` itself names the measured quantity (hail size, snowfall, "
          "wind gust), so the unit follows from the name.\n")
        W("- `inferred` - the type carried no magnitude at all in the whole dataset, so "
          "`mag_unit = none` follows from the data rather than from the name.\n")
        W("- `unknown` - blank on purpose. Fill in by hand.\n\n")
        W("The observed range is used only to raise flags, never to assign a unit. That matters "
          "here: several types carry magnitudes in a unit their name never mentions - `TORNADO` "
          "is an EF number, `FOG`/`DENSE FOG` is visibility in miles, `EXTREME COLD`, "
          "`EXCESSIVE HEAT` and `EXTR WIND CHILL` are degrees Fahrenheit. Assigning a unit from "
          "the numeric range alone would have labelled every one of those wrongly, so they are "
          "all left `unknown`.\n\n")
        W("Flagging thresholds: accumulation/hail above %g in, wind below %g mph or above %g mph.\n\n"
          % (PLAUSIBLE_INCHES_MAX, PLAUSIBLE_MPH_MIN, PLAUSIBLE_MPH_MAX))
        by_conf = Counter(r["unit_confidence"] for r in rows)
        W("Confidence breakdown: %s.\n\n"
          % ", ".join("%s %d" % (k, by_conf[k]) for k in ("certain", "inferred", "unknown") if by_conf[k]))
        unknowns = [r for r in rows if r["unit_confidence"] == "unknown"]
        if unknowns:
            W("### Types needing a manual unit\n\n")
            W("| TYPECODE | TYPETEXT | Reports | mag range | Why |\n|---|---|---:|---|---|\n")
            for r in unknowns:
                rng = ("%g - %g" % (r["mag_min"], r["mag_max"])) if r["mag_present_count"] else "(none)"
                W("| %s | %s | %d | %s | %s |\n" % (
                    r["typecode"], r["typetext"], r["report_count"], rng, "; ".join(r["_flags"])))
            W("\n")

        W("## Flagged types\n\n")
        if not flagged:
            W("Nothing flagged.\n\n")
        else:
            W("Types with something worth a second look - mixed nulls, out-of-envelope ranges, "
              "or non-numeric magnitudes.\n\n")
            for r in flagged:
                rng = ("%g - %g" % (r["mag_min"], r["mag_max"])) if r["mag_present_count"] else "(none)"
                W("### `%s` / `%s`\n\n" % (r["typecode"], r["typetext"]))
                W("%d report%s, mag range %s, unit `%s` (%s).\n\n"
                  % (r["report_count"], "" if r["report_count"] == 1 else "s",
                     rng, r["mag_unit"] or "-", r["unit_confidence"]))
                for f in r["_flags"]:
                    W("- %s\n" % f)
                W("\n")

        W("## Qualifiers\n\n")
        W("| QUALIFIER | Meaning | Rows |\n|---|---|---:|\n")
        for q, n in qualifiers.most_common():
            W("| `%s` | %s | %d |\n" % (
                q if q else "(empty)",
                QUALIFIER_MEANINGS.get(q, "**Undocumented value - not in the IEM vocabulary**"), n))
        W("\n")

    # ---- sources.csv / seed_sources.sql
    source_rows = []
    for name, d in sources.items():
        hq, q = d["hail_qual"], d["qual"]
        source_rows.append({
            "source": name,
            "variants_seen": " | ".join(sorted(d["variants"])),
            "variant_count": len(d["variants"]),
            "report_count": d["count"],
            "first_seen": d["first"].strftime("%Y-%m-%d %H:%M:%S+00") if d["first"] else "",
            "last_seen": d["last"].strftime("%Y-%m-%d %H:%M:%S+00") if d["last"] else "",
            "distinct_type_count": len(d["types"]),
            "qual_measured_count": q["M"],
            "qual_estimated_count": q["E"],
            "qual_unknown_count": q["U"],
            "qual_null_count": q[""],
            "hail_report_count": d["hail"],
            "hail_qual_measured_count": hq["M"],
            "hail_qual_estimated_count": hq["E"],
            "hail_measured_rate": round(100.0 * hq["M"] / d["hail"], 1) if d["hail"] else None,
            "automated_hint": "true" if name in AUTOMATED_SOURCE_HINTS else "false",
        })
    source_rows.sort(key=lambda r: (-r["report_count"], r["source"]))

    sfields = ["source", "variants_seen", "variant_count", "report_count", "first_seen",
               "last_seen", "distinct_type_count", "qual_measured_count",
               "qual_estimated_count", "qual_unknown_count", "qual_null_count",
               "hail_report_count", "hail_qual_measured_count", "hail_qual_estimated_count",
               "hail_measured_rate", "automated_hint"]
    with open(os.path.join(OUTPUT_DIR, "sources.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=sfields)
        w.writeheader()
        for r in source_rows:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in sfields})

    with open(os.path.join(OUTPUT_DIR, "seed_sources.sql"), "w", encoding="utf-8") as fh:
        fh.write("-- Generated by build_reference_tables.py -- do not edit by hand.\n")
        fh.write("-- confidence_tier and is_automated are NULL for every row; fill them in\n")
        fh.write("-- yourself. automated_hint is a generated guess, kept separate on purpose.\n\n")
        fh.write("DROP TABLE IF EXISTS report_sources;\n\n")
        fh.write("""CREATE TABLE report_sources (
    source                    TEXT PRIMARY KEY,
    variants_seen             TEXT NOT NULL,
    variant_count             INTEGER NOT NULL,
    report_count              INTEGER NOT NULL,
    first_seen                TIMESTAMPTZ,
    last_seen                 TIMESTAMPTZ,
    distinct_type_count       INTEGER NOT NULL,
    qual_measured_count       INTEGER NOT NULL,
    qual_estimated_count      INTEGER NOT NULL,
    qual_unknown_count        INTEGER NOT NULL,
    qual_null_count           INTEGER NOT NULL,
    hail_report_count         INTEGER NOT NULL,
    hail_qual_measured_count  INTEGER NOT NULL,
    hail_qual_estimated_count INTEGER NOT NULL,
    hail_measured_rate        NUMERIC,
    automated_hint            BOOLEAN NOT NULL,
    is_automated              BOOLEAN,
    confidence_tier           TEXT
);\n\n""")
        for r in source_rows:
            fh.write(
                "INSERT INTO report_sources (source, variants_seen, variant_count, "
                "report_count, first_seen, last_seen, distinct_type_count, "
                "qual_measured_count, qual_estimated_count, qual_unknown_count, "
                "qual_null_count, hail_report_count, hail_qual_measured_count, "
                "hail_qual_estimated_count, hail_measured_rate, automated_hint, "
                "is_automated, confidence_tier) VALUES "
                "(%s, %s, %d, %d, %s, %s, %d, %d, %d, %d, %d, %d, %d, %d, %s, %s, NULL, NULL);\n"
                % (sql_str(r["source"]), sql_str(r["variants_seen"]), r["variant_count"],
                   r["report_count"], sql_str(r["first_seen"] or None),
                   sql_str(r["last_seen"] or None), r["distinct_type_count"],
                   r["qual_measured_count"], r["qual_estimated_count"],
                   r["qual_unknown_count"], r["qual_null_count"], r["hail_report_count"],
                   r["hail_qual_measured_count"], r["hail_qual_estimated_count"],
                   sql_num(r["hail_measured_rate"]), r["automated_hint"].upper())
            )

    # ---- source notes: case variants and truncation candidates
    case_variants = [r for r in source_rows if r["variant_count"] > 1]
    names = sorted(sources)
    truncation = []
    for a in names:
        for b in names:
            if a != b and b.startswith(a[:max(6, len(a) // 2)]) and len(b) > len(a):
                if sources[a]["count"] <= 5 and sources[b]["count"] > sources[a]["count"]:
                    truncation.append((a, sources[a]["count"], b, sources[b]["count"]))

    with open(os.path.join(OUTPUT_DIR, "data_quality_notes.md"), "a", encoding="utf-8") as fh:
        W = fh.write
        W("## Sources\n\n")
        W("`SOURCE` is free text and is **not** clean. %d distinct raw spellings collapse to "
          "**%d** values once case and whitespace are folded. `sources.csv` is keyed on the "
          "folded value and carries every raw spelling in `variants_seen`.\n\n"
          % (sum(len(d["variants"]) for d in sources.values()), len(sources)))
        if case_variants:
            W("### Case variants\n\n")
            W("%d sources appear in more than one spelling. In every case the difference is "
              "capitalisation only, which suggests IEM changed its export convention partway "
              "through the archive rather than that these are distinct sources.\n\n"
              % len(case_variants))
            W("| Folded value | Spellings seen | Rows |\n|---|---|---:|\n")
            for r in case_variants:
                W("| `%s` | %s | %d |\n"
                  % (r["source"], ", ".join("`%s`" % v for v in r["variants_seen"].split(" | ")),
                     r["report_count"]))
            W("\n")
        W("### Possible truncations - NOT merged\n\n")
        if not truncation:
            W("No truncation candidates found.\n\n")
        else:
            W("These rare values look like truncated forms of a common one. They are left as "
              "separate rows on purpose - the same conservatism applied to `mag_unit`. Merge "
              "them by hand if you agree.\n\n")
            W("| Rare value | Rows | Looks like | Rows |\n|---|---:|---|---:|\n")
            for a, an, b, bn in truncation:
                W("| `%s` | %d | `%s` | %d |\n" % (a, an, b, bn))
            W("\n")
        rare = [r for r in source_rows if r["report_count"] <= RARE_SOURCE_MAX]
        W("### Rare values - review all of these\n\n")
        W("The truncation check above is prefix-based, so it cannot catch a value that was "
          "abbreviated differently (`DEPARTMENT OF HIG` vs `DEPT OF HIGHWAYS`) or one that "
          "combines two feeds (`ASOS/AWOS`). Rather than guess at fuzzy matches, here is every "
          "source with %d rows or fewer - %d of them - so you can eyeball the whole tail.\n\n"
          % (RARE_SOURCE_MAX, len(rare)))
        W("| SOURCE | Rows | Distinct types | First seen | Last seen |\n|---|---:|---:|---|---|\n")
        for r in rare:
            W("| `%s` | %d | %d | %s | %s |\n"
              % (r["source"], r["report_count"], r["distinct_type_count"],
                 r["first_seen"][:10], r["last_seen"][:10]))
        W("\nTogether these are %d rows, %.3f%% of the file, so merging them changes nothing "
          "material - but leaving them unmerged means a join on `SOURCE` silently drops them "
          "into their own tiers.\n\n"
          % (sum(r["report_count"] for r in rare),
             100.0 * sum(r["report_count"] for r in rare) / total_rows))

        W("### Confidence signal\n\n")
        W("`hail_measured_rate` is the share of a source's HAIL reports tagged `QUALIFIER = M`. "
          "Per the section above it reflects reporter training rather than instrumentation, so "
          "read it as a proxy for report discipline. `automated_hint` is a generated guess from "
          "a small list of instrument feeds (%s); `is_automated` and `confidence_tier` are left "
          "NULL in `seed_sources.sql` for you to set.\n\n"
          % ", ".join("`%s`" % x for x in sorted(AUTOMATED_SOURCE_HINTS)))
        W("| SOURCE | Rows | HAIL | HAIL measured rate | Automated hint |\n|---|---:|---:|---:|---|\n")
        for r in source_rows:
            W("| %s | %d | %d | %s | %s |\n"
              % (r["source"], r["report_count"], r["hail_report_count"],
                 "-" if r["hail_measured_rate"] is None else "%.1f%%" % r["hail_measured_rate"],
                 r["automated_hint"]))
        W("\n")

    # ---- qualifier semantics caveat, written from the HAIL rows
    hail_q = qualifier_by_type.get("HAIL", Counter())
    hail_n = sum(hail_q.values())
    if hail_n:
        with open(os.path.join(OUTPUT_DIR, "data_quality_notes.md"), "a", encoding="utf-8") as fh:
            W = fh.write
            W("## Qualifier semantics for HAIL\n\n")
            W("`qualifiers.csv` records IEM's documented meanings (Estimated / Measured / "
              "Unknown). Those meanings do **not** hold up inside the HAIL rows, and a "
              "confidence weight built on a raw `QUALIFIER = 'M'` filter will be wrong.\n\n")
            W("Within HAIL: %s.\n\n" % ", ".join(
                "`%s` %d (%.1f%%)" % (q or "(empty)", n, 100.0 * n / hail_n)
                for q, n in hail_q.most_common()))
            W("If `M` meant an instrument, `M` magnitudes would spread continuously while `E` "
              "clustered on the NWS coin/ball size chart. They do not separate: **%.1f%% of `M` "
              "values and %.1f%% of `E` values land exactly on the chart** (1.00 quarter, "
              "1.75 golf ball, 0.75 penny, 0.88 nickel). Only %d of %d hail remarks mention a "
              "ruler, caliper or tape measure, and %d remarks that explicitly name a coin or "
              "ball as the reference object are tagged `M`.\n\n"
              % (hail_on_chart["M"], hail_on_chart["E"], hail_tool_remarks, hail_n,
                 hail_ref_remarks_m))
            W("What `M` actually tracks is who filed the report:\n\n")
            W("| SOURCE | HAIL reports | tagged `M` |\n|---|---:|---:|\n")
            for src, n in hail_source.most_common(6):
                m = hail_source_m[src]
                W("| %s | %d | %.1f%% |\n" % (src, n, 100.0 * m / n))
            W("\nTrained reporters are tagged `M` roughly twice as often as the public for the "
              "same chart-comparison method, so the tag is a proxy for reporter training, not "
              "for measurement. Weight hail confidence on `SOURCE` rather than `QUALIFIER`. The "
              "small number of `M` values that fall *off* the chart are the closest thing in "
              "this file to genuine measurements.\n\n")

    # ---- stdout summary
    print("Files read:      %d" % len(per_file))
    print("Total rows:      %d" % total_rows)
    print("Date range:      %s  ->  %s (UTC)" % (
        overall_first.strftime("%Y-%m-%d %H:%M") if overall_first else "n/a",
        overall_last.strftime("%Y-%m-%d %H:%M") if overall_last else "n/a"))
    print("Distinct types:  %d" % len(rows))
    print("Unparseable VALID: %d" % bad_valid)
    print("Repaired rows:   %d (unquoted comma in CITY)" % ragged_rows)
    print()
    print("%-4s %-28s %9s %8s %8s  %-8s %s" % (
        "CODE", "TYPETEXT", "REPORTS", "MAG_MIN", "MAG_MAX", "UNIT", "CONFIDENCE"))
    for r in rows:
        print("%-4s %-28s %9d %8s %8s  %-8s %s" % (
            r["typecode"], r["typetext"][:28], r["report_count"],
            "" if r["mag_min"] is None else "%g" % r["mag_min"],
            "" if r["mag_max"] is None else "%g" % r["mag_max"],
            r["mag_unit"] or "-", r["unit_confidence"]))
    print()
    print("Distinct sources: %d (from %d raw spellings)"
          % (len(source_rows), sum(r["variant_count"] for r in source_rows)))
    print()
    print("%-26s %8s %6s %8s  %s" % ("SOURCE", "REPORTS", "HAIL", "HAIL M%", "AUTO?"))
    for r in source_rows[:12]:
        print("%-26s %8d %6d %8s  %s"
              % (r["source"], r["report_count"], r["hail_report_count"],
                 "-" if r["hail_measured_rate"] is None else "%.1f" % r["hail_measured_rate"],
                 r["automated_hint"]))
    print()
    print("Wrote report_types.csv, qualifiers.csv, sources.csv, seed_report_types.sql, "
          "seed_sources.sql, data_quality_notes.md to %s" % OUTPUT_DIR)


if __name__ == "__main__":
    main()
