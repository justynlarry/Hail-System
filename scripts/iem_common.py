"""Shared machinery for the IEM ingest scripts.

iem_backfill.py and iem_ingest.py differ in how they decide
which window to request.  The backfill takes a date range and chops it into
months; the nightly computes a rolling window from the clock.  Everything after
that (request, state assertion, row loop, run lifecycle, counters)
is the same.

This module talks to the network and the database.  iem_parse.py does neither,
which is why that one is unit-tested and this one is exercised by running it.
"""

import csv
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg

from iem_parse import RESTKEY, parse_row

# ------ ------  ------ ------  ------ ------  ------ ------  ------ ------
# IEM ENDPOINT
# ------ ------  ------ ------  ------ ------  ------ ------  ------ ------

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py"

# state=CO, WFO list does not cover all the areas in a manner consistent with
# the coverage area.

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
    """Emit one logfmt line: key=value pairs, space separated.

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


def configure_logging():
    """stdout only.  systemd captures it into journald tagged by unit, so
    there is no file to rotate and no path to get wrong.  No timestamp or
    logger name in the format -- journald supplies both.
    """
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format="%(message)s",
    )


def build_url(**params):
    """Build request URL from a parameter dictionary.

    urlencode over string concatenation.  state and fmt are constant for every
    request; callers supply the window, which is the only thing that differs
    between the two scripts -- sts/ets for an archive range, recent for the
    rolling nightly window.
    """

    query = {"state": IEM_STATE, "fmt": "csv", **params}
    return f"{IEM_URL}?{urllib.parse.urlencode(query)}"


def archive_url(window_start, window_end):
    """sts/ets form.  Half-open: ets is exclusive, verified 2026-09-09."""
    return build_url(
        sts=window_start.strftime(IEM_TIME_FORMAT),
        ets=window_end.strftime(IEM_TIME_FORMAT),
    )


def recent_url(seconds):
    """recent form.  SECONDS, not hours -- hours= returns HTTP 422."""
    return build_url(recent=seconds)


def fetch(url, run_id):
    """GET URL, and retry transient failures, return body as text

    urlopen raises on 4xx and 5xx instead of returning a response object
    to check.  A malformed request ends the run without any effort.
    """

    last_error = None

    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "hail-system/ingest"}
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


def assert_single_state(lines):
    """Raise if the response carries any state other than IEM_STATE.

    IEM validates fmt (a bad value returns 422) but silently IGNORES unknown
    filter parameters -- ?stat=CO returns HTTP 200 and every LSR in the
    country.  Verified 2026-09-09: a single transposed character produced 27 NJ
    rows, 13 KS, 11 TX, with CO fourth on the list.  Nothing upstream reports
    this, so filter correctness has to be checked in the response.

    Overflow rows are excluded on purpose.  A row with an unquoted comma in
    CITY shifts every later column by one, so COUNTY lands in STATE and the
    2018 archive row reads STATE='GARFIELD'.  Asserting on those would end the
    run on each of the 76 known malformed rows -- the very thing the
    field-count check runs first to prevent.  They still reach parse_row and
    still become rejects.  An ignored filter produces thousands of WELL-FORMED
    out-of-state rows, so nothing is lost by ignoring the malformed ones here.
    """

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
            f"{sorted(wrong_state)} -- check the filter parameter name"
        )


def load_valid_types(cursor):
    """report_type/report_text pairs, read once at run-start.

    This is for error message and letting batch continue after unknown type.
    """

    cursor.execute(VALID_TYPES_SQL)
    return {(row[0], row[1]) for row in cursor.fetchall()}


@dataclass
class Counters:
    """Attempted totals, and the totals as of the last successful COMMIT.

    The two differ only after a rollback.  Writing attempted counts to
    ingest_runs on a failed run would claim rows that no longer exist, so the
    committed set is what goes in the columns and the gap between them is what
    goes in error_detail.
    """

    seen: int = 0
    inserted: int = 0
    skipped: int = 0
    done_seen: int = 0
    done_inserted: int = 0
    done_skipped: int = 0

    def checkpoint(self):
        """Call immediately after a successful commit."""
        self.done_seen = self.seen
        self.done_inserted = self.inserted
        self.done_skipped = self.skipped


def load_lines(conn, run_id, lines, valid_types, ingested_at, counters):
    """Parse and insert every data row in one fetched body.

    Commits every COMMIT_CHUNK rows and once at the end, checkpointing the
    counters each time.

    There is no try/except around the row loop, deliberately.  Every expected
    problem comes back from parse_row as a reject; anything that raises is
    either a bug or a changed upstream contract, and both should end the run
    rather than be counted and forgotten.
    """

    reader = csv.DictReader(lines, restkey=RESTKEY)

    # iem_ingest_rejects.raw_row needs the original text verbatim.
    #
    # Do NOT zip lines[1:] against the reader.  A quoted REMARK may contain a
    # newline, which is one CSV record spanning two physical lines: the reader
    # consumes both, the zip advances one, and every raw_row after it is the
    # wrong line.  That would break the one property that makes rejecting
    # non-lossy.  reader.line_num is the physical line count actually consumed,
    # so slicing by it stays aligned no matter how many lines a record spans.
    # It starts at 1, the header.
    consumed = 1

    with conn.cursor() as cur:
        for row in reader:
            raw_line = "\n".join(lines[consumed:reader.line_num])
            consumed = reader.line_num

            counters.seen += 1
            record, reject = parse_row(row, valid_types)

            if reject is not None:
                cur.execute(INSERT_REJECT_SQL, (
                    run_id, raw_line, reject["reason"], reject["detail"],
                ))
                counters.skipped += 1
            else:
                record["ingested_at"] = ingested_at
                cur.execute(INSERT_ROW_SQL, record)
                # rowcount after ON CONFLICT DO NOTHING reports rows actually
                # inserted, excluding conflicts.  A Python counter would report
                # rows handed over, which would make seen == inserted always
                # and hide whether the overlap is doing anything.
                counters.inserted += cur.rowcount

            if counters.seen % COMMIT_CHUNK == 0:
                conn.commit()
                counters.checkpoint()
                log_event(
                    "progress", run_id,
                    seen=counters.seen, inserted=counters.inserted,
                    skipped=counters.skipped,
                )

        # The tail.  Without this the final partial chunk is rolled back on
        # exit and disappears silently.
        conn.commit()
        counters.checkpoint()


def perform_run(mode, window_start, window_end, windows):
    """Run one ingest end to end.  Returns an exit status.

    windows is an iterable of (chunk_start, chunk_end, url).  That is the only
    thing the two scripts supply differently: the backfill yields one tuple per
    month with an sts/ets url, the nightly yields a single tuple with a recent
    url.  window_start and window_end are what gets recorded in ingest_runs --
    what was asked for, not how it was chopped up.

    A run that skipped rows still returns 0.  The process did its job, and a
    unit stuck in `failed` because a malformed row arrived would train us to
    ignore `systemctl --failed`.  The alert belongs on the skip count read from
    ingest_runs, and on a sustained or sudden rise rather than any non-zero
    value -- the comma-in-CITY rows fire during normal operation.
    """

    # One timestamp for every row a run inserts
    ingested_at = datetime.now(timezone.utc)

    with psycopg.connect() as conn:
        with conn.cursor() as cur:
            valid_types = load_valid_types(cur)

            # Written before fetch and committed, leaving a record of failed
            # fetches.  A row created afterward would mean the failure most
            # worth recording is the one that leaves no trace.
            cur.execute(START_RUN_SQL, (mode, window_start, window_end))
            run_id = cur.fetchone()[0]
            conn.commit()

        log_event(
            "start", run_id, mode=mode,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            valid_types=len(valid_types),
        )

        counters = Counters()
        started = time.monotonic()

        try:
            for chunk_start, chunk_end, url in windows:
                body = fetch(url, run_id)
                lines = body.splitlines()

                log_event(
                    "fetch_ok", run_id,
                    window_start=chunk_start.isoformat(),
                    window_end=chunk_end.isoformat(),
                    bytes=len(body), lines=len(lines),
                )

                assert_single_state(lines)
                load_lines(
                    conn, run_id, lines, valid_types, ingested_at, counters,
                )

        except Exception as exc:
            conn.rollback()

            # Committed counts go in the columns; the attempted counts are
            # named in the text, because the gap between them is exactly how
            # much work the rollback threw away.
            detail = (
                f"{type(exc).__name__}: {exc} "
                f"[committed seen={counters.done_seen} "
                f"inserted={counters.done_inserted} "
                f"skipped={counters.done_skipped}; "
                f"attempted seen={counters.seen} "
                f"inserted={counters.inserted} skipped={counters.skipped}]"
            )
            with conn.cursor() as cur:
                cur.execute(FINISH_RUN_SQL, (
                    "failed", counters.done_seen, counters.done_inserted,
                    counters.done_skipped, detail, run_id,
                ))

            conn.commit()
            logging.exception("run_id=%s event=failed", run_id)
            raise

        with conn.cursor() as cur:
            cur.execute(FINISH_RUN_SQL, (
                "complete", counters.seen, counters.inserted,
                counters.skipped, None, run_id,
            ))
        conn.commit()

    elapsed = round(time.monotonic() - started, 1)
    log_event(
        "complete", run_id,
        seen=counters.seen, inserted=counters.inserted,
        skipped=counters.skipped, elapsed=elapsed,
    )

    if counters.skipped:
        log_event(
            "rows_skipped", run_id, level=logging.WARNING,
            count=counters.skipped,
        )

    return 0
