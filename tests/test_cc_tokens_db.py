"""Database tests for hailsys/constantcontact/tokens.py against oauth_tokens.

Needs a live Postgres with sql/034 applied, the libpq PG* variables, and the
`cryptography` package, so run it in the web image, as hail_app:

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_tokens_db

Skips (does not fail) when there is no database or no `cryptography`, so
`python3 -m unittest discover -s tests` on the host stays green.

Nothing is ever committed: every test runs in a transaction that tearDown rolls
back.  Each test uses its own account_id, so a real grant already in the table
cannot change a result.  The table's own rules are in sql/guard_test.sql; this
checks that the Python and the SQL agree with each other, the real grants and
the guard trigger.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from cryptography.fernet import Fernet
    from hailsys.constantcontact import tokens
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

HAVE_DB = bool(os.environ.get("PGHOST"))

# Inserted directly, because the table forbids UPDATE: this is the only way to
# make a row look old.
_INSERT_OLD = (
    "INSERT INTO oauth_tokens (provider, account_id, access_token_enc, "
    "refresh_token_enc, scope, access_expires_at, created_at) "
    "VALUES ('constant_contact', %s, %s, %s, 's', now(), "
    "        now() - interval '40 days') RETURNING token_id")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(HAVE_DB, "no database (PGHOST unset)")
class TokenStoreDbTest(unittest.TestCase):
    def setUp(self):
        key = Fernet.generate_key().decode()
        patcher = mock.patch.dict(os.environ, {"HAIL_TOKEN_KEY": key})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        # Registered after close, so it runs first (cleanups run in reverse):
        # roll back before the connection closes.
        self.addCleanup(self.conn.rollback)
        self.acct = f"test-{uuid.uuid4()}"
        self.exp = datetime.now(timezone.utc) + timedelta(hours=24)

    def save(self, access, refresh, account=None):
        return tokens.save_token(
            self.conn, account_id=account or self.acct, access_token=access,
            refresh_token=refresh, scope="s", access_expires_at=self.exp)

    def ids(self, account=None):
        rows = self.conn.execute(
            "SELECT token_id FROM oauth_tokens WHERE account_id = %s "
            "ORDER BY token_id", (account or self.acct,)).fetchall()
        return [r["token_id"] for r in rows]

    def test_save_then_latest_round_trips(self):
        first = self.save("ACCESS-ONE-secret", "REFRESH-ONE-secret")
        grant = tokens.latest_token(self.conn, account_id=self.acct)
        self.assertEqual(grant.token_id, first)
        self.assertEqual(grant.access_token, "ACCESS-ONE-secret")
        self.assertEqual(grant.refresh_token, "REFRESH-ONE-secret")
        self.assertIsNone(tokens.latest_token(self.conn, account_id="no-such"))

    def test_newest_token_id_wins(self):
        self.save("ACCESS-ONE-secret", "REFRESH-ONE-secret")
        second = self.save("ACCESS-TWO-secret", "REFRESH-TWO-secret")
        grant = tokens.latest_token(self.conn, account_id=self.acct)
        self.assertEqual(grant.token_id, second)
        self.assertEqual(grant.access_token, "ACCESS-TWO-secret")
        self.assertNotIn("ACCESS-TWO-secret", repr(grant))

    def test_stored_bytes_are_not_plaintext(self):
        tid = self.save("ACCESS-ONE-secret", "REFRESH-ONE-secret")
        row = self.conn.execute(
            "SELECT access_token_enc, refresh_token_enc FROM oauth_tokens "
            "WHERE token_id = %s", (tid,)).fetchone()
        self.assertNotIn(b"ACCESS-ONE-secret", bytes(row["access_token_enc"]))
        self.assertNotIn(b"REFRESH-ONE-secret", bytes(row["refresh_token_enc"]))

    def test_purge_removes_only_old_superseded_rows(self):
        young1 = self.save("A1-secret", "R1-secret")
        young2 = self.save("A2-secret", "R2-secret")
        self.assertEqual(tokens.purge_old(self.conn, self.acct), 0)
        self.conn.execute(_INSERT_OLD, (self.acct, b"x", b"y"))
        newest = self.save("A3-secret", "R3-secret")
        self.assertEqual(tokens.purge_old(self.conn, self.acct), 1)
        self.assertEqual(self.ids(), [young1, young2, newest])

    def test_purge_spares_a_lone_old_grant(self):
        # An unused account: its only row is older than 30 days and is the
        # current grant.  The guard would refuse it and fail the whole DELETE.
        self.conn.execute(_INSERT_OLD, (self.acct, b"x", b"y"))
        self.assertEqual(tokens.purge_old(self.conn, self.acct), 0)
        self.assertEqual(len(self.ids()), 1)

    def test_app_role_cannot_update_or_truncate(self):
        user = self.conn.execute("SELECT current_user AS u").fetchone()["u"]
        if user != "hail_app":
            self.skipTest(f"runs as {user}, not hail_app")
        for sql in ("UPDATE oauth_tokens SET scope = 'x'",
                    "TRUNCATE oauth_tokens"):
            with self.subTest(sql=sql):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with self.conn.transaction():
                        self.conn.execute(sql)


if __name__ == "__main__":
    unittest.main()
