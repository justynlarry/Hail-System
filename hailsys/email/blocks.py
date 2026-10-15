"""Email template blocks:  the model behind the template builder.

No database/network, works like render.py.  A template is a dict of settings and an
ordered list of blocks.  compile_blocks() turns it into the (subject_src, body_src) 
pair that templatestore saves and render.render renders, so the sandbox, ALLOWED_VARS
and validate_template stay the authority.

Nothing a user types becomes template syntax:
* static text is HTML-escaped and its braces are written as character references, so no
    '{{', '{%', '{#' can reach the template source.
* Merge fields are a fixed list, typed {name} and compiled to {{ name }}.  Any other brace is
    an error.
* Inline formatting is an allow-list: [b], [b=colour], [i], [color=name], [link=url].  
    Colours come from PALETTE, a link is https (or {schedule_url}), tags must nest and close.
* Double square brackets are refused -> Constant Contact reads [[...]] as its own tags.
"""

import html
import json
import re

from hailsys.email import render

PALETTE = {"navy": "#1b4a80", "blue": "#1f5c9e", "red": "#c0392b", "grey": "#64748b",
           "slate": "#475569", "dark": "#1e293b", "gold": "#e8a317"}
TEXT_TOKENS = {"storm_dates_label", "largest_hail", "nearest_miles", "total"}
LINK_TOKENS = {"schedule_url"}
SIZES = {12: 19, 14: 21, 15: 22, 16: 25}        # font size -> line height, in px
HEADING_SIZES = {19: 24, 21: 26, 24: 30}        # headings: font size -> line height

MAX_BLOCKS = 30
MAX_TEXT = 2000
MAX_TITLE = 100
MAX_PREHEADER = 150
MAX_HTML = 90_000       # gmail clips a message at around 102KB
MAX_NESTING = 4

SUBJECT_PRESETS = {
    "hail_alert": (
        "{% if storm_count == 1 %}A hail storm was{% else %}Hail storms were{% endif %} "
        "reported near {% if total == 1 %}your listing at {{ properties[0].address }}"
        "{% else %}several of your listings{% endif %}"),
}

LINK_STYLE = ("color:#1f5c9e; font-weight:bold; text-decoration:none; "
              "border-bottom:2px solid #2d7dd2; padding-bottom:2px;")

_TAG = re.compile(r"\[(/?)(b|i|color|link)(?:=([^\[\]]*))?\]")
_TOKEN = re.compile(r"\{([a-z_]+)\}")
_CLOSE = {"b": "</strong>", "i": "</em>", "color": "</span>", "link": "</a>"}


class BlockError(ValueError):
    """A template or block is not valid, message says where.

    'problems' is the same information as data:  a list of (block number, message), the
    number counted from 1, or None for a problem with the template as a whole.  The editor
    shows each message beside its block."""

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = problems if problems is not None else [(None, message)]


# ------ text --------------------------

def _literal(text):
    """Static text for the body source: escaped, with braces as character references."""
    return html.escape(text, quote=True).replace("{", "&#123;").replace("}", "&#125;")


def _around(text, at, before=10, after=25):
    """A short quote of `text` around position `at`, for a message that says where."""
    start, end = max(at - before, 0), min(at + after, len(text))
    return '"' + ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "") + '"'


def _squash(text, limit, where):
    if not isinstance(text, str):
        raise BlockError(f"{where} must be text")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        raise BlockError(f"{where} is limited to {limit} characters")
    if "[[" in text:
        raise BlockError(f"{where} may not contain double square brackets "
                         f"(at {_around(text, text.index('[['))})")
    return text


def _fields(text, where, allowed, base=0, whole=None):
    """[("text", s) | ("field", name), ...].  Braces are only for merge fields.  `base` is where
    `text` starts inside `whole` (the full text), so a message can quote the place."""
    whole = text if whole is None else whole

    def no_braces(chunk, offset):
        for i, character in enumerate(chunk):
            if character in "{}":
                raise BlockError(f"{where}: curly braces are only for merge fields "
                                 f"(at {_around(whole, offset + i)})")

    parts, pos = [], 0
    for m in _TOKEN.finditer(text):
        if m.group(1) not in allowed:
            raise BlockError(f"{where}: {{{m.group(1)}}} is not a merge field you can use here "
                             f"(at {_around(whole, base + m.start())})")
        no_braces(text[pos:m.start()], base + pos)
        parts.append(("text", text[pos:m.start()]))
        parts.append(("field", m.group(1)))
        pos = m.end()
    no_braces(text[pos:], base + pos)
    parts.append(("text", text[pos:]))
    return parts


