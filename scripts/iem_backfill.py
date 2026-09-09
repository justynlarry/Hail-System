"""Backfill iem_data from the IEM Local Storm Report Archive

One-time historical load -> replay path for a window that needs re-fetching.
Takes an explicit date range, this is separate from the nightly job.

    python3 scripts/iem_backfill.py --start 2021-01-01 --end 2021-02-01

Re-running this script is safe for iem_data: the natural key plus ON CONFLICT
DO NOTHING means a window that has already been loaded inserts zero rows, so
re-running after a failure cannot duplicate storm reports.

iem_ingest_rejects is deliberately NOT deduplicated.  A re-run writes its
rejects again under the new run_id, because that table answers "what did run
47 drop" -- collapsing rejects across runs would destroy the question it
exists to answer.  Expect reject counts to grow with each re-run; that is the
design, not a leak.
"""

import argparse
import csv
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import psycopg

from iem_parse import RESTKEY, parse_row

# ------ ------  ------ ------  ------ ------  ------ ------  ------ ------  ------
# IEM ENDPOINT
# ------ ------  ------ ------  ------ ------  ------ ------  ------ ------  ------

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py"

# state=CO, WFO list does not cover all the areas in a manner consistent with the
# coverage area.

IEM_STATE = "CO"

# IEM Date Format from VALID2: 2024-01-01T00:00Z

IEM_TIME_FORMAT = "%Y-%m-%dT%H:%MZ"

# urlopen needs a timeout, otherwise it stays open indefinitely

HTTP_TIMEOUT = 120

# Transient failure mitigation

HTTP_ATTEMPTS = 3
HTTP_BACKOFF = 2

# Rows per commit, in chunks to reduce transaction overhead

COMMIT_CHUNK = 4000

# Archive floor is fixed, not rolling, at 5 years.

ARCHIVE_FLOOR = datetime(2021, 1, 1, tzinfo=timezone.utc)

VALID_TYPES_SQL = "SELECT report_type, report_text FROM report_types"

START_RUN_SQL = """
    INSERT INTO ingest_runs (run_mode, window_start, window_end)
    VALUES (%s, %s, %s)
    RETURNING run_id
"""

# Status / Finished_at in ONE statement, finished_has_timestamp will reject a
# terminal status with a null finished_at

FINISH_RUN_SQL = """
    UPDATE ingest_runs
        SET run_status = %s,
            finished_at = now(),
            rows_seen = %s,
            rows_inserted = %s,
            rows_skipped = %s,
            error_detail = %s
        WHERE run_id = %s
"""

# Named Placeholders, passes parse_row from record dict directly
#
# TODO: iem_data.nws_issuer is NOT NULL, but parse_row returns _clean(WFO),
# which is None for an empty WFO field.  No reject reason covers it, so such a
# row would raise IntegrityError mid-batch and end the run.  Not yet observed
# in the archive; left unpatched deliberately rather than guessed at.

INSERT_ROW_SQL = """
    INSERT INTO iem_data (
        utc_datetime, latitude, longitude, magnitude,
        report_type, report_text, nws_issuer, report_source,
        report_qualifier, county, state, nws_geo_code, remark, ingested_at
    ) VALUES (
        %(utc_datetime)s, %(latitude)s, %(longitude)s, %(magnitude)s,
        %(report_type)s, %(report_text)s, %(nws_issuer)s, %(report_source)s,
        %(report_qualifier)s, %(county)s, %(state)s, %(nws_geo_code)s,
        %(remark)s, %(ingested_at)s
    )
    ON CONFLICT (utc_datetime, latitude, longitude, report_text, magnitude)
    DO NOTHING
"""

INSERT_REJECT_SQL = """
    INSERT INTO iem_ingest_rejects (run_id, raw_row, reason, detail)
    VALUES (%s, %s, %s, %s)
"""

def log_event(event, run_id=None, level=logging.INFO, **fields):
    """Emit on logfmt line: key=value pairs, space separated.

    Readable in 'journalctl -u iem-ingest' by eye - parseable by a log
    shipper later without regex
    """

    parts = []
    if run_id is not None:
        parts.append(f"run_id={run_id}")
    parts.append(f"event={event}")
    for key, value in fields.items():
        text = str(value)
        parts.append(f"{key}={text!r}" if " " in text else f"{key}={text}")
    logging.log(level, " ".join(parts))


