"""The saved version of the hail-alert template.

sent_emails.template_id is NOT NULL, email_templates is append-only
because a template is superseded, not edited.  Files in 
hailsys/email/templates are the source, which turns them into a 
row.  The same text as the active version doesn't change anything, 
changed text inserts a new version that supersedes the old one, then
reitures it.

Never commits, the caller owns the transaction.  The advisory lock is
released at commit or rollback, so two sends starting together can't 
each insert a version.
"""

from pathlib import Path

from hailsys.email import render

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "hail_alert"

_ACTIVE_SQL = """
    SELECT template_id, subject, body
      FROM email_templates
    WHERE template_name = %s AND is_active
    ORDER BY template_id DESC
    LIMIT 1
"""


def load_files():
    """(subject_src, body_src) as shipped in the repo."""
    return ((TEMPLATE_DIR / "hail_alert_subject.txt").read_text(encoding="utf-8"),
            (TEMPLATE_DIR / "hail_alert.html").read_text(encoding="utf-8"))


def ensure_current(conn, *, created_by, name=TEMPLATE_NAME, subject_src=None, body_src=None):
    """template_id to put on a send, saving a new version if the text changed."""
    if subject_src is None or body_src is None:
        subject_src, body_src = load_files()
    render.validate_template(subject_src, body_src)     # refuse to save broken template

    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"email_template:{name}",))
    active = conn.execute(_ACTIVE_SQL, (name,)).fetchone()
    if active and active["subject"] == subject_src and active["body"] == body_src:
        return active["template_id"]

    new_id = conn.execute(
        "INSERT INTO email_templates (template_name, subject, body, created_by, supersedes_id) "
        "VALUES(%s, %s, %s, %s, %s) RETURNING template_id",
        (name, subject_src, body_src, created_by,
         active["template_id"] if active else None)).fetchone()["template_id"]
        # One activer version per name.  Retiring is one-way.
    conn.execute(
        "UPDATE email_templates SET is_active = FALSE "
        "WHERE template_name = %s AND is_active AND template_id <> %s", (name, new_id))
    return new_id