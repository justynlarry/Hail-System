"""Stand-in storm data for the template preview (item 203, piece 4).

Four situations that change what the email says: one storm, several storms, more listings than
fit, and no first name.  Fixed dates and values, so a preview is the same every time.  These are
invented addresses; the preview never reads a real recipient or listing.
"""

from datetime import datetime, timezone
from decimal import Decimal

AUG15 = datetime(2026, 8, 15, 20, 19, tzinfo=timezone.utc)
AUG21 = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


def _match(listing_id, address, when, magnitude, miles):
    return {"listing_id": listing_id, "address": address, "utc_datetime": when,
            "magnitude": Decimal(magnitude), "distance_miles": Decimal(miles)}


def _many():
    """More listings than render.MAX_LISTINGS, to show the 'and N more' line."""
    return [_match(n, f"{100 + n} Example Ave, Denver, CO 802{n:02d}", AUG15, "1.50",
                   str(n % 5 + 0.5)) for n in range(1, 19)]


_ONE = "123 Example St, Denver, CO 80202"

SITUATIONS = {
    "one": {"label": "One storm, one listing", "agent": "Pat Example",
            "matches": [_match(1, _ONE, AUG15, "1.75", "1.2")]},
    "several": {"label": "Several storms and listings", "agent": "Pat Example",
                "matches": [_match(1, _ONE, AUG15, "1.75", "1.2"),
                            _match(1, _ONE, AUG21, "1.00", "2.1"),
                            _match(2, "9 Sample Ave, Aurora, CO 80010", AUG21, "1.25", "1.5")]},
    "more": {"label": "More listings than fit (the 'and N more' line)",
             "agent": "Pat Example", "matches": _many()},
    "no_name": {"label": "No first name on file", "agent": "",
                "matches": [_match(1, _ONE, AUG15, "1.75", "1.2")]},
}
DEFAULT = "one"


def situation(key):
    """(agent name, matches) for a situation; an unknown key gives the default one.  The matches
    are a copy, so a caller cannot change the stand-in data."""
    chosen = (SITUATIONS.get(key) if isinstance(key, str) else None) or SITUATIONS[DEFAULT]
    return chosen["agent"], [dict(m) for m in chosen["matches"]]