def iso_date(text):
    """argparse type: validate YYY-MM-DD and return an aware UTC datetime
    """

    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date in YYYY-MM-DD form"
        )
    return parsed.replace(tzinfo=timezone.utc)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Backfill iem_data from the IEM LSR archive.",
    )
    parser.add_argument(
        "--start", required=True, type=iso_date,
        help="window start, YYYY-MM-DD (UTC, inclusive)",
    )
    parser.add_argument(
        "--end", required=True, type=iso_date,
        help="window end, YYYY-MM-DD (UTC, EXCLUSIVE -- the date given is "
             "midnight UTC, so its reports are not fetched)",
    )
    parser.add_argument(
        "--mode", default="backfill", choices=("backfill", "replay"),
        help="value recorded in ingest_runs.run_mode (default: backfill)",
    )
    args = parser.parse_args(argv)
    if args.end <= args.start:
        parser.error("--end must be after --start")

    return args


def build_url(window_start, window_end):
    """Build request URL from a parameter dicitonary.

    urlencode over string concatenation
    """

    params = {
        "state": IEM_STATE,
        "fmt": "csv",
        "sts": window_start.strftime(IEM_TIME_FORMAT),
        "ets": window_end.strftime(IEM_TIME_FORMAT),
    }
    return f"{IEM_URL}?{urllib.parse.urlencode(params)}"


def fetch(url, run_id):
    """GET URL, and retry transient failures, return body as text

    urlopen raises on 4xx and 5xx instead of returning a response object
    to check.  A malformed request ends the run without any effort.
    """

    last_error = None

    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "hail-system/backfill"}
            )
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
                return resp.read().decode("utf-8")

        # ------ HTTPError Handling ------
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise
            last_error = exc
        except urllib.error.URLError as exc:
            last_error = exc

        if attempt == HTTP_ATTEMPTS:
            raise last_error

        delay = HTTP_BACKOFF ** attempt
        log_event(
            "fetch_retry", run_id, level=logging.WARNING,
            attempt=attempt, of=HTTP_ATTEMPTS, delay=delay, error=last_error,
        )
        time.sleep(delay)


def month_windows(start, end):
    """Yield (start, end) sub-windows covering [start, end), one per month.

    Five years of Colorado LSRs in a single GET is one request that either
    finishes inside HTTP_TIMEOUT or loses everything.  A month is small enough
    to retry cheaply and gives the progress log something to say.  All windows
    run under ONE run_id: this is one backfill, and ingest_runs.window_start /
    window_end record what was asked for, not how it was chopped up.

    Boundaries are half-open, so the caller's --end stays exclusive and no
    report is fetched twice.
    """

    cursor = start
    while cursor < end:
        if cursor.month == 12:
            following = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            following = cursor.replace(month=cursor.month + 1, day=1)
        following = min(following, end)
        yield cursor, following
        cursor = following


def load_valid_types(cursor):
    """report_type/report_text pairs, read once at run-start.

    This is for error message and letting batch continue after unknown type.
    """

    cursor.execute(VALID_TYPES_SQL)
    return {(row[0], row[1]) for row in cursor.fetchall()}


