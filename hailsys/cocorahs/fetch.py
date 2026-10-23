"""One request to the CoCoRaHS hail export.

Public data, no key.  Asked for with TimesInGMT=True so every time comes back in UTC.  The
request names whoever runs it in its User-Agent (contact setting) and refuses to go out with 
none, so CoCoRaHS can reach us if it ever needs to.  One request, a pause after it, three tries,
and anything that is not the export raises.  An HTML error page with HTTP 200 is a failure, not
an empty day.

The network and the sleep are arguments so the tests never touch CoCoRaHS.
"""

import re
import time
import urllib.error
import urllib.parse
import urllib.request

EXPORT_URL = "https://data.cocorahs.org/cocorahs/export/exportreports.aspx"
# Report Date selects by the day the hail was observed.  Report Timestamp by the time the report
# was entered or edited, which is how late entries and edits are found.
DATE_TYPES = ("reportdate", "timestamp")
TRIES = 3
TIMEOUT_S = 120
BACKOFF_S = 5       # Waits 5 seconds after the first failure, 10 seconds after the 2nd
DEFAULT_PAUSE_S = 1.0
_FIRST_COLUMN = "ObservationDate,"


class FetchError(Exception):
    """The export could not be read.  A client error, or three failed tries."""


class _NotTheExport(ValueError):
    """The answer came back but is not the export's CSV."""


def user_agent(contact):
    return f"hail-system (research; {contact})"

def _us_date(day):
    return f"{day.month}/{day.day}/{day.year}"


def build_url(state, date_type, start, end):
    query = urllib.parse.urlencode({
        "ReportType": "Hail", "Format": "csv", "State": state, "ReportDateType": date_type,
        "StartDate": _us_date(start), "EndDate": _us_date(end), "TimesInGMT": "True", "dtf": "1"})
    return EXPORT_URL + "?" + query


def fetch_hail(state, date_type, start, end, contact, *, pause=DEFAULT_PAUSE_S,
               opener=urllib.request.urlopen, sleep=time.sleep):
    """The export's text for one state and window.  ValueError for a bad argument, FetchError
    for a failed fetch."""
    if not re.fullmatch(r"[A-Z]{2}", state or ""):
        raise ValueError(f"state must be two capital letters, got {state!r}")
    if date_type not in DATE_TYPES:
        raise ValueError(f"date_type must be one of {DATE_TYPES}, got {date_type!r}")
    if end < start:
        raise ValueError(f"the window ends before it starts: {start} to {end}")
    if not (contact or "").strip():
        raise ValueError("a contact is needed for the User-Agent (settings.contact_email)")
    req = urllib.request.Request(build_url(state, date_type, start, end),
                                 headers={"User-Agent": user_agent(contact.strip())})
    what = f"{state} {date_type} {_us_date(start)} to {_us_date(end)}"
    cause = None
    for attempt in range(1, TRIES + 1):
        try:
            with opener(req, timeout=TIMEOUT_S) as resp:
                body = resp.read()
            text = body.decode("utf-8-sig")
            if not text.startswith(_FIRST_COLUMN):
                raise _NotTheExport(f"answer starts {text[:60]!r}")
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:       # request is wrong, asking again won't change it
                raise FetchError(f"CoCoRaHS export refused ({what}): HTTP {exc.code}") from exc
            cause = exc
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, _NotTheExport) as exc:
            cause = exc
        else:
            sleep(pause)
            return text
        if attempt < TRIES:
            sleep(BACKOFF_S * attempt)
    raise FetchError(f"CoCoRaHS export failed after {TRIES} tries ({what}): {cause}") from cause

