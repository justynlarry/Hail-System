"""The send-permission list: Who can be emailed.

Admin uploads a list of addresses that have permission and what type they are.  Service clients,
or Constant Contact subscribers.  Like the DNC import (dncimport.py), an upload is parsed once into
permission_import_rows.  The preview reads from there and the commit promotes from there, so what the admin
confirms is what was reviewed.  A permission row is removed, not deleted.  An address with no active row is not 
emailed, the send list leaves it out (sendlist.py) and the database refuses to queue it (sql/066).
"""

import csv
import io
import re
import secrets

BASES = {"service_client": "RBI service clients (the in-house database)",
         "cc_subscriber": "Constant Contact subscribers"}
MAX_NOTE = 200

# The same test as the send page's address list and the DNC import.
_ADDRESS = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")

# Client File's headings are unknown, so the email column is found by its heading
# ignoring case and extra spaces.
_EMAIL_HEADINGS = ("email address", "email", "e-mail", "email_address")
# A name, when the file has one.  first and last, or a single name column.
_NAME_HEADINGS = (("first name", "last name"), ("name",), ("contact name",))


def _heading(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def parse_upload(raw_bytes):
    """(rows, error).  error is a string when the FILE is unusable (not UTF-8, no email column).
    a single bad row is kept and marked rejected.  A Constant Contact export gives permission only
    for its Active rows.
    """
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], "That file isn't UTF-8 text.  Export it as a CSV and try again."

    reader = csv.DictReader(io.StringIO(text))
    headings = {_heading(h): h for h in (reader.fieldnames or [])}
    email_col = next((headings[h] for h in _EMAIL_HEADINGS if h in headings), None)
    if email_col is None:
        return [], "No email column found.  The file needs a column headed Email or Email address."
    status_col = headings.get("email status")
    name_cols = next(([headings[h] for h in group] for group in _NAME_HEADINGS
                      if all(h in headings for h in group)), [])

    rows, seen = [], set()
    for line_no, row in enumerate(reader, start=2):
        email = (row.get(email_col) or "").strip()
        name = " ".join(p.strip() for p in (row.get(c) for c in name_cols) if p and p.strip())
        status = (row.get(status_col) or "").strip() if status_col else ""

        rejected = None
        if not email:
            rejected = "no email address"
        elif not _ADDRESS.fullmatch(email):
            rejected = "not an email address"
        elif status_col and status.lower() != "active":
            rejected = f"status is {status or '(blank)'}, not Active"
        elif email.lower() in seen:
            rejected = "already earlier in this file"
        if rejected is None:
            seen.add(email.lower())

        rows.append({"line_no": line_no, "email_raw": email or None, "name": name or None,
                     "rejected": rejected})
    return rows, None

_SWEEP_SQL = """
DELETE FROM permission_import_batches
WHERE committed_at IS NULL
    AND uploaded_at < now() - interval '1 day'
"""

_INSERT_BATCH_SQL = """
INSERT INTO permission_import_batches (token, filename, basis, as_of, note, uploaded_by, row_count)
VALUES (%(token)s, %(filename)s, %(basis)s, %(as_of)s, %(note)s, %(uploaded_by)s, %(row_count)s)
RETURNING batch_id
"""

_INSERT_ROW_SQL = """
INSERT INTO permission_import_rows (batch_id, line_no, email_raw, name, rejected)
VALUES (%(batch_id)s, %(line_no)s, %(email_raw)s, %(name)s, %(rejected)s)
"""


def stage(conn, *, rows, filename, uploaded_by, basis, as_of, note=None):
    """The token of a new, uncommitted batch.  ValueError for an unknown basis or a long note."""
    if basis not in BASES:
        raise ValueError(f"unknown basis: {basis!r}")
    note = (note or "").strip() or None
    if note is not None and len(note) > MAX_NOTE:
        raise ValueError(f"the note is longer than {MAX_NOTE} characters")
    token = secrets.token_urlsafe(16)
    with conn.cursor() as cur:
        cur.execute(_SWEEP_SQL)     # unfinished previews don't pile up
        cur.execute(_INSERT_BATCH_SQL, {
            "token": token, "filename": filename, "basis": basis, "as_of": as_of, "note": note,
            "uploaded_by": uploaded_by, "row_count": len(rows),
        })
        batch_id = cur.fetchone()["batch_id"]
        for r in rows:
            cur.execute(_INSERT_ROW_SQL, dict(r, batch_id=batch_id))
    return token


_BATCH_SQL = """
SELECT b.batch_id, b.token, b.filename, b.basis, b.as_of, b.note, b.uploaded_at,
       b.committed_at, b.row_count, u.user_name
FROM permission_import_batches b
JOIN users u ON u.emp_id = b.uploaded_by
WHERE b.token = %(token)s
"""

