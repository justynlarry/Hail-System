"""The Constant Contact calls behind one realtor-send.

Each function is one step and goes through api.request, so the throttle and the rule "a POST is
never retried after an ambiguous failure" apply.  They return only the ids the engine stores
in sent_emails.  The database writes and the DNC re-check belong to the engine, not this file.

* Every id in an answer is checked before it can reach a later URL, and a message names the
  field, not the value.
* is_unknown_outcome(): the POST may have taken effect, so it shouldn't be repeated.
* stops_batch(): the whole batch must stop (credentials or quota), not just one email.
"""

import re
import time

from hailsys.constantcontact import api
from hailsys.constantcontact.api import ApiError

DONE = "DONE"
FAILED_STATES = frozenset({"ERROR", "REMOVED"})
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _need(doc, key):
    """doc[key] as a safe id, or an error that names the field."""
    value = (doc or {}).get(key)
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ApiError(f"Constant Contact's answer had no usable {key}.")
    return value


def is_unknown_outcome(exc):
    """True when the request may have taken effect.  No answer, a server error, or a 
    success status that was unexpected.  Don't retry, a user should verify first."""
    status = getattr(exc, "status", None)
    return status is None or status >= 500 or 200 <= status < 300


def stops_batch(exc):
    """Credentials or quota, this batch can't continue."""
    return getattr(exc, "status", None) in (401, 403, 429)


def find_contact(email):
    """{'contact_id', 'permission'} for this exact address, or None."""
    _, doc = api.request("GET", "/contacts", query={"email": email, "status": "all"})
    wanted = email.strip().lower()
    for contact in (doc or {}).get("contacts", []):
        address = contact.get("email_address") or {}
        if (address.get("address") or "").strip().lower() == wanted:
            return {"contact_id": _need(contact, "contact_id"),
                    "permission": address.get("permission_to_send")}
    return None


def create_list(name):
    _, doc = api.request("POST", "/contact_lists",
                        body={"name": name, "description": "hail-system send",
                            "favorite": False},
                        expect=(201,))
    return _need(doc, "list_id")


def create_contact(email, first_name, list_id):
    """A new contact, on the list, with implicit permission.  409 means it
    already exists, the caller then reads it again rather than overwriting anything."""
    body = {"email_address": {"address": email, "permission_to_send": "implicit"},
            "create_source": "Account", "list_memberships": [list_id]}
    if first_name:
        body["first_name"] = first_name
    _, doc = api.request("POST", "/contacts", body=body, expect=(201,))
    return _need(doc, "contact_id")


def add_to_list(contact_id, list_id, *, sleep=time.sleep):
    """An existing contact onto the list.  A bulk activity, start it then wait for it."""
    _, doc = api.request("POST", "/activities/add_list_memberships",
                         body={"source": {"contact_ids": [contact_id]}, "list_ids": [list_id]},
                         expect=(201,))
    api.wait_for_activity(_need(doc, "activity_id"), sleep=sleep)


def campaign_fields(*, subject, html, from_name, from_email, reply_to):
    return {"format_type": 5, "from_name": from_name, "from_email": from_email,
            "reply_to_email": reply_to, "subject": subject, "html_content": html}


def create_campaign(name, fields):
    """{'campaign_id', 'activity_id'}.  The answer holds two activities; the one we send is
    the 'primary_email', chosen by role not by position."""
    _, doc = api.request("POST", "/emails",
                         body={"name": name, "email_campaign_activities": [fields]},
                         expect=(200, 201))
    primary = [a for a in (doc or {}).get("campaign_activities", [])
               if a.get("role") == "primary_email"]
    if len(primary) != 1:
        raise ApiError("Constant Contact's answer had no single primary email activity.")
    return {"campaign_id": _need(doc, "campaign_id"),
            "activity_id": _need(primary[0], "campaign_activity_id")}


def update_campaign(activity_id, fields, list_id):
    api.request("PUT", f"/emails/activities/{activity_id}",
                body={**fields, "contact_list_ids": [list_id]}, expect=(200,))


def schedule(activity_id):
    """Send now.  The one call that cannot be taken back."""
    api.request("POST", f"/emails/activities/{activity_id}/schedules",
                body={"scheduled_date": "0"}, expect=(201,))


def campaign_status(activity_id):
    _, doc = api.request("GET", f"/emails/activities/{activity_id}")
    status = (doc or {}).get("current_status")
    return status.upper() if isinstance(status, str) else None


def wait_until_done(activity_id, *, timeout=600.0, interval=5.0,
                    sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while True:
        status = campaign_status(activity_id)
        if status == DONE:
            return
        if status in FAILED_STATES:
            raise ApiError("Constant Contact reports the campaign did not send.")
        if clock() >= deadline:
            raise ApiError("The campaign did not reach DONE in time.")
        sleep(interval)


def delete_list(list_id):
    """Asynchronous on their side (202), the contacts stay."""
    api.request("DELETE", f"/contact_lists/{list_id}", expect=(202, 204))