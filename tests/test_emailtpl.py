"""Tests for the template editor pages (hailsys/web/emailtpl.py, item 203, piece 4.3).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_emailtpl

Read-only pages.  The database is only used to find an admin user; the template list, the saved
blocks and the email settings are replaced, so nothing here depends on or changes what is saved.
"""

import contextlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import blockform, render, samples, templatestore
    from hailsys.web import create_app, emailtpl
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

WHEN = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)


def row(name, has_blocks=True, **over):
    base = {"template_id": 1, "template_name": name, "description": "A note", "subject": "s",
            "body": "b", "has_blocks": has_blocks, "versions": 2, "created_at": WHEN,
            "emp_fname": "Ann", "emp_lname": "Admin", "last_used": WHEN, "sends": 3}
    base.update(over)
    return base


def settings():
    images = {"logo": {"url": "https://x.test/l.png", "alt": "Logo"},
              "badge_cra": {"url": "https://x.test/c.png", "alt": "CRA"},
              "badge_bbb": {"url": "https://x.test/b.png", "alt": "BBB"}}
    return render.EmailSettings("https://x.test/l.png", "https://x.test/c.png",
                                "https://x.test/b.png", "e@x.test", "https://x.test/s",
                                images=images)


def blockform_form(draft):
    """The values a browser would post for this draft (every field as a string)."""
    values = {"title": draft["title"], "preheader": draft["preheader"],
              "n_blocks": str(len(draft["blocks"]))}
    sub = blockform.subject_form(draft)
    values.update(subject_mode=sub["mode"], subject_preset=sub["preset"], subject_text=sub["text"])
    for i, block in enumerate(draft["blocks"]):
        values[f"b{i}_kind"] = block["kind"]
        for field in blockform.fields_for(block, i):
            if field["widget"] == "checkbox":
                if field["checked"]:
                    values[field["name"]] = "on"
            else:
                values[field["name"]] = field["value"]
    return values


