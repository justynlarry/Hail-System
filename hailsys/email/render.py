"""Render the hail-alert email.

No Database, no Network, caller supplies the realtor's name, the matches
already chose as eligible, and the settings, then this bbuilds the template
context and renders the subject and the HTML body.

Rules:
* Body is HTML-escaped and subject is not.  Control characters are stripped
  from the subject.
* Missing or Unknown placeholders are errors, so they're never blank
  (StrictUndefined).  Template is checked at save.
* Links on free-hosting domans are refused, Constant Contact will accept
  them but will not deliver.
* Hail size goes through hailsys.formatting.magnitude so the email and the
  map can't drift apart.  NoN and Infinity are treated as 'not reported'.
* Storm day is the Denver calendar day, not UTC.
* Errors name a placeholder or setting.
"""

import os
import re
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal

from jinja2 import StrictUndefined, TemplateError, TemplateSyntaxError, meta
from jinja2.sandbox import SandboxedEnvironment

from hailsys.formatting import magnitude
from hailsys.tuning import DISPLAY_TZ

MAX_LISTINGS = 15
MAX_SUBJECT = 150
TRACKING_TAG = "[[trackingImage]]"
NOT_REPORTED = "size not reported"
FREE_HOSTS = ("azurewebsites.net",)

# Everything that the body may use, 'total' is also used by the subject
ALLOWED_VARS = frozenset({
    "logo_url", "badge_cra_url", "badge_bbb_url", "contact_email", "schedule_url",
    "agent_first_name", "storm_count", "storm_dates_label", "largest_hail",
    "nearest_miles", "properties", "more_count", "total",
})

_BODY_ENV = SandboxedEnvironment(autoescape=True, undefined=StrictUndefined)
_SUBJECT_ENV = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)

_COMPANY_WORDS = frozenset({
    "llc", "inc", "co", "corp", "company", "team", "group", "realty", "realtors",
    "properties", "homes", "real", "estate", "associates", "partners", "brokers",
    "brokerage", "office", "mls", "and", "&",
})

class RenderError(ValueError):
    """Message names a placeholder or setting."""

class SettingsError(RenderError):
    pass


def _check_url(name, url):
    if not isinstance(url, str) or not re.fullmatch(r"https://\S+", url):
        raise SettingsError(f"{name} must be an https:// address")
    host = urllib.parse.urlsplit(url).hostname or ""
    if any(host == h or host.endswith("." + h) for h in FREE_HOSTS):
        raise SettingsError(
            f"{name} points at a free-hosting domain ({host}).  Constant "
            "Contact will not deliver to a free-hosting domain."
        )

_ENV_NAMES = {
    "logo_url": "EMAIL_LOGO_URL",
    "badge_cra_url": "EMAIL_BADGE_CRA_URL",
    "badge_bbb_url": "EMAIL_BADGE_BBB_URL",
    "contact_email": "EMAIL_CONTACT_EMAIL",
    "schedule_url": "EMAIL_SCHEDULE_URL",
}

@dataclass(frozen=True)
class EmailSettings:
    logo_url: str
    badge_cra_url: str
    badge_bbb_url: str
    contact_email: str
    schedule_url: str

    def __post_init__(self):
        for name in ("logo_url", "badge_cra_url", "badge_bbb_url", "schedule_url"):
            _check_url(name, getattr(self, name))
        if "@" not in self.contact_email or re.search(r"\s", self.contact_email):
            raise SettingsError("contact_email is not an email address")

    @classmethod
    def from_env(cls):
        values = {}
        for field, var in _ENV_NAMES.items():
            value = (os.environ.get(var) or "").strip()
            if not value:
                raise SettingsError(f"{var} is not set in the environment")
            values[field] = value
        return cls(**values)

# ------ small formatters ---------------------------------------------------------

