"""Wording for emails whose reason is the hail zones, not a National Weather Service report.

Phase 5c step 4.  No database, network, nothing imported from the rest of the system.  The
numbers in the radar sentence come from the parameter set in use and are passed in, not typed
in here or in a template.

Two things:
    * the sentences an email may use to say what was observed, by basis.
    * a check that refuses a text containing a banned claim.  It matches claim phrases, not the
      plain words "damage" or "%", which the shipped marketing copy uses in its disclaimers and
      statistics.  It catches the known claims and can't prove a text compliant:  a person
      reads the rendered preview of each basis before a template version is used.
"""

import html
import re
from decimal import Decimal

KM_PER_MILE = 1.609344

BASES = ("radar", "observer", "both")

OBSERVER_SENTENCE = "A trained volunteer weather observer reported hail nearby."
ATTRIBUTION = ("Volunteer hail observations from the Community Collaborative Rain, Hail and "
               "Snow Network (CoCoRaHS), licensed under CC BY 4.0.")


class WordingError(ValueError):
    """The text makes a claim the wording rules refuse, or a basis is unknown."""


def inches_label(hail_in):
    """1.00 -> '1 inch', 1.25 -> '1.25 inches'."""
    value = Decimal(str(hail_in)).normalize()
    text = format(value, "f")
    return f"{text} inch" if value == 1 else f"{text} inches"


def miles_label(radius_km):
    """The report radius in whole miles, at least one:  5 km -> '3 miles'."""
    miles = max(1, round(float(radius_km) / KM_PER_MILE))
    return f"{miles} mile" if miles == 1 else f"{miles} miles"


def radar_sentence(hail_in, radius_km):
    return (f"Weather radar indicated hail of at least {inches_label(hail_in)} "
            f"within about {miles_label(radius_km)} of this address.")


def sentences(bases, hail_in, radius_km):
    """The evidence sentences for an email whose matches have these bases, each once, radar first."""
    bases = set(bases)
    unknown = bases - set(BASES)
    if unknown:
        raise WordingError(f"not a zone basis: {sorted(unknown)}")
    out = []
    if bases & {"radar", "both"}:
        out.append(radar_sentence(hail_in, radius_km))
    if bases & {"observer", "both"}:
        out.append(OBSERVER_SENTENCE)
    return out


def attribution_for(bases):
    """The data attribution the CoCoRaHS licence requires, or '' when no observer is behind the email."""
    return ATTRIBUTION if set(bases) & {"observer", "both"} else ""


# (what it is, pattern).  Claim phrases only.
_BANNED = (
    ("a radar technical term", r"\b(mesh|dbz|reflectivity)\b"),
    ("a radar size or estimate",
     r"\b(radar|estimated|measured|maximum)\s+(hail\s+)?size\b|\bradar[- ](estimated|measured|detected|confirmed)\b"),
    ("a claim about the person's own property",
     r"\b(at|on|over|above|in)\s+your\s+(property|home|house|roof|address|yard)\b"
     r"|\b(hit|struck|damaged|destroyed|ruined)\s+your\b"
     r"|\byour\s+(property|home|house|roof)\s+(was|were|is|has|had|got|took|suffered)\b"
     r"|\bthis\s+(property|home|house|roof)\s+(was|were|has been)\b"),
    ("a probability",
     r"\b(chance|probability|likelihood|odds)s?\s+of\b"
     r"|\b(likely|probably|certainly|definitely)\s+(had|has|was|were|got|suffered|damaged|hit)\b"
     r"|\b\d+(\.\d+)?\s*(%|percent)\s+(chance|probability|likelihood|certain|sure)\b"),
    ("a guarantee or a confirmation", r"\bguarante\w*|\b(confirmed|verified|proven|proof)\b"),
)
_COMPILED = tuple((label, re.compile(pattern, re.I)) for label, pattern in _BANNED)


# alt, title and aria-label text is read too (mail clients that block images show it, so do screen readers).
_ATTRS = re.compile(r"""\b(?:alt|title|aria-label)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)


def plain(text):
    """The words a reader sees:  template and HTML comments and styles removed, tags removed, entities decoded,
    and the text of alt, title and aria-label attributes kept."""
    text = re.sub(r"(?s)\{#.*?#\}", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<style.*?</style>", " ", text)
    described = " ".join(a or b for a, b in _ATTRS.findall(text))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text + " " + described).split())


def banned_claims(text):
    """[(what it is, the words matched)] for every banned claim in the text;  empty when none."""
    seen = plain(text)
    return [(label, m.group(0)) for label, pattern in _COMPILED for m in pattern.finditer(seen)]


def check(text, where="the text"):
    """Raise WordingError naming each banned claim, or return the text unchanged."""
    found = banned_claims(text)
    if found:
        listed = "; ".join(f"'{words}' ({label})" for label, words in found)
        raise WordingError(f"{where} makes a claim the wording rules refuse: {listed}.")
    return text
