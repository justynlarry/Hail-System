"""Direct check of estimate.py's recency lookup, no storm window needed."""
from hailsys.db import get_connection
from hailsys.rentcast.estimate import _latest_call_per_zip

with get_connection() as conn:
    print(_latest_call_per_zip(conn, ["80014", "99999"]))