def first_name(agent_name):
    """A first name when the name plainly belogs to a person, otherwise None.
    
    RentCast gives one free-text name.  None means email opens with "Hello.".
    """
    if not agent_name:
        return None
    name = agent_name.strip()
    if "," in name:
        return None
    words = name.split()
    if len(words) < 2:
        return None
    for w in words:
        if w.lower().strip(".,") in _COMPANY_WORDS or any(c.isdigit() for c in w):
            return None
    first=words[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z'-]{1,24}", first):
        return None
    if first.isupper() or first.islower():
        first = first.capitalize()
    return first

def _finite(value):
    if value is None:
        return False
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.is_finite()


def hail_label(value):
    return magnitude(value, "inches") if _finite(value) else NOT_REPORTED


def miles_label(value):
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite():
        raise RenderError("a match has a distance that is not a number")
    return f"{d:.1f} mi"


def local_date(utc_datetime):
    return utc_datetime.astimezone(DISPLAY_TZ).date()


def date_label(d):
    return f"{d:%B} {d.day}, {d.year}"


def storm_dates_label(dates):
    """August 15, 2026' / 'August 15 and August 21, 2026' / 'A, B and C, 2026'."""
    dates = sorted(set(dates))
    if not dates:
        raise RenderError("no storm dates")
    if len(dates) == 1:
        return date_label(dates[0])
    if len({d.year for d in dates}) == 1:
        parts = [f"{d:%B} {d.day}" for d in dates]
        return ", ".join(parts[:-1]) + f" and {parts[-1]}, {dates[0].year}"
    parts = [date_label(d) for d in dates]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


# ------ the context ---------------------------------------------------------------

def _largest(events):
    values = [Decimal(str(e["magnitude"])) for e in events if _finite(e["magnitude"])]
    return max(values) if values else None


def build_context(agent_name, matches, settings, max_listings=MAX_LISTINGS):
    """Template variables for on realtor's eligible matches.
    
    `matches`: dicst with listing_id, address (one display line), utc_datetime
    (aware), magnitude (Decimal or None) and distance_miles (Decimal).
    """
    if not matches:
        raise RenderError("no matches to render")

    grouped = {}
    for m in matches:
        grouped.setdefault(m["listing_id"], {"address": m["address"], "events": []})
        grouped[m["listing_id"]]["events"].append(m)

    listings = []
    for item in grouped.values():
        events = sorted(item["events"], key = lambda m: m["utc_datetime"])
        largest = _largest(events)
        listings.append({
            "address": item["address"],
            "_rank": (-len(events), -(largest if largest is not None else Decimal(-1)),
                      item["address"]),
            "events": [{"date": date_label(local_date(e["utc_datetime"])),
                        "hail_size": hail_label(e["magnitude"]),
                        "miles": miles_label(e["distance_miles"])} for e in events],
        })
    listings.sort(key=lambda p: p["_rank"])
    for p in listings:
        del p["_rank"]

    # Figures over everything matched, including listing the cap hides.
    largest_all = _largest(matches)
    nearest_all = min(Decimal(str(m["distance_miles"])) for m in matches)
    days = [local_date(m["utc_datetime"]) for m in matches]

    shown = listings[:max_listings]
    return {
        "logo_url": settings.logo_url,
        "badge_cra_url": settings.badge_cra_url,
        "badge_bbb_url": settings.badge_bbb_url,
        "contact_email": settings.contact_email,
        "schedule_url": settings.schedule_url,
        "agent_first_name": first_name(agent_name) or "",
        "storm_count": len(set(days)),
        "storm_dates_label": storm_dates_label(days),
        "largest_hail": hail_label(largest_all),
        "nearest_miles": miles_label(nearest_all),
        "properties": shown,
        "more_count": len(listings) - len(shown),
        "total": len(listings),
    }


# ------ templates ---------------------------------------------------------------

def validate_template(subject_src, body_src):
    """Raise RenderError listing every problem, or return None, run on save."""
    problems = []
    for label, env, src in (("subject", _SUBJECT_ENV, subject_src),
                            ("body", _BODY_ENV, body_src)):
        try:
            tree = env.parse(src)
        except TemplateSyntaxError as exc:
            problems.append(f"{label}: syntax error on lin {exc.lineno}")
            continue
        unknown = sorted(meta.find_undeclared_variables(tree) - ALLOWED_VARS)
        if unknown:
            problems.append(f"{label}: unknown placeholder(s) {', '.join(unknown)}")
    if TRACKING_TAG not in body_src:
        problems.append(f"body: missing the {TRACKING_TAG} tag Constant Contact requires")
    for host in FREE_HOSTS:
        if re.search(re.escape(host), body_src, re.I):
            problems.append(f"body: links to {host} are not allowed")
    if re.search(r"(?i)\b(?:href|src)\s*=\s*[\"']http://", body_src):
        problems.append("body: use https links only")
    if problems:
        raise RenderError("; ".join(problems))


def render(subject_src, body_src, context):
    """(subject, html) for one email.  The pair goes into sent_emails as is."""
    try:
        subject = _SUBJECT_ENV.from_string(subject_src).render(**context)
        html = _BODY_ENV.from_string(body_src).render(**context)
    except TemplateError as exc:
        # Class name only, message can quote context values.
        raise RenderError(f"template could not be rendered ({type(exc).__name__})") from None
    subject = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", subject)).strip()
    if not subject:
        raise RenderError("the subject rendered empty")
    if len(subject) > MAX_SUBJECT:
        subject = subject[:MAX_SUBJECT - 3].rstrip() + "..."
    if TRACKING_TAG not in html:
        raise RenderError(f"the body is missing the {TRACKING_TAG} tag")
    return subject, html
