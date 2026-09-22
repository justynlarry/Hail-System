import hashlib
import hmac
import os
from base64 import b64decode, b64encode
from datetime import datetime
from functools import wraps

from hailsys.db import get_connection

from flask import g, redirect, session, url_for, abort

# Cost parameters:  n is the work factor, r and p tune, block size and
# parallelism.  These are stored 'with' each hash, so raising them later
# leaves existing passwords verifiable.  Verification uses the stored 
# values.  These constants only apply to newly created hashes.

_N = 2 ** 15
_R = 8
_P = 1
_SALT_BYTES = 16
_DKLEN = 32
_MAXMEM = 64 * 1024 * 1024

def load_current_user():
    """Registered as app.before_request.  Re-checks is_active and
    the fail-safe timestamp on every request.
    """
    emp_id = session.get("emp_id")
    if emp_id is None:
        g.user = None
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT is_active, sessions_invalidated_at FROM users WHERE emp_id = %s",
            (emp_id,),
        )
        row = cur.fetchone()

    # Compare real datetimes, not stringified ones.  sessions_invalidated_at
    # comes back TIMESTAMPTZ (aware); issued_at has to be parsed back to an
    # aware datetime too, or an aware/naive or string/string compare can sort
    # wrong instead of raising.
    issued_at_raw = session.get("issued_at")
    issued_at = datetime.fromisoformat(issued_at_raw) if issued_at_raw else None

    booted = (
        row is None
        or not row["is_active"]
        or (row["sessions_invalidated_at"] is not None and issued_at is not None
            and row["sessions_invalidated_at"] > issued_at)
    )
    if booted:
        session.clear()
        g.user = None
        return

    g.user = {"emp_id": emp_id, "role": session.get("role")}

def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("main.login"))
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def hash_password(password):
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM,
    )
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P,
        b64encode(salt).decode("ascii"),
        b64encode(digest).decode("ascii"),
    )

def verify_password(password, stored):
    # '!' marks an account that can't authenticate (system account
    # CHECK constraint). Refuse explicitly instead of falling to parse error

    if not stored or stored == "!":
        return False

    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False

    expected = b64decode(digest_b64)
    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=b64decode(salt_b64),
        n=int(n), r=int(r), p=int(p),
        dklen=len(expected),
        maxmem=_MAXMEM,
    )

    # compare_digest - not ==
    return hmac.compare_digest(candidate, expected)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)
    return wrapped