def plain(text, where, limit, tokens=(), subject=False):
    """Text with merge fields and no formatting, as body source (or subject source)."""
    out = []
    for kind, value in _fields(_squash(text, limit, where), where, tokens):
        if kind == "field":
            out.append("{{ " + value + " }}")
        elif subject:
            if value:           # a Jinja string literal: nothing typed can end it early
                out.append("{{ " + json.dumps(value, ensure_ascii=False) +" }}")
        else:
            out.append(_literal(value))
    return "".join(out)


def _colour(value, where):
    if value not in PALETTE:
        raise BlockError(f"{where}: the color must be one of {', '.join(PALETTE)}")
    return PALETTE[value]


def _href(value, where):
    if value is None:
        raise BlockError(f"{where}: a link needs an address, [link=https://...]")
    for token in LINK_TOKENS:
        if value == "{" + token + "}":
            return "{{ " + token + " }}"
    try:
        render.check_url("A link", value)
    except render.SettingsError as exc:
        raise BlockError(f"{where}: {exc}") from None
    return _literal(value)


def _open(name, value, stack, where):
    if name == "b":
        if value is None:
            return "<strong>"
        return f'<strong style="color:{_colour(value, where)};">'
    if name == "i":
        if value is not None:
            raise BlockError(f"{where}: [i] takes no setting")
        return "<em>"
    if name == "color":
        return f'<span style="color:{_colour(value, where)};">'
    if "link" in stack:
        raise BlockError(f"{where}: a link cannot hold another link")
    return f'<a href="{_href(value, where)}" style="{LINK_STYLE}">'

def rich(text, where):
    """Body source for text with the inline tags and merge fields."""
    text = _squash(text, MAX_TEXT, where)
    out, names, opened, pos = [], [], [], 0

    def emit(chunk, base):
        for kind, value in _fields(chunk, where, TEXT_TOKENS, base, text):
            out.append(_literal(value) if kind == "text" else "{{ " + value + " }}")

    for m in _TAG.finditer(text):
        emit(text[pos:m.start()], pos)
        pos = m.end()
        closing, name, value = m.group(1) == "/", m.group(2), m.group(3)
        here = _around(text, m.start())
        if closing:
            if value is not None or not names or names[-1] != name:
                raise BlockError(f"{where}: [/{name}] does not match an open tag (at {here})")
            names.pop()
            opened.pop()
            out.append(_CLOSE[name])
        else:
            if len(names) >= MAX_NESTING:
                raise BlockError(f"{where}: tags are nested more than {MAX_NESTING} deep "
                                 f"(at {here})")
            try:
                out.append(_open(name, value, names, where))
            except BlockError as exc:
                raise BlockError(f"{exc} (at {here})") from None
            names.append(name)
            opened.append(m.start())
    emit(text[pos:], pos)
    if names:
        raise BlockError(f"{where}: [{names[-1]}] is never closed "
                         f"(opened at {_around(text, opened[-1])})")
    return "".join(out)


# ------ Block Settings -------------------------

def _choice(*allowed):
    def check(value, where):
        if value not in allowed:
            raise BlockError(f"{where} must be one of {', '.join(map(str, allowed))}")
        return value
    return check


def _int(low, high, step=1):
    def check(value, where):
        if (not isinstance(value, int) or isinstance(value, bool)
                or not low <= value <= high or (value - low) % step):
            raise BlockError(f"{where} must be a whole number from {low} to {high}"
                             + (f" in steps of {step}" if step > 1 else ""))
        return value
    return check


def _flag(value, where):
    if not isinstance(value, bool):
        raise BlockError(f"{where} must be true or false")
    return value


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise BlockError(f"{where} is required")
    return value

def _name(value, where):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
        raise BlockError(f"{where} must be an image name")
    return value


def _phone(value, where):
    if not isinstance(value, str) or not re.fullmatch(r"\(\d{3}\) \d{3}-\d{4}", value):
        raise BlockError(f"{where} must look like (303) 750-1900")
    return value

