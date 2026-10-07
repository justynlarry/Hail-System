"""Constant Contact spike.

Throwaway exploration, not part of the app.  Each subcommand makes one call against
the dev trial account and prints the status and body, so what Constant Contact actually
does is transparent.  The only command that sends a real mail to a list is 'schedule.'
Test-send send the 50-per-day test copy (up to 5 addresses).

Run it in the web image, which has credentials and the code:

    docker compose run --rm --no-deps -v ./scripts:/app/scripts:ro web \
        python scripts/cc_spike.py <command> ...

Access token comes from hailsys.constantcontact.oauth (stored,encrypted, refreshed
under the lock).  Bodies printed here are Constant Contact's answers about our own
test account.
"""


import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from hailsys.constantcontact import oauth
from hailsys.db import get_connection

BASE = "https://api.cc.email/v3"

# No personalization tag or unsubscribe tag.  Testing for: footer added, tag naming.
HTML = """<html><body>[[trackingImage]]
<p>Hello,</p>
<p>SPIKE TEST, not a real notice. Hail was reported near these properties:</p>
<table border="1" cellpadding="4">
<tr><th>Property</th><th>Distance</th><th>Hail</th></tr>
<tr><td>1 Test St</td><td>0.8 mi</td><td>1.25 in</td></tr>
<tr><td>2 Test St</td><td>1.9 mi</td><td>1.00 in</td></tr>
<tr><td>3 Test St</td><td>3.2 mi</td><td>0.75 in</td></tr>
</table>
<p>Roof Brokers, Inc.</p>
</body></html>
"""

def call(method, path, body=None, query=None):
    """One request.  Prints status and body, returns(status, parsed)"""
    with get_connection() as conn:
        token = oauth.get_access_token(conn)
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"Accept": "application/json",
               "Authorization": f"Bearer {token}",
               "User-Agent": oauth.USER_AGENT}

    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    print(f"\n{method} {path} -> {status}")
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw)
            print(json.dumps(parsed, indent=2))
        except ValueError:
            print(raw[:500])
    time.sleep(0.3)     # stay under 4 requests/second
    return status, parsed


def activity_fields(a):
    return {"format_type": 5, "from_name": a.from_name, "from_email": a.sender,
            "reply_to_email": a.sender, "subject": a.subject,
            "html_content": HTML}

def cmd_account(a):
    call("GET", "/account/summary")
    call("GET", "/account/emails")
    call("GET", "/contact_lists", query={"include_count": "true", "limit": 50})


def cmd_list_create(a):
    call("POST", "/contact_lists",
         {"name": a.name, "description": "hail-system spike", "favorite": False})


def cmd_list_delete(a):
    call("DELETE", f"/contact_lists/{a.list_id}")


def cmd_contact(a):
    call("POST", "/contacts/sign_up_form",
         {"email_address": a.email, "first_name": a.first,
          "list_memberships": [a.list_id]})


def cmd_contact_get(a):
    call("GET", "/contacts", query={"email": a.email, "status": "all",
                                    "include": "list_memberships"})

def cmd_unsubscribed(a):
    query = {"status": "unsubscribed", "limit": a.limit}
    if a.updated_after:
        query["updated_after"] = a.updated_after
    call("GET", "/contacts", query=query)

def cmd_contact_create(a):
    call("POST", "/contacts",
         {"email_address": {"address": a.email,
                            "permission_to_send": a.permission},
          "create_source": "Account", "first_name": a.first,
          "list_memberships": [a.list_id]})

def cmd_bounces(a):
    call("GET", f"/reports/email_reports/{a.activity_id}/tracking/bounces",
         query={"limit": 50})


def cmd_optouts(a):
    call("GET", f"/reports/email_reports/{a.activity_id}/tracking/optouts",
         query={"limit": 50})


def cmd_list_add(a):
    # Body shape is from a summary of the docs, not the reference page.
    call("POST", "/activities/add_list_memberships",
         {"source": {"contact_ids": a.contact_ids}, "list_ids": [a.list_id]})


def cmd_bulk_status(a):
    call("GET", f"/activities/{a.bulk_id}")


def cmd_campaign_delete(a):
    call("DELETE", f"/emails/{a.campaign_id}")

def cmd_create(a):
    status, doc = call("POST", "/emails",
                       {"name": a.name,
                        "email_campaign_activities": [activity_fields(a)]})
    if status >= 300:
        sys.exit("create failed; nothing more done")
    activity_id = doc["campaign_activities"][0]["campaign_activity_id"]
    print(f"\ncampaign_activity_id = {activity_id}")
    if a.list_id:
        call("PUT", f"/emails/activities/{activity_id}",
             {**activity_fields(a), "contact_list_ids": [a.list_id]})


def cmd_test_send(a):
    call("POST", f"/emails/activities/{a.activity_id}/tests",
         {"email_addresses": a.emails, "personal_message": "hail-system spike test"})


def cmd_activity(a):
    call("GET", f"/emails/activities/{a.activity_id}")


def cmd_schedule(a):
    status, doc = call("GET", f"/emails/activities/{a.activity_id}")
    print("\nThis will send to the lists shown above.")
    if not a.yes:
        sys.exit("not scheduled: add --yes to send for real")
    call("POST", f"/emails/activities/{a.activity_id}/schedules",
         {"scheduled_date": "0"})


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args):
        sp = sub.add_parser(name)
        for flag, kw in args:
            sp.add_argument(flag, **kw)
        sp.set_defaults(fn=fn)

    add("account", cmd_account)
    add("list-create", cmd_list_create, ("name", {}))
    add("list-delete", cmd_list_delete, ("list_id", {}))
    add("contact", cmd_contact, ("list_id", {}), ("email", {}), ("first", {}))
    add("contact-get", cmd_contact_get, ("email", {}))
    add("create", cmd_create,
        ("--sender", {"required": True}),
        ("--from-name", {"default": "Roof Brokers, Inc."}),
        ("--subject", {"default": "Spike test"}),
        ("--name", {"default": f"hail-spike-{int(time.time())}"}),
        ("--list-id", {"default": None}))
    add("test-send", cmd_test_send, ("activity_id", {}),
        ("emails", {"nargs": "+"}))
    add("activity", cmd_activity, ("activity_id", {}))
    add("schedule", cmd_schedule, ("activity_id", {}),
        ("--yes", {"action": "store_true"}))
    add("unsubscribed", cmd_unsubscribed,
        ("--limit", {"type": int, "default": 50}),
        ("--updated-after", {"default": None}))
    add("contact-create", cmd_contact_create, ("list_id", {}), ("email", {}),
        ("first", {}), ("--permission", {"default": "explicit"}))
    add("bounces", cmd_bounces, ("activity_id", {}))
    add("optouts", cmd_optouts, ("activity_id", {}))
    add("list-add", cmd_list_add, ("list_id", {}), ("contact_ids", {"nargs": "+"}))
    add("bulk-status", cmd_bulk_status, ("bulk_id", {}))
    add("campaign-delete", cmd_campaign_delete, ("campaign_id", {}))


    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()