# Counted against the batch's own basis:  an address already a client can still be added as a
# subscriber.  on_dnc is for information:  a do-not-contact address keeps its row, and is still
# never emailed, because the DNC rule comes first.
_SUMMARY_SQL = """
SELECT
    count(*) FILTER (WHERE r.rejected IS NOT NULL)                     AS rejected,
    count(*) FILTER (WHERE r.rejected IS NULL AND p.permission_id IS NOT NULL) AS already_listed,
    count(*) FILTER (WHERE r.rejected IS NULL AND p.permission_id IS NULL)     AS to_add,
    count(*) FILTER (WHERE r.rejected IS NULL AND d.dnc_id IS NOT NULL)        AS on_dnc
FROM permission_import_rows r
JOIN permission_import_batches b ON b.batch_id = r.batch_id
LEFT JOIN send_permission p
    ON p.email_norm = lower(trim(r.email_raw)) AND p.basis = b.basis AND p.removed_at IS NULL
LEFT JOIN dnc_list d
    ON d.email_norm = lower(trim(r.email_raw)) AND d.removed_at IS NULL
WHERE r.batch_id = %(batch_id)s
"""

_SAMPLE_SQL = """
SELECT r.line_no, r.email_raw, r.name
FROM permission_import_rows r
JOIN permission_import_batches b ON b.batch_id = r.batch_id
LEFT JOIN send_permission p
    ON p.email_norm = lower(trim(r.email_raw)) AND p.basis = b.basis AND p.removed_at IS NULL
WHERE r.batch_id = %(batch_id)s AND r.rejected IS NULL AND p.permission_id IS NULL
ORDER BY r.line_no LIMIT 20
"""

_REJECTS_SQL = """
SELECT line_no, email_raw, rejected
FROM permission_import_rows
WHERE batch_id = %(batch_id)s AND rejected IS NOT NULL
ORDER BY line_no LIMIT 50
"""


def fetch_batch(conn, token):
    with conn.cursor() as cur:
        cur.execute(_BATCH_SQL, {"token": token})
        return cur.fetchone()


def fetch_preview(conn, batch_id):
    """(summary, sample, rejects) for the preview page."""
    with conn.cursor() as cur:
        cur.execute(_SUMMARY_SQL, {"batch_id": batch_id})
        summary = cur.fetchone()
        cur.execute(_SAMPLE_SQL, {"batch_id": batch_id})
        sample = cur.fetchall()
        cur.execute(_REJECTS_SQL, {"batch_id": batch_id})
        rejects = cur.fetchall()
    return summary, sample, rejects


_COMMIT_SQL = """
INSERT INTO send_permission (email_raw, basis, as_of, batch_id, detail, added_by)
SELECT r.email_raw, b.basis, b.as_of, b.batch_id,
       'Uploaded ' || b.filename || coalesce(': ' || b.note, ''), %(added_by)s
FROM permission_import_rows r
JOIN permission_import_batches b ON b.batch_id = r.batch_id
WHERE r.batch_id = %(batch_id)s
    AND r.rejected IS NULL
    AND NOT EXISTS (SELECT 1 FROM send_permission p
                    WHERE p.email_norm = lower(trim(r.email_raw))
                      AND p.basis = b.basis AND p.removed_at IS NULL)
ON CONFLICT (email_norm, basis) WHERE removed_at IS NULL DO NOTHING
RETURNING permission_id
"""

_MARK_COMMITTED_SQL = """
UPDATE permission_import_batches SET committed_at = now() WHERE batch_id = %(batch_id)s
"""


def commit_batch(conn, *, batch_id, added_by):
    """How many addresses were added.  Already-listed addresses are skipped, not duplicated."""
    with conn.cursor() as cur:
        cur.execute(_COMMIT_SQL, {"batch_id": batch_id, "added_by": added_by})
        added = len(cur.fetchall())
        cur.execute(_MARK_COMMITTED_SQL, {"batch_id": batch_id})
    return added


def discard_batch(conn, batch_id):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM permission_import_batches WHERE batch_id = %(batch_id)s",
                    {"batch_id": batch_id})


_REMOVE_SQL = """
UPDATE send_permission SET removed_at = now(), removed_by = %(removed_by)s
WHERE email_norm = lower(trim(%(email)s)) AND removed_at IS NULL
RETURNING permission_id
"""


def remove_address(conn, *, email, removed_by):
    """Take away an address's permission (every basis it has).  How many rows were removed:  0 when
    it had none.  The rows stay, marked removed, as in dnc_list."""
    with conn.cursor() as cur:
        cur.execute(_REMOVE_SQL, {"email": email, "removed_by": removed_by})
        return len(cur.fetchall())


_COUNTS_SQL = """
SELECT basis, count(*) AS n
FROM send_permission
WHERE removed_at IS NULL
GROUP BY basis
"""


def counts(conn):
    """{basis: active addresses} for every basis, 0 where there are none."""
    with conn.cursor() as cur:
        cur.execute(_COUNTS_SQL)
        found = {r["basis"]: r["n"] for r in cur.fetchall()}
    return {basis: found.get(basis, 0) for basis in BASES}
