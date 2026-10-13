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
import psycopg

from hailsys.email import render

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "hail_alert"

MAX_DESCRIPTION = 300

class TemplateNotFound(LookupError):
    """No active template has this name."""

class DescriptionFrozen(Exception):
    """The template has been used in an email, so its description can no longer change."""


_ACTIVE_SQL = """
    SELECT template_id, subject, body, description
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
        "INSERT INTO email_templates (template_name, subject, body, created_by, supersedes_id," \
        "description) "
        "VALUES(%s, %s, %s, %s, %s, %s) RETURNING template_id",
        (name, subject_src, body_src, created_by,
         active["template_id"] if active else None,
         active["description"] if active else None)).fetchone()["template_id"]
        # One activer version per name.  Retiring is one-way.
    conn.execute(
        "UPDATE email_templates SET is_active = FALSE "
        "WHERE template_name = %s AND is_active AND template_id <> %s", (name, new_id))
    return new_id


def load(conn, name=TEMPLATE_NAME):
    """(subject_src, body_src, template_id) to render and save under 'name'.
    
    The built-in template comes from its files and its template_id is None:  it is saved by
    ensure_current only when a send is actually queued, so a preview writes nothing.  Any other
    name comes from its active row.  Raises TemplateNotFound for an unknown or retired name.
    """
    if name == TEMPLATE_NAME:
        subject_src, body_src = load_files()
        return subject_src, body_src, None
    row = conn.execute(_ACTIVE_SQL, (name,)).fetchone()
    if row is None:
        raise TemplateNotFound(name)
    return row["subject"], row["body"], row["template_id"]


def set_description(conn, template_id, text):
    """Set the note shown beside an active template.  User commits.
    
    Empty text clears it.  Raises ValueError for text that is too long, TemplateNotFound if
    there is no active template with this id, and DescriptionFrozen if the database refuses
    because the template has been used in an email (saving the text it already has is fine).
    """
    text = (text or "").strip() or None
    if text is not None and len(text) > MAX_DESCRIPTION:
        raise ValueError(f"The description is limited to {MAX_DESCRIPTION} characters.")
    try:
        cur = conn.execute(
            "UPDATE email_templates SET description = %s WHERE template_id = %s AND is_active",
            (text, template_id))
    except psycopg.errors.RestrictViolation:
        conn.rollback()
        raise DescriptionFrozen("This template has been used in an email, so its description "
                                "can no longer change.") from None
    if cur.rowcount == 0:
        raise TemplateNotFound(template_id)
