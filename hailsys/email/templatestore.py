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

import re
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from hailsys.email import blocks as blocklib, emailconfig, render

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "hail_alert"

MAX_DESCRIPTION = 300
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")


class TemplateNotFound(LookupError):
    """No active template has this name."""

class DescriptionFrozen(Exception):
    """The template has been used in an email, so its description can no longer change."""


class TemplateRefused(ValueError):
    """A block template or its name was refused, message states the reason, fit to show."""


_ACTIVE_SQL = """
    SELECT template_id, subject, body, blocks, description
      FROM email_templates
    WHERE template_name = %s AND is_active
    ORDER BY template_id DESC
    LIMIT 1
"""


def load_files():
    """(subject_src, body_src) as shipped in the repo."""
    return ((TEMPLATE_DIR / "hail_alert_subject.txt").read_text(encoding="utf-8"),
            (TEMPLATE_DIR / "hail_alert.html").read_text(encoding="utf-8"))


def ensure_current(conn, *, created_by, name=TEMPLATE_NAME, subject_src=None, body_src=None,
                   blocks=None):
    """template_id to put on a send, saving a new version if the text changed.

    'blocks' is the block list subject_src and body_src were compiled from (save_blocks passes
    it).  A change to the blocks alone is also a new version."""
    if subject_src is None or body_src is None:
        subject_src, body_src = load_files()
    render.validate_template(subject_src, body_src)     # refuse to save broken template

    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"email_template:{name}",))
    active = conn.execute(_ACTIVE_SQL, (name,)).fetchone()
    if (active and active["subject"] == subject_src and active["body"] == body_src
            and active["blocks"] == blocks):
        return active["template_id"]

    new_id = conn.execute(
        "INSERT INTO email_templates (template_name, subject, body, blocks, created_by, "
        "supersedes_id, description) "
        "VALUES(%s, %s, %s, %s, %s, %s, %s) RETURNING template_id",
        (name, subject_src, body_src, None if blocks is None else Jsonb(blocks), created_by,
         active["template_id"] if active else None,
         active["description"] if active else None)).fetchone()["template_id"]
        # One activer version per name.  Retiring is one-way.
    conn.execute(
        "UPDATE email_templates SET is_active = FALSE "
        "WHERE template_name = %s AND is_active AND template_id <> %s", (name, new_id))
    return new_id


def save_blocks(conn, *, created_by, name, template, must_be_new=False):
    """template_id of a saved block template.  A new version if anything changed.  Doesn't commit.

    Stores the cleaned template (every default filled in), so the same template always saves
    the same row.  Raises TemplateRefused, with a message for a bad or reserved name, a
    template that does not validate, or an image that is not available.
    With must_be_new, an active template of that name is refused, a new template must not
    quietly become a new version of another."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise TemplateRefused("The template name may use letters, digits, hyphens and "
                              "underscores, up to 40 characters.")
    if name.lower() == TEMPLATE_NAME:
        raise TemplateRefused(f"{TEMPLATE_NAME} is the built-in template's name.  Pick another.")
    try:
        cleaned = blocklib.clean_template(template)
        subject_src, body_src = blocklib.compile_blocks(cleaned)
    except blocklib.BlockError as exc:
        raise TemplateRefused(str(exc)) from None
    gone = sorted(blocklib.images_used(cleaned) - set(emailconfig.load_images(conn)))
    if gone:
        raise TemplateRefused(f"These images are not available: {', '.join(gone)}.  Add them "
                              "under Email images on the admin page, or they have been retired.")
    # Locked on the lowercased name, so "Spring" and "spring" cannot both start a template at
    # the same moment.  ensure_current takes its own lock on the exact name.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                 (f"email_template_name:{name.lower()}",))
    same = conn.execute("SELECT template_name FROM email_templates "
                        "WHERE lower(template_name) = lower(%s) LIMIT 1", (name,)).fetchone()
    if same and same["template_name"] != name:
        raise TemplateRefused(f"There is already a template named {same['template_name']}. "
                              "Names ignore upper and lower case.")
    if must_be_new and conn.execute(
        "SELECT 1 FROM email_templates WHERE lower(template_name) = lower(%s) AND is_active",
        (name,)).fetchone():
        raise TemplateRefused(f"There is already a template named {name}.  Open it from the "
                              "list to make a new version.")
    return ensure_current(conn, created_by=created_by, name=name, subject_src=subject_src,
                          body_src=body_src, blocks=cleaned)

def load_blocks(conn, name):
    """The block list the active version of 'name' was built from, or None if it was not built
    from blocks.  Raises TemplateNotFound for an unknown or retired name."""
    row = conn.execute(_ACTIVE_SQL, (name,)).fetchone()
    if row is None:
        raise TemplateNotFound(name)
    return row["blocks"]


def active_id(conn, name):
    """template_id of the active version of 'name', or None."""
    row = conn.execute(_ACTIVE_SQL, (name,)).fetchone()
    return row["template_id"] if row else None


def retire(conn, name):
    """Retire the active version of 'name'.  One-way.  Never commits.  Raises TemplateRefused
    for the built-in, TemplateNotFound if nothing is active."""
    if not isinstance(name, str):
        raise TemplateNotFound(name)
    if name.lower() == TEMPLATE_NAME:
        raise TemplateRefused(f"{TEMPLATE_NAME} is the built-in template and cannot be retired.")
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"email_template:{name}",))
    cur = conn.execute("UPDATE email_templates SET is_active = FALSE "
                       "WHERE template_name = %s AND is_active", (name,))
    if cur.rowcount == 0:
        raise TemplateNotFound(name)


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
