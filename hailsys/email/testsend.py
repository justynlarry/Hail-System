"""A test send of the template on screen, to the admin who requested it.

One click, one email, to the signed-in admin's own address:  the recipient is read from the
database, not the form, so it cannot email anyone else.  The draft is built with the same code
as the preview and sent through a temporary Constant Contact campaign (create, test, delete:
three calls).  Not a send: no sent_emails or send_log row, nothing is scheduled.  Every
attempt that reached Constant Contact is logged to email_test_sends.

A 204 from the test endpoint means accepted, not delivered (item 186).
"""

import logging
from datetime import datetime, timezone

from hailsys.constantcontact import budget, campaigns
from hailsys.constantcontact.api import ApiError
from hailsys.email import deliver, preview, render, samples

logger = logging.getLogger(__name__)

# Fixed ceilings, kept in code like the other safety limits
MAX_PER_HOUR = 5
CALLS_PER_TEST = 3          # Create the campaign, post test, delete campaign
_DETAIL = 300

_ADDRESS_SQL = "SELECT emp_email FROM users WHERE emp_id = %s AND is_active"
_RECENT_SQL = ("SELECT count(*) AS n FROM email_test_sends "
               "WHERE sent_by = %s AND created_at > now() - interval '1 hour'")

_LOG_SQL = """
    INSERT INTO email_test_sends (sent_by, recipient, template_name, situation, subject,
                                  html_bytes, cc_campaign_id, cc_activity_id, outcome,
                                  campaign_cleanup, detail)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class TestSendRefused(Exception):
    """Nothing was sent and no Constant Contact call was made.  The message is fit to show."""


def _recipient(conn, user_id):
    row = conn.execute(_ADDRESS_SQL, (user_id,)).fetchone()
    address = ((row or {}).get("emp_email") or "").strip()
    try:
        render.check_email("Your email address", address)
    except render.SettingsError:
        raise TestSendRefused("Your account has no usable email address, so there is nowhere "
                              "to send a test.") from None
    return address


def send_test(conn, *, user_id, template, template_name, situation, settings, sender,
              cc=campaigns, now=None):
    """Send the draft to the user's own address.  Returns what happened:
    {'outcome', 'recipient', 'subject', 'cleanup', 'detail', 'campaign_name'}.

    Raises TestSendRefused, before any Constant Contact call, for a missing address, an address
    on the do-not-contact list, too many tests this hour, too few calls left today, or a draft
    that does not build.  Commits (no transaction is open across the Constant Contact calls).
    """
    recipient = _recipient(conn, user_id)
    if deliver._on_dnc(conn, recipient):
        raise TestSendRefused("Your address is on the do-not-contact list, so no test is "
                              "sent to it.")
    recent = conn.execute(_RECENT_SQL, (user_id,)).fetchone()["n"]
    conn.commit()
    if recent >= MAX_PER_HOUR:
        raise TestSendRefused(f"You have already sent {MAX_PER_HOUR} tests in the last hour.  "
                              "Try again later.")
    if budget.remaining() - budget.HEADROOM < CALLS_PER_TEST:
        raise TestSendRefused("Too few Constant Contact calls are left today to send a test.")
    situation = situation if isinstance(situation, str) and situation in samples.SITUATIONS \
        else samples.DEFAULT
    shown = preview.build_preview(template, settings, situation)
    if not shown["ok"]:
        raise TestSendRefused("The template cannot be built yet: "
                              + "; ".join(message for _, message in shown["problems"]))
    now = now or datetime.now(timezone.utc)
    name = f"TEST {now:%Y-%m-%d %H:%M:%S} user {user_id}"
    label = samples.SITUATIONS[situation]["label"]
    message = (f"Test from the template editor ({template_name or 'new template'}), "
               f"sample data: {label}.")
    fields = cc.campaign_fields(subject=shown["subject"], html=shown["html"], **sender)

    campaign_id = activity_id = None
    outcome, detail = "failed", None
    try:
        made = cc.create_campaign(name, fields)
        campaign_id, activity_id = made["campaign_id"], made["activity_id"]
        cc.send_test(activity_id, recipient, message)
        outcome = "accepted"
    except ApiError as exc:
        outcome = "unknown" if cc.is_unknown_outcome(exc) else "failed"
        detail = str(exc)
        if outcome == "unknown" and campaign_id is None:
            detail += f"  A campaign named '{name}' may have been created."
    except Exception:       # recorded rather than lost, calls may have been made
        logger.exception("event=test_send_unexpected user_id=%s", user_id)
        outcome, detail = "unknown", "An unexpected error stopped the test send."

    cleanup = "none"
    if campaign_id is not None:
        try:
            cc.delete_campaign(campaign_id)
            cleanup = "deleted"
        except ApiError:
            cleanup = "left_behind"
            logger.warning("event=test_campaign_not_deleted user_id=%s", user_id)
    if cleanup == "left_behind":
        detail = ((detail + "  ") if detail else "") + (
            f"The temporary campaign '{name}' could not be deleted.  Remove it in Constant Contact.")
    conn.execute(_LOG_SQL, (user_id, recipient, (template_name or None) and template_name[:100],
                            situation, shown["subject"], shown["size"], campaign_id,
                            activity_id, outcome, cleanup, detail and detail[:_DETAIL]))
    conn.commit()
    return {"outcome": outcome, "recipient": recipient, "subject": shown["subject"],
            "cleanup": cleanup, "detail": detail, "campaign_name": name}
