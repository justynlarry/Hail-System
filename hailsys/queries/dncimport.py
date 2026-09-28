"""Staging and promotion for admin-uploaded DNC lists.

Upload is parsed once into dnc_import_rows.  Preview reads from
there and the commit promotes from there, so what admin confirms
is the rows that were reviewed.
"""

import csv
import io
import secrets
from datetime import datetime

# Constant Contacts export.
EXPECTED_HEADERS = {"Email address", "Email status", "Created At"}

_DATE_FORMATS = [
        "%Y-%m-%d %H:%M:%S %z",     # 2019-07-09 00:25:06 +0000
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %I:%M%p",
    "%m/%d/%Y",
]


def _parse_date(text):
    text = (text or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_upload(raw_bytes):
    """Returns (rows, error), error is a string when the FILE is
    unusable (wrong columns, undecodable) as opposed to single
    rows being bad, which are kept and marked rejected.
    """
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], "That file isn't UTF-8 text.  Export it as CSV and retry."

    reader = csv.DictReader(io.StringIO(text))
    missing = EXPECTED_HEADERS - set(reader.fieldnames or [])
    if missing:
        return [], ("Missing column(s): "+",".join(sorted(missing))
                    + ". Expected a Constant Contact export.")

    rows = []
    for line_no, row in enumerate(reader, start=2):
        email = (row.get("Email address") or "").strip()
        status = (row.get("Email status") or "").strip()
        name = " ".join(
            p.strip() for p in (row.get("First name"), row.get("Last name"))
            if p and p.strip())

        rejected = None
        if not email:
            rejected = "no email address"
        elif "@" not in email:
            rejected = "not an email address"
        elif status.lower() != "unsubscribed":
            rejected = f"status is {status or '(blank)'}, not Unsubscribed"

        rows.append({
            "line_no": line_no,
            "email_raw": email or None,
            "name_at_add": name or None,
            "added_at": _parse_date(row.get("Created At")),
            "rejected": rejected,
        })
    return rows, None

_SWEEP_SQL = """
DELETE FROM dnc_import_batches
WHERE committed_at IS NULL
    AND uploaded_at < now() - interval '1 day'
"""

_INSERT_BATCH_SQL = """
INSERT INTO dnc_import_batches (token, filename, uploaded_by, row_count)
VALUES (%(token)s, %(filename)s, %(uploaded_by)s, %(row_count)s)
RETURNING batch_id
"""

_INSERT_ROW_SQL = """
INSERT INTO dnc_import_rows
    (batch_id, line_no, email_raw, name_at_add, added_at, rejected)
VALUES (%(batch_id)s, %(line_no)s, %(email_raw)s, %(name_at_add)s,
        %(added_at)s, %(rejected)s)
"""


def stage(conn, *, rows, filename, uploaded_by):
    token = secrets.token_urlsafe(16)
    with conn.cursor() as cur:
        # Remove unfinished previews so they don't accumulate.
        cur.execute(_SWEEP_SQL)
        cur.execute(_INSERT_BATCH_SQL, {
            "token": token, "filename": filename,
            "uploaded_by": uploaded_by, "row_count": len(rows),
        })
        batch_id = cur.fetchone()["batch_id"]
        for r in rows:
            cur.execute(_INSERT_ROW_SQL, dict(r, batch_id=batch_id))
    return token

_BATCH_SQL = """
SELECT b.batch_id, b.token, b.filename, b.uploaded_at, b.committed_at,
        b.row_count, u.user_name
FROM dnc_import_batches b
JOIN users u ON u.emp_id = b.uploaded_by
WHERE b.token = %(token)s
"""

_SUMMARY_SQL = """
SELECT
    count(*) FILTER (WHERE r.rejected IS NOT NULL)      AS rejected,
    count(*) FILTER (WHERE r.rejected IS NULL
                     AND d.dnc_id IS NOT NULL)          AS already_listed,
    count(*) FILTER (WHERE r.rejected IS NULL
                     AND d.dnc_id IS NULL)              AS to_add,
    count(*) FILTER (WHERE r.rejected IS NULL
                     AND r.added_at IS NULL)            AS no_date
FROM dnc_import_rows r
LEFT JOIN dnc_list d
    ON d.email_norm = lower(trim(r.email_raw))
WHERE r.batch_id = %(batch_id)s
"""

_SAMPLE_SQL = """
SELECT r.line_no, r.email_raw, r.name_at_add, r.added_at
FROM dnc_import_rows r
LEFT JOIN dnc_list d ON d.email_norm = lower(trim(r.email_raw))
WHERE r.batch_id = %(batch_id)s AND r.rejected IS NULL AND d.dnc_id IS NULL
ORDER BY r.line_no LIMIT 20
"""

_REJECTS_SQL = """
SELECT line_no, email_raw, rejected
FROM dnc_import_rows
WHERE batch_id = %(batch_id)s AND rejected IS NOT NULL
ORDER BY line_no LIMIT 50
"""


def fetch_batch(conn, token):
    with conn.cursor() as cur:
        cur.execute(_BATCH_SQL, {"token": token})
        return cur.fetchone()


def fetch_preview(conn, batch_id):
    with conn.cursor() as cur:
        cur.execute(_SUMMARY_SQL, {"batch_id": batch_id})
        summary = cur.fetchone()
        cur. execute(_SAMPLE_SQL, {"batch_id": batch_id})
        sample = cur.fetchall()
        cur.execute(_REJECTS_SQL, {"batch_id": batch_id})
        rejects = cur.fetchall()
    return summary, sample, rejects


_COMMIT_SQL = """
INSERT INTO dnc_list
    (email_raw, added_at, added_by, source, reason, name_at_add)
SELECT
    r.email_raw,
    coalesce(r.added_at, now()),
    %(added_by)s,
    'legacy_import',
    CASE WHEN r.added_at IS NULL
        THEN %(reason)s || ' (source data unreadable)'
        ELSE %(reason)s END,
    r.name_at_add
FROM dnc_import_rows r
WHERE r.batch_id = %(batch_id)s
    AND r.rejected IS NULL
ON CONFLICT (email_norm) DO NOTHING
RETURNING dnc_id
"""

_MARK_COMMITTED_SQL = """
UPDATE dnc_import_batches SET committed_at = now() WHERE batch_id = %(batch_id)s
"""


def commit_batch(conn, *, batch_id, added_by, filename):
    with conn.cursor() as cur:
        cur.execute(_COMMIT_SQL, {
            "batch_id": batch_id, "added_by": added_by,
            "reason": f"Uploaded DNC list: {filename}",
        })
        inserted = len(cur.fetchall())
        cur.execute(_MARK_COMMITTED_SQL, {"batch_id": batch_id})
    return inserted


_DISCARD_SQL = "DELETE FROM dnc_import_batches WHERE batch_id = %(batch_id)s"


def discard_batch(conn, batch_id):
    with conn.cursor() as cur:
        cur.execute(_DISCARD_SQL, {"batch_id": batch_id})
    