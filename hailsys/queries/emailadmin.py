"""Admin-page writes for the email images and the email values in settings.

Each function checks a value with the same rules the renderer uses when it loads it
(render.check_url, render.check_email), so what is saved here can always be loaded for a
send.  The database CHECKs are the backstop.  Caller commits, a refusal is an 
EmailAdminError.
"""

import re

import psycopg

from hailsys.email import blocks, emailconfig, render

MAX_URL = 500
MAX_ALT = 200
MAX_NAME_SENDER = 100


class EmailAdminError(Exception):
    """A value was refused, message explains."""


_FETCH_IMAGES = """
    SELECT i.image_id, i.name, i.url, i.alt_text, i.is_active, i.updated_at,
        u.user_name AS updated_by_name
    FROM email_images i
    JOIN users u ON u.emp_id = i.updated_by
    ORDER BY i.is_active DESC, lower(i.name)
"""

_ACTIVE_BLOCK_TEMPLATES = """
    SELECT template_name, blocks FROM email_templates
    WHERE is_active AND blocks IS NOT NULL
    ORDER BY template_name
"""


def fetch_images(conn):
    return conn.execute(_FETCH_IMAGES).fetchall()


def _check(fn, *args):
    try:
        fn(*args)
    except render.SettingsError as exc:
        raise EmailAdminError(str(exc)) from None


def _clean_image(url, alt_text):
    url = (url or "").strip()
    alt = (alt_text or "").strip()
    _check(render.check_url, "The image address", url)
    if len(url) > MAX_URL:
        raise EmailAdminError(f"The image address is limited to {MAX_URL} characters.")
    if not alt:
        raise EmailAdminError("Describe the image in the alt text.")
    if len(alt) > MAX_ALT:
        raise EmailAdminError(f"The alt text is limited to {MAX_ALT} characters.")
    return url, alt

def add_image(conn, user_id, name, url, alt_text):
    name = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise EmailAdminError("The name may use letters, digits, hyphens, and underscores, "
                              "up to 40 characters.")
    url, alt = _clean_image(url, alt_text)
    try:
        conn.execute(
            "INSERT INTO email_images (name, url, alt_text, created_by, updated_by) "
            "VALUES (%s, %s, %s, %s, %s)", (name, url, alt, user_id, user_id))
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise EmailAdminError(f"There is already an image named {name} "
                              "(names ignore upper and lower case).") from None


def update_image(conn, user_id, image_id, url, alt_text):
    url, alt = _clean_image(url, alt_text)
    cur = conn.execute(
        "UPDATE email_images SET url = %s, alt_text = %s, updated_by = %s, updated_at = now() "
        "WHERE image_id = %s", (url, alt, user_id, image_id))
    if cur.rowcount == 0:
        raise EmailAdminError("That image does not exist.")


def _check_unused(conn, image_id):
    """Refuse to retire an image that a block template in use still refers to.

    Only the active version of each template counts.  The three images the built-in
    template needs are left to the database, which refuses to retire them."""
    row = conn.execute("SELECT name FROM email_images WHERE image_id = %s",
                       (image_id,)).fetchone()
    if row is None or row["name"] in emailconfig.REQUIRED_IMAGES:
        return              # the UPDATE below reports it, or the database refuses
    users = [t["template_name"] for t in conn.execute(_ACTIVE_BLOCK_TEMPLATES).fetchall()
            if row["name"] in blocks.images_used(t["blocks"])]
    if users:
        raise EmailAdminError(
            f"The image {row['name']} is used by {', '.join(users)}.  Change those templates "
            "so they stop using it, then retire it.")


def set_image_active(conn, user_id, image_id, active):
    if not active:
        _check_unused(conn, image_id)
    try:
        cur = conn.execute(
            "UPDATE email_images SET is_active = %s, updated_by = %s, updated_at = now() "
            "WHERE image_id = %s", (active, user_id, image_id))
    except psycopg.errors.CheckViolation:
        conn.rollback()
        raise EmailAdminError("The built-in template needs that image, so it cannot be "
                              "retired.  Edit its address instead.") from None
    if cur.rowcount == 0:
        raise EmailAdminError("That image does not exist.")


def save_email_settings(conn, user_id, contact_email, schedule_url, from_name,
                        from_email, reply_to):
    """Save the five email values.  settings_history trigger logs the change"""
    contact_email, schedule_url, from_name, from_email, reply_to = (
         (v or "").strip()
         for v in (contact_email, schedule_url, from_name, from_email, reply_to))
    _check(render.check_email, "The contact email", contact_email)
    _check(render.check_url, "The schedule address", schedule_url)
    _check(render.check_email, "The sender email", from_email)
    _check(render.check_email, "The reply-to email", reply_to)
    if not 1 <= len(from_name) <= MAX_NAME_SENDER:
        raise EmailAdminError(f"The sender name must be 1 to {MAX_NAME_SENDER} characters.")
    # The history trigger reads the acting user from this transaction-local setting.
    conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(user_id),))
    conn.execute(
        "UPDATE settings SET contact_email = %s, schedule_url = %s, from_name = %s, "
        "from_email = %s, reply_to = %s WHERE id = 1",
        (contact_email, schedule_url, from_name, from_email, reply_to))