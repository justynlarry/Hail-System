"""A preview of a draft template (item 203, piece 4): compile it, render it with stand-in storm
data, and report everything wrong with it.  Pure: the caller supplies the EmailSettings (which
carry the images).  Never raises, and never saves or sends anything.
"""

import re

from hailsys.email import blocks, render, samples

# Gmail cuts a message off at about this many bytes and hides the rest behind "View entire message".
GMAIL_CLIP = 102_000


def _fail(problems):
    return {"ok": False, "subject": None, "html": None, "size": None, "problems": problems,
            "warnings": []}


def _missing_images(cleaned, settings):
    """A problem for each block that names an image that is not available, with the image named
    (the render itself would only say UndefinedError)."""
    problems = []
    for number, block in enumerate(cleaned["blocks"], 1):
        for name in sorted(blocks.images_used({"blocks": [block]})):
            if name not in settings.images:
                problems.append((number, f"The image {name} is not available.  Add it under "
                                         "Email images on the admin page, or it has been retired."))
    return problems


# The marks compile_blocks(mark=True) adds; escaped text cannot contain one (quotes are escaped).
_MARKS = re.compile(r' data-block="\d+"')


def build_preview(template, settings, situation=samples.DEFAULT, mark_blocks=False):
    """{'ok', 'subject', 'html', 'size', 'problems', 'warnings'}.

    mark_blocks=True is for the on-screen editor only (item 294, slice 7):  each block's rows carry
    data-block="N" so a click can open the block.  It is off by default because testsend.py sends
    this same html to the admin as a test email, which must be the email exactly.  'size' and the
    Gmail warning are always measured without the marks.

    'problems' is a list of (block number from 1 or None, message); when there are any, there is
    no subject or html.  'size' is the rendered email in bytes; 'warnings' are things that still
    render but will cause trouble (a message Gmail would clip)."""
    try:
        cleaned = blocks.clean_template(template)
        blocks.check_required(cleaned)              # shown here, refused at save (item 294, slice 5)
    except blocks.BlockError as exc:
        return _fail(exc.problems)
    except Exception:                       # a draft is untrusted: never let it raise
        return _fail([(None, "the template could not be read")])
    missing = _missing_images(cleaned, settings)
    if missing:
        return _fail(missing)
    try:
        subject_src, body_src = blocks.compile_blocks(cleaned, mark=mark_blocks)
        agent, matches = samples.situation(situation)
        context = render.build_context(agent, matches, settings)
        subject, html = render.render(subject_src, body_src, context)
    except blocks.BlockError as exc:
        return _fail(exc.problems)
    except render.RenderError as exc:
        return _fail([(None, str(exc))])
    size = len(_MARKS.sub("", html).encode("utf-8"))
    warnings = []
    if size > GMAIL_CLIP:
        warnings.append(f"This situation renders to {size // 1000} KB.  Gmail clips messages "
                        f"over about {GMAIL_CLIP // 1000} KB and hides the rest.")
    return {"ok": True, "subject": subject, "html": html, "size": size, "problems": [],
            "warnings": warnings}