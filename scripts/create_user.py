#!/usr/bin/env python3

"""Create a user, until Phase 4 there is not an admin acount, this is a placeholder
until then.  Runs as hail_admin.
"""

import argparse
import getpass
import sys

import psycopg

from hailsys.web.auth import hash_password

ROLES = ("admin", "sender", "viewer")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Create a Hail-System user.")
    p.add_argument("--user-name", required=True)
    p.add_argument("--first-name", required=True)
    p.add_argument("--last-name", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True, choices=ROLES)
    return p.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)

    # getpass, not input().  The password is not echoed and doesn't land in terminal
    # scrollback, omits it from shell history.

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if len(password) < 12:
        print("Password must be at least 12 characters.", file=sys.stderr)
        return 1

    # CHECK constraints require lowercase user_name and emp_email, so it's
    # normalized here.

    with psycopg.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_name, emp_fname, emp_lname, emp_email,
                               password_hash, role)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING emp_id
            """,
            (args.user_name.lower(), args.first_name, args.last_name,
             args.email.lower(), hash_password(password), args.role),
        )
        print(f"Created emp_id={cur.fetchone()[0]}")

    return 0

if __name__ == "__main__":
    sys.exit(main())