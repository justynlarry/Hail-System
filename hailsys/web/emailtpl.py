"""Template editor pages and actions (item 203, piece 4).  Admin only.

The list, a new template (the built-in as a starting draft) and a saved block template, each
with a preview made from stand-in storm data.  The editor form posts to `act`, which applies one
action (add, move, remove, preview) to the draft in the form and shows the result.  Nothing is
saved here.
"""

from flask import Blueprint, abort, jsonify, render_template, request

from hailsys.db import get_connection
from hailsys.email import (blockform, blocks, emailconfig, preview, render, samples,
                           templatestore)
from hailsys.queries import templatelist
from hailsys.tuning import DISPLAY_TZ
from hailsys.web.auth import require_role

emailtpl_bp = Blueprint("emailtpl", __name__, url_prefix="/templates")


@emailtpl_bp.before_request
def _admin_only():
    return require_role("admin")


def _no_preview(message):
    return {"ok": False, "subject": None, "html": None, "size": None, "warnings": [],
            "problems": [(None, message)]}


def _preview(draft, situation):
    """The preview with the real images, or the reason there is none."""
    try:
        with get_connection() as conn:
            settings = emailconfig.load_settings(conn)
            conn.rollback()
    except render.SettingsError as exc:
        return _no_preview(str(exc))
    return preview.build_preview(draft, settings, situation)


def _situation(raw):
    return raw if raw in samples.SITUATIONS else samples.DEFAULT


def _context(draft, name, is_new, situation, notice=None):
    """Everything the editor page and its partials show."""
    shown = _preview(draft, situation)
    return {"name": name, "is_new": is_new, "title": draft.get("title", ""),
            "preheader": draft.get("preheader", ""), "subject": blockform.subject_form(draft),
            "block_views": blockform.describe(draft, shown["problems"]), "preview": shown,
            "situations": samples.SITUATIONS, "situation": situation,
            "addable": blockform.ADDABLE, "notice": notice}


def _editor(draft, name, is_new):
    context = _context(draft, name, is_new, _situation(request.args.get("situation")))
    return render_template("template_edit.html", **context)


def _apply(draft, action, add_kind):
    """(the draft after the action, a notice to show or None).  An action that is not
    understood, or cannot be done, changes nothing."""
    verb, _, arg = action.partition(":")
    if verb == "add":
        if add_kind not in dict(blockform.ADDABLE):
            return draft, "Pick a kind of block to add."
        done = blockform.add_block(draft, add_kind)
        if len(done["blocks"]) == len(draft["blocks"]):
            return draft, f"A template is limited to {blocks.MAX_BLOCKS} blocks."
        return done, None
    if verb in ("up", "down", "remove") and arg.isascii() and arg.isdigit():
        index = int(arg)
        if verb == "remove":
            return blockform.remove_block(draft, index), None
        return blockform.move_block(draft, index, -1 if verb == "up" else 1), None
    return draft, None


@emailtpl_bp.route("/")
def index():
    with get_connection() as conn:
        rows = templatelist.fetch_all(conn)
        conn.rollback()
    if not any(r["template_name"] == templatestore.TEMPLATE_NAME for r in rows):
        # Not saved yet, first send saves it from its files.
        rows.insert(0, {"template_name": templatestore.TEMPLATE_NAME, "description": None,
                        "versions": 0, "created_at": None, "emp_fname": None, "emp_lname": None,
                        "last_used": None, "sends": 0, "has_blocks": False})
    return render_template("template_list.html", rows=rows, display_tz=DISPLAY_TZ,
                           builtin_name=templatestore.TEMPLATE_NAME)


@emailtpl_bp.route("/new")
def new():
    return _editor(blockform.starter(), "", True)


@emailtpl_bp.route("/edit/<name>")
def edit(name):
    with get_connection() as conn:
        try:
            draft = templatestore.load_blocks(conn, name)
        except templatestore.TemplateNotFound:
            abort(404)
        finally:
            conn.rollback()
    if draft is None:
        return render_template("template_notblocks.html", name=name)
    return _editor(draft, name, False)


@emailtpl_bp.route("/edit", methods=["POST"])
def act():
    """One editor action on the draft in the form.  A page for a plain request; for a `fetch`
    request, JSON with the new block list, the new preview and the problems."""
    draft = blockform.parse_form(request.form)
    done, notice = _apply(draft, request.form.get("action", ""), request.form.get("add_kind", ""))
    context = _context(done, request.form.get("name", ""), request.form.get("mode") == "new",
                       _situation(request.form.get("situation")), notice)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(blocks=render_template("_editor_blocks.html", **context),
                       preview=render_template("_editor_preview.html", **context),
                       problems=[[number, message] for number, message in
                                 context["preview"]["problems"]])
    return render_template("template_edit.html", **context)

