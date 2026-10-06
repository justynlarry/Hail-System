"""Tests for hailsys/constantcontact/tokens.py -- encryption and token hygiene.

Needs the `cryptography` package, which the host Python lacks, so run in the
web image with the tests mounted:

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_tokens

Pure: no database.  The table's own rules are tested in sql/guard_test.sql.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet

from hailsys.constantcontact import tokens

SECRET = "super-secret-token-value"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def with_key(key=None):
    key = key or Fernet.generate_key().decode()
    return mock.patch.dict(os.environ, {"HAIL_TOKEN_KEY": key})


class CryptoTest(unittest.TestCase):
    def test_round_trip(self):
        with with_key():
            sealed = tokens.encrypt(SECRET)
            self.assertNotIn(SECRET.encode(), sealed)
            self.assertEqual(tokens.decrypt(sealed, tokens.KEY_VERSION), SECRET)

    def test_wrong_key_fails_without_leaking(self):
        with with_key():
            sealed = tokens.encrypt(SECRET)
        with with_key():
            with self.assertRaises(tokens.TokenCryptoError) as cm:
                tokens.decrypt(sealed, tokens.KEY_VERSION)
        self.assertNotIn(SECRET, str(cm.exception))

    def test_missing_key_is_loud(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(tokens.TokenCryptoError):
                tokens.encrypt(SECRET)

    def test_bad_key_does_not_echo_the_key(self):
        with with_key("not-a-key"):
            with self.assertRaises(tokens.TokenCryptoError) as cm:
                tokens.encrypt(SECRET)
        self.assertNotIn("not-a-key", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)

    def test_key_version_mismatch_refused(self):
        with with_key():
            sealed = tokens.encrypt(SECRET)
            with self.assertRaises(tokens.TokenCryptoError):
                tokens.decrypt(sealed, tokens.KEY_VERSION + 1)


class GrantTest(unittest.TestCase):
    def test_repr_hides_tokens(self):
        g = tokens.Grant(token_id=1, account_id="a", scope="s",
                         access_expires_at=NOW, created_at=NOW,
                         access_token=SECRET, refresh_token=SECRET + "2")
        self.assertNotIn(SECRET, repr(g))
        self.assertNotIn(SECRET, str(g))


class SaveTest(unittest.TestCase):
    def test_naive_expiry_rejected_before_any_sql(self):
        conn = mock.Mock()
        with self.assertRaises(ValueError):
            tokens.save_token(conn, account_id="a", access_token=SECRET,
                              refresh_token=SECRET, scope="s",
                              access_expires_at=datetime(2026, 10, 7))
        conn.execute.assert_not_called()

    def test_save_encrypts_and_does_not_commit(self):
        conn = mock.Mock()
        conn.execute.return_value.fetchone.return_value = {"token_id": 7}
        with with_key():
            tid = tokens.save_token(
                conn, account_id="a", access_token=SECRET,
                refresh_token=SECRET + "2", scope="s",
                access_expires_at=NOW + timedelta(hours=24))
        self.assertEqual(tid, 7)
        params = conn.execute.call_args[0][1]
        self.assertNotIn(SECRET, params)        # plaintext never sent
        self.assertNotIn(SECRET.encode(), params)
        conn.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()