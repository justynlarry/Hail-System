"""Constant Contact connection state, from the database only.

This runs on page load, so it never calls Constant Contact, and a 
Constant Contact outage cannot break the page.  It's limited to knowing
a Grant was stored, not if it's still avlid.  A revoked or lapsed refresh
token reads as connected until a refresh fails.
"""

# Module pulls 'cryptography'
PROVIDER = "constant_contact"


def fetch_state(conn):
    # created_at only, the token columns are not read or decrypted here.
    row = conn.execute(
        "SELECT created_at FROM oauth_tokens WHERE provider = %s "
        "ORDER BY token_id DESC LIMIT 1", (PROVIDER,)).fetchone()
    if row is None:
        return {"connected": False, "issued_at": None}
    return {"connected": True, "issued_at": row["created_at"]}