def blocks_limit():
    from hailsys.email import blocks
    return blocks.MAX_BLOCKS


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class EmailTplPagesTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(conn.close)
        self.users = {}
        for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                              "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.fetch_all = stack.enter_context(mock.patch.object(
            emailtpl.templatelist, "fetch_all", return_value=[row("hail_alert", has_blocks=False),
                                                              row("spring"), row("plain", False)]))
        self.load_blocks = stack.enter_context(mock.patch.object(
            emailtpl.templatestore, "load_blocks", return_value=blockform.starter()))
        self.load_settings = stack.enter_context(mock.patch.object(
            emailtpl.emailconfig, "load_settings", return_value=settings()))

    def client_as(self, role):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.users[role]
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def get(self, path, role="admin"):
        return self.client_as(role).get(path)

    def text(self, path):
        resp = self.get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp.get_data(as_text=True)

    PATHS = ("/templates/", "/templates/new", "/templates/edit/spring")

    # ---- who may see them ----

    def test_only_an_admin_may_open_any_page(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                for path in self.PATHS:
                    self.assertEqual(self.get(path, role).status_code, 403, f"{role} {path}")

    def test_signed_out_goes_to_login(self):
        for path in self.PATHS:
            resp = self.app.test_client().get(path)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/login", resp.headers["Location"])

    def test_the_nav_link_is_for_admins_only(self):
        self.assertIn('href="/templates/"', self.text("/templates/"))
        if "viewer" in self.users:
            page = self.get("/", "viewer").get_data(as_text=True)
            self.assertNotIn('href="/templates/"', page)

    # ---- the list ----

    def test_the_list_labels_each_template_and_links_only_block_templates(self):
        page = self.text("/templates/")
        self.assertIn("Files (the built-in)", page)
        self.assertIn("Blocks", page)
        self.assertIn("Text only", page)
        self.assertIn('href="/templates/edit/spring"', page)
        self.assertNotIn('href="/templates/edit/plain"', page)
        self.assertNotIn('href="/templates/edit/hail_alert"', page)
        self.assertIn('href="/templates/new"', page)
        self.assertIn("2026-10-14", page)

    def test_the_built_in_is_listed_even_before_it_is_saved(self):
        self.fetch_all.return_value = [row("spring")]
        page = self.text("/templates/")
        self.assertIn("hail_alert", page)
        self.assertIn("not saved yet", page)

    def test_an_empty_database_still_lists_the_built_in(self):
        self.fetch_all.return_value = []
        self.assertIn("hail_alert", self.text("/templates/"))

    def test_template_names_are_escaped_in_the_list(self):
        self.fetch_all.return_value = [row('<script>alert(1)</script>')]
        page = self.text("/templates/")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)

    # ---- new and edit ----

    def test_the_new_page_shows_the_whole_starter(self):
        page = self.text("/templates/new")
        starter = blockform.starter()
        self.assertIn(f'name="n_blocks" value="{len(starter["blocks"])}"', page)
        for i, block in enumerate(starter["blocks"]):
            self.assertIn(f'name="b{i}_kind" value="{block["kind"]}"', page)
        self.assertIn('name="name"', page)
        self.assertIn("Cannot be changed later", page.replace("It cannot", "Cannot"))
        self.assertIn("wording fixed", page)                      # the system blocks say so
        self.assertEqual(self.load_blocks.call_count, 0)

    def test_the_edit_page_loads_the_saved_blocks_and_shows_the_name_not_a_box(self):
        page = self.text("/templates/edit/spring")
        self.load_blocks.assert_called_once()
        self.assertEqual(self.load_blocks.call_args.args[1], "spring")
        self.assertIn("Template: spring", page)
        self.assertNotIn('size="30" maxlength="40"', page)                  # no name box
        self.assertIn('<input type="hidden" name="name" value="spring">', page)

    def test_an_unknown_template_is_a_404(self):
        self.load_blocks.side_effect = templatestore.TemplateNotFound("nope")
        self.assertEqual(self.get("/templates/edit/nope").status_code, 404)

    def test_a_template_not_built_from_blocks_says_so(self):
        self.load_blocks.return_value = None
        page = self.text("/templates/edit/plain")
        self.assertIn("not built from blocks", page)
        self.assertNotIn('name="n_blocks"', page)

    def test_a_name_with_markup_is_escaped_on_the_edit_page(self):
        page = self.text("/templates/edit/%3Cb%3Ex")
        self.assertNotIn("<b>x", page)
        self.assertIn("&lt;b&gt;x", page)

    def test_typed_text_in_a_block_is_escaped_inside_its_box(self):
        draft = blockform.starter()
        draft["blocks"][6]["text"] = "</textarea><script>alert(1)</script>"
        self.load_blocks.return_value = draft
        page = self.text("/templates/edit/spring")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;/textarea&gt;&lt;script&gt;", page)

    def test_every_widget_kind_renders_with_its_value(self):
        draft = blockform.starter()
        draft["blocks"][6].update(bold=True, size=14, space_after=30)
        self.load_blocks.return_value = draft
        page = self.text("/templates/edit/spring")
        self.assertRegex(page, r'<input type="checkbox" id="b6_bold" name="b6_bold" checked>')
        self.assertRegex(page, r'<option value="14" selected>14</option>')
        self.assertIn('id="b6_space_after" name="b6_space_after" value="30"', page)
        self.assertRegex(page, r'<textarea id="b10_items"[^>]*>\[b\]\$35')   # band items, one per line

    # ---- the subject and the preview ----

    def test_the_subject_choice_shows_the_preset_and_the_label(self):
        page = self.text("/templates/new")
        self.assertIn('name="subject_mode" value="preset" checked', page)
        self.assertIn("Hail alert (the wording follows", page)

    def test_the_text_subject_is_shown_when_chosen(self):
        draft = blockform.starter()
        draft["subject"] = {"text": "Hello {total}"}
        self.load_blocks.return_value = draft
        page = self.text("/templates/edit/spring")
        self.assertIn('name="subject_mode" value="text" checked', page)
        self.assertIn('value="Hello {total}"', page)

    def test_the_preview_is_a_sandboxed_iframe_with_the_subject_and_size(self):
        page = self.text("/templates/new")
        self.assertIn('<iframe sandbox title="Preview" srcdoc="', page)
        self.assertIn("A hail storm was reported near your listing at 123 Example St", page)
        self.assertIn("KB", page)

    def test_the_email_html_is_escaped_inside_the_srcdoc_attribute(self):
        import re
        page = self.text("/templates/new")
        value = re.search(r'srcdoc="([^"]*)"', page).group(1)
        self.assertNotIn("<", value)                               # no raw markup in the attribute
        self.assertIn("&lt;html", value)
        self.assertIn("[[trackingImage]]", value)                  # and it is the whole email
        self.assertIn("&lt;/html&gt;", value)

    def test_a_new_template_starts_with_an_empty_name(self):
        self.assertRegex(self.text("/templates/new"), r'name="name" size="30" maxlength="40" value=""')

    def test_the_templates_link_is_highlighted_on_these_pages(self):
        for path in ("/templates/", "/templates/new", "/templates/edit/spring"):
            self.assertRegex(self.text(path), r'href="/templates/"\s+class="active">Templates')
        self.assertNotRegex(self.text("/send/"), r'href="/templates/"\s+class="active"')

    def test_the_situation_choice_changes_the_preview(self):
        several = self.text("/templates/new?situation=several")
        self.assertIn("Hail storms were reported near several of your listings", several)
        self.assertIn('<option value="several" selected>', several)
        many = self.text("/templates/new?situation=more")
        self.assertIn("and 3 more", many)
        for key in samples.SITUATIONS:
            self.assertIn(f'<option value="{key}"', many)

    def test_an_unknown_situation_falls_back_to_the_default(self):
        for junk in ("nope", "", "%3Cscript%3E"):
            page = self.text(f"/templates/new?situation={junk}")
            self.assertIn('<option value="one" selected>', page)

    def test_a_broken_draft_shows_the_problem_in_its_block_and_no_preview(self):
        draft = blockform.starter()
        draft["blocks"][6]["text"] = "[b]never closed"
        self.load_blocks.return_value = draft
        page = self.text("/templates/edit/spring")
        self.assertIn("never closed", page)
        self.assertIn("There is no preview yet", page)
        self.assertNotIn("<iframe", page)
        block7 = page[page.index('id="b6"'):page.index('id="b7"')]
        self.assertIn('class="error"', block7)
        self.assertIn("never closed", block7)

    def test_a_missing_image_is_named_in_its_block(self):
        draft = blockform.starter()
        draft["blocks"][0]["image"] = "ghost"
        self.load_blocks.return_value = draft
        page = self.text("/templates/edit/spring")
        block1 = page[page.index('id="b0"'):page.index('id="b1"')]
        self.assertIn("The image ghost is not available", block1)

    def test_unusable_email_settings_say_why_instead_of_a_preview(self):
        self.load_settings.side_effect = render.SettingsError("The email image logo is not set up")
        page = self.text("/templates/new")
        self.assertIn("The email image logo is not set up", page)
        self.assertNotIn("<iframe", page)
        self.assertIn('name="n_blocks"', page)                      # the form still shows

    def test_the_pages_write_nothing(self):
        with mock.patch.object(emailtpl.templatestore, "save_blocks") as save, \
                mock.patch.object(emailtpl.templatestore, "ensure_current") as ensure:
            for path in self.PATHS:
                self.text(path)
            save.assert_not_called()
            ensure.assert_not_called()

    def test_the_pages_are_get_only(self):
        client = self.client_as("admin")
        self.app.config["WTF_CSRF_ENABLED"] = False
        for path in self.PATHS:
            self.assertEqual(client.post(path).status_code, 405, path)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class EmailTplActionsTest(unittest.TestCase):
    """POST /templates/edit: one action on the draft in the form."""

    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(conn.close)
        self.users = {}
        for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                              "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(emailtpl.emailconfig, "load_settings",
                                              return_value=settings()))
        stack.enter_context(mock.patch.object(emailtpl.templatestore, "load_blocks",
                                              return_value=blockform.starter()))
        self.save = stack.enter_context(mock.patch.object(emailtpl.templatestore, "save_blocks"))
        self.ensure = stack.enter_context(mock.patch.object(emailtpl.templatestore,
                                                            "ensure_current"))
        self.draft = blockform.starter()

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.users[role]
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def form(self, draft=None, **over):
        values = {"mode": "new", "name": "spring", "situation": "one"}
        values.update(blockform_form(draft or self.draft))
        values.update(over)
        return values

    def post(self, data, role="admin", fetch=False, csrf=False):
        self.app.config["WTF_CSRF_ENABLED"] = csrf
        headers = {"X-Requested-With": "fetch"} if fetch else {}
        return self.client_as(role).post("/templates/edit", data=data, headers=headers)

    def act(self, action, draft=None, **over):
        """Post the form with an action; return the blocks as the next page shows them."""
        resp = self.post(self.form(draft, action=action, **over), fetch=True)
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()

    def kinds_in(self, html):
        import re
        return re.findall(r'name="b\d+_kind" value="([a-z_]*)"', html)

    # ---- access ----

    def test_a_post_without_a_token_is_refused(self):
        resp = self.post(self.form(action="preview"), csrf=True)
        self.assertEqual(resp.status_code, 400)

    def test_only_an_admin_may_post(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post(self.form(action="preview"), role).status_code, 403)

    def test_signed_out_goes_to_login(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        resp = self.app.test_client().post("/templates/edit", data=self.form(action="preview"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_the_action_url_is_post_only(self):
        self.assertEqual(self.client_as().get("/templates/edit").status_code, 405)

    # ---- the actions ----

    def test_add_puts_the_new_block_above_the_footer(self):
        got = self.act("add", add_kind="heading")
        kinds = self.kinds_in(got["blocks"])
        self.assertEqual(len(kinds), len(self.draft["blocks"]) + 1)
        self.assertEqual(kinds[-2:], ["heading", "footer"])
        self.assertIn('name="n_blocks" value="18"', got["blocks"])

    def test_every_kind_can_be_added(self):
        for kind, _ in blockform.ADDABLE:
            got = self.act("add", add_kind=kind)
            self.assertEqual(self.kinds_in(got["blocks"])[-2:], [kind, "footer"], kind)
            self.assertEqual(got["problems"], [], kind)           # and it is valid as added

    def test_add_with_no_or_unknown_kind_says_so_and_changes_nothing(self):
        for bad in ("", "footer", "zzz", "<script>"):
            got = self.act("add", add_kind=bad)
            self.assertIn("Pick a kind of block to add.", got["blocks"], bad)
            self.assertEqual(len(self.kinds_in(got["blocks"])), len(self.draft["blocks"]))

    def test_the_block_limit_is_a_notice_not_an_error(self):
        full = {**self.draft, "blocks": [{"kind": "divider"}] * (blocks_limit() - 1)
                + [{"kind": "footer"}]}
        got = self.act("add", full, add_kind="text")
        self.assertIn("limited to 30 blocks", got["blocks"])
        self.assertEqual(len(self.kinds_in(got["blocks"])), 30)

    def test_move_up_and_down_swap_neighbours(self):
        got = self.act("up:7")
        kinds = self.kinds_in(got["blocks"])
        original = [b["kind"] for b in self.draft["blocks"]]
        self.assertEqual(kinds[6], original[7])
        self.assertEqual(kinds[7], original[6])
        got = self.act("down:0")
        self.assertEqual(self.kinds_in(got["blocks"])[:2], [original[1], original[0]])

    def test_the_text_travels_with_a_moved_block(self):
        draft = {**self.draft, "blocks": [{"kind": "text", "text": "first"},
                                          {"kind": "text", "text": "second"},
                                          {"kind": "footer"}]}
        got = self.act("down:0", draft)["blocks"]
        self.assertLess(got.index("second"), got.index("first"))

    def test_the_footer_cannot_move_or_be_moved_across_or_removed(self):
        n = len(self.draft["blocks"])
        for action in (f"up:{n - 1}", f"down:{n - 1}", f"down:{n - 2}", f"remove:{n - 1}", "up:0"):
            got = self.act(action)
            self.assertEqual(self.kinds_in(got["blocks"]),
                             [b["kind"] for b in self.draft["blocks"]], action)

    def test_remove_drops_just_that_block(self):
        got = self.act("remove:1")
        original = [b["kind"] for b in self.draft["blocks"]]
        self.assertEqual(self.kinds_in(got["blocks"]), original[:1] + original[2:])

    def test_actions_that_cannot_be_done_change_nothing_and_do_not_fail(self):
        original = [b["kind"] for b in self.draft["blocks"]]
        for action in ("up:abc", "up:-1", "up:\u00b2", "down:99", "remove:99", "remove:", "up",
                       "sideways:3", "", "remove:1.5", "up: 3", "remove:\u0663"):
            got = self.act(action)
            self.assertEqual(self.kinds_in(got["blocks"]), original, repr(action))

    def test_preview_and_an_unknown_action_just_show_the_preview(self):
        for action in ("preview", "", "zzz", "SAVE", "save "):
            got = self.act(action)
            self.assertIn("<iframe", got["preview"], action)
            self.assertEqual(got["problems"], [])

    # ---- what the page keeps ----

    def test_typed_values_are_kept_exactly_as_typed(self):
        values = self.form(action="preview")
        values["b6_text"] = "  typed   with   gaps  "
        values["b6_space_after"] = "lots"
        got = self.post(values, fetch=True).get_json()
        self.assertIn(">  typed   with   gaps  </textarea>", got["blocks"])
        self.assertIn('id="b6_space_after" name="b6_space_after" value="lots"', got["blocks"])

    def test_a_bad_value_is_reported_beside_the_right_block_after_a_move(self):
        values = self.form(action="up:7")
        values["b7_space_after"] = "lots"                 # block 8 (index 7) moves up to index 6
        got = self.post(values, fetch=True).get_json()
        block6 = got["blocks"][got["blocks"].index('id="b6"'):got["blocks"].index('id="b7"')]
        self.assertIn('class="error"', block6)
        self.assertIn("space_after", block6)
        self.assertEqual([n for n, _ in got["problems"]], [7])

    def test_name_mode_title_preheader_and_subject_come_back(self):
        values = self.form(action="preview", title="My title", preheader="My line",
                           subject_mode="text", subject_text="Hello {total}", situation="several")
        page = self.post(values).get_data(as_text=True)
        self.assertIn('value="My title"', page)
        self.assertIn('value="My line"', page)
        self.assertIn('name="subject_mode" value="text" checked', page)
        self.assertIn('value="Hello {total}"', page)
        self.assertIn('<option value="several" selected>', page)
        self.assertIn('name="name" size="30" maxlength="40" value="spring"', page)

    def test_an_existing_template_keeps_its_name_in_a_hidden_field(self):
        page = self.post(self.form(action="preview", mode="edit")).get_data(as_text=True)
        self.assertIn('<input type="hidden" name="name" value="spring">', page)
        self.assertNotIn('size="30" maxlength="40"', page)

    def test_the_situation_is_used_for_the_preview(self):
        got = self.post(self.form(action="preview", situation="more"), fetch=True).get_json()
        self.assertIn("and 3 more", got["preview"])
        got = self.post(self.form(action="preview", situation="nope"), fetch=True).get_json()
        self.assertNotIn("and 3 more", got["preview"])

    # ---- the two kinds of reply ----

    def test_a_plain_post_returns_the_whole_page_and_a_fetch_returns_json(self):
        plain = self.post(self.form(action="preview"))
        self.assertIn("text/html", plain.content_type)
        self.assertIn("<!DOCTYPE html>", plain.get_data(as_text=True))
        fetch = self.post(self.form(action="preview"), fetch=True)
        self.assertIn("application/json", fetch.content_type)
        self.assertEqual(set(fetch.get_json()), {"blocks", "preview", "problems"})

    def test_the_json_replaces_only_the_parts_that_change(self):
        got = self.act("remove:1")
        self.assertNotIn("<html", got["blocks"])
        self.assertNotIn("<form", got["blocks"])
        self.assertNotIn("<html", got["preview"].replace("&lt;html", ""))

    # ---- the form itself ----

    def test_enter_in_a_text_box_presses_the_preview_button_not_a_blocks_remove(self):
        import re
        page = self.client_as().get("/templates/new").get_data(as_text=True)
        start = page.index('<form id="editor"')
        form = page[start:page.index("</form>", start)]
        first = re.search(r'<button type="submit"[^>]*>', form).group(0)
        self.assertIn('value="preview"', first)
        self.assertIn("tpl-default", first)

    def editor_form(self, path):
        page = self.client_as().get(path).get_data(as_text=True)
        start = page.index('<form id="editor"')
        return page[start:page.index("</form>", start)]

    def test_the_editor_form_itself_carries_the_token_and_the_mode(self):
        new = self.editor_form("/templates/new")
        self.assertIn('name="csrf_token"', new)                     # inside the editor, not the header
        self.assertIn('name="mode" value="new"', new)
        edit = self.editor_form("/templates/edit/spring")
        self.assertIn('name="csrf_token"', edit)
        self.assertIn('name="mode" value="edit"', edit)

    def test_every_block_button_posts_to_the_action_url_with_an_anchor_at_its_block(self):
        import re
        page = self.client_as().get("/templates/new").get_data(as_text=True)
        self.assertRegex(page, r'<form id="editor" method="post" action="/templates/edit">')
        self.assertIn('name="csrf_token"', page)
        self.assertRegex(page, r'value="up:5"\s+formaction="/templates/edit#b4"')
        self.assertRegex(page, r'value="down:5"\s+formaction="/templates/edit#b6"')
        self.assertRegex(page, r'value="remove:5"\s+formaction="/templates/edit#b5"')
        self.assertRegex(page, r'value="add"\s+formaction="/templates/edit#add-block"')

    def test_the_first_block_cannot_move_up_and_the_footer_has_no_buttons(self):
        page = self.client_as().get("/templates/new").get_data(as_text=True)
        self.assertRegex(page, r'value="up:0"[^>]*disabled>Move up')
        self.assertNotRegex(page, r'value="down:0"[^>]*disabled>')
        last = len(self.draft["blocks"]) - 1
        self.assertNotIn(f'value="up:{last}"', page)
        self.assertNotIn(f'value="remove:{last}"', page)
        self.assertIn("The footer is required and stays last.", page)
        self.assertRegex(page, r'value="down:%d"[^>]*disabled>Move down' % (last - 1))

    # ---- safety ----

    def test_nothing_is_saved_by_any_action(self):
        for action in ("add", "up:3", "down:3", "remove:3", "preview", "zzz"):
            self.act(action, add_kind="text")
        self.save.assert_not_called()
        self.ensure.assert_not_called()

    def test_markup_in_every_field_is_escaped_in_the_reply(self):
        nasty = '"><script>alert(1)</script>'
        values = self.form(action="preview", title=nasty, preheader=nasty, name=nasty,
                           subject_text=nasty, subject_mode="text")
        values["b6_text"] = nasty
        values["b9_url"] = nasty
        for fetch in (False, True):
            resp = self.post(values, fetch=fetch)
            body = resp.get_json()["blocks"] if fetch else resp.get_data(as_text=True)
            self.assertNotIn("<script>alert(1)", body)

    def test_odd_forms_do_not_fail(self):
        for data in ({}, {"action": "preview"}, {"action": "add", "n_blocks": "abc"},
                     {"action": "up:1", "n_blocks": "99999"}, {"n_blocks": "-5"},
                     {"action": "remove:0", "n_blocks": "1", "b0_kind": "zzz"}):
            self.assertEqual(self.post(data).status_code, 200, data)
            self.assertEqual(self.post(data, fetch=True).status_code, 200, data)


class FakeConn:
    """Stands in for the database connection: records commits, so a save can be checked
    without leaving a row behind (templates can never be deleted)."""
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class EmailTplSaveRetireTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(conn.close)
        self.users = {}
        for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                              "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.fake = FakeConn()
        stack.enter_context(mock.patch.object(emailtpl, "get_connection", return_value=self.fake))
        stack.enter_context(mock.patch.object(emailtpl.emailconfig, "load_settings",
                                              return_value=settings()))
        stack.enter_context(mock.patch.object(emailtpl.templatestore, "load_blocks",
                                              return_value=blockform.starter()))
        self.save = stack.enter_context(mock.patch.object(
            emailtpl.templatestore, "save_blocks", return_value=11))
        self.active = stack.enter_context(mock.patch.object(
            emailtpl.templatestore, "active_id", return_value=10))
        self.retire = stack.enter_context(mock.patch.object(emailtpl.templatestore, "retire"))
        self.fetch_one = stack.enter_context(mock.patch.object(
            emailtpl.templatelist, "fetch_one", return_value=row("spring")))
        self.fetch_all = stack.enter_context(mock.patch.object(
            emailtpl.templatelist, "fetch_all",
            return_value=[row("hail_alert", has_blocks=False), row("spring"), row("fresh", versions=0)]))
        self.draft = blockform.starter()

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.users[role]
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def form(self, draft=None, **over):
        values = {"mode": "new", "name": "spring", "situation": "one", "action": "save"}
        values.update(blockform_form(draft or self.draft))
        values.update(over)
        return values

    def post(self, path, data=None, role="admin", fetch=False, csrf=False, follow=False):
        self.app.config["WTF_CSRF_ENABLED"] = csrf
        headers = {"X-Requested-With": "fetch"} if fetch else {}
        return self.client_as(role).post(path, data=data or {}, headers=headers,
                                         follow_redirects=follow)

    # ---- save ----

    def test_a_new_template_is_saved_committed_and_the_user_is_sent_to_it(self):
        resp = self.post("/templates/edit", self.form())
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/templates/edit/spring"))
        self.save.assert_called_once()
        kwargs = self.save.call_args.kwargs
        self.assertEqual((kwargs["name"], kwargs["must_be_new"]), ("spring", True))
        self.assertEqual(kwargs["created_by"], self.users["admin"])
        self.assertEqual(kwargs["template"], blockform.parse_form(self.form()))
        self.assertEqual(self.fake.commits, 1)

    def test_the_outcome_is_flashed_on_the_next_page(self):
        text = self.post("/templates/edit", self.form(), follow=True).get_data(as_text=True)
        self.assertIn("Created spring.", text)
        self.assertIn("Template: spring", text)

    def test_saving_an_existing_template_says_new_version_or_no_changes(self):
        values = self.form(mode="edit")
        text = self.post("/templates/edit", values, follow=True).get_data(as_text=True)
        self.assertIn("Saved spring as a new version.", text)
        self.assertFalse(self.save.call_args.kwargs["must_be_new"])
        self.save.return_value = 10                                   # same as the active one
        text = self.post("/templates/edit", values, follow=True).get_data(as_text=True)
        self.assertIn("No changes: spring is already saved as it is.", text)

    def test_a_refused_save_is_shown_with_the_draft_and_is_not_committed(self):
        self.save.side_effect = emailtpl.templatestore.TemplateRefused("Names ignore case.")
        values = self.form(title="Kept title")
        values["b6_text"] = "kept words"
        resp = self.post("/templates/edit", values)
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        self.assertIn("Names ignore case.", text)
        self.assertIn('value="Kept title"', text)
        self.assertIn(">kept words</textarea>", text)
        self.assertEqual(self.fake.commits, 0)

    def test_an_invalid_draft_is_not_sent_to_the_store_at_all(self):
        values = self.form()
        values["b6_text"] = "[b]never closed"
        text = self.post("/templates/edit", values).get_data(as_text=True)
        self.save.assert_not_called()
        self.assertEqual(self.fake.commits, 0)
        self.assertIn("Not saved.  Fix the problems marked below.", text)
        block = text[text.index('id="b6"'):text.index('id="b7"')]
        self.assertIn("never closed", block)

    def test_a_save_by_fetch_answers_with_where_to_go_or_with_the_page_parts(self):
        got = self.post("/templates/edit", self.form(), fetch=True)
        self.assertEqual(got.get_json(), {"redirect": "/templates/edit/spring"})
        self.save.side_effect = emailtpl.templatestore.TemplateRefused("Refused for a reason.")
        got = self.post("/templates/edit", self.form(), fetch=True).get_json()
        self.assertIn("Refused for a reason.", got["blocks"])
        self.assertIn("<iframe", got["preview"])

    def test_save_needs_a_token_and_an_admin(self):
        self.assertEqual(self.post("/templates/edit", self.form(), csrf=True).status_code, 400)
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post("/templates/edit", self.form(), role).status_code, 403)
        self.save.assert_not_called()

    def test_the_editor_offers_save_at_both_ends_with_the_right_label(self):
        client = self.client_as()
        text = client.get("/templates/new").get_data(as_text=True)
        self.assertEqual(text.count('value="save"'), 2)
        self.assertEqual(text.count(">Create template<"), 2)
        text = client.get("/templates/edit/spring").get_data(as_text=True)
        self.assertEqual(text.count(">Save as a new version<"), 2)

    # ---- retire ----

    def test_the_list_offers_retire_for_saved_templates_except_the_built_in(self):
        text = self.client_as().get("/templates/").get_data(as_text=True)
        self.assertIn('href="/templates/retire/spring"', text)
        self.assertNotIn('href="/templates/retire/hail_alert"', text)
        self.assertNotIn('href="/templates/retire/fresh"', text)        # never saved: no versions

    def test_the_confirmation_page_says_what_retiring_does(self):
        text = self.client_as().get("/templates/retire/spring").get_data(as_text=True)
        self.assertIn("Retire spring?", text)
        self.assertRegex(text, r"2 versions, sent in 3\s+sends")
        self.assertIn("2026-10-14", text)
        self.assertIn("Emails already sent keep their exact text", text)
        self.assertIn('action="/templates/retire/spring"', text)
        self.assertIn('name="csrf_token"', text)
        self.retire.assert_not_called()

    def test_the_retire_form_itself_carries_the_token(self):
        text = self.client_as().get("/templates/retire/spring").get_data(as_text=True)
        start = text.index('<form method="post" action="/templates/retire/spring"')
        form = text[start:text.index("</form>", start)]
        self.assertIn('name="csrf_token"', form)             # inside this form, not the header's
        self.assertIn("Retire spring</button>", form)

    def test_the_confirmation_page_is_404_for_the_built_in_or_an_unknown_name(self):
        self.assertEqual(self.client_as().get("/templates/retire/hail_alert").status_code, 404)
        self.assertEqual(self.client_as().get("/templates/retire/HAIL_ALERT").status_code, 404)
        self.fetch_one.return_value = None
        self.assertEqual(self.client_as().get("/templates/retire/nope").status_code, 404)

    def test_retiring_commits_flashes_and_goes_back_to_the_list(self):
        resp = self.post("/templates/retire/spring")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/templates/"))
        self.retire.assert_called_once()
        self.assertEqual(self.retire.call_args.args[1], "spring")
        self.assertEqual(self.fake.commits, 1)
        text = self.post("/templates/retire/spring", follow=True).get_data(as_text=True)
        self.assertIn("Retired spring.", text)

    def test_retiring_an_unknown_template_is_a_404_and_commits_nothing(self):
        self.retire.side_effect = emailtpl.templatestore.TemplateNotFound("nope")
        self.assertEqual(self.post("/templates/retire/nope").status_code, 404)
        self.assertEqual(self.fake.commits, 0)

    def test_retiring_the_built_in_is_refused_with_a_message(self):
        self.retire.side_effect = emailtpl.templatestore.TemplateRefused("It is the built-in.")
        resp = self.post("/templates/retire/hail_alert", follow=True)
        self.assertIn("It is the built-in.", resp.get_data(as_text=True))
        self.assertEqual(self.fake.commits, 0)

    def test_retiring_needs_a_post_a_token_and_an_admin(self):
        self.assertEqual(self.post("/templates/retire/spring", csrf=True).status_code, 400)
        for role in ("viewer", "sender"):
            if role in self.users:
                self.assertEqual(self.post("/templates/retire/spring", role=role).status_code, 403)
                self.assertEqual(self.client_as(role).get("/templates/retire/spring").status_code, 403)
        self.retire.assert_not_called()

    def test_a_name_with_markup_is_escaped_on_the_confirmation_page(self):
        self.fetch_one.return_value = row("<b>x<script>alert(1)")
        text = self.client_as().get("/templates/retire/%3Cb%3Ex").get_data(as_text=True)
        self.assertNotIn("<b>x", text)
        self.assertNotIn("<script>alert(1)", text)
        self.assertIn("&lt;b&gt;x&lt;script&gt;", text)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class EditorScriptTest(unittest.TestCase):
    """templates.js has no Node on this machine to run it (tests/js/templates_check.js does, in a
    container).  These check the script against the page and the server it talks to, so the names
    cannot drift apart unnoticed."""

    JS = ROOT / "hailsys/web/static/templates.js"

    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(conn.close)
        self.admin = conn.execute("SELECT emp_id FROM users WHERE role = 'admin' AND is_active "
                                  "ORDER BY emp_id LIMIT 1").fetchone()["emp_id"]
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(emailtpl.emailconfig, "load_settings",
                                              return_value=settings()))
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.admin, "admin", "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        self.script = self.JS.read_text(encoding="utf-8")

    def page(self):
        return self.client.get("/templates/new").get_data(as_text=True)

    def test_the_script_is_served_and_the_editor_page_loads_it_once(self):
        resp = self.client.get("/static/templates.js")
        self.assertEqual(resp.status_code, 200)
        resp.close()
        self.assertEqual(self.page().count('src="/static/templates.js"'), 1)
        for path in ("/templates/", "/templates/retire/x"):
            self.assertNotIn("templates.js", self.client.get(path).get_data(as_text=True))

    def test_every_id_the_script_looks_up_is_on_the_page(self):
        import re
        page = self.page()
        for element_id in re.findall(r"getElementById\('([^']+)'\)", self.script):
            self.assertIn(f'id="{element_id}"', page, element_id)
        for element_id in ("add_kind", "situation"):                     # compared by id in change
            self.assertIn(f"'{element_id}'", self.script)
            self.assertIn(f'id="{element_id}"', page)

    def test_the_selectors_match_the_markup_the_server_draws(self):
        page = self.page()
        self.assertIn("'.block-errors[data-block=\"'", self.script)
        self.assertIn('<div class="block-errors" data-block="0"></div>', page)
        self.assertIn("p.error", self.script)
        self.app.config["WTF_CSRF_ENABLED"] = False
        broken = self.client.post("/templates/edit", data={"n_blocks": "1", "b0_kind": "text",
                                                           "b0_text": "[b]x"})
        self.assertIn('<p class="error">', broken.get_data(as_text=True))
        self.assertIn("button[name=\"action\"]", self.script)
        self.assertIn('name="action"', page)

    def test_the_script_talks_to_the_server_the_way_the_server_listens(self):
        self.assertIn("'X-Requested-With': 'fetch'", self.script)
        self.assertIn('X-Requested-With") == "fetch"', Path(emailtpl.__file__).read_text())
        for key in ("blocks", "preview", "problems"):
            self.assertIn(f"reply.{key}", self.script)
        self.assertIn("form.getAttribute('action')", self.script)
        self.assertNotIn("form.action", self.script.replace("form.getAttribute('action')", "")
                         .replace("so form.action would be", ""))                   # the clobbering trap

    def test_save_is_never_intercepted_and_preview_is(self):
        self.assertIn("button.value === 'save'", self.script)
        self.assertIn("send('preview')", self.script)

    def test_no_curly_quotes_or_stray_characters(self):
        self.assertEqual([c for c in self.script if ord(c) > 127], [])
        self.assertEqual(self.script.count("{"), self.script.count("}"))
        self.assertEqual(self.script.count("("), self.script.count(")"))
        self.assertEqual(self.script.count("["), self.script.count("]"))


if __name__ == "__main__":
    unittest.main()
