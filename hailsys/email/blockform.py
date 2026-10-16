"""The template editor's form: what each block's settings look like, and how a
posted form becomes a draft again.

No Flask/Database.  A draft is the dict blocks.clean_template takes.  Form does not
decide what is valid, just carries values, clean_template decides validity.  A value
that cannot be read (a number box holding words) is passed on as typed, so the same
check that guards a save reports it.

Form names: title, preheader, subject_mode ('preset' or 'text'), subject_preset, subject_text,
n_blocks, and for block i (counted from 0) b<i>_kind and b<i>_<setting>.
"""

import copy
import re

from hailsys.email import blocks, render, samples

# One more than allowed, so a form that's too long is reported.
MAX_FORM_BLOCKS = blocks.MAX_BLOCKS + 1

KIND_LABELS = {
    "text": "Paragraph", "heading": "Heading", "image": "Image", "button": "Button",
    "divider": "Divider line", "band": "Highlight band with checklist",
    "testimonial": "Testimonial", "badges": "Badges (images in a row)",
    "headline": "Storm headline", "greeting": "Greeting and lede",
    "storm_panel": "Storm data panel", "listings": "Listings table",
    "footer": "Footer",
}

# Wording fixed in code, filled in from the storm data.
SYSTEM_KINDS = {"headline", "greeting", "storm_panel", "listings"}

RICH_HELP = "Formatting and merge fields: see the guide at the top of the page."
_SPACE = "Pixels, 0 to 40, in steps of 2"

PRESET_LABELS = {
    "hail_alert": "Hail alert (the wording follows the number of storms and listings)",
}


# The guide at the top of the editor.  The tag examples are compiled by a test, so the guide
# cannot describe a tag the compiler would refuse.
TAG_GUIDE = [
    ("[b]text[/b]", "Bold"),
    ("[i]text[/i]", "Italic"),
    ("[b=navy]text[/b]", "Bold, in a colour from the list"),
    ("[color=red]text[/color]", "A colour from the list"),
    ("[link=https://example.com]text[/link]", "A link; the address must start with https://"),
    ("[link={schedule_url}]text[/link]", "A link to the schedule address set on the admin page"),
]

# One entry for every merge field blocks.TEXT_TOKENS allows (a test keeps the two in step).
MERGE_MEANINGS = {
    "total": "How many of the agent's listings were hit",
    "storm_dates_label": "The storm date, or dates, in words",
    "largest_hail": "The largest hail size reported",
    "nearest_miles": "How far the nearest report was",
}

_LEGEND_SETTINGS = render.EmailSettings(
    "https://example.test/logo.png", "https://example.test/cra.png",
    "https://example.test/bbb.png", "legend@example.test", "https://example.test/schedule")


def legend(situation=samples.DEFAULT):
    """What the guide shows: the tags, the palette, and each merge field with the value it takes
    in the chosen sample storm."""
    agent, matches = samples.situation(situation)
    context = render.build_context(agent, matches, _LEGEND_SETTINGS)
    return {
        "tags": [{"code": code, "meaning": meaning} for code, meaning in TAG_GUIDE],
        "colours": [{"name": name, "hex": code} for name, code in blocks.PALETTE.items()],
        "merge_fields": [{"token": "{" + name + "}", "meaning": MERGE_MEANINGS[name],
                          "example": str(context[name])}
                         for name in sorted(blocks.TEXT_TOKENS)]}


def _f(key, label, widget, choices=None, help="", rich=False):
    """One setting as the form shows it.  'rich' marks text that takes the inline formatting
    tags (the editor puts a toolbar on it)."""
    return {"key": key, "label": label, "widget": widget, "choices": choices, "help": help,
            "rich": rich}


