"""Rehearsal of one send on hail-dev.

Run from host:
    docker compose run --rm --no-deps -T web python - [--send N] < scripts/rehearse_send.py

"""

import sys
from datetime import date, datetime, timezone

from hailsys.db import get_connection
from hailsys.email import deliver, emailconfig
from hailsys.queries import sendlist

DAY = date(2026, 9, 19)
ALLOWED = ["rbi.justyn+t2@gmail.com", "rbi.justyn@gmail.com",
           "rbi-bounce-test-7f3k9q@gmail.com",
           "rbi.justyn+t4@gmail.com", "rbi.justyn+t5@gmail.com",
           "rbi.justyn+t6@gmail.com", "rbi.justyn+t7@gmail.com"]

def main():
    send_n = None
    if "--send" in sys.argv:
        send_n = int(sys.argv[sys.argv.index("--send") + 1])
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        settings = emailconfig.load_settings(conn)
        sender = emailconfig.load_sender(conn)
        user_id = conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        found = sendlist.build_send_list(conn, [DAY], now=now)
        conn.commit()

    mine = [r for r in found["realtors"] if r["email"] in ALLOWED]
    print(f"Storm day {DAY}: considered {found["considered"]}")
    print("Excluded:", {k: v for k, v in found["excluded"].items() if v["matches"]})
    print(f"Would send {len(mine)} email(s) to: {[r['email'] for r in mine]}")
    print(f"From: {sender['from_name']} <{sender['from_email']}>")

    if send_n is None:
        print("Preview only.  Nothing written, Constant Contact not called.")
        return
    if not mine:
        raise SystemExit("Nothing to Send.")

    print(f"SENDING at most {send_n} email(s) now...")
    summary = deliver.run_send(
        user_id=user_id, storm_days=[DAY], email_settings=settings, sender=sender,
        allowed_emails=ALLOWED, max_emails=send_n)
    print("Queued:", {k: summary[k] for k in ("batch_id", "emails", "matches", "skipped")})
    print("Delivery:", summary.get("delivery"))


main()