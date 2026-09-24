"""Logging setup for the web app (gunicor workers and pull thread).

stdout only, logfmt.  Web's stdout goes to Docker's json
file driver -> Docker stamps the time, and this format
carries the log level and logger name.
"""

import logging
import sys

def configure_logging():
    # basicConfig is quiet no-op if root logger has a
    # handler.  Works here because nothing configures
    # root before create_app(), gunicorn only touches
    # its own gunicorn.* loggers.
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="level=%(levelname)s logger=%(name)s %(message)s",
    )