FIELDS = {
    "text": [
        _f("text", "Text", "textarea", help=RICH_HELP, rich=True),
        _f("size", "Size", "select", list(blocks.SIZES)),
        _f("color", "Colour", "select", list(blocks.PALETTE)),
        _f("bold", "Bold", "checkbox"),
        _f("italic", "Italic", "checkbox"),
        _f("align", "Align", "select", ["left", "center"]),
        _f("space_before", "Space above", "number", help=_SPACE),
        _f("space_after", "Space below", "number", help=_SPACE),
    ],
    "heading": [
        _f("text", "Text", "text", help=RICH_HELP, rich=True),
        _f("size", "Size", "select", list(blocks.HEADING_SIZES)),
        _f("color", "Colour", "select", list(blocks.PALETTE)),
        _f("align", "Align", "select", ["left", "center"]),
        _f("space_before", "Space above", "number", help=_SPACE),
        _f("space_after", "Space below", "number", help=_SPACE),
    ],
    "image": [
        _f("image", "Image name", "text", help="A name from Email images on the admin page"),
        _f("width", "Width", "number", help="Pixels, 40 to 560, in steps of 2"),
        _f("placement", "Placement", "select", ["header", "body"]),
    ],
    "button": [
        _f("label", "Label", "text"),
        _f("url", "Address", "text", help="https://... or {schedule_url}"),
        _f("color", "Colour", "select", list(blocks.PALETTE)),
        _f("space_before", "Space above", "number", help=_SPACE),
        _f("space_after", "Space below", "number", help=_SPACE),
    ],
    "divider": [],
    "band": [
        _f("title", "Title", "text"),
        _f("intro", "Introduction", "textarea", help=RICH_HELP, rich=True),
        _f("items", "Checklist, one item per line", "lines", help="1 to 10 items; formatting works on each line", rich=True),
    ],
    "testimonial": [
        _f("quote", "Quote", "textarea"),
        _f("who", "Who said it", "text"),
        _f("stars", "Stars", "number", help="0 to 5"),
        _f("more_label", "Link label", "text", help="Optional, with the address below"),
        _f("more_url", "Link address", "text", help="https://... or {schedule_url}"),
    ],
    "badges": [
        _f("images", "Image names, one per line", "lines", help="1 to 4 names"),
        _f("width", "Width", "number", help="Pixels, 40 to 200, in steps of 2"),
    ],
    "headline": [], "greeting": [], "storm_panel": [], "listings": [],
    "footer": [_f("phone", "Phone", "text", help="Like (303) 750-1900")],
}

# What a block starts as when it is added: the settings that have no default.
STARTERS = {
    "text": {"text": "New paragraph"},
    "heading": {"text": "New heading"},
    "image": {"image": "logo"},
    "button": {"label": "Schedule an inspection", "url": "{schedule_url}"},
    "band": {"title": "Title", "intro": "Introduction", "items": ["First item"]},
    "testimonial": {"quote": "A quote", "who": "Someone"},
}

ADDABLE = [(kind, KIND_LABELS[kind]) for kind in FIELDS if kind != "footer"]


def new_block(kind):
    """A block of this kind with every setting at its default."""
    block = {"kind": kind}
    for key, (default, _) in blocks.SPECS[kind].items():
        if default is not None:
            block[key] = copy.deepcopy(default)
    block.update(copy.deepcopy(STARTERS.get(kind, {})))
    return block


def starter():
    """The draft a new template starts from:  Built-in template, defaults filled in."""
    return blocks.clean_template(copy.deepcopy(blocks.HAIL_ALERT_BLOCKS))


# ------ form -> draft --------------------------------------------------------------

def _read(field, raw):
    widget = field["widget"]
    if widget == "select":
        for choice in field["choices"]:
            if str(choice) == raw:
                return choice
        return raw
    if widget == "number":
        return int(raw) if re.fullmatch(r"-?\d{1,9}", raw.strip()) else raw
    if widget == "lines":
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return raw


def parse_form(form):
    """A draft from a posted form (anything with .get).  Never raises, unreadable
    values are passed on as typed for clean_template to refuse."""
    try:
        count = max(0, min(int(form.get("n_blocks", "0")), MAX_FORM_BLOCKS))
    except ValueError:
        count = 0
    parsed = []
    for i in range(count):
        kind = form.get(f"b{i}_kind", "")
        block = {"kind": kind}
        for field in FIELDS.get(kind, []):
            raw = form.get(f"b{i}_{field['key']}")
            if field["widget"] == "checkbox":
                block[field["key"]] = raw is not None
            elif raw is not None:
                block[field["key"]] = _read(field, raw)
        parsed.append(block)
    if form.get("subject_mode") == "preset":
        subject = {"preset": form.get("subject_preset", "")}
    else:
        subject = {"text": form.get("subject_text", "")}
    return {"title": form.get("title", ""), "preheader": form.get("preheader", ""),
            "subject": subject, "blocks": parsed}