def _rich(limit):
    def check(value, where):
        if not isinstance(value, str) or not value.strip():
            raise BlockError(f"{where} is required")
        if len(value) > limit:
            raise BlockError(f"{where} is limited to {limit} characters")
        rich(value, where)
        return value
    return check

def _short(limit):
    """Plain text (merge fields and formatting not allowed), required."""
    def check(value, where):
        if not isinstance(value, str) or not value.strip():
            raise BlockError(f"{where} is required")
        plain(value, where, limit)
        return value.strip()
    return check

def _address(value, where):
    if not isinstance(value, str):
        raise BlockError(f"{where} must be a web address")
    _href(value.strip(), where)
    return value.strip()

def _optional(check):
    def inner(value, where):
        return value if value == "" else check(value, where)
    return inner

def _list(low, high, check):
    def inner(value, where):
        if not isinstance(value, list) or not low <= len(value) <= high:
            raise BlockError(f"{where} must be a list of {low} to {high} entries")
        return [check(v, f"{where} entry {i}") for i, v in enumerate(value, 1)]
    return inner


# setting -> (default, check). A default of None means the setting is required.

SPECS = {
    "text": {"text": (None, _text), "size": (16, _choice(*SIZES)),
        "color": ("dark", _choice(*PALETTE)), "bold": (False, _flag),
        "italic": (False, _flag), "align": ("left", _choice("left", "center")),
        "space_before": (0, _int(0, 40, 2)), "space_after": (20, _int(0, 40, 2))},
    "divider": {},
    "image": {"image": (None, _name), "width": (220, _int(40, 560, 2)),
              "placement": ("body", _choice("header", "body"))},
    "footer": {"phone": ("(303) 750-1900", _phone)},
    "heading": {"text": (None, _rich(200)), "size": (24, _choice(*HEADING_SIZES)),
                "color": ("navy", _choice(*PALETTE)), "align": ("center", _choice("left", "center")),
                "space_before": (0, _int(0, 40, 2)), "space_after": (12, _int(0, 40, 2))},
    "button": {"label": (None, _short(40)), "url": (None, _address),
               "color": ("navy", _choice(*PALETTE)),
               "space_before": (0, _int(0, 40, 2)), "space_after": (16, _int(0, 40, 2))},
    "band": {"title": (None, _short(80)), "intro": (None, _rich(400)),
             "items": (None, _list(1, 10, _rich(200)))},
    "testimonial": {"quote": (None, _short(500)), "who": (None, _short(80)),
                    "stars": (5, _int(0, 5)), "more_label": ("", _optional(_short(60))),
                    "more_url": ("", _optional(_address))},
    "badges": {"images": (["badge_cra", "badge_bbb"], _list(1, 4, _name)),
               "width": (140, _int(40, 200, 2))},
    "headline": {},
    "greeting": {},
    "storm_panel": {},
    "listings": {},
}


def clean_block(block, where="block"):
    """The block with defaults filled in.  BlockError for anything not allowed."""
    kind = block.get("kind") if isinstance(block, dict) else None
    if not isinstance(kind, str) or kind not in SPECS:
        raise BlockError(f"{where}: unknown kind of block")
    spec = SPECS[kind]
    extra = set(block) - set(spec) - {"kind"}
    if extra:
        raise BlockError(f"{where} ({kind}): unknown setting(s){', '.join(sorted(map(str, extra)))}")
    out = {"kind": kind}
    for key, (default, check) in spec.items():
        out[key] = check(block.get(key, default), f"{where} ({kind}) {key}")
    if kind == "testimonial" and bool(out["more_label"]) != bool(out["more_url"]):
        raise BlockError(f"{where} (testimonial): give both a link label and a link address, "
                         "or neither")
    if kind == "text":
        rich(out["text"], f"{where} (text) text")       # checks the markup, compiling renders it again
    return out

# ------ block HTML -----------------------------------

_FONT = "font-family:Arial,Helvetica,sans-serif;"

_DIVIDER = (
    '<tr><td style="padding:0 30px;">'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
    '<tr><td style="border-top:2px dotted #2d7dd2; font-size:0; line-height:0;">&nbsp;</td></tr>'
    '</table></td></tr>')

def _px(n):
    return f"{n}px" if n else "0"


