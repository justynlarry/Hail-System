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
    """A template or block is not valid, message says where."""


# ------ text --------------------------

def _literal(text):
    """Static text for the body source: escaped, with braces as character references."""
    return html.escape(text, quote=True).replace("{", "&#123;").replace("}", "&#125;")


def _squash(text, limit, where):
    if not isinstance(text, str):
        raise BlockError(f"{where} must be text")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        raise BlockError(f"{where} is limited to {limit} characters")
    if "[[" in text:
        raise BlockError(f"{where} may not contain double square brackets")
    return text


def _fields(text, where, allowed):
    """[("text", s) | ("field", name), ...]. Braces are only for merge fields."""
    parts, pos = [], 0
    for m in _TOKEN.finditer(text):
        if m.group(1) not in allowed:
            raise BlockError(f"{where}: {{{m.group(1)}}} is not a merge field you can use here")
        parts.append(("text", text[pos:m.start()]))
        parts.append(("field", m.group(1)))
        pos = m.end()
    parts.append(("text", text[pos:]))
    for kind, value in parts:
        if kind == "text" and ("{" in value or "}" in value):
            raise BlockError(f"{where} curly braces are only for merge fields")
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
    out, stack, pos = [], [], 0

    def emit(chunk):
        for kind, value in _fields(chunk, where, TEXT_TOKENS):
            out.append(_literal(value) if kind == "text" else "{{ " + value + " }}")

    for m in _TAG.finditer(text):
        emit(text[pos:m.start()])
        pos = m.end()
        closing, name, value = m.group(1) == "/", m.group(2), m.group(3)
        if closing:
            if value is not None or not stack or stack[-1] != name:
                raise BlockError(f"{where}:[/{name}] does not match an open tag")
            stack.pop()
            out.append(_CLOSE[name])
        else:
            if len(stack) >= MAX_NESTING:
                raise BlockError(F"{where}: tags are nested more than {MAX_NESTING} deep")
            out.append(_open(name, value, stack, where))
            stack.append(name)
    emit(text[pos:])
    if stack:
        raise BlockError(f"{where}: [{stack[-1]}] is never closed")
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


RENDERERS = {"text": _render_text, "divider": lambda b: _DIVIDER,
             "image": _render_image, "footer": _render_footer}


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

def _try(problems, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BlockError as exc:
        problems.append(str(exc))
        return None


def clean_template(template):
    """The template with every block checked and defaults filled in.  A BlockError lists every
    problem found, not only the first."""
    if not isinstance(template, dict):
        raise BlockError("the template must be a set of settings")
    problems = []
    unknown = set(template) - {"title", "preheader", "subject", "blocks"}
    if unknown:
        problems.append(f"unknown setting(s) {', '.join(sorted(map(str, unknown)))}")

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
            problems.append("subject is empty")
        elif done:
            out["subject"] = {"text": subject["text"].strip()}
    else:
        problems.append("subject must be a preset or some text")

    blocks = template.get("blocks")
    cleaned = []
    if not isinstance(blocks, list) or not blocks:
        problems.append("the template needs at least one block")
    elif len(blocks) > MAX_BLOCKS:
        problems.append(f"a template is limited to {MAX_BLOCKS} blocks")
    else:
        for i, block in enumerate(blocks, 1):
            done = _try(problems, clean_block, block, f"block {i}")
            if done is not None:
                cleaned.append(done)
        kinds = [b["kind"] for b in cleaned]
        if len(cleaned) == len(blocks) and (kinds.count("footer") != 1 or kinds[-1] != "footer"):
            problems.append("the footer block is required, once, and must be last")
    if problems:
        raise BlockError("; ".join(problems))
    out["blocks"] = cleaned
    return out

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