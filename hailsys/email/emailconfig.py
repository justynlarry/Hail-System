"""The email identity values, read from the database:  3 required images
(email_images) and the contact email, schedule link and sender (settings).

Admin changes them on the admin page.  Every problem is a 
render.SettingsError, so send screens already show it as "Sending is
not configured."
"""


from hailsys.email import render

# Built-in template's fixed image slots: image name -> EmailSettings field.
REQUIRED_IMAGES = {"logo": "logo_url", "badge_cra": "badge_cra_url", "badge_bbb": "badge_bbb_url"}

_IMAGES_SQL = "SELECT name, url FROM email_images WHERE is_active AND name = ANY(%s)"
_SETTINGS_SQL = ("SELECT contact_email, schedule_url, from_name, from_email, reply_to "
                 "FROM settings")


def load_settings(conn):
    """The EmailSettings the renderer wants."""
    urls = {r["name"]: r["url"]
            for r in conn.execute(_IMAGES_SQL, (list(REQUIRED_IMAGES),)).fetchall()}
    missing = [name for name in REQUIRED_IMAGES if name not in urls]
    if missing:
        raise render.SettingsError(
            f"The email image {', '.join(missing)} is not set up (see Email images on the "
            "admin page)")
    row = conn.execute(_SETTINGS_SQL).fetchone()
    values = {field: urls[name] for name, field in REQUIRED_IMAGES.items()}
    return render.EmailSettings(contact_email=row["contact_email"],
                                schedule_url=row["schedule_url"], **values)


def load_sender(conn):
    """The 'sender' dict deliver_email wants."""
    row = conn.execute(_SETTINGS_SQL).fetchone()
    return {"from_name": row["from_name"], "from_email": row["from_email"],
            "reply_to": row["reply_to"]}

