"""Which way a drip step re-checks its listings:  One lookup per listing by id, or a
fresh pull of the zips they sit in.

choose_method() is pure.  It takes the two costs and returns both, so the confirm screen
can show what each way would cost before anyone clicks, and names the cheaper of the two.
On a tie it picks by_id, no pull, no api_pulls row and no listings written.  zip_calls_estimate()
is the only part that touches the database, and it reads api_call_log with the same rule 
the pull screen uses (last logged calls for a zip, else DEFAULT_CALLS_PER_ZIP).
"""

from hailsys.rentcast.estimate import DEFAULT_CALLS_PER_ZIP, _latest_call_per_zip

BY_ID = "by_id"
ZIP_PULL = "zip_pull"


def _count(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a whole number, 0 or more")
    return value


def choose_method(*, listings, zip_calls):
    """listings is how many listings the step will check.  zip_calls is the 
    estimated calls to pull the zips they sit in.  By id costs one call per
    listing
    """
    by_id_calls = _count(listings, "listings")
    zip_pull_calls = _count(zip_calls, "zip_calls")
    method = ZIP_PULL if zip_pull_calls < by_id_calls else BY_ID
    calls = zip_pull_calls if method == ZIP_PULL else by_id_calls
    return {"method": method, "calls": calls, "by_id_calls": by_id_calls,
            "zip_pull_calls": zip_pull_calls,
            "saves": abs(by_id_calls - zip_pull_calls)}


def zip_calls_estimate(conn, zip_codes):
    """Estimated calls to pull these zips.  The last logged calls for each
    else the default.
    """
    zips = sorted(set(zip_codes))
    history = _latest_call_per_zip(conn, zips)
    return sum(history[z]["calls_made"] if z in history else DEFAULT_CALLS_PER_ZIP for z in zips)
