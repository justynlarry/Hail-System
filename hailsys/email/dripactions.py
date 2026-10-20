"""What a user can change on a drip campaign:  stop recipients, pause, resume, cancel.

Each function runs on the connection it is given and leaves the commit to the
caller.  ActionError messages are for a user to read.  Stopping a recipient writes
only the campaign's own table, it does not add the address to dnc_list, does not undo
a stop.
"""


import re

REASONS = ("manual", "replied")
MOVES = {"pause": (("active",), "paused"),
         "resume": (("paused",), "active"),
         "cancel": (("active", "paused"), "cancelled")}

_ADDRESS = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")



class ActionError(Exception):
    """Nothing was changed, message explains."""


_STATUS_SQL = "SELECT status FROM drip_campaigns WHERE campaign_id = %(campaign_id)s"

_STOP_SQL = """
UPDATE drip_recipients
    SET stop_reason = %(reason)s, stopped_by = %(user_id)s
  WHERE campaign_id = %(campaign_id)s
    AND stopped_at IS NULL
    AND (realtor_id = %(realtor_id)s::bigint
        OR lower(btrim(recipient_email)) = ANY(%(emails)s::text[]))
RETURNING realtor_id, lower(btrim(recipient_email)) AS email
"""

_KNOWN_SQL = """
SELECT lower(btrim(recipient_email)) AS email, stopped_at IS NOT NULL AS stopped
FROM drip_recipients
WHERE campaign_id = %(campaign_id)s AND lower(btrim(recipient_email)) = ANY(%(emails)s::text[])
"""

_MOVE_SQL = """
UPDATE drip_campaigns
    SET status = %(new)s, status_changed_by = %(user_id)s
  WHERE campaign_id = %(campaign_id)s AND status = ANY(%(allowed)s::text[])
RETURNING status
"""


def _check_open(conn, campaign_id, reason):
    if reason not in REASONS:
        raise ActionError("Choose why the recipient is being stopped.")
    row = conn.execute(_STATUS_SQL, {"campaign_id": campaign_id}).fetchone()
    if row is None:
        raise ActionError("That campaign does not exist.")
    if row["status"] not in ("active", "paused"):
        raise ActionError(f"This campaign is {row['status']}, so its recipients cannot change.")


def _stop(conn, campaign_id, user_id, reason, *, realtor_id=None, emails=()):
    return conn.execute(_STOP_SQL, {"campaign_id": campaign_id, "user_id": user_id,
                                   "reason": reason, "realtor_id": realtor_id,
                                   "emails": list(emails)}).fetchall()


def stop_recipient(conn, *, campaign_id, realtor_id, reason, user_id):
    """Stop one recipient.  ActionError if the campaign is closed or they are already stopped."""
    _check_open(conn, campaign_id, reason)
    if not _stop(conn, campaign_id, user_id, reason, realtor_id=realtor_id):
        raise ActionError("That recipient is already stopped, or is not in this campaign.")


def stop_by_addresses(conn, *, campaign_id, text, reason, user_id):
    """Stop every recipient whose address is in the pasted text.
    
    Returns {'stopped', 'already', 'unknown'}: sorted lists of addresses stopped
    now, found but stopped before, and not recipients of this campaign.
    """
    _check_open(conn, campaign_id, reason)
    emails = sorted({m.group().lower() for m in _ADDRESS.finditer(text or "")})
    if not emails:
        raise ActionError("No email addresses were found in what you pasted.")
    known = {r["email"]: r["stopped"] for r in conn.execute(
        _KNOWN_SQL, {"campaign_id": campaign_id, "emails": emails}).fetchall()}
    to_stop = [e for e in emails if known.get(e) is False]
    if to_stop:
        _stop(conn, campaign_id, user_id, reason, emails=to_stop)
    return {"stopped": to_stop,
            "already": [e for e in emails if known.get(e) is True],
            "unknown": [e for e in emails if e not in known]}


def set_status(conn, *, campaign_id, move, user_id):
    """pause, resume, or cancel.  ActionError if the campaign is not in a state that allows it."""
    if move not in MOVES:
        raise ActionError("Unknown action.")
    allowed, new = MOVES[move]
    row = conn.execute(_MOVE_SQL, {"campaign_id": campaign_id, "user_id": user_id,
                                   "new": new, "allowed": list(allowed)}).fetchone()
    if row is None:
        raise ActionError(f"This campaign cannot be {new} now: it is not "
                          f"{' or '.join(allowed)}.")
    