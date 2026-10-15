"""Template editor pages and actions (item 203, piece 4).  Admin only.

The list, a new template (the built-in as a starting draft) and a saved block template, each
with a preview made from stand-in storm data.  The editor form posts to `act`, which applies one
action (add, move, remove, preview) to the draft in the form and shows the result.  Nothing is
saved here.
"""

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template, request, session,
                   url_for)

from hailsys.db import get_connection
from hailsys.email import (blockform, blocks, emailconfig, preview, render, samples,
                           templatestore, testsend)
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


def _my_address():
    """The signed-in admin's email address, shown on the test button (and read again, from the
    database, by testsend: nothing posted is ever used as the recipient)."""
    with get_connection() as conn:
        row = conn.execute("SELECT emp_email FROM users WHERE emp_id = %s",
                           (session["emp_id"],)).fetchone()
        conn.rollback()
    return row["emp_email"] if row else None


def _context(draft, name, is_new, situation, notice=None, test_notice=None, unsaved=False):
    """Everything the editor page and its partials show."""
    shown = _preview(draft, situation)
    return {"name": name, "is_new": is_new, "title": draft.get("title", ""),
            "preheader": draft.get("preheader", ""), "subject": blockform.subject_form(draft),
            "block_views": blockform.describe(draft, shown["problems"]), "preview": shown,
            "situations": samples.SITUATIONS, "situation": situation,
            "addable": blockform.ADDABLE, "notice": notice, "test_notice": test_notice,
            "test_address": _my_address(), "unsaved": unsaved}


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

def _save(draft, name, is_new):
    """(a redirect, None) after a save that worked, or (None, a notice) when it was refused."""
    try:
        blocks.clean_template(draft)
    except blocks.BlockError:
        return None, "Not saved.  Fix the problems marked below."
    with get_connection() as conn:
        try:
            before = None if is_new else templatestore.active_id(conn, name)
            saved = templatestore.save_blocks(conn, created_by=session["emp_id"], name=name,
                                              template=draft, must_be_new=is_new)
        except templatestore.TemplateRefused as exc:
            conn.rollback()
            return None, str(exc)
        conn.commit()
    if is_new:
        flash(f"Created {name}.")
    elif saved == before:
        flash(f"No changes: {name} is already saved as it is.")
    else:
        flash(f"Saved {name} as a new version.")
    return redirect(url_for("emailtpl.edit", name=name)), None


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
    request, JSON with the new block list, the new preview and the problems (or, after a save
    that worked, where to go)."""
    draft = blockform.parse_form(request.form)
    action = request.form.get("action", "")
    name = request.form.get("name", "")
    is_new = request.form.get("mode") == "new"
    fetch = request.headers.get("X-Requested-With") == "fetch"
    done, notice = draft, None
    if action == "save":
        saved, notice = _save(draft, name, is_new)
        if saved is not None:
            return jsonify(redirect=saved.location) if fetch else saved
    else:
        done, notice = _apply(draft, action, request.form.get("add_kind", ""))
    context = _context(done, name, is_new, _situation(request.form.get("situation")), notice,
                       unsaved=True)
    if fetch:
        return jsonify(blocks=render_template("_editor_blocks.html", **context),
                       preview=render_template("_editor_preview.html", **context),
                       problems=[[number, message] for number, message in
                                 context["preview"]["problems"]])
    return render_template("template_edit.html", **context)


@emailtpl_bp.route("/retire/<name>")
def retire_confirm(name):
    with get_connection() as conn:
        row = templatelist.fetch_one(conn, name)
        conn.rollback()
    if row is None or name.lower() == templatestore.TEMPLATE_NAME:
        abort(404)
    return render_template("template_retire.html", row=row, display_tz=DISPLAY_TZ)


@emailtpl_bp.route("/retire/<name>", methods=["POST"])
def retire(name):
    with get_connection() as conn:
        try:
            templatestore.retire(conn, name)
        except templatestore.TemplateNotFound:
            conn.rollback()
            abort(404)
        except templatestore.TemplateRefused as exc:
            conn.rollback()
            flash(str(exc))
            return redirect(url_for("emailtpl.index"))
        conn.commit()
    flash(f"Retired {name}.  It is no longer on the Send page; emails already sent are "
          "unchanged.")
    return redirect(url_for("emailtpl.index"))


def _test_notice(result):
    """What the page says after a test send."""
    where, detail = result["recipient"], result["detail"] or ""
    if result["outcome"] == "accepted":
        text = (f"Test sent to {where}.  Constant Contact accepted it, which is not the same as "
                "delivered: check your inbox and your spam folder.")
        return f"{text} {detail}".strip() if detail else text
    if result["outcome"] == "unknown":
        return (f"The test to {where} may or may not have been sent.  {detail}  Check your inbox "
                "before trying again: a test is never retried automatically.")
    return f"The test to {where} was not sent.  {detail}"


@emailtpl_bp.route("/test", methods=["POST"])
def send_test_email():
    """Send the draft in the form to the signed-in admin's own address.  The page comes
    back with the draft as it was and the result under the button."""
    draft = blockform.parse_form(request.form)
    name = request.form.get("name", "")
    is_new = request.form.get("mode") == "new"
    situation = _situation(request.form.get("situation"))
    try:
        with get_connection() as conn:
            settings = emailconfig.load_settings(conn)
            sender = emailconfig.load_sender(conn)
            result = testsend.send_test(
                conn, user_id=session["emp_id"], template=draft, template_name=name or None,
                situation=situation, settings=settings, sender=sender)
        notice = _test_notice(result)
    except testsend.TestSendRefused as exc:
        notice = str(exc)
    except render.SettingsError as exc:
        notice = f"Sending is not set up: {exc}"
    context = _context(draft, name, is_new, situation, test_notice=notice, unsaved=True)
    return render_template("template_edit.html", **context)
