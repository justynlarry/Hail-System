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

from hailsys.email import blocks

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

FORMAT_HELP = ("Formatting: [b]bold[/b], [i]italic[/i], [b=navy]bold in a colour[/b], "
               "[color=red]a colour[/color], [link=https://...]a link[/link], "
               "[link={schedule_url}]the schedule link[/link].  Merge fields: "
               + ", ".join("{" + t + "}" for t in sorted(blocks.TEXT_TOKENS)) + ".")
_SPACE = "Pixels, 0 to 40, in steps of 2"


def _f(key, label, widget, choices=None, help=""):
    return {"key": key, "label": label, "widget": widget, "choices": choices, "help": help}


FIELDS = {
    "text": [
        _f("text", "Text", "textarea", help=FORMAT_HELP),
        _f("size", "Size", "select", list(blocks.SIZES)),
        _f("color", "Colour", "select", list(blocks.PALETTE)),
        _f("bold", "Bold", "checkbox"),
        _f("italic", "Italic", "checkbox"),
        _f("align", "Align", "select", ["left", "center"]),
        _f("space_before", "Space above", "number", help=_SPACE),
        _f("space_after", "Space below", "number", help=_SPACE),
    ],
    "heading": [
        _f("text", "Text", "text", help=FORMAT_HELP),
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
        _f("intro", "Introduction", "textarea", help=FORMAT_HELP),
        _f("items", "Checklist, one item per line", "lines", help="1 to 10 items"),
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
            "text": subject.get("text", ""), "presets": list(blocks.SUBJECT_PRESETS)}


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


def move_block(template, index, step):
    """Move the block at 'index' (from 0) one place: step -1 up, +1 down."""
    done = copy.deepcopy(template)
    block_list = done["blocks"]
    other = index + step
    if (step in (-1, 1) and 0 <= index < len(block_list) and 0 <= other < len(block_list)
            and not _is_footer(block_list[index]) and not _is_footer(block_list[other])):
        block_list[index], block_list[other] = block_list[other], block_list[index]
    return done


def remove_block(template, index):
    done = copy.deepcopy(template)
    block_list = done["blocks"]
    if 0 <= index < len(block_list) and not _is_footer(block_list[index]):
        del block_list[index]
    return done
