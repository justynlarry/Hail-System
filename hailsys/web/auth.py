import hashlib
import hmac
import os
from base64 import b64decode, b64encode
from functools import wraps

from flask import redirect, session, url_for

# Cost parameters:  n is the work factor, r and p tune, block size and
# parallelism.  These are stored 'with' each hash, so raising them later
# leaves existing passwords verifiable.  Verification uses the stored 
# values.  These constants only apply to newly created hashes.

_N = 2 ** 15
_R = 8
_P = 1
_SALT_BYTES = 16
_DKLEN = 32

def hash_password(password):
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_N, r=_R, p=_P, dklen=_DKLEN,
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
    )

    # compare_digest - not ==
    return hmac.compare_digest(candidate, expected)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("emp_id") is None:
            return redirect(url_for("main.logic"))
        return view(*args, **kwargs)
    return wrapped