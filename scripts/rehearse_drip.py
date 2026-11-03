"""DEV ONLY.  Run one follow-up of a rehearsal drip campaign with RentCast's lookup answered here (item 204,
piece 7).  Never run on production.

A real follow-up re-checks each listing with RentCast before sending.  A rehearsal listing has a made-up id
(RentCast says 404, so nobody is emailed), and a real listing names its real agent (item 227 drops it), so a
follow-up to a test address cannot be sent for real.  This runs the real job, the same one the step screen
starts, under the same lock, with one thing replaced:  RentCast's answer for a TEST-REHEARSAL-DRIP listing is
"Active, seen today, the agent is the address step 1 went to", for no call.  The unsubscribe sync, the
follow-up list, the queue and the Constant Contact sends are real.

It refuses unless every recipient of the campaign is one of the developer's +t test addresses and every
listing to check is a TEST-REHEARSAL-DRIP listing, and it never pulls a zip.

Run from the host, after step 1 was sent on the screens (sql/dev_rehearsal_seed_drip.sql):
    docker compose run --rm --no-deps -T web python - --campaign N --step 1 < scripts/rehearse_drip.py
Add --send to start it;  without it, it only prints what it would do.
"""

import argparse
import re
from datetime import datetime, timezone
from unittest import mock

from hailsys.db import get_connection
from hailsys.queries import dripscreen
from hailsys.rentcast import client
from hailsys.rentcast import dripstep as rentcast_checks
from hailsys.settings import fetch_settings
from hailsys.web import sendjobs

TEST_ADDRESS = re.compile(r"^rbi\.justyn\+t\d+@gmail\.com$")
TEST_LISTING = "TEST-REHEARSAL-DRIP-"


def stand_in_lookup(agents):
    """get_sale_listing for rehearsal listings only:  (answer, calls).  No request is made, so 0 calls."""
    def lookup(rentcast_id):
        if not rentcast_id.startswith(TEST_LISTING) or rentcast_id not in agents:
            raise SystemExit(f"refusing: {rentcast_id} is not a rehearsal listing")
        return ({"id": rentcast_id, "status": "Active", "removedDate": None,
                 "lastSeenDate": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                 "listingAgent": {"email": agents[rentcast_id]}}, 0)
    return lookup


def no_zip_pull(*args, **kwargs):
    raise rentcast_checks.StepStopped("rehearsal: a zip pull is not allowed here", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", type=int, required=True)
    ap.add_argument("--step", type=int, required=True)
    ap.add_argument("--max-emails", type=int, default=10)
    ap.add_argument("--send", action="store_true", help="start it;  without this, only print")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)

    with get_connection() as conn:
        recipients = conn.execute(
            "SELECT recipient_email, stopped_at FROM drip_recipients WHERE campaign_id = %s",
            (args.campaign,)).fetchall()
        if not recipients:
            raise SystemExit(f"refusing: campaign {args.campaign} has no recipients")
        strangers = [r["recipient_email"] for r in recipients if not TEST_ADDRESS.match(r["recipient_email"])]
        if strangers:
            raise SystemExit(f"refusing: campaign {args.campaign} has recipients who are not test addresses")
        agents = {r["rentcast_id"]: r["list_agent_email"] for r in conn.execute(
            "SELECT rentcast_id, list_agent_email FROM listings WHERE rentcast_id LIKE %s",
            (TEST_LISTING + "%",)).fetchall()}
        settings = fetch_settings(conn)
        preview = dripscreen.step_preview(conn, args.campaign, args.step, now=now,
                                          cap_days=settings["email_cap_days"])
        user_id = conn.execute("SELECT emp_id FROM users WHERE role = 'admin' AND is_active "
                               "ORDER BY emp_id LIMIT 1").fetchone()["emp_id"]
        conn.rollback()

    print(f"campaign {args.campaign}, follow-up {args.step}:  recipients "
          f"{[(r['recipient_email'], 'stopped' if r['stopped_at'] else 'active') for r in recipients]}")
    print(f"preview:  {preview['to_check']} listing(s) to check, at most {preview['emails_at_most']} email(s)")
    if not args.send:
        print("Preview only.  Add --send to run it.  Nothing written, RentCast and Constant Contact not called.")
        return

    email_settings, sender = sendjobs.load_config()
    run_kwargs = {"email_settings": email_settings, "sender": sender, "max_emails": args.max_emails,
                  "freshness_days": settings["listing_freshness_days"], "cap_days": settings["email_cap_days"]}
    with mock.patch.object(client, "get_sale_listing", stand_in_lookup(agents)), \
         mock.patch.object(rentcast_checks, "check_by_zip_pull", no_zip_pull):
        sendjobs._run_drip_step(user_id, args.campaign, args.step, run_kwargs)     # inline, under the send lock

    with get_connection() as conn:
        run = conn.execute("SELECT status, method, calls_made, emails, detail FROM drip_step_runs "
                           "WHERE campaign_id = %s AND step_no = %s ORDER BY run_id DESC LIMIT 1",
                           (args.campaign, args.step)).fetchone()
        step = conn.execute("SELECT batch_id FROM drip_steps WHERE campaign_id = %s AND step_no = %s",
                            (args.campaign, args.step)).fetchone()
        campaign = conn.execute("SELECT status FROM drip_campaigns WHERE campaign_id = %s",
                                (args.campaign,)).fetchone()
        conn.rollback()
    print(f"run: {dict(run) if run else None}")
    print(f"step batch: {step['batch_id'] if step else None};  campaign: {campaign['status']}")


if __name__ == "__main__":
    main()
