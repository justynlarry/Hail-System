"""Logging setup for the web app (gunicorn workers and pull thread).

stdout only, logfmt, one event per line.
Web's stdout goes to Docker's json
file driver -> Docker stamps the time, and this format
carries the log level and logger name.

IEM scripts (ingest, backfill, export):  stdout goes through the systemd
unit to journald, which stamps time and unit.  Line carries level only,
log_event() logs through the root logger.  journald files all stdout at
one priority, so level= in the text is the only way to find warnings.
"""

import logging
import sys

def configure_logging(include_logger=True):
    # basicConfig is quiet no-op if root logger has a
    # handler.  Works here because nothing configures
    # root first: gunicorn only touches its own gunicorn.*
    # loggers, and the ingest scripts call this at startup.
    fmt = "level=%(levelname)s "
    if include_logger:
        fmt += "logger=%(name)s "
    fmt += "%(message)s"
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format=fmt)