def _render_text(b):
    style = (f"padding:{_px(b['space_before'])} 34px {_px(b['space_after'])} 34px; {_FONT} "
             f"font-size:{b['size']}px; line-height:{SIZES[b['size']]}px; "
             f"color:{PALETTE[b['color']]};")
    if b["bold"]:
        style += " font-weight:bold;"
    if b["italic"]:
        style += " font-style:italic;"
    align = ' align="center"' if b["align"] == "center" else ""
    return f'<tr><td{align} style="{style}">' + rich(b["text"], "text") + "</td></tr>"


def _render_image(b):
    name, width = b["image"], str(b["width"])
    if b["placement"] == "header":
        padding = "26px 30px 18px 30px; background-color:#ffffff;"
    else:
        padding = "0 34px 26px 34px;"
    return ('<tr><td align="center" style="padding:' + padding + '">'
            '<img src="{{ images[\'' + name + '\'][\'url\'] }}" width="' + width + '" '
            'alt="{{ images[\'' + name + '\'][\'alt\'] }}" '
            'style="display:block; border:0; width:' + width + 'px; max-width:100%; '
            'height:auto;" /></td></tr>')

def _render_footer(b):
    # Constant Contact adds the postal address, Unsubscribe and Update Profile itself, it does
    # not add the advertisement notice (CAN-SPAM), so this block carries it and CANNOT be removed.
    digits = re.sub(r"\D", "", b["phone"])
    return ('<tr><td align="center" style="padding:0 34px 26px 34px; ' + _FONT + ' '
            'font-size:12px; line-height:19px; color:#64748b;"> This is an advertisement. '
            '&nbsp;&middot;&nbsp; '
            '<a href="tel:' + digits + '" style="color:#1f5c9e; text-decoration:none;">'
            + _literal(b["phone"]) + '</a> &nbsp;&middot;&nbsp; '
            '<a href="mailto:{{ contact_email }}" style="color:#1f5c9e; text-decoration:none;">'
            '{{ contact_email }}</a></td></tr>')


def _render_heading(b):
    style = (f"padding:{_px(b['space_before'])} 34px {_px(b['space_after'])} 34px; {_FONT} "
             f"font-size:{b['size']}px; line-height:{HEADING_SIZES[b['size']]}px; "
             f"font-weight:bold; color:{PALETTE[b['color']]};")
    align = ' align="center"' if b["align"] == "center" else ""
    return f'<tr><td{align} style="{style}">' + rich(b["text"], "heading") + "</td></tr>"


def _render_button(b):
    colour = PALETTE[b["color"]]
    return (f'<tr><td align="center" style="padding:{_px(b["space_before"])} 34px '
            f'{_px(b["space_after"])} 34px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
            f'<tr><td align="center" bgcolor="{colour}" '
            f'style="background-color:{colour}; border-radius:28px;">'
            f'<a href="{_href(b["url"], "button")}" style="display:block; padding:17px 24px; '
            f'{_FONT} font-size:16px; line-height:20px; font-weight:bold; letter-spacing:1px; '
            'color:#ffffff; text-decoration:none; border-radius:28px;">'
            + plain(b["label"], "button label", 40) + '</a></td></tr></table></td></tr>')


def _render_band(b):
    rows, last = [], len(b["items"]) - 1
    for i, item in enumerate(b["items"]):
        gap = i != last
        rows.append('<tr><td width="22" valign="top" style="color:#1f5c9e; font-weight:bold;'
                    + (" padding-bottom:10px;" if gap else "") + '">&#10003;</td>'
                    + ('<td style="padding-bottom:10px;">' if gap else "<td>")
                    + rich(item, "band item") + "</td></tr>")
    return ('<tr><td style="padding:0;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            'style="background-color:#f3f7fc; border-top:2px solid #1f5c9e; '
            'border-bottom:2px solid #1f5c9e;">'
            '<tr><td style="padding:28px 34px 10px 34px;" align="center">'
            f'<div style="{_FONT} font-size:21px; line-height:26px; font-weight:bold; '
            'color:#1b4a80;">' + plain(b["title"], "band title", 80) + '</div></td></tr>'
            f'<tr><td align="center" style="padding:0 34px 22px 34px; {_FONT} font-size:15px; '
            'line-height:23px; color:#334155;">' + rich(b["intro"], "band intro") + '</td></tr>'
            '<tr><td style="padding:0 34px 30px 34px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            'style="background-color:#ffffff; border:1px solid #dbe6f2;">'
            '<tr><td style="padding:20px 22px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="{_FONT} font-size:15px; line-height:22px; color:#1e293b;">'
            + "".join(rows) + '</table></td></tr></table></td></tr></table></td></tr>')


