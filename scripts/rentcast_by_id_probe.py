"""Spike to check what RentCast's lookup by ID returns

One request per id given (at most 3), no retries, prints what comes back.
Reads nothing from and does not write to the database, calls will not appear
in api_call_log, verify in RentCast.

usage:
    docker compose exec -T web python scripts/rentcast_by_id_probe.py ID [ID ...]
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.rentcast.io/v1/listings/sale/"
MAX_IDS = 3
SHOW = ("id", "status", "listedDate", "lastSeenDate", "removedDate", "daysOnMarket", "price")

def probe(listing_id, key):
    """One request, returns (status, headers, body text)."""
    url = BASE_URL + urllib.parse.quote(listing_id, safe=",-")
    request = urllib.request.Request(url, headers={"X-Api-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8", "replace")

def main(ids):
    if not 1 <= len(ids) <= MAX_IDS:
        sys.exit(f"Give 1 to {MAX_IDS} ids: each one is a billed RentCast call.")
    key = os.environ.get("RENTCAST_KEY")
    if not key:
        sys.exit("RENTCAST_KEY is not set.")
    for listing_id in ids:
        print("=" * 70)
        print("id:", listing_id)
        started = time.monotonic()
        status, headers, text = probe(listing_id, key)
        print(f"HTTP {status} in {time.monotonic() - started:.2f}s")
        for name, value in headers.items():
            if name.lower().startswith(("x-", "retry-after")):
                print(f"  header {name}: {value}")
        try:
            body = json.loads(text)
        except ValueError:
            print("  body is not JSON:", text[:300])
            continue
        if isinstance(body, dict):
            for field in SHOW:
                print(f"  {field}: {body.get(field, '(absent)')}")
            print("  all fields:", ", ".join(sorted(body)))
        else:
            print("  body is a", type(body).__name__, ":", text[:300])


if __name__ == "__main__":
    main(sys.argv[1:])