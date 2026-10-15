"""Tests for hailsys/email/samples.py and preview.py (item 203, piece 4.2).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_preview

The first classes are pure.  The last runs the real settings and images from the database
(skipped without one).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import blockform, blocks as b, emailconfig, preview as p, render as r, \
        samples as s
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

FOOTER = {"kind": "footer"}
SUBJECT = {"text": "Storms near {total} homes"}


def tpl(*body, **over):
    t = {"title": "", "preheader": "", "subject": SUBJECT, "blocks": list(body) + [FOOTER]}
    t.update(over)
    return t


def settings(**extra_images):
    images = {"logo": {"url": "https://x.test/l.png", "alt": "Logo"},
              "badge_cra": {"url": "https://x.test/c.png", "alt": "CRA"},
              "badge_bbb": {"url": "https://x.test/b.png", "alt": "BBB"}}
    images.update(extra_images)
    return r.EmailSettings("https://x.test/l.png", "https://x.test/c.png", "https://x.test/b.png",
                           "e@x.test", "https://x.test/s", images=images)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SamplesTest(unittest.TestCase):
    def context(self, key):
        agent, matches = s.situation(key)
        return r.build_context(agent, matches, settings())

    def test_the_four_situations_differ_in_the_ways_they_claim(self):
        self.assertEqual(list(s.SITUATIONS), ["one", "several", "more", "no_name"])
        one = self.context("one")
        self.assertEqual((one["storm_count"], one["total"], one["agent_first_name"]),
                         (1, 1, "Pat"))
        several = self.context("several")
        self.assertEqual((several["storm_count"], several["total"]), (2, 2))
        self.assertEqual(sorted(len(x["events"]) for x in several["properties"]), [1, 2])
        more = self.context("more")
        self.assertEqual((len(more["properties"]), more["more_count"], more["total"]),
                         (r.MAX_LISTINGS, 3, 18))
        self.assertEqual(self.context("no_name")["agent_first_name"], "")

    def test_every_situation_has_a_label(self):
        for key, value in s.SITUATIONS.items():
            self.assertTrue(value["label"], key)

    def test_an_unknown_or_unreadable_key_gives_the_default(self):
        default = s.situation(s.DEFAULT)
        for key in ("nope", "", None, 5, ["one"], {"a": 1}):
            self.assertEqual(s.situation(key), default, repr(key))

    def test_the_caller_cannot_change_the_stand_in_data(self):
        _, matches = s.situation("one")
        matches[0]["address"] = "changed"
        matches.append({})
        _, again = s.situation("one")
        self.assertEqual(len(again), 1)
        self.assertNotEqual(again[0]["address"], "changed")

    def test_the_data_is_invented_and_fixed(self):
        _, first = s.situation("several")
        _, second = s.situation("several")
        self.assertEqual(first, second)
        self.assertTrue(all("Example" in m["address"] or "Sample" in m["address"] for m in first))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class PreviewTest(unittest.TestCase):
    def test_a_good_template_previews_in_every_situation(self):
        for key in s.SITUATIONS:
            got = p.build_preview(blockform.starter(), settings(), key)
            self.assertTrue(got["ok"], key)
            self.assertEqual(got["problems"], [])
            self.assertIn("[[trackingImage]]", got["html"])
            self.assertEqual(got["size"], len(got["html"].encode("utf-8")))
            self.assertTrue(got["subject"], key)

    def test_the_situation_changes_the_preview(self):
        one = p.build_preview(blockform.starter(), settings(), "one")
        several = p.build_preview(blockform.starter(), settings(), "several")
        more = p.build_preview(blockform.starter(), settings(), "more")
        no_name = p.build_preview(blockform.starter(), settings(), "no_name")
        self.assertIn("A hail storm was reported near your listing at 123 Example St",
                      one["subject"])
        self.assertIn("Hail storms were reported near several of your listings", several["subject"])
        self.assertIn("and 3 more", more["html"])
        self.assertNotIn("and 3 more", one["html"])
        self.assertIn("Hello,", no_name["html"])
        self.assertIn("Pat,", one["html"])

    def test_the_default_situation_is_used_when_none_is_given(self):
        self.assertEqual(p.build_preview(blockform.starter(), settings())["html"],
                         p.build_preview(blockform.starter(), settings(), "one")["html"])

    def test_a_bad_block_comes_back_with_its_number_and_no_html(self):
        got = p.build_preview(tpl({"kind": "text", "text": "ok"}, {"kind": "text", "text": "[b]x"}),
                              settings())
        self.assertFalse(got["ok"])
        self.assertEqual([n for n, _ in got["problems"]], [2])
        self.assertIsNone(got["html"])
        self.assertIsNone(got["subject"])
        self.assertIsNone(got["size"])

    def test_template_level_problems_have_no_number(self):
        got = p.build_preview(tpl({"kind": "divider"}, title="x" * 500), settings())
        self.assertEqual([n for n, _ in got["problems"]], [None])

    def test_every_problem_is_returned(self):
        got = p.build_preview(tpl({"kind": "text", "text": "{nope}"}, {"kind": "zzz"},
                                  title="x" * 500), settings())
        self.assertEqual(sorted(str(n) for n, _ in got["problems"]), ["1", "2", "None"])

    def test_a_missing_image_is_named_against_each_block_using_it(self):
        got = p.build_preview(tpl({"kind": "image", "image": "ghost"}, {"kind": "divider"},
                                  {"kind": "badges", "images": ["badge_cra", "ghost", "spook"]}),
                              settings())
        self.assertFalse(got["ok"])
        self.assertEqual([n for n, _ in got["problems"]], [1, 3, 3])
        text = " ".join(m for _, m in got["problems"])
        self.assertIn("ghost", text)
        self.assertIn("spook", text)
        self.assertNotIn("badge_cra is not", text)
        self.assertIsNone(got["html"])

    def test_an_available_extra_image_previews(self):
        extra = {"url": "https://x.test/promo.png", "alt": "Promo"}
        got = p.build_preview(tpl({"kind": "image", "image": "promo"}), settings(promo=extra))
        self.assertTrue(got["ok"])
        self.assertIn('src="https://x.test/promo.png"', got["html"])
        self.assertIn('alt="Promo"', got["html"])

    def test_a_template_too_big_to_compile_is_a_problem_not_an_exception(self):
        big = tpl(*[{"kind": "text", "text": "&" * b.MAX_TEXT}] * (b.MAX_BLOCKS - 1))
        got = p.build_preview(big, settings())
        self.assertFalse(got["ok"])
        self.assertIn("too large", got["problems"][0][1])

    def test_a_render_failure_is_a_problem_not_an_exception(self):
        with mock.patch.object(p.render, "render", side_effect=r.RenderError("boom")):
            got = p.build_preview(blockform.starter(), settings())
        self.assertEqual(got["problems"], [(None, "boom")])
        self.assertFalse(got["ok"])

    def test_it_never_raises_on_junk(self):
        circular = {}
        circular["blocks"] = [circular]
        for junk in (None, [], "text", 5, {}, {"blocks": None}, {"blocks": [None]}, circular,
                     {"subject": 5, "blocks": "x", "title": []}):
            got = p.build_preview(junk, settings())
            self.assertFalse(got["ok"], repr(junk)[:40])
            self.assertTrue(got["problems"], repr(junk)[:40])
        for key in (None, 5, ["one"], {}):
            self.assertTrue(p.build_preview(blockform.starter(), settings(), key)["ok"])

    def test_a_message_over_the_gmail_limit_warns_but_still_renders(self):
        with mock.patch.object(p, "GMAIL_CLIP", 1000):
            got = p.build_preview(blockform.starter(), settings(), "more")
        self.assertTrue(got["ok"])
        self.assertTrue(got["html"])
        self.assertEqual(len(got["warnings"]), 1)
        self.assertIn("Gmail", got["warnings"][0])

    def test_a_message_under_the_limit_has_no_warning(self):
        self.assertEqual(p.build_preview(blockform.starter(), settings())["warnings"], [])

    def test_the_built_in_in_the_biggest_situation_is_within_the_gmail_limit(self):
        got = p.build_preview(blockform.starter(), settings(), "more")
        self.assertLess(got["size"], p.GMAIL_CLIP)

    def test_it_does_not_change_the_draft_or_the_settings(self):
        draft = blockform.starter()
        before = repr(draft)
        cfg = settings()
        images = repr(cfg.images)
        p.build_preview(draft, cfg, "more")
        self.assertEqual(repr(draft), before)
        self.assertEqual(repr(cfg.images), images)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class RealSettingsTest(unittest.TestCase):
    """The preview of the built-in starter, with the real images, equals the shipped file."""

    def test_the_starter_previews_exactly_like_the_shipped_template(self):
        import re
        import html as html_lib
        conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(conn.close)
        self.addCleanup(conn.rollback)
        cfg = emailconfig.load_settings(conn)
        shipped_subject = (ROOT / "hailsys/email/templates/hail_alert_subject.txt").read_text()
        shipped_body = (ROOT / "hailsys/email/templates/hail_alert.html").read_text()

        def norm(markup):
            markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
            markup = re.sub(r"\s+", " ", html_lib.unescape(markup))
            return re.sub(r"\s*([<>])\s*", r"\1", markup).strip()

        for key in s.SITUATIONS:
            with self.subTest(situation=key):
                agent, matches = s.situation(key)
                context = r.build_context(agent, matches, cfg)
                theirs = r.render(shipped_subject, shipped_body, context)
                got = p.build_preview(blockform.starter(), cfg, key)
                self.assertTrue(got["ok"], got["problems"])
                self.assertEqual(got["subject"], theirs[0])
                self.assertEqual(norm(got["html"]), norm(theirs[1]))


if __name__ == "__main__":
    unittest.main()