# ------ draft -> what the page shows ------------------------------------------------

def fields_for(block, index):
    """The block's settings as the page shows them:  the field description plus its form name
    and current value (a default when the draft has none)."""
    kind = block.get("kind")
    spec = blocks.SPECS.get(kind, {})
    shown = []
    for field in FIELDS.get(kind, []):
        default = spec[field["key"]][0] if field["key"] in spec else None
        value = block.get(field["key"], default)
        item = dict(field, name=f"b{index}_{field['key']}")
        if field["widget"] == "checkbox":
            item["checked"] = value is True
        elif field["widget"] == "lines" and isinstance(value, list):
            item["value"] = "\n".join(str(v) for v in value)
        else:
            item["value"] = "" if value is None else str(value)
        shown.append(item)
    return shown


def subject_form(template):
    """The subject as the page shows it:  mode, the chosen preset and the typed text."""
    subject = template.get("subject") if isinstance(template, dict) else None
    subject = subject if isinstance(subject, dict) else {}
    return {"mode": "preset" if "preset" in subject else "text",
            "preset": subject.get("preset", next(iter(blocks.SUBJECT_PRESETS))),
            "text": subject.get("text", ""), "presets": list(blocks.SUBJECT_PRESETS),
            "labels": PRESET_LABELS}


# ------ changing a draft --------------------------------------------------------------
# Each returns a changed copy and leaves the draft it is given alone.  The footer stays last,
# blocks are added above it, and neither it nor a block next to it moves across it.

def _is_footer(block):
    return isinstance(block, dict) and block.get("kind") == "footer"


def add_block(template, kind):
    done = copy.deepcopy(template)
    block_list = done["blocks"]
    if kind not in FIELDS or kind == "footer" or len(block_list) >= blocks.MAX_BLOCKS:
        return done
    at = len(block_list) - 1 if block_list and _is_footer(block_list[-1]) else len(block_list)
    block_list.insert(at, new_block(kind))
    return done


def _can_move(block_list, index, step):
    other = index + step
    return (step in (-1, 1) and 0 <= index < len(block_list) and 0 <= other < len(block_list)
            and not _is_footer(block_list[index]) and not _is_footer(block_list[other]))


def move_block(template, index, step):
    """Move the block at 'index' (from 0) one place: step -1 up, +1 down."""
    done = copy.deepcopy(template)
    block_list = done["blocks"]
    if _can_move(block_list, index, step):
        other = index + step
        block_list[index], block_list[other] = block_list[other], block_list[index]
    return done


def remove_block(template, index):
    done = copy.deepcopy(template)
    block_list = done["blocks"]
    if 0 <= index < len(block_list) and not _is_footer(block_list[index]):
        del block_list[index]
    return done


def _summary(block):
    """The first words of a block's own text, for its heading, so a long editor can be
    scanned."""
    for key in ("text", "label", "title", "image", "who", "phone"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            value = blocks._TAG.sub("", value).strip()
            return value[:50] + ("..." if len(value) > 50 else "")
    images = block.get("images")
    if isinstance(images, list) and images:
        return ", ".join(str(name) for name in images)[:50]
    return ""


def describe(template, problems=()):
    """The blocks as the page shows them, in order: label, fields, the problems that belong
    to the block ('problems' is a list of (block number, message)) and whether it can move."""
    by_number = {}
    for number, message in problems:
        by_number.setdefault(number, []).append(message)
    block_list = template.get("blocks") if isinstance(template, dict) else None
    block_list = block_list if isinstance(block_list, list) else []
    shown = []
    for i, block in enumerate(block_list):
        block = block if isinstance(block, dict) else {}
        kind = block.get("kind")
        shown.append({
            "index": i, "number": i + 1, "kind": kind or "",
            "label": KIND_LABELS.get(kind, "Unknown block"),
            "summary": _summary(block),
            "system": kind in SYSTEM_KINDS, "footer": kind == "footer",
            "fields": fields_for(block, i), "problems": by_number.get(i + 1, []),
            "can_up": _can_move(block_list, i, -1), "can_down": _can_move(block_list, i, 1)})
    return shown
