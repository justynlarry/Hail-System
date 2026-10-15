"""Tests for hailsys/email/blocks.py (step 2.1) -- pure, no database, no network.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_email_blocks
"""

import re
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
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
       "images": {"logo": {"url": "https://x.test/logo.png", "alt": 'Logo "A"'},
                  "badge_cra": {"url": "https://x.test/c.png", "alt": "CRA Member"},
                  "badge_bbb": {"url": "https://x.test/b.png", "alt": "BBB Accredited"}}}


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


SHIPPED = (ROOT / "hailsys/email/templates/hail_alert.html").read_text() if MISSING is None else ""


def section(start, end):
    """The shipped template between two of its '<!-- ---------- NAME' comments."""
    a = SHIPPED.index(f"<!-- ---------- {start}")
    return SHIPPED[a:SHIPPED.index(f"<!-- ---------- {end}", a)]


def norm(markup):
    """Markup with comments, entities, whitespace and layout removed, to compare content."""
    import html as _html
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    markup = re.sub(r"\s+", " ", _html.unescape(markup))
    return re.sub(r"\s*([<>])\s*", r"\1", markup).strip()


def render_src(src, ctx=None):
    # the compiled frame defines `multi` before the blocks; do the same here
    return r._BODY_ENV.from_string("{% set multi = storm_count > 1 %}" + src).render(**(ctx or CTX))


def event(date, hail, miles):
    return {"date": date, "hail_size": hail, "miles": miles}


def storm_ctx(properties, storm_count=1, more_count=0, name="Pat", dates="August 15, 2026"):
    return {**CTX, "agent_first_name": name, "storm_count": storm_count,
            "storm_dates_label": dates, "largest_hail": '1.75"', "nearest_miles": "0.4 mi",
            "properties": properties, "more_count": more_count,
            "total": len(properties) + more_count}


STORMS = {
    "one storm, one listing": storm_ctx(
        [{"address": "1 Main St, Denver, CO", "events": [event("Aug 15", '1.75"', "0.4 mi")]}]),
    "several storms and listings": storm_ctx(
        [{"address": "1 Main St, Denver, CO",
          "events": [event("Aug 15", '1.75"', "0.4 mi"), event("Aug 21", '1.00"', "2.1 mi")]},
         {"address": "9 Oak Ave & Co, Aurora, CO", "events": [event("Aug 21", '1.25"', "1.5 mi")]}],
        storm_count=2, dates="August 15 and August 21, 2026"),
    "and N more": storm_ctx(
        [{"address": f"{n} Elm St, Golden, CO", "events": [event("Aug 15", '1.50"', "1.0 mi")]}
         for n in range(1, 4)], more_count=7),
    "no first name": storm_ctx(
        [{"address": "1 Main St, Denver, CO", "events": [event("Aug 15", '1.75"', "0.4 mi")]}],
        name=""),
}


def blk_dict(kind):
    return {"kind": kind}