def _render_testimonial(b):
    out = []
    if b["stars"]:
        out.append(f'<tr><td align="center" style="padding:8px 34px 16px 34px; {_FONT} '
                   'font-size:17px; line-height:20px; color:#e8a317; letter-spacing:3px;">'
                   + "&#9733;" * b["stars"] + "</td></tr>")
    out.append('<tr><td style="padding:0 34px 14px 34px;">'
               '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
               'width="100%" style="border-left:3px solid #dbe6f2;"><tr>'
               '<td style="padding:2px 0 2px 18px; font-family:Georgia,\'Times New Roman\','
               'serif; font-size:15px; line-height:24px; color:#334155; font-style:italic;">'
               "&ldquo;" + plain(b["quote"], "quote", 500) + "&rdquo;"
               f'<div style="padding-top:10px; {_FONT} font-size:13px; line-height:18px; '
               'color:#64748b; font-style:normal;">&mdash; ' + plain(b["who"], "who", 80)
               + "</div></td></tr></table></td></tr>")
    if b["more_url"]:
        out.append('<tr><td align="center" style="padding:6px 34px 26px 34px;">'
                   f'<a href="{_href(b["more_url"], "testimonial link")}" style="{_FONT} '
                   'font-size:15px; line-height:20px; font-weight:bold; color:#1f5c9e; '
                   'text-decoration:none; border-bottom:2px solid #2d7dd2; padding-bottom:2px;">'
                   + plain(b["more_label"], "link label", 60) + "</a></td></tr>")
    return "".join(out)


def _badge(name, width):
    return ('<td align="center" style="padding:0 12px;">'
            "<img src=\"{{ images['" + name + "']['url'] }}\" width=\"" + str(width) + "\" "
            "alt=\"{{ images['" + name + "']['alt'] }}\" "
            'style="display:block; border:0; width:' + str(width) + 'px; max-width:100%; '
            'height:auto;" /></td>')


