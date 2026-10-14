"""Tests for hailsys/email/blocks.py (step 2.1) -- pure, no database, no network.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_email_blocks
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.email import blocks as b, render as r
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

FOOTER = {"kind": "footer"}
SUBJECT = {"text": "Storms near {total} homes"}


def tpl(*body, **over):
    t = {"title": "Hail", "preheader": "", "subject": SUBJECT,
         "blocks": list(body) + [FOOTER]}
    t.update(over)
    return t


def text(s, **kw):
    return {"kind": "text", "text": s, **kw}


CTX = {"logo_url": "https://x.test/l.png", "badge_cra_url": "https://x.test/c.png",
       "badge_bbb_url": "https://x.test/b.png", "contact_email": "e@x.test",
       "schedule_url": "https://x.test/s", "agent_first_name": "Pat", "storm_count": 1,
       "storm_dates_label": "Aug 15", "largest_hail": '1.5"', "nearest_miles": "0.8",
       "properties": [{"address": "1 Main St"}], "more_count": 0, "total": 1,
       "images": {"logo": {"url": "https://x.test/logo.png", "alt": 'Logo "A"'}}}


def body_of(*blks, **over):
    return b.compile_blocks(tpl(*blks, **over))[1]


def rendered(*blks, ctx=None, **over):
    subject, body = b.compile_blocks(tpl(*blks, **over))
    return r.render(subject, body, ctx or CTX)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class InlineMarkupTest(unittest.TestCase):
    def test_plain_text_is_escaped(self):
        self.assertEqual(b.rich('a < b & "c"', "t"), "a &lt; b &amp; &quot;c&quot;")

    def test_bold_italic_and_colours(self):
        self.assertEqual(b.rich("[b]x[/b]", "t"), "<strong>x</strong>")
        self.assertEqual(b.rich("[i]x[/i]", "t"), "<em>x</em>")
        self.assertEqual(b.rich("[b=navy]x[/b]", "t"),
                         '<strong style="color:#1b4a80;">x</strong>')
        self.assertEqual(b.rich("[color=red]x[/color]", "t"),
                         '<span style="color:#c0392b;">x</span>')

    def test_every_palette_colour_compiles(self):
        for name, code in b.PALETTE.items():
            self.assertIn(code, b.rich(f"[color={name}]x[/color]", "t"))

    def test_unknown_colour_is_refused(self):
        for bad in ("[color=pink]x[/color]", "[b=#ff0000]x[/b]", "[color]x[/color]"):
            with self.assertRaises(b.BlockError, msg=bad):
                b.rich(bad, "t")

    def test_merge_fields_become_placeholders(self):
        self.assertEqual(b.rich("{total} homes", "t"), "{{ total }} homes")

    def test_unknown_or_unavailable_merge_fields_are_refused(self):
        for bad in ("{nope}", "{agent_first_name}", "{Total}", "{ total }", "{}"):
            with self.assertRaises(b.BlockError, msg=bad):
                b.rich(bad, "t")

    def test_stray_braces_are_refused(self):
        for bad in ("{", "}", "{{ 7*7 }}", "{% if x %}", "{# c #}", "a } b", "{total"):
            with self.assertRaises(b.BlockError, msg=bad):
                b.rich(bad, "t")

    def test_links(self):
        self.assertIn('href="{{ schedule_url }}"', b.rich("[link={schedule_url}]x[/link]", "t"))
        out = b.rich("[link=https://x.test/a?b=1&c=2]x[/link]", "t")
        self.assertIn('href="https://x.test/a?b=1&amp;c=2"', out)
        self.assertIn("border-bottom:2px solid #2d7dd2", out)

    def test_bad_links_are_refused(self):
        for bad in ("[link=http://x.test]x[/link]", "[link=javascript:alert(1)]x[/link]",
                    "[link=https://a.azurewebsites.net/p]x[/link]", "[link]x[/link]",
                    "[link=https://x.test/a b]x[/link]", "[link={total}]x[/link]"):
            with self.assertRaises(b.BlockError, msg=bad):
                b.rich(bad, "t")

    def test_a_quote_in_a_link_cannot_end_the_attribute(self):
        out = b.rich('[link=https://x.test/"onclick=1]x[/link]', "t")
        self.assertIn("https://x.test/&quot;onclick=1", out)
        self.assertNotIn('"onclick', out)

    def test_a_link_cannot_hold_a_link(self):
        with self.assertRaises(b.BlockError):
            b.rich("[link={schedule_url}][link={schedule_url}]x[/link][/link]", "t")

    def test_tags_must_nest_and_close(self):
        for bad in ("[b]x", "x[/b]", "[b][i]x[/b][/i]", "[b]x[/i]", "[/b][b]"):
            with self.assertRaises(b.BlockError, msg=bad):
                b.rich(bad, "t")
        self.assertEqual(b.rich("[b][i]x[/i][/b]", "t"), "<strong><em>x</em></strong>")

    def test_closing_tags_take_no_setting(self):
        with self.assertRaises(b.BlockError):
            b.rich("[b]x[/b=navy]", "t")

    def test_nesting_limit(self):
        ok = "[b][i][b][i]x[/i][/b][/i][/b]"
        self.assertTrue(b.rich(ok, "t"))
        with self.assertRaises(b.BlockError):
            b.rich("[b]" * 5 + "x" + "[/b]" * 5, "t")

    def test_unknown_tags_stay_literal_text(self):
        self.assertEqual(b.rich("[script]x[/script]", "t"), "[script]x[/script]")
        self.assertEqual(b.rich("[ref 1]", "t"), "[ref 1]")

    def test_double_brackets_are_refused(self):
        for bad in ("[[trackingImage]]", "a [[x]] b", "[[", "[b][[x]][/b]"):
            with self.assertRaises(b.BlockError, msg=bad):
                b.rich(bad, "t")

    def test_html_is_never_passed_through(self):
        out = b.rich("<script>alert(1)</script><img src=x onerror=1>", "t")
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)

    def test_length_limit(self):
        self.assertTrue(b.rich("a" * b.MAX_TEXT, "t"))
        with self.assertRaises(b.BlockError):
            b.rich("a" * (b.MAX_TEXT + 1), "t")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(b.rich("  a \n\n  b\t", "t"), "a b")

    def test_non_text_is_refused(self):
        for bad in (None, 5, ["a"]):
            with self.assertRaises(b.BlockError):
                b.rich(bad, "t")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class PlainTextTest(unittest.TestCase):
    def test_body_text_is_escaped_with_braces_as_references(self):
        self.assertEqual(b.plain("a & b", "t", 50), "a &amp; b")

    def test_subject_text_is_a_string_literal(self):
        out = b.plain('He said "hi" \\ {total}', "t", 50, ("total",), subject=True)
        self.assertEqual(out, '{{ "He said \\"hi\\" \\\\ " }}{{ total }}')

    def test_subject_literals_render_back_to_the_text(self):
        for s in ('say "x"', "a\\b", "it's", "plain", "café"):
            src = b.plain(s, "t", 50, subject=True)
            self.assertEqual(r._SUBJECT_ENV.from_string(src).render(), s)

    def test_limit_and_brackets(self):
        with self.assertRaises(b.BlockError):
            b.plain("a" * 11, "t", 10)
        with self.assertRaises(b.BlockError):
            b.plain("[[x]]", "t", 10)

    def test_tokens_default_to_none_allowed(self):
        with self.assertRaises(b.BlockError):
            b.plain("{total}", "t", 50)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class BlockSettingsTest(unittest.TestCase):
    def test_defaults_are_filled_in(self):
        t = b.clean_block({"kind": "text", "text": "hi"})
        self.assertEqual(t, {"kind": "text", "text": "hi", "size": 16, "color": "dark",
                             "bold": False, "italic": False, "align": "left",
                             "space_before": 0, "space_after": 20})
        self.assertEqual(b.clean_block({"kind": "image", "image": "logo"}),
                         {"kind": "image", "image": "logo", "width": 220, "placement": "body"})
        self.assertEqual(b.clean_block(FOOTER), {"kind": "footer", "phone": "(303) 750-1900"})
        self.assertEqual(b.clean_block({"kind": "divider"}), {"kind": "divider"})

    def test_unknown_kinds_and_settings_are_refused(self):
        for bad in ({}, {"kind": "script"}, {"kind": 5}, "text", None, ["text"],
                    {"kind": "divider", "color": "red"},
                    {"kind": "text", "text": "x", "onclick": "1"}):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block(bad)

    def test_required_settings(self):
        for bad in ({"kind": "text"}, {"kind": "text", "text": "  "},
                    {"kind": "image"}, {"kind": "image", "image": ""}):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block(bad)

    def test_text_choices(self):
        for key, bad in (("size", 13), ("size", "16"), ("color", "pink"), ("align", "right"),
                         ("bold", 1), ("italic", "yes"), ("space_before", -2),
                         ("space_before", 3), ("space_after", 42), ("space_after", True),
                         ("space_after", 2.0)):
            with self.assertRaises(b.BlockError, msg=f"{key}={bad!r}"):
                b.clean_block(text("x", **{key: bad}))
        for size in b.SIZES:
            self.assertEqual(b.clean_block(text("x", size=size))["size"], size)
        self.assertEqual(b.clean_block(text("x", space_after=40, space_before=0))["space_after"], 40)

    def test_image_settings(self):
        for bad in ({"image": "a b"}, {"image": "a'b"}, {"image": "x" * 41},
                    {"image": "logo", "width": 39}, {"image": "logo", "width": 561},
                    {"image": "logo", "width": 221}, {"image": "logo", "placement": "side"},
                    {"image": "logo']}}{{ 7*7 }}{{ x['"}):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block({"kind": "image", **bad})
        self.assertEqual(b.clean_block({"kind": "image", "image": "a-B_9" + "x" * 35})["width"], 220)
        self.assertEqual(b.clean_block({"kind": "image", "image": "l", "width": 560})["width"], 560)

    def test_phone_format(self):
        for bad in ("3037501900", "303-750-1900", "(303)750-1900", "(303) 750-190",
                    "(303) 750-1900 x", '(303) 750-1900"><b>', ""):
            with self.assertRaises(b.BlockError, msg=bad):
                b.clean_block({"kind": "footer", "phone": bad})
        self.assertEqual(b.clean_block({"kind": "footer", "phone": "(720) 555-0100"})["phone"],
                         "(720) 555-0100")

    def test_markup_errors_in_a_text_block_surface_at_clean_time(self):
        with self.assertRaises(b.BlockError):
            b.clean_block(text("[b]never closed"))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class TemplateRulesTest(unittest.TestCase):
    def refused(self, template, *words):
        with self.assertRaises(b.BlockError) as ctx:
            b.compile_blocks(template)
        for w in words:
            self.assertIn(w, str(ctx.exception))

    def test_the_footer_is_required_single_and_last(self):
        self.refused({"title": "", "subject": SUBJECT, "blocks": [text("x")]}, "footer")
        self.refused({"title": "", "subject": SUBJECT, "blocks": [FOOTER, text("x")]}, "footer")
        self.refused({"title": "", "subject": SUBJECT, "blocks": [text("x"), FOOTER, FOOTER]},
                     "footer")
        self.assertTrue(b.compile_blocks({"subject": SUBJECT, "blocks": [FOOTER]}))

    def test_block_count_limit(self):
        ok = [text("x")] * (b.MAX_BLOCKS - 1)
        self.assertTrue(b.compile_blocks({"subject": SUBJECT, "blocks": ok + [FOOTER]}))
        self.refused({"subject": SUBJECT, "blocks": ok + [text("x"), FOOTER]}, "limited")

    def test_empty_or_wrong_type_blocks(self):
        for bad in ([], None, "x", {}):
            self.refused({"subject": SUBJECT, "blocks": bad}, "at least one block")

    def test_every_problem_is_listed_with_its_block_number(self):
        with self.assertRaises(b.BlockError) as ctx:
            b.compile_blocks(tpl(text("[b]x"), text("{nope}"), {"kind": "zzz"}))
        msg = str(ctx.exception)
        for n in ("block 1", "block 2", "block 3"):
            self.assertIn(n, msg)

    def test_unknown_template_settings_are_refused(self):
        self.refused(tpl(text("x"), css="x"), "css")
        self.refused([], "set of settings")

    def test_subject_forms(self):
        for bad in (None, "text", {}, {"preset": "nope"}, {"text": ""}, {"text": "   "},
                    {"preset": "hail_alert", "text": "x"}, {"text": "{{ 7*7 }}"},
                    {"text": "[[trackingImage]]"}, {"text": "{nope}"},
                    {"text": "x" * (r.MAX_SUBJECT + 1)}):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.compile_blocks(tpl(text("x"), subject=bad))
        self.assertTrue(b.compile_blocks(tpl(text("x"), subject={"preset": "hail_alert"})))
        self.assertTrue(b.compile_blocks(
            tpl(text("x"), subject={"text": "x" * r.MAX_SUBJECT})))

    def test_title_and_preheader_limits_and_tokens(self):
        self.assertTrue(b.compile_blocks(tpl(text("x"), title="t" * b.MAX_TITLE)))
        self.refused(tpl(text("x"), title="t" * (b.MAX_TITLE + 1)), "title")
        self.refused(tpl(text("x"), title="{total}"), "title")
        self.assertTrue(b.compile_blocks(tpl(text("x"), preheader="p" * b.MAX_PREHEADER)))
        self.refused(tpl(text("x"), preheader="p" * (b.MAX_PREHEADER + 1)), "preheader")
        self.refused(tpl(text("x"), preheader="{nope}"), "preheader")

    def test_size_limit(self):
        many = [text("&" * b.MAX_TEXT)] * (b.MAX_BLOCKS - 1)    # each & grows to &amp;
        self.refused({"subject": SUBJECT, "blocks": many + [FOOTER]}, "too large")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class CompileTest(unittest.TestCase):
    def test_a_small_template_compiles_validates_and_renders(self):
        subject, html = rendered(
            {"kind": "image", "image": "logo", "placement": "header"}, {"kind": "divider"},
            text("Hello [b=navy]there[/b], {total} home near {storm_dates_label}.",
                 size=14, color="slate", bold=True, align="center"),
            preheader="{largest_hail} hail")
        self.assertEqual(subject, "Storms near 1 homes")
        self.assertIn("[[trackingImage]]", html)
        self.assertIn("Hello <strong", html)
        self.assertIn("1 home near Aug 15.", html)
        self.assertIn('src="https://x.test/logo.png"', html)
        self.assertIn("font-size:14px; line-height:21px; color:#475569; font-weight:bold;", html)
        self.assertIn('<td align="center" style="padding:0 34px 20px 34px;', html)
        self.assertIn("1.5&#34;", html.replace("&quot;", "&#34;"))   # preheader, escaped by Jinja
        self.assertIn("tel:3037501900", html)
        self.assertIn('mailto:e@x.test', html)

    def test_the_footer_comes_last_inside_the_frame(self):
        html = body_of(text("first"))
        self.assertLess(html.index("first"), html.index("This is an advertisement."))
        self.assertLess(html.index("This is an advertisement."), html.index("</body>"))

    def test_the_frame_matches_the_shipped_template(self):
        shipped = (ROOT / "hailsys/email/templates/hail_alert.html").read_text()
        for piece in (b._FRAME_OPEN.split("\n")[0], b._PREHEADER_OPEN.strip(),
                      'border:3px solid #1f5c9e;'):
            self.assertIn(piece.split("\n")[0], shipped)
        self.assertEqual(shipped.count("&#847;&zwnj;&nbsp;"), 20)
        self.assertEqual(b._PREHEADER_FILLER.count("&#847;&zwnj;&nbsp;"), 20)

    def test_the_preheader_is_only_present_when_given(self):
        self.assertNotIn("display:none", body_of(text("x")))
        self.assertIn("display:none", body_of(text("x"), preheader="hi"))

    def test_the_preset_subject_renders_like_the_shipped_one(self):
        shipped = (ROOT / "hailsys/email/templates/hail_alert_subject.txt").read_text()
        preset = b.SUBJECT_PRESETS["hail_alert"]
        one = dict(CTX)
        many = {**CTX, "storm_count": 2, "total": 3,
                "properties": [{"address": "1 Main St"}] * 3}
        for ctx in (one, many):
            self.assertEqual(r.render(preset, "[[trackingImage]]", ctx)[0],
                             r.render(shipped, "[[trackingImage]]", ctx)[0])

    def test_compile_is_deterministic(self):
        t = tpl(text("x"), {"kind": "divider"})
        self.assertEqual(b.compile_blocks(t), b.compile_blocks(t))

    def test_compile_does_not_change_the_template_it_is_given(self):
        t = tpl(text("x"))
        before = repr(t)
        b.compile_blocks(t)
        self.assertEqual(repr(t), before)

    def test_a_missing_image_fails_loudly_at_render_time(self):
        subject, html = b.compile_blocks(tpl({"kind": "image", "image": "ghost"}))
        with self.assertRaises(r.RenderError):
            r.render(subject, html, CTX)

    def test_image_alt_text_is_escaped_into_the_attribute(self):
        _, html = rendered({"kind": "image", "image": "logo"})
        self.assertIn('alt="Logo &#34;A&#34;"', html)

    def test_image_sizes(self):
        html = body_of({"kind": "image", "image": "logo", "width": 100})
        self.assertIn('width="100"', html)
        self.assertIn("width:100px;", html)
        self.assertIn("26px 30px 18px 30px", body_of(
            {"kind": "image", "image": "logo", "placement": "header"}))

    def test_divider_matches_the_shipped_markup(self):
        shipped = (ROOT / "hailsys/email/templates/hail_alert.html").read_text()
        self.assertIn("border-top:2px dotted #2d7dd2; font-size:0; line-height:0;", shipped)
        self.assertIn("border-top:2px dotted #2d7dd2; font-size:0; line-height:0;", b._DIVIDER)

    def test_the_footer_phone_is_a_parameter(self):
        html = body_of(text("x"))
        self.assertIn("(303) 750-1900", html)
        t = tpl(text("x"))
        t["blocks"][-1] = {"kind": "footer", "phone": "(720) 555-0100"}
        html = b.compile_blocks(t)[1]
        self.assertIn("tel:7205550100", html)
        self.assertNotIn("303", html)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class InjectionTest(unittest.TestCase):
    """Whatever is typed, it comes out as text, never as template syntax or markup."""

    NASTY = ("{{ 7*7 }}", "{% for i in range(9) %}x{% endfor %}", "{# hi #}",
             "<script>alert(1)</script>", '"><img src=x onerror=1>', "&lt;b&gt;",
             "{{config}}", "{{ ''.__class__ }}")

    def test_text_blocks_never_reach_jinja_or_html(self):
        for nasty in self.NASTY:
            for typed in (nasty, nasty.replace("{", "").replace("}", "")):
                try:
                    _, html = rendered(text(typed))
                except b.BlockError:
                    continue            # refused outright is also safe
                self.assertNotIn("<script", html, typed)
                self.assertNotIn("<img src=x", html, typed)
                self.assertNotIn("onerror=1>", html.replace("onerror=1&gt;", ""), typed)

    def test_braces_in_text_are_refused_not_executed(self):
        for nasty in self.NASTY:
            if "{" in nasty:
                with self.assertRaises(b.BlockError, msg=nasty):
                    b.compile_blocks(tpl(text(nasty)))

    def test_titles_preheaders_subjects_and_phones_cannot_inject(self):
        for field in ("title", "preheader"):
            for nasty in self.NASTY:
                try:
                    _, html = rendered(text("x"), **{field: nasty})
                except b.BlockError:
                    continue
                self.assertNotIn("<script", html, (field, nasty))
                self.assertNotIn("<img src=x", html, (field, nasty))
        for nasty in self.NASTY:
            try:
                subject, _ = rendered(text("x"), subject={"text": nasty})
            except b.BlockError:
                continue
            self.assertNotIn("49", subject, nasty)

    def test_the_tracking_tag_is_present_exactly_once_and_cannot_be_typed(self):
        self.assertEqual(body_of(text("x")).count("[[trackingImage]]"), 1)
        for field in ("title", "preheader"):
            with self.assertRaises(b.BlockError):
                b.compile_blocks(tpl(text("x"), **{field: "[[trackingImage]]"}))

    def test_literal_braces_cannot_survive_into_the_source(self):
        # the only braces in the compiled source are the ones the compiler wrote
        subject, body = b.compile_blocks(tpl(text("a & b <c> 'd'")))
        self.assertEqual(b._literal("{{ x }}"), "&#123;&#123; x &#125;&#125;")
        self.assertNotIn("{", b._literal("{{ x }}{% y %}{# z #}"))
        self.assertNotIn("}", b._literal("{{ x }}{% y %}{# z #}"))


if __name__ == "__main__":
    unittest.main()