def blk(**kw):
    return b.RENDERERS[kw["kind"]](b.clean_block(kw))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ShippedMarkupTest(unittest.TestCase):
    """Each block reproduces the shipped template's markup for the same content."""

    def same(self, ours, theirs):
        self.assertEqual(norm(ours), norm(theirs))

    def test_cta_button(self):
        self.same(blk(kind="button", label="SCHEDULE A FREE INSPECTION",
                      url="https://www.roofbrokersinc.com/Account/LoginRegister"),
                  section("CTA BUTTON", "SECONDARY LINK"))

    def test_band_with_checklist(self):
        self.same(blk(kind="band", title="Most of These Are Small",
                      intro="[b]80% of homes are certifiable as-is[/b] or with minor repair "
                            "work, and most hail repairs come in under the insurance deductible.",
                      items=["[b]$35 Five-Year Certification[/b] once it passes",
                             "Minimum repairs to certify \u2014 we fix what\u2019s necessary, "
                             "nothing more",
                             "Rapid-response reporting", "Rush repair scheduling",
                             "Multiple vendor estimates", "Pay out of closing costs"]),
                  section("BAND: MINOR DAMAGES", "TESTIMONIAL"))

    def test_testimonial_heading_stars_quote_and_link(self):
        ours = blk(kind="heading", text="Why Realtors Use Roof Brokers", size=19,
                   space_before=30, space_after=4) + blk(
            kind="testimonial",
            quote="I\u2019ve referred Roof Brokers many times over the years, often numerous "
                  "times per year when we have had hail storms. They have always been reliable, "
                  "reasonable and professional. They do what they say they\u2019ll do, often "
                  "when there is a time crunch.",
            who="Jeff G., Yelp", more_label="See more reviews",
            more_url="https://birdeye.com/roof-brokers-inc-145262047197938?rsource=2#reviews")
        self.same(ours, section("TESTIMONIAL", "DOTTED RULE"))

    def test_badges_render_like_the_shipped_pair(self):
        ours = render_src(blk(kind="badges"))
        self.same(ours, render_src(section("BADGES", "AD NOTICE")))

    def test_since_1992_line_is_a_text_block(self):
        self.same(blk(kind="text", text="Roof Brokers has certified roofs for Denver Metro real "
                      "estate agents [b=navy]since 1992[/b].", size=14, color="slate",
                      align="center", space_before=22, space_after=20),
                  section("SINCE 1992", "BADGES"))

    def test_secondary_link_line_is_a_text_block(self):
        self.same(blk(kind="text", text="Already have an account? [link={schedule_url}]Schedule "
                      "a new inspection[/link]", size=15, color="slate", align="center",
                      space_after=32),
                  render_src(section("SECONDARY LINK", "BAND: MINOR")).replace(
                      CTX["schedule_url"], "{{ schedule_url }}"))

    def test_body_copy_and_find_out_now_are_text_blocks(self):
        self.same(blk(kind="text", text="This came from a public weather record, and while "
                      "it\u2019s not confirmation that the roof was damaged, it may be worth "
                      "having a licensed roofer come out to check for damage.", space_after=20)
                  + blk(kind="text", text="Find out now, while you have time to deal with it.",
                        color="navy", bold=True, space_after=26),
                  section("BODY COPY", "CTA BUTTON"))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class MoreBlockSettingsTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(b.clean_block({"kind": "heading", "text": "Hi"}),
                         {"kind": "heading", "text": "Hi", "size": 24, "color": "navy",
                          "align": "center", "space_before": 0, "space_after": 12})
        self.assertEqual(b.clean_block({"kind": "badges"}),
                         {"kind": "badges", "images": ["badge_cra", "badge_bbb"], "width": 140})
        t = b.clean_block({"kind": "testimonial", "quote": " q ", "who": "w"})
        self.assertEqual((t["stars"], t["more_label"], t["more_url"], t["quote"]), (5, "", "", "q"))
        self.assertEqual(b.clean_block({"kind": "button", "label": "Go",
                                        "url": "https://x.test/"})["space_after"], 16)

    def test_required_settings(self):
        for bad in ({"kind": "heading"}, {"kind": "heading", "text": " "},
                    {"kind": "button", "label": "Go"}, {"kind": "button", "url": "https://x.test/"},
                    {"kind": "band", "title": "t", "intro": "i"},
                    {"kind": "band", "title": "t", "items": ["a"]},
                    {"kind": "testimonial", "quote": "q"}, {"kind": "testimonial", "who": "w"}):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block(bad)

    def test_heading_sizes(self):
        for size in (19, 21, 24):
            self.assertEqual(b.clean_block({"kind": "heading", "text": "x", "size": size})["size"], size)
        for bad in (16, 20, "24"):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block({"kind": "heading", "text": "x", "size": bad})

    def test_heading_text_uses_the_inline_markup_rules(self):
        for bad in ("[b]x", "{nope}", "[[x]]", "a" * 201):
            with self.assertRaises(b.BlockError, msg=bad):
                b.clean_block({"kind": "heading", "text": bad})

    def test_button_addresses(self):
        for ok in ("https://x.test/a", "{schedule_url}"):
            self.assertEqual(b.clean_block({"kind": "button", "label": "Go", "url": ok})["url"], ok)
        for bad in ("http://x.test", "javascript:alert(1)", "", "x.test", 5, None,
                    "https://a.azurewebsites.net/x", "{total}", "https://x.test/a b"):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block({"kind": "button", "label": "Go", "url": bad})

    def test_button_label_is_plain_text(self):
        for bad in ("[b]x[/b]x{total}", "{{ 7*7 }}", "x" * 41, "[[x]]"):
            with self.assertRaises(b.BlockError, msg=bad):
                b.clean_block({"kind": "button", "label": bad, "url": "https://x.test/"})
        html = blk(kind="button", label="A & <B>", url="https://x.test/")
        self.assertIn("A &amp; &lt;B&gt;", html)

    def test_button_colour_is_in_both_places(self):
        html = blk(kind="button", label="Go", url="https://x.test/", color="red")
        self.assertIn('bgcolor="#c0392b"', html)
        self.assertIn("background-color:#c0392b", html)

    def test_band_items(self):
        base = {"kind": "band", "title": "t", "intro": "i"}
        self.assertEqual(len(b.clean_block({**base, "items": ["a"] * 10})["items"]), 10)
        for bad in ([], ["a"] * 11, "a", [""], [5], ["[b]x"], ["a" * 201], None):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block({**base, "items": bad})
        for key, bad in (("title", "x" * 81), ("title", "{total}"), ("intro", "x" * 401),
                         ("intro", "[b]x")):
            with self.assertRaises(b.BlockError, msg=key):
                b.clean_block({**base, "items": ["a"], key: bad})

    def test_band_last_row_has_no_spacing(self):
        html = blk(kind="band", title="t", intro="i", items=["a", "b"])
        self.assertEqual(html.count("padding-bottom:10px;"), 2)     # first row only: tick + text

    def test_testimonial_settings(self):
        base = {"kind": "testimonial", "quote": "q", "who": "w"}
        for stars in (0, 3, 5):
            self.assertEqual(b.clean_block({**base, "stars": stars})["stars"], stars)
        for bad in (-1, 6, "5", True):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block({**base, "stars": bad})
        for key, bad in (("quote", "x" * 501), ("quote", "{total}"), ("who", "x" * 81),
                         ("more_label", "x" * 61)):
            with self.assertRaises(b.BlockError, msg=key):
                b.clean_block({**base, key: bad})

    def test_testimonial_link_needs_both_parts_and_an_https_address(self):
        base = {"kind": "testimonial", "quote": "q", "who": "w"}
        for bad in ({"more_label": "More"}, {"more_url": "https://x.test/"},
                    {"more_label": "More", "more_url": "http://x.test/"}):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block({**base, **bad})
        html = blk(**base)
        self.assertNotIn("<a ", html)
        self.assertEqual(html.count("&#9733;"), 5)
        self.assertNotIn("&#9733;", blk(**base, stars=0))
        self.assertIn("<a ", blk(**base, more_label="More", more_url="https://x.test/"))

    def test_testimonial_star_count_and_row(self):
        base = {"kind": "testimonial", "quote": "q", "who": "w"}
        self.assertEqual(blk(**base, stars=3).count("&#9733;"), 3)
        self.assertNotIn("letter-spacing:3px", blk(**base, stars=0))
        self.assertIn("letter-spacing:3px", blk(**base, stars=1))

    def test_testimonial_link_can_use_the_schedule_address(self):
        html = blk(kind="testimonial", quote="q", who="w", more_label="More",
                   more_url="{schedule_url}")
        self.assertIn('href="{{ schedule_url }}"', html)
        self.assertEqual(render_src(html).count('href="https://x.test/s"'), 1)

    def test_testimonial_text_is_escaped(self):
        html = blk(kind="testimonial", quote="<b>x</b> & y", who='"Z"')
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", html)
        self.assertNotIn("<b>", html)

    def test_badges_settings(self):
        for bad in ([], ["a"] * 5, "badge_cra", [5], ["a b"], ["x']}}{{ 7*7 }}{{ y['"], None):
            with self.assertRaises(b.BlockError, msg=repr(bad)):
                b.clean_block({"kind": "badges", "images": bad})
        for width in (38, 202, 141):
            with self.assertRaises(b.BlockError, msg=repr(width)):
                b.clean_block({"kind": "badges", "width": width})
        html = blk(kind="badges", images=["one", "two", "three"], width=100)
        self.assertEqual(html.count("<img "), 3)
        self.assertIn("images['three']['url']", html)

    def test_defaults_list_is_not_shared_between_blocks(self):
        first = b.clean_block({"kind": "badges"})
        first["images"].append("zzz")
        self.assertEqual(b.clean_block({"kind": "badges"})["images"], ["badge_cra", "badge_bbb"])

    def test_all_blocks_together_compile_validate_and_render(self):
        t = tpl({"kind": "heading", "text": "Hi"}, {"kind": "button", "label": "Go",
                                                      "url": "{schedule_url}"},
                {"kind": "band", "title": "t", "intro": "i", "items": ["a", "b"]},
                {"kind": "testimonial", "quote": "q", "who": "w"}, {"kind": "badges"})
        subject, html = b.compile_blocks(t)
        out = r.render(subject, html, {**CTX, "images": {
            "badge_cra": {"url": "https://x.test/c.png", "alt": "C"},
            "badge_bbb": {"url": "https://x.test/b.png", "alt": "B"}}})[1]
        self.assertIn('href="https://x.test/s"', out)
        self.assertEqual(out.count("<img "), 2)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SystemBlockTest(unittest.TestCase):
    """The data-driven blocks render exactly what the shipped template renders."""

    PAIRS = (("headline", "HEADLINE", "GREETING"),
             ("greeting", "GREETING", "THE STORM DATA PANEL"),
             ("storm_panel", "THE STORM DATA PANEL", "YOUR LISTINGS"),
             ("listings", "YOUR LISTINGS", "BODY COPY"))

    def test_each_block_matches_the_shipped_section_in_every_situation(self):
        for kind, start, end in self.PAIRS:
            for label, ctx in STORMS.items():
                with self.subTest(block=kind, situation=label):
                    self.assertEqual(norm(render_src(blk(kind=kind), ctx)),
                                     norm(render_src(section(start, end), ctx)))

    def test_the_situations_really_differ(self):
        # guards the test above: the singular/plural and conditional branches are exercised
        out = {label: render_src(blk(kind="headline") + blk(kind="greeting")
                                 + blk(kind="listings"), ctx) for label, ctx in STORMS.items()}
        self.assertIn("Recent Hail Storm Reported Near Your Listing", out["one storm, one listing"])
        self.assertIn("Storms Reported Near Your Listings", out["several storms and listings"])
        self.assertIn("reports</span>", out["several storms and listings"])
        self.assertIn("and 7 more", out["and N more"])
        self.assertNotIn("and 7 more", out["one storm, one listing"])
        self.assertIn("Hello,", out["no first name"])
        self.assertIn("Pat,", out["one storm, one listing"])

    def test_the_blocks_take_no_settings(self):
        for kind in ("headline", "greeting", "storm_panel", "listings"):
            self.assertEqual(b.clean_block({"kind": kind}), {"kind": kind})
            with self.assertRaises(b.BlockError, msg=kind):
                b.clean_block({"kind": kind, "text": "reworded"})

    def test_values_from_the_data_are_escaped(self):
        ctx = storm_ctx([{"address": "<script>x</script>", "events": [event("d", "h", "m")]}],
                        name="<i>Pat</i>")
        out = render_src(blk(kind="greeting") + blk(kind="listings"), ctx)
        self.assertNotIn("<script", out)
        self.assertNotIn("<i>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_a_missing_storm_field_fails_loudly(self):
        for kind, field in (("headline", "storm_dates_label"), ("storm_panel", "largest_hail"),
                            ("storm_panel", "nearest_miles"), ("listings", "properties")):
            subject, html = b.compile_blocks(tpl(blk_dict(kind)))
            ctx = {k: v for k, v in STORMS["one storm, one listing"].items() if k != field}
            with self.assertRaises(r.RenderError, msg=f"{kind} without {field}"):
                r.render(subject, html, ctx)

    def test_a_full_storm_email_compiles_validates_and_renders(self):
        kinds = ("headline", "greeting", "storm_panel", "listings")
        subject, html = b.compile_blocks(tpl(*[blk_dict(k) for k in kinds]))
        out = r.render(subject, html, STORMS["several storms and listings"])[1]
        self.assertIn("Recent Hail Storms Reported Near Your Listings", out)
        self.assertIn("9 Oak Ave &amp; Co", out)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class GoldenTest(unittest.TestCase):
    """The built-in template, built from blocks, is the shipped template (item 203, piece 2)."""

    @staticmethod
    def with_images(ctx):
        # the images the built-in template needs, as the admin page seeds them
        return {**ctx, "images": {
            "logo": {"url": ctx["logo_url"], "alt": "Roof Brokers Inc."},
            "badge_cra": {"url": ctx["badge_cra_url"], "alt": "CRA Member"},
            "badge_bbb": {"url": ctx["badge_bbb_url"], "alt": "BBB Accredited"}}}

    def test_block_built_email_matches_the_shipped_file_in_every_situation(self):
        subject_src, body_src = b.compile_blocks(b.HAIL_ALERT_BLOCKS)
        shipped_subject = (ROOT / "hailsys/email/templates/hail_alert_subject.txt").read_text()
        for label, ctx in STORMS.items():
            with self.subTest(situation=label):
                ctx = self.with_images(ctx)
                ours = r.render(subject_src, body_src, ctx)
                theirs = r.render(shipped_subject, SHIPPED, ctx)
                self.assertEqual(ours[0], theirs[0])
                self.assertEqual(norm(ours[1]), norm(theirs[1]))

    def test_the_comparison_sees_a_real_difference(self):
        # guards the test above against comparing two empty or identical-by-accident strings
        tweaked = {**b.HAIL_ALERT_BLOCKS, "blocks": [
            {**blk_, "space_after": 22} if blk_["kind"] == "text" and blk_.get("space_after") == 20
            else blk_ for blk_ in b.HAIL_ALERT_BLOCKS["blocks"]]}
        ctx = self.with_images(STORMS["one storm, one listing"])
        subject_src, body_src = b.compile_blocks(tweaked)
        ours = r.render(subject_src, body_src, ctx)[1]
        theirs = r.render("x", SHIPPED, ctx)[1]
        self.assertNotEqual(norm(ours), norm(theirs))
        self.assertGreater(len(norm(theirs)), 5000)

    def test_the_seed_is_a_valid_template_and_is_not_changed_by_compiling(self):
        before = repr(b.HAIL_ALERT_BLOCKS)
        self.assertEqual(b.clean_template(b.HAIL_ALERT_BLOCKS)["blocks"][-1]["kind"], "footer")
        b.compile_blocks(b.HAIL_ALERT_BLOCKS)
        self.assertEqual(repr(b.HAIL_ALERT_BLOCKS), before)

    def test_the_seed_only_uses_images_the_admin_page_always_has(self):
        used = {x["image"] for x in b.HAIL_ALERT_BLOCKS["blocks"] if x["kind"] == "image"}
        for x in b.HAIL_ALERT_BLOCKS["blocks"]:
            if x["kind"] == "badges":
                used.update(x.get("images", ["badge_cra", "badge_bbb"]))
        self.assertEqual(used, {"logo", "badge_cra", "badge_bbb"})


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ImagesUsedTest(unittest.TestCase):
    def test_names_from_image_and_badges_blocks(self):
        t = {"blocks": [{"kind": "image", "image": "logo"}, {"kind": "text", "text": "x"},
                        {"kind": "badges", "images": ["a", "b"]},
                        {"kind": "image", "image": "logo"}]}
        self.assertEqual(b.images_used(t), {"logo", "a", "b"})

    def test_a_badges_block_without_images_uses_the_default_pair(self):
        self.assertEqual(b.images_used({"blocks": [{"kind": "badges"}]}),
                         {"badge_cra", "badge_bbb"})

    def test_the_built_in_seed_uses_exactly_its_three(self):
        self.assertEqual(b.images_used(b.HAIL_ALERT_BLOCKS),
                         {"logo", "badge_cra", "badge_bbb"})

    def test_a_template_with_no_images(self):
        self.assertEqual(b.images_used({"blocks": [{"kind": "divider"}]}), set())

    def test_malformed_input_is_tolerated(self):
        for bad in (None, [], "x", {}, {"blocks": None}, {"blocks": "x"},
                    {"blocks": [None, 5, "x", {"kind": "image"}, {"kind": "image", "image": 5},
                                {"kind": "badges", "images": "badge_cra"},
                                {"kind": "badges", "images": [5, None]}]}):
            self.assertEqual(b.images_used(bad), set(), repr(bad))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ContextImagesTest(unittest.TestCase):
    def settings(self, images):
        return r.EmailSettings("https://x.test/l.png", "https://x.test/c.png",
                               "https://x.test/b.png", "e@x.test", "https://x.test/s",
                               images=images)

    MATCH = [{"listing_id": 1, "address": "1 Main", "magnitude": None,
              "utc_datetime": datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc),
              "distance_miles": Decimal("1")}]

    def test_the_context_carries_the_settings_images(self):
        images = {"logo": {"url": "https://x.test/l.png", "alt": "L"}}
        ctx = r.build_context("Pat", self.MATCH, self.settings(images))
        self.assertEqual(ctx["images"], images)

    def test_settings_without_images_give_an_empty_map(self):
        self.assertEqual(r.build_context("Pat", self.MATCH, self.settings({}))["images"], {})
        plain = r.EmailSettings("https://x.test/l.png", "https://x.test/c.png",
                                "https://x.test/b.png", "e@x.test", "https://x.test/s")
        self.assertEqual(r.build_context("Pat", self.MATCH, plain)["images"], {})

    def test_the_context_gets_a_copy_so_one_email_cannot_change_the_next(self):
        images = {"logo": {"url": "https://x.test/l.png", "alt": "L"}}
        settings = self.settings(images)
        ctx = r.build_context("Pat", self.MATCH, settings)
        ctx["images"]["logo"]["alt"] = "changed"
        ctx["images"]["new"] = {}
        self.assertEqual(settings.images, {"logo": {"url": "https://x.test/l.png", "alt": "L"}})


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ErrorPositionTest(unittest.TestCase):
    """A markup error quotes the place it is (item 203, piece 5.2)."""

    def error(self, text, fn=None):
        with self.assertRaises(b.BlockError) as ctx:
            (fn or (lambda t: b.rich(t, "w")))(text)
        return str(ctx.exception)

    @staticmethod
    def quote(message):
        """(the quoted text without its ellipses, cut at the start?, cut at the end?)"""
        import re
        found = re.search(r'\((?:at|opened at) "(.*)"\)$', message)
        assert found, message
        inner = found.group(1)
        return inner.removeprefix("...").removesuffix("..."), inner.startswith("..."), inner.endswith("...")

    def check(self, text, must_contain, **kw):
        message = self.error(text, **kw)
        inner, _, _ = self.quote(message)
        collapsed = " ".join(text.split())
        self.assertIn(inner, collapsed, message)                  # a real piece of the text
        self.assertIn(must_contain, inner, message)               # and it holds the culprit
        self.assertLessEqual(len(inner), 35, message)
        return message

    def test_an_unclosed_tag_quotes_where_it_was_opened(self):
        text = "Our spring sale: [b]1992 plus more words follow here and keep going on"
        message = self.check(text, "[b]1992")
        self.assertIn("[b] is never closed (opened at", message)

    def test_it_is_the_innermost_unclosed_tag_that_is_named(self):
        message = self.check("[b]one [i]two three four five six seven eight nine ten", "[i]two")
        self.assertIn("[i] is never closed", message)

    def test_a_stray_closing_tag(self):
        message = self.check("Hello there [/b] and more", "[/b]")
        self.assertIn("[/b] does not match an open tag", message)

    def test_a_closing_tag_in_the_wrong_order(self):
        message = self.check("[b]bold [i]both[/b] end[/i]", "[/b]")
        self.assertIn("[/b] does not match", message)

    def test_a_closing_tag_with_a_setting(self):
        self.check("[b]x[/b=navy]", "[/b=navy]")

    def test_nesting_too_deep(self):
        message = self.check("[b][i][b][i][b]x[/b][/i][/b][/i][/b]", "[b]x")
        self.assertIn("nested more than 4 deep", message)

    def test_an_unknown_merge_field(self):
        message = self.check("Dear agent, {nope} was hit by hail near you", "{nope}")
        self.assertIn("{nope} is not a merge field", message)

    def test_a_merge_field_that_belongs_to_another_place(self):
        self.check("Hello {agent_first_name}, welcome", "{agent_first_name}")

    def test_a_stray_brace_is_quoted_at_the_brace(self):
        self.assertIn("a } b", self.check("a } b", "}"))
        self.check("Plenty of earlier words here, then a { brace and more after it", "{ brace")
        self.check("closing } first, then {total} fine", "}")

    def test_double_square_brackets(self):
        message = self.check("see [[trackingImage]] here", "[[trackingImage")
        self.assertIn("double square brackets", message)

    def test_a_bad_colour_or_link_inside_a_tag(self):
        self.assertIn("the color must be one of", self.check("text [color=pink]x[/color] tail", "[color=pink]"))
        self.assertIn("https://", self.check("go [link=http://x.test]here[/link]", "[link=http"))
        self.assertIn("needs an address", self.check("go [link]here[/link] now", "[link]"))
        self.check("a [b=#ff0000]x[/b] b", "[b=#ff0000]")

    def test_a_link_inside_a_link(self):
        message = self.check("[link={schedule_url}]a [link={schedule_url}]b[/link][/link]", "[link={sch")
        self.assertIn("cannot hold another link", message)

    def test_double_square_brackets_far_into_the_text(self):
        message = self.check("w" * 80 + " see [[trackingImage]] here " + "w" * 40, "[[trackingImage")
        self.assertTrue(self.quote(message)[1])                     # the quote was cut at the start

    def test_a_problem_after_a_tag_is_quoted_at_its_own_place_not_the_start_of_the_chunk(self):
        # the text between tags is checked piece by piece; each piece must know where it begins
        for tail, culprit in ((" {nope} then more words", "{nope}"), (" a } brace after it", "}"),
                              (" and {agent_first_name} here", "{agent_first_name}")):
            text = "[b]" + "x" * 60 + "[/b] " + "y" * 30 + tail
            message = self.check(text, culprit)
            self.assertTrue(self.quote(message)[1], message)

    def test_a_stray_brace_before_a_merge_field_in_the_same_stretch(self):
        message = self.check("w" * 60 + " } " + "{total} and then the rest of it", "w } {total")
        self.assertTrue(self.quote(message)[1], message)

    def test_a_stray_brace_after_an_earlier_merge_field_in_the_same_stretch(self):
        message = self.check("{total} " + "w" * 60 + " } " + "{total} end of it all", "w } {total")
        self.assertTrue(self.quote(message)[1], message)

    def test_a_stray_brace_in_a_stretch_that_follows_several_merge_fields(self):
        text = "{total}" + "a" * 40 + "{total}" + "w" * 60 + " } " + "{total} end of it all"
        message = self.check(text, "w } {total")
        self.assertTrue(self.quote(message)[1], message)

    def test_a_stray_brace_after_a_tag_and_after_merge_fields(self):
        text = ("q" * 60 + "[b]" + "{total}" + "a" * 40 + "{total}" + "z" * 60 + " } " + "{total} end")
        self.check(text, "z } {total")

    def test_a_problem_in_the_stretch_after_a_late_tag(self):
        message = self.check("w" * 60 + "[b]x[/b]" + "y" * 50 + " {nope} " + "[i]z[/i] tail", "{nope}")
        self.assertTrue(self.quote(message)[1], message)

    def test_a_problem_between_two_tags_not_only_after_the_last(self):
        message = self.check("[b]x[/b] " + "y" * 50 + " {nope} " + "[i]z[/i] tail", "{nope}")
        self.assertTrue(self.quote(message)[1], message)

    def test_the_unclosed_tag_is_found_when_other_tags_sit_far_after_it(self):
        text = "[b]start" + "x" * 70 + "[i]inner[/i]" + "y" * 40
        message = self.check(text, "[b]start")
        self.assertIn("[b] is never closed", message)

    def test_a_problem_after_two_tags_is_still_found(self):
        text = "[b]a[/b] " + "m" * 40 + " [i]b[/i] " + "n" * 40 + " {nope} end"
        self.check(text, "{nope}")

    def test_the_unclosed_tag_is_the_one_quoted_when_others_were_opened_and_closed_after_it(self):
        message = self.check("[b]start [i]inner[/i] and then it never closes, long text here", "[b]start")
        self.assertIn("[b] is never closed", message)
        message = self.check("[b]one[/b] [i]two [b]three[/b] four and no end to the italic", "[i]two")
        self.assertIn("[i] is never closed", message)

    def test_the_first_problem_in_reading_order_is_the_one_reported(self):
        message = self.error("start {first} then {second} and [b]open")
        self.assertIn("{first}", message)
        self.assertNotIn("{second}", message.split("(at")[0])

    def test_the_quote_comes_from_the_text_as_it_is_compiled(self):
        message = self.error("line one\n\n   line   two [b]oops")
        self.assertIn('"... line two [b]oops"', message)
        self.assertNotIn("\n", message)

    def test_cutting_marks_appear_only_where_the_text_was_cut(self):
        _, front, back = self.quote(self.error("a } b"))
        self.assertEqual((front, back), (False, False))
        _, front, back = self.quote(self.error("x" * 50 + "[b]y" + "z" * 50))
        self.assertEqual((front, back), (True, True))
        _, front, back = self.quote(self.error("[b]" + "y" * 80))
        self.assertEqual((front, back), (False, True))
        _, front, back = self.quote(self.error("y" * 80 + " }"))
        self.assertEqual((front, back), (True, False))

    def test_at_the_very_start_and_the_very_end(self):
        self.assertTrue(self.quote(self.error("{nope} then some more text after it ends here"))[0].startswith("{nope}"))
        self.assertTrue(self.quote(self.error("some words before it ends here then }"))[0].endswith("}"))

    def test_non_ascii_text_is_quoted_whole(self):
        message = self.error("caf\u00e9 \u2019 \u2014 {nope} \u00fcber")
        self.assertIn("caf\u00e9 \u2019 \u2014 {nope}", message)

    def test_the_plain_text_fields_quote_too(self):
        for fn in (lambda t: b.plain(t, "title", 100), lambda t: b.plain(t, "subject", 100, subject=True)):
            message = self.check("hello {oops}", "{oops}", fn=fn)
            self.assertIn("not a merge field", message)
            message = self.check("close } here", "}", fn=fn)
            self.assertIn("curly braces", message)

    def test_a_full_template_reports_the_quote_against_its_block(self):
        template = {"subject": {"text": "Storms near {total} homes"},
                    "blocks": [{"kind": "text", "text": "fine"},
                               {"kind": "text", "text": "Early words then [b]never closed here"},
                               {"kind": "footer"}]}
        with self.assertRaises(b.BlockError) as ctx:
            b.clean_template(template)
        (number, message), = ctx.exception.problems
        self.assertEqual(number, 2)
        self.assertIn('opened at "...ords then [b]never closed here"', message)

    def test_valid_text_is_unchanged(self):
        text = "Hello [b=navy]{total}[/b] [i]homes[/i] and [link={schedule_url}]book[/link]"
        self.assertEqual(b.rich(text, "w"),
                         'Hello <strong style="color:#1b4a80;">{{ total }}</strong> <em>homes</em> and '
                         '<a href="{{ schedule_url }}" style="' + b.LINK_STYLE + '">book</a>')

    def test_messages_still_hold_their_key_phrases(self):
        for text, phrase in (("[b]x", "never closed"), ("x[/b]", "does not match"),
                             ("{nope}", "merge field"), ("a }", "curly braces"),
                             ("[[x]]", "double square brackets")):
            self.assertIn(phrase, self.error(text))

    def test_a_quote_with_markup_in_it_is_just_text_in_the_message(self):
        message = self.error("<script>alert(1)</script> {nope}")
        self.assertIn("</script> {nope}", message)                # escaping is the page's job
        self.assertNotIn("\n", message)


if __name__ == "__main__":
    unittest.main()
