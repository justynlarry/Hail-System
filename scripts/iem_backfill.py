"""Backfill iem_data from the IEM Local Storm Report Archive

One-time historical load -> replay path for a window that needs re-fetching.
Takes an explicit date range, this is separate from the nightly job.

    python3 scripts/iem_backfill.py --start 2021-01-01 --end 2021-02-01

Re-running this script is safe, iem_data's key plus ON CONFLICT DO NOTHING
will return zero rows if a time window has already been loaded.  Benefit
is that running it multiple times after a failure won't duplicate existing
records.
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
    RETURNS run_id
"""

# Status / Finished_at in ONE statement, finished_has_timestamp will reject a
# terminal status with a null finished_at

FINISH_RUN_SQL = """
    UPDATE ingest_runs
        SET run_stats = %s,
            finished_at = now(),
            rows_seen = %s,
            rows_inserted = %s,
            rows_skipped = %s,
            error_detail = %s
        WHERE run_id = %s
"""

# Named Placeholders, passes parse_row from record dict directly

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


def iso_date(txt):
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
        help="window end, YYYY-MM-DD (UTC)",
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
            "fetch_retry", run_id, level=loggin.WARNING,
            attempt=attempt, of=HTTP_ATTEMPTS, delay=delay, error=last_error,
        )
        time.sleep(delay)


def load_valid_types(cursor):
    """report_type/report_text pairs, read once at run-start.

    This is for error message and letting batch continue after unknown type.
    """

    cursor.execute(VALID_TYPES_SQL)
    return {(row[0], row[1]) for row in cursor.fetchall()}


def main(argv=None):
    logging.basicConfig(
        stream=sys.stdout, level=loggin.INFO, format="%(message)s",
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
        started = time.monotonic()

        try:
            url = build_url(args.start, args.end)
            body = fetch(url, run_id)

            # iem_ingest_rejects.raw_raw needs original text verbatim
            # List is kept and zipped against the reader

            lines = body.splitlines()
            reader = csv.DictReader(lines, restkey=RESTKEY)

            log_event("fetch_ok", run_id, bytes=len(body), lines=len(lines))

            with conn.cursor() as cur:
                for raw_line, row in zip(lines[1:], reader):
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
                        curr.execute(INSERT_ROW_SQL, record)
                        inserted += cur.row.count

                    if seen % COMMIT_CHUNK == 0:
                        conn.commit()
                        log_event(
                            "progress", run_id,
                            seen=seen, inserted=inserted, skipped=skipped,
                    )

                conn.commit()

        except Exception as exc:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(FINISH_RUN_SQL, (
                    "failed", seen, inserted, skipped,
                    "f{type(exc).__name__}: {exc}", run_id,
                ))

            conn.commit()
            logging.exception("run_id=%s event=failed", run_id)
            raise

        with conn.cursor() as cur:
            cur.execute(FINISH_RUN_SQL, (
                "complete", seen, inserted, skipped, None, run_id,
            ))
        conn.commit()

    elapsed = rount(time.monotonic() - started, 1)
    log_event(
        "complete", run_id,
        seen=seen, inserted=inserted, skipped=skipped, elapsed=elapsed,
    )

    if skipped:
        log_event(
            "rows_skipped", run_id, level=loggin.WARNING, count=skipped,
        )

    return 0

if __name__ == "__main__":
    sys.exit(main())