def main(argv=None):
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format="%(message)s",
    )

    args = parse_args(argv)

    if args.start < ARCHIVE_FLOOR:
        log_event(
            "below_archive_floor", level=logging.WARNING,
            start=args.start.date(), floor=ARCHIVE_FLOOR.date(),
        )


    # One timestamp for every row a run inserts

    ingested_at = datetime.now(timezone.utc)

    with psycopg.connect() as conn:
        with conn.cursor() as cur:
            valid_types = load_valid_types(cur)

            # Written before fetch and committed, leaving a record of failed fetches
            cur.execute(
                START_RUN_SQL, (args.mode, args.start, args.end)
            )
            run_id = cur.fetchone()[0]
            conn.commit()

        log_event(
            "start", run_id, mode=args.mode,
            window_start=args.start.isoformat(),
            window_end=args.end.isoformat(),
            valid_types=len(valid_types),
        )

        seen = inserted = skipped =0

        # Counters as of the last COMMIT.  The running totals above count
        # attempts, and a rollback discards an uncommitted chunk -- writing
        # those to ingest_runs would claim rows that no longer exist.
        done_seen = done_inserted = done_skipped = 0

        started = time.monotonic()

        try:
            for chunk_start, chunk_end in month_windows(args.start, args.end):
                url = build_url(chunk_start, chunk_end)
                body = fetch(url, run_id)

                lines = body.splitlines()
                reader = csv.DictReader(lines, restkey=RESTKEY)

                log_event(
                    "fetch_ok", run_id,
                    window_start=chunk_start.isoformat(),
                    window_end=chunk_end.isoformat(),
                    bytes=len(body), lines=len(lines),
                )

                # IEM validates fmt (a bad value returns 422) but silently
                # IGNORES unknown filter parameters -- ?stat=CO returns HTTP 200
                # and every LSR in the country.  Verified 2026-09-09: a single
                # transposed character produced 27 NJ rows, 13 KS, 11 TX, with
                # CO fourth on the list.  Nothing upstream reports this, so
                # filter correctness has to be checked in the response.
                #
                # Overflow rows are excluded on purpose.  A row with an
                # unquoted comma in CITY shifts every later column by one, so
                # COUNTY lands in STATE and the 2018 archive row reads
                # STATE='GARFIELD'.  Asserting on those would end the run on
                # each of the 76 known malformed rows -- the very thing the
                # field-count check runs first to prevent.  They still reach
                # parse_row and still become rejects.  An ignored filter
                # produces thousands of WELL-FORMED out-of-state rows, so
                # nothing is lost by ignoring the malformed ones here.
                wrong_state = {
                    row["STATE"].strip()
                    for row in csv.DictReader(lines, restkey=RESTKEY)
                    if not row.get(RESTKEY)
                    and row.get("STATE")
                    and row["STATE"].strip() != IEM_STATE
                }
                if wrong_state:
                    raise ValueError(
                        f"response contains states other than {IEM_STATE}: "
                        f"{sorted(wrong_state)} -- check the filter parameter "
                        f"name"
                    )

                # iem_ingest_rejects.raw_row needs the original text verbatim.
                #
                # Do NOT zip lines[1:] against the reader.  A quoted REMARK may
                # contain a newline, which is one CSV record spanning two
                # physical lines: the reader consumes both, the zip advances
                # one, and every raw_row after it is the wrong line.  That
                # would break the one property that makes rejecting non-lossy.
                # reader.line_num is the physical line count actually consumed,
                # so slicing by it stays aligned no matter how many lines a
                # record spans.  It starts at 1, the header.
                consumed = 1

                with conn.cursor() as cur:
                    for row in reader:
                        raw_line = "\n".join(lines[consumed:reader.line_num])
                        consumed = reader.line_num

                        seen +=1
                        record, reject = parse_row(row, valid_types)

                        if reject is not None:
                            cur.execute(INSERT_REJECT_SQL, (
                                run_id, raw_line,
                                reject["reason"], reject["detail"],
                            ))
                            skipped +=1
                        else:
                            record["ingested_at"] = ingested_at
                            cur.execute(INSERT_ROW_SQL, record)
                            inserted += cur.rowcount

                        if seen % COMMIT_CHUNK == 0:
                            conn.commit()
                            done_seen, done_inserted, done_skipped = (
                                seen, inserted, skipped
                            )
                            log_event(
                                "progress", run_id,
                                seen=seen, inserted=inserted, skipped=skipped,
                            )

                    conn.commit()
                    done_seen, done_inserted, done_skipped = (
                        seen, inserted, skipped
                    )

        except Exception as exc:
            conn.rollback()

            # Committed counts go in the columns; the attempted counts are
            # named in the text, because the gap between them is exactly how
            # much work the rollback threw away.
            detail = (
                f"{type(exc).__name__}: {exc} "
                f"[committed seen={done_seen} inserted={done_inserted} "
                f"skipped={done_skipped}; attempted seen={seen} "
                f"inserted={inserted} skipped={skipped}]"
            )
            with conn.cursor() as cur:
                cur.execute(FINISH_RUN_SQL, (
                    "failed", done_seen, done_inserted, done_skipped,
                    detail, run_id,
                ))

            conn.commit()
            logging.exception("run_id=%s event=failed", run_id)
            raise

        with conn.cursor() as cur:
            cur.execute(FINISH_RUN_SQL, (
                "complete", seen, inserted, skipped, None, run_id,
            ))
        conn.commit()

    elapsed = round(time.monotonic() - started, 1)
    log_event(
        "complete", run_id,
        seen=seen, inserted=inserted, skipped=skipped, elapsed=elapsed,
    )

    if skipped:
        log_event(
            "rows_skipped", run_id, level=logging.WARNING, count=skipped,
        )

    return 0

if __name__ == "__main__":
    sys.exit(main())



