import hashlib
import hmac
import os
from base64 import b64decode, b64encode
from datetime import datetime, timedelta, timezone
from functools import wraps

from hailsys.db import get_connection
from hailsys.settings import fetch_settings

from flask import flash, g, redirect, session, url_for, abort, request

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
MIN_PASSWORD_LENGTH = 12


# Failed sign-ins.  Safety limits, so they live in code, not on the admin page.  Five wrong
# passwords for one user name, counted since that name's last good sign-in and within the last
# fifteen minutes, refuse the name until the oldest of them is fifteen minutes old.  Counted by
# the name as typed, an account or not, so a refusal doesn't say anything about which names
# exist.  Kept in login_attempts (sql/067), not in memory.  web runs two worker processes, and
# each would see only its half.
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
# login_attempts.user_name's CHECK.  A longer typed name is recorded and counted cut to this.
MAX_ATTEMPT_NAME_LENGTH = 200


def load_current_user():
    """Registered as app.before_request.  Re-checks is_active and both
    fail-safe timestamps -- per-user and system-wide -- on every request -- and
    the session's age against settings.session_max_hours on every request.
    """
    emp_id = session.get("emp_id")
    if emp_id is None:
        g.user = None
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT u.is_active, u.sessions_invalidated_at,"
            "       s.global_sessions_invalidated_at, s.session_max_hours"
            "  FROM users u"
            # LEFT JOIN: settings is a singleton the migration always
            # inserts, but a missing row there is a data anomaly unrelated
            # to this user -- it should read as "no global invalidation
            # set" (NULL), not silently boot everyone.
            "  LEFT JOIN settings s ON s.id = 1"
            " WHERE u.emp_id = %s",
            (emp_id,),
        )
        row = cur.fetchone()

        # Compare real datetimes, not stringified ones.  Both *_invalidated_at
        # columns come back TIMESTAMPTZ (aware); issued_at has to be parsed back
        # to an aware datetime too, or an aware/naive or string/string compare
        # can sort wrong instead of raising.
        issued_at_raw = session.get("issued_at")
        issued_at = datetime.fromisoformat(issued_at_raw) if issued_at_raw else None

        def invalidated_since_login(invalidated_at):
            return (invalidated_at is not None and issued_at is not None
                    and invalidated_at > issued_at)

        # Too Old:  signed in longer ago than the setting allows.  A session with no
        # issued_at is treated as too old, not as ageless:  every sign-in sets it.  A 
        # missing settings row (NULL through the LEFT JOIN) sets no limit, as above.
        max_hours = row["session_max_hours"] if row is not None else None
        too_old = max_hours is not None and (
            issued_at is None
            or datetime.now(timezone.utc) - issued_at > timedelta(hours=max_hours))

        booted = (
            row is None
            or not row["is_active"]
            or invalidated_since_login(row["sessions_invalidated_at"])
            or invalidated_since_login(row["global_sessions_invalidated_at"])
            or too_old
        )
        if booted:
            session.clear()
            g.user = None
            if too_old:
                flash(f"You were signed out after {max_hours} hours.  Please sign in again.")
            return

        g.user = {"emp_id": emp_id, "role": session.get("role")}
        g.settings = fetch_settings(conn)


def require_role(*roles):
    """Core check, returns a redirect/abort response, or None if allowed."""
    if g.user is None:
        return redirect(url_for("main.login"))
    if roles and g.user["role"] not in roles:
        abort(403)
    return None

def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            resp = require_role(*roles)
            if resp is not None:
                return resp
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

# A real hash, used only so the failure paths cost the same as a 
# successful verification.  Generated per process from random bytes.
# Cannot be authenticated against.
DUMMY_PASSWORD_HASH = hash_password(b64encode(os.urandom(_SALT_BYTES)).decode("ascii"))


# Failures since the name's last good sign-in, inside the window.  "Since" is by attempt_id, not
# time:  attempts in one transaction share now(), and the id still orders them.
_RECENT_FAILURES_SQL = """
SELECT count(*) AS failures
FROM login_attempts
WHERE user_name = %(name)s
  AND outcome = 'failed'
  AND attempted_at > now() - %(window)s
  AND attempt_id > coalesce((SELECT max(attempt_id) FROM login_attempts
                             WHERE user_name = %(name)s AND outcome = 'ok'), 0)
"""


def login_locked(cur, user_name):
    """True when this name has reached LOGIN_FAILURE_LIMIT."""
    cur.execute(_RECENT_FAILURES_SQL, {"name": user_name, "window": LOGIN_FAILURE_WINDOW})
    return cur.fetchone()['failures'] >= LOGIN_FAILURE_LIMIT


def record_login_attempt(cur, user_name, outcome):
    """One row in login_attempts:  'ok', 'failed', or 'locked'.  The caller commits."""
    cur.execute("INSERT INTO login_attempts (user_name, outcome) VALUES (%s, %s)",
                (user_name, outcome))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)
    return wrapped

PUBLIC_ENDPOINTS = frozenset({
    "main.login",
    "static",
})

def require_login():
    """App-wide default-deny.  Registered after load_current_user, so g.user
    is already populated.

    Per-route @login_required decorators stay: this is defence in depth.
    If this hook is ever misregistered or an endpoint is wrongly
    whitelisted, the decorators still hold.
    """
    # request.endpoint is None for an unmatched URL -- Flask's own 404
    # handles it, and redirecting to login would be confusing.
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if g.user is None:
        return redirect(url_for("main.login"))
    return None