def _render_badges(b):
    return ('<tr><td align="center" style="padding:0 34px 26px 34px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            + "".join(_badge(n, b["width"]) for n in b["images"]) + "</tr></table></td></tr>")


# ------ system blocks: wording fixed here, data from the storm context -----------------
# These read the storm variables (multi, total, storm_dates_label ...), so they have no
# settings: a person cannot reword them, which keeps the singular/plural logic right.

_HEADLINE = (
    '<tr><td align="center" style="padding:26px 34px 6px 34px; @F@ font-size:24px; '
    'line-height:30px; font-weight:bold; color:#1b4a80;">'
    'Recent Hail {% if multi %}Storms{% else %}Storm{% endif %} Reported Near '
    '{% if total == 1 %}Your Listing{% else %}Your Listings{% endif %}</td></tr>'
    '<tr><td align="center" style="padding:0 34px 24px 34px; @F@ font-size:14px; '
    'line-height:20px; color:#64748b;">'
    'National Weather Service storm {% if multi %}reports{% else %}report{% endif %} '
    '&middot; {{ storm_dates_label }}</td></tr>').replace("@F@", _FONT)

_GREETING = (
    '<tr><td style="padding:0 34px 18px 34px; @F@ font-size:16px; line-height:24px; '
    'color:#1e293b;"><strong style="color:#1b4a80;">'
    '{% if agent_first_name %}{{ agent_first_name }},{% else %}Hello,{% endif %}'
    '</strong></td></tr>'
    '<tr><td style="padding:0 34px 24px 34px; @F@ font-size:16px; line-height:25px; '
    'color:#1e293b;">The National Weather Service logged '
    '{% if multi %}hail reports on {{ storm_dates_label }}{% else %}a hail report{% endif %} '
    'near {% if total == 1 %}your listing{% else %}<strong>{{ total }}</strong> of your '
    'listings{% endif %}.</td></tr>').replace("@F@", _FONT)


def _panel_row(label, value, colour, last=False):
    gap = "" if last else " padding-bottom:12px;"
    return ('<tr><td width="46%" style="@F@ font-size:11px; line-height:16px; '
            'letter-spacing:1.2px; text-transform:uppercase; color:#64748b;' + gap + '">'
            + label + '</td><td align="right" style="@F@ font-size:17px; line-height:20px; '
            'font-weight:bold; color:' + colour + ';' + gap + '">' + value
            + '</td></tr>').replace("@F@", _FONT)


_PANEL_RULE = ('<tr><td colspan="2" style="border-top:1px solid #dbe6f2; font-size:0; '
               'line-height:0; padding-bottom:12px;">&nbsp;</td></tr>')

_PANEL = (
    '<tr><td style="padding:0 34px 26px 34px;">'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
    'style="background-color:#f3f7fc; border-left:5px solid #c0392b;">'
    '<tr><td style="padding:22px 24px 22px 22px;">'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
    + _panel_row("Storm {% if multi %}dates{% else %}date{% endif %}",
                 "{{ storm_dates_label }}", "#1e293b") + _PANEL_RULE
    + _panel_row("{% if multi %}Largest reported hail size{% else %}Reported hail size{% endif %}",
                 "{{ largest_hail }}", "#c0392b") + _PANEL_RULE
    + _panel_row("Nearest report", "{{ nearest_miles }}", "#1e293b", last=True)
    + '</table></td></tr></table></td></tr>')


def _th(align, label):
    return ('<td' + align + ' style="padding:0 0 8px 0; @F@ font-size:11px; line-height:16px; '
            'letter-spacing:1.2px; text-transform:uppercase; color:#64748b;">'
            + label + '</td>').replace("@F@", _FONT)


_LISTINGS = (
    '<tr><td style="padding:0 34px 26px 34px;">'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
    + _th("", "{% if multi %}Listing and date{% else %}Listing{% endif %}")
    + _th(' align="center"', "{% if multi %}Hail{% else %}Largest hail{% endif %}")
    + _th(' align="right"', "Nearest report") + '</tr>'
    '{% for p in properties %}{% if multi %}'
    '<tr><td colspan="3" style="padding:12px 0 4px 0; border-top:1px solid #dbe6f2; @F@ '
    'font-size:15px; line-height:21px; font-weight:bold; color:#1e293b;">{{ p.address }}'
    '{% if p.events|length > 1 %} <span style="font-weight:normal; color:#64748b;">'
    '&middot; {{ p.events|length }} reports</span>{% endif %}</td></tr>'
    '{% for e in p.events %}'
    '<tr><td style="padding:2px 8px 6px 14px; @F@ font-size:14px; line-height:20px; '
    'color:#475569;">{{ e.date }}</td>'
    '<td align="center" style="padding:2px 8px 6px 8px; @F@ font-size:14px; line-height:20px; '
    'font-weight:bold; color:#c0392b;">{{ e.hail_size }}</td>'
    '<td align="right" style="padding:2px 0 6px 8px; @F@ font-size:14px; line-height:20px; '
    'color:#475569;">{{ e.miles }}</td></tr>'
    '{% endfor %}{% else %}'
    '<tr><td style="padding:11px 8px 11px 0; border-top:1px solid #dbe6f2; @F@ '
    'font-size:15px; line-height:21px; color:#1e293b;">{{ p.address }}</td>'
    '<td align="center" style="padding:11px 8px; border-top:1px solid #dbe6f2; @F@ '
    'font-size:15px; line-height:21px; font-weight:bold; color:#c0392b;">'
    '{{ p.events[0].hail_size }}</td>'
    '<td align="right" style="padding:11px 0 11px 8px; border-top:1px solid #dbe6f2; @F@ '
    'font-size:15px; line-height:21px; color:#1e293b;">{{ p.events[0].miles }}</td></tr>'
    '{% endif %}{% endfor %}'
    '{% if more_count %}'
    '<tr><td colspan="3" style="padding:11px 0 0 0; border-top:1px solid #dbe6f2; @F@ '
    'font-size:14px; line-height:20px; color:#64748b;">and {{ more_count }} more</td></tr>'
    '{% endif %}</table></td></tr>').replace("@F@", _FONT)


RENDERERS = {"text": _render_text, "divider": lambda b: _DIVIDER, "image": _render_image,
             "footer": _render_footer, "heading": _render_heading, "button": _render_button,
             "band": _render_band, "testimonial": _render_testimonial,
             "badges": _render_badges, "headline": lambda b: _HEADLINE,
             "greeting": lambda b: _GREETING, "storm_panel": lambda b: _PANEL,
             "listings": lambda b: _LISTINGS}


# ------ the frame ----------------------------------------
_HEAD = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="x-apple-disable-message-reformatting" />
<meta name="color-scheme" content="light only" />
<meta name="supported-color-schemes" content="light only" />
<title>@@TITLE@@</title>
<!--[if mso]>
<style type="text/css">
  body, table, td, a { font-family: Arial, Helvetica, sans-serif !important; }
</style>
<![endif]-->
</head>

<body style="margin:0; padding:0; background-color:#eef2f6; -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%;">
[[trackingImage]]
{% set multi = storm_count > 1 %}
"""

_PREHEADER_OPEN = ('<div style="display:none; font-size:1px; color:#eef2f6; line-height:1px; '
                   'max-height:0; max-width:0; opacity:0; overflow:hidden;">\n')
_PREHEADER_FILLER = "&#847;&zwnj;&nbsp;" * 20      # keeps the inbox preview from showing the body
_PREHEADER_CLOSE = "\n</div>\n"

_FRAME_OPEN = (
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
    'style="background-color:#eef2f6;">\n<tr>\n<td align="center" style="padding:24px 12px;">\n'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" '
    'style="width:600px; max-width:600px; background-color:#ffffff; border:3px solid #1f5c9e;">')
_FRAME_CLOSE = "</table>\n</td>\n</tr>\n</table>\n</body>\n</html>\n"


# ------ the template -------------------------------------

def _try(problems, fn, *args, _at=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BlockError as exc:
        problems.append((_at, str(exc)))
        return None


def clean_template(template):
    """The template with every block checked and defaults filled in.  A BlockError lists every
    problem found, not only the first."""
    if not isinstance(template, dict):
        raise BlockError("the template must be a set of settings")
    problems = []
    unknown = set(template) - {"title", "preheader", "subject", "blocks"}
    if unknown:
        problems.append((None, f"unknown setting(s) {', '.join(sorted(map(str, unknown)))}"))

    out = {}
    for key, limit, tokens in (("title", MAX_TITLE, ()), ("preheader", MAX_PREHEADER, TEXT_TOKENS)):
        value = template.get(key, "")
        if _try(problems, plain, value, key, limit, tokens) is not None:
            out[key] = value.strip()

    subject = template.get("subject")
    if (isinstance(subject, dict) and set(subject) == {"preset"}
            and isinstance(subject["preset"], str) and subject["preset"] in SUBJECT_PRESETS):
        out["subject"] = {"preset": subject["preset"]}
    elif isinstance(subject, dict) and set(subject) == {"text"}:
        done = _try(problems, plain, subject["text"], "subject", render.MAX_SUBJECT,
                    TEXT_TOKENS, subject=True)
        if done is not None and not done:
            problems.append((None, "subject is empty"))
        elif done:
            out["subject"] = {"text": subject["text"].strip()}
    else:
        problems.append((None, "subject must be a preset or some text"))

    blocks = template.get("blocks")
    cleaned = []
    if not isinstance(blocks, list) or not blocks:
        problems.append((None, "the template needs at least one block"))
    elif len(blocks) > MAX_BLOCKS:
        problems.append((None, f"a template is limited to {MAX_BLOCKS} blocks"))
    else:
        for i, block in enumerate(blocks, 1):
            done = _try(problems, clean_block, block, f"block {i}", _at=i)
            if done is not None:
                cleaned.append(done)
        kinds = [b["kind"] for b in cleaned]
        if len(cleaned) == len(blocks) and (kinds.count("footer") != 1 or kinds[-1] != "footer"):
            problems.append((None, "the footer block is required, once, and must be last"))
    if problems:
        raise BlockError("; ".join(message for _, message in problems), problems)
    out["blocks"] = cleaned
    return out


def images_used(template):
    """The names of the email images a block template refers to, as a set.

    Reads a template as saved, so it tolerates anything:  used to stop an image being
    retired while a template needs it."""
    used = set()
    blocks = template.get("blocks") if isinstance(template, dict) else None
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("kind") == "image" and isinstance(block.get("image"), str):
            used.add(block["image"])
        elif block.get("kind") == "badges":
            names = block.get("images", SPECS["badges"]["images"][0])
            if isinstance(names, list):
                used.update(n for n in names if isinstance(n, str))
    return used


def compile_blocks(template):
    """(subject_src, body_src) for templatestore and render.  BlockError if anything is wrong,
    including anything render.validate_template refuses."""
    t = clean_template(template)
    if "preset" in t["subject"]:
        subject = SUBJECT_PRESETS[t["subject"]["preset"]]
    else:
        subject = plain(t["subject"]["text"], "subject", render.MAX_SUBJECT, TEXT_TOKENS,
                        subject=True)
    parts = [_HEAD.replace("@@TITLE@@", plain(t["title"], "title", MAX_TITLE))]
    preheader = plain(t["preheader"], "preheader", MAX_PREHEADER, TEXT_TOKENS)
    if preheader:
        parts.append(_PREHEADER_OPEN + preheader + " " + _PREHEADER_FILLER + _PREHEADER_CLOSE)
    parts.append(_FRAME_OPEN)
    parts.extend(RENDERERS[b["kind"]](b) for b in t["blocks"])
    parts.append(_FRAME_CLOSE)
    body = "\n".join(parts)
    if len(body.encode("utf-8")) > MAX_HTML:
        raise BlockError(f"the template is too large (over {MAX_HTML // 1000} KB)")
    try:
        render.validate_template(subject, body)
    except render.RenderError as exc:
        raise BlockError(str(exc)) from None
    return subject, body


# ------ the built-in template, as blocks --------------------------------
# The same email hail_alert.html produces.  tests/test_email_blocks.py compiles this and
# requires the same output as the shipped file, so a change to either shows up there.

HAIL_ALERT_BLOCKS = {
    "title": "Hail reported near your listing",
    "preheader": "{largest_hail} hail, {nearest_miles} away, on {storm_dates_label}.",
    "subject": {"preset": "hail_alert"},
    "blocks": [
        {"kind": "image", "image": "logo", "placement": "header"},
        {"kind": "divider"},
        {"kind": "headline"},
        {"kind": "greeting"},
        {"kind": "storm_panel"},
        {"kind": "listings"},
        {"kind": "text", "space_after": 20,
         "text": "This came from a public weather record, and while it\u2019s not "
                 "confirmation that the roof was damaged, it may be worth having a licensed "
                 "roofer come out to check for damage."},
        {"kind": "text", "color": "navy", "bold": True, "space_after": 26,
         "text": "Find out now, while you have time to deal with it."},
        {"kind": "button", "label": "SCHEDULE A FREE INSPECTION",
         "url": "https://www.roofbrokersinc.com/Account/LoginRegister"},
        {"kind": "text", "size": 15, "color": "slate", "align": "center", "space_after": 32,
         "text": "Already have an account? "
                 "[link={schedule_url}]Schedule a new inspection[/link]"},
        {"kind": "band", "title": "Most of These Are Small",
         "intro": "[b]80% of homes are certifiable as-is[/b] or with minor repair work, and "
                  "most hail repairs come in under the insurance deductible.",
         "items": ["[b]$35 Five-Year Certification[/b] once it passes",
                   "Minimum repairs to certify \u2014 we fix what\u2019s necessary, nothing more",
                   "Rapid-response reporting",
                   "Rush repair scheduling",
                   "Multiple vendor estimates",
                   "Pay out of closing costs"]},
        {"kind": "heading", "text": "Why Realtors Use Roof Brokers", "size": 19,
         "space_before": 30, "space_after": 4},
        {"kind": "testimonial", "who": "Jeff G., Yelp",
         "quote": "I\u2019ve referred Roof Brokers many times over the years, often numerous "
                  "times per year when we have had hail storms. They have always been "
                  "reliable, reasonable and professional. They do what they say they\u2019ll "
                  "do, often when there is a time crunch.",
         "more_label": "See more reviews",
         "more_url": "https://birdeye.com/roof-brokers-inc-145262047197938?rsource=2#reviews"},
        {"kind": "divider"},
        {"kind": "text", "size": 14, "color": "slate", "align": "center",
         "space_before": 22, "space_after": 20,
         "text": "Roof Brokers has certified roofs for Denver Metro real estate agents "
                 "[b=navy]since 1992[/b]."},
        {"kind": "badges"},
        {"kind": "footer"},
    ],
}