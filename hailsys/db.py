"""Connection seam.

One context manager, one row shape.  Every caller gets dict rows so a query
module can name its columns instead of indexing a tuple.  No pool: these are
short-lived scripts (an export, a nightly ingest run), not a server holding
connections open between requests. Add one when something is actually
long-running enough to need it.

Connection settings are read the same way `psycopg.connect()` always has here:
from the standard libpq environment variables (PGHOST, PGDATABASE, PGUSER,
PGPASSWORD), set per-service in docker-compose.yml. Nothing here overrides
that -- it's the existing behavior, just centralised.
"""

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def get_connection():
    with psycopg.connect(row_factory=dict_row) as conn:
        yield conn
