#!/usr/bin/env python3

"""Create a user from the command line, runs as hail_admin.

Bootstrap path only.  Everyday account creation should go through /admin,
which sets created_by from the signed-in admin.

created_by is nullable for the first user.
"""

import argparse
import getpass
import sys

import psycopg

from hailsys.web.auth import hash_password, MIN_PASSWORD_LENGTH

ROLES = ("admin", "sender", "viewer")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Create a Hail-System user.")
    p.add_argument("--user-name", required=True)
    p.add_argument("--first-name", required=True)
    p.add_argument("--last-name", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument(
        "--created-by", type=int, default=None,
        help="emp_id of the admin creating this account.  Omit only for the "
             "first user on a fresh database."
    )
    return p.parse_args(argv)

def refuse_unattributed(args):
    """Omitting --created-by is only legitimate for the first real user.

    The system account always exists and has no creator, so "fresh" means no
    other user yet.  Checked before the password prompts so nobody types a
    password twice only to be refused.  Returns an error message or None.
    """
    if args.created_by is not None:
        return None
    with psycopg.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE role <> 'system' LIMIT 1")
        if cur.fetchone() is None:
            return None
    return ("Users already exist, so --created-by <emp_id> is required.  "
            "Omit it only for the first user on a fresh database.")


def main(argv=None):
    args = parse_args(argv)

    error = refuse_unattributed(args)
    if error:
        print(error, file=sys.stderr)
        return 1

    # getpass, not input().  The password is not echoed and doesn't land in terminal
    # scrollback, omits it from shell history.

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1

    # CHECK constraints require lowercase user_name and emp_email, so it's
    # normalized here.

    with psycopg.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (user_name, emp_fname, emp_lname, emp_email,
                               password_hash, role, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING emp_id
            """,
            (args.user_name.lower(), args.first_name, args.last_name,
             args.email.lower(), hash_password(password), args.role,
             args.created_by),
        )
        print(f"Created emp_id={cur.fetchone()[0]}")

    return 0

if __name__ == "__main__":
    sys.exit(main())