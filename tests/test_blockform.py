"""Tests for hailsys/email/blockform.py and BlockError.problems (item 203, piece 4.1) -- pure.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_blockform
"""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.email import blockform as f, blocks as b
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

FOOTER = {"kind": "footer"}
SUBJECT = {"text": "Storms near {total} homes"}


def tpl(*body, **over):
    t = {"title": "", "preheader": "", "subject": SUBJECT, "blocks": list(body) + [FOOTER]}
    t.update(over)
    return t


def form_from(template):
    """The form values a browser would post for this template: every field, as strings."""
    values = {"title": template["title"], "preheader": template["preheader"],
              "n_blocks": str(len(template["blocks"]))}
    sub = f.subject_form(template)
    values.update(subject_mode=sub["mode"], subject_preset=sub["preset"], subject_text=sub["text"])
    for i, block in enumerate(template["blocks"]):
        values[f"b{i}_kind"] = block["kind"]
        for field in f.fields_for(block, i):
            if field["widget"] == "checkbox":
                if field["checked"]:
                    values[field["name"]] = "on"
            else:
                values[field["name"]] = field["value"]
    return values


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ProblemsTest(unittest.TestCase):
    def problems(self, template):
        with self.assertRaises(b.BlockError) as ctx:
            b.clean_template(template)
        return ctx.exception.problems

    def test_a_block_problem_carries_its_block_number(self):
        got = self.problems(tpl({"kind": "text", "text": "ok"}, {"kind": "text", "text": "[b]x"},
                                {"kind": "zzz"}))
        self.assertEqual([n for n, _ in got], [2, 3])
        self.assertIn("never closed", got[0][1])
        self.assertIn("unknown kind", got[1][1])

    def test_template_level_problems_have_no_number(self):
        for template, word in ((tpl(title="x" * 200), "title"),
                               (tpl(subject={"text": ""}), "subject"),
                               (tpl(subject={"preset": "nope"}), "subject"),
                               ({"subject": SUBJECT, "blocks": []}, "at least one block"),
                               ({"subject": SUBJECT, "blocks": [{"kind": "divider"}]}, "footer"),
                               (tpl(css="x"), "unknown setting"),
                               ({"subject": SUBJECT, "blocks": [FOOTER] * 31}, "limited")):
            got = self.problems(template)
            self.assertTrue(any(n is None and word in m for n, m in got), (word, got))

    def test_block_and_template_problems_are_listed_together(self):
        got = self.problems(tpl({"kind": "text", "text": "{nope}"}, title="x" * 200))
        self.assertEqual(sorted(n is None for n, _ in got), [False, True])

    def test_the_message_is_still_the_joined_text(self):
        with self.assertRaises(b.BlockError) as ctx:
            b.clean_template(tpl({"kind": "text", "text": "[b]x"}, {"kind": "zzz"}))
        self.assertEqual(str(ctx.exception),
                         "; ".join(m for _, m in ctx.exception.problems))
        self.assertIn("block 1", str(ctx.exception))
        self.assertIn("block 2", str(ctx.exception))

    def test_a_plain_block_error_has_one_unnumbered_problem(self):
        self.assertEqual(b.BlockError("boom").problems, [(None, "boom")])
        with self.assertRaises(b.BlockError) as ctx:
            b.clean_template("not a template")
        self.assertEqual(ctx.exception.problems, [(None, "the template must be a set of settings")])

    def test_a_footer_not_last_is_not_numbered_to_a_block(self):
        got = self.problems({"subject": SUBJECT, "blocks": [FOOTER, {"kind": "divider"}]})
        self.assertEqual(got, [(None, "the footer block is required, once, and must be last")])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SpecTest(unittest.TestCase):
    def test_the_form_describes_exactly_the_settings_the_blocks_have(self):
        self.assertEqual(set(f.FIELDS), set(b.SPECS))
        for kind, fields in f.FIELDS.items():
            self.assertEqual({x["key"] for x in fields}, set(b.SPECS[kind]), kind)

    def test_every_kind_has_a_label_and_the_footer_cannot_be_added(self):
        self.assertEqual(set(f.KIND_LABELS), set(b.SPECS))
        self.assertNotIn("footer", [kind for kind, _ in f.ADDABLE])
        self.assertEqual({k for k, _ in f.ADDABLE}, set(b.SPECS) - {"footer"})

    def test_system_kinds_have_nothing_to_edit(self):
        for kind in f.SYSTEM_KINDS:
            self.assertEqual(f.FIELDS[kind], [])

    def test_select_choices_are_the_values_the_blocks_accept(self):
        sizes = next(x for x in f.FIELDS["text"] if x["key"] == "size")
        self.assertEqual(sizes["choices"], list(b.SIZES))
        colours = next(x for x in f.FIELDS["heading"] if x["key"] == "color")
        self.assertEqual(colours["choices"], list(b.PALETTE))

    def test_every_new_block_is_valid_as_added(self):
        for kind, _ in f.ADDABLE:
            block = f.new_block(kind)
            self.assertEqual(block["kind"], kind)
            b.clean_block(block)                          # raises if not valid

    def test_a_new_block_uses_the_defaults_and_the_starters(self):
        self.assertEqual(f.new_block("text")["size"], 16)
        self.assertEqual(f.new_block("text")["text"], "New paragraph")
        self.assertEqual(f.new_block("band")["items"], ["First item"])
        self.assertEqual(f.new_block("badges")["images"], ["badge_cra", "badge_bbb"])
        self.assertEqual(f.new_block("divider"), {"kind": "divider"})

    def test_new_blocks_do_not_share_their_lists(self):
        first = f.new_block("badges")
        first["images"].append("zzz")
        self.assertEqual(f.new_block("badges")["images"], ["badge_cra", "badge_bbb"])
        band = f.new_block("band")
        band["items"].append("zzz")                       # a list that comes from STARTERS
        self.assertEqual(f.new_block("band")["items"], ["First item"])
        self.assertEqual(f.STARTERS["band"]["items"], ["First item"])

    def test_the_starter_is_the_built_in_template_cleaned(self):
        s = f.starter()
        self.assertEqual(s, b.clean_template(b.HAIL_ALERT_BLOCKS))
        self.assertEqual(b.compile_blocks(s), b.compile_blocks(b.HAIL_ALERT_BLOCKS))
        s["blocks"].clear()
        self.assertTrue(f.starter()["blocks"])            # not shared with the next caller


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RoundTripTest(unittest.TestCase):
    def test_the_built_in_template_survives_the_form_unchanged(self):
        template = f.starter()
        self.assertEqual(b.clean_template(f.parse_form(form_from(template))), template)

    def test_every_kind_survives_the_form_unchanged(self):
        for kind, _ in f.ADDABLE:
            template = tpl(f.new_block(kind))
            self.assertEqual(b.clean_template(f.parse_form(form_from(template))),
                             b.clean_template(template), kind)

    def test_non_ascii_text_and_apostrophes_survive(self):
        template = tpl({"kind": "text", "text": "it’s — café & \"q\" <b>"})
        back = b.clean_template(f.parse_form(form_from(template)))
        self.assertEqual(back["blocks"][0]["text"], "it’s — café & \"q\" <b>")

    def test_unticked_boxes_come_back_false_and_ticked_true(self):
        template = tpl({"kind": "text", "text": "x", "bold": True, "italic": False})
        values = form_from(template)
        self.assertIn("b0_bold", values)
        self.assertNotIn("b0_italic", values)
        block = f.parse_form(values)["blocks"][0]
        self.assertIs(block["bold"], True)
        self.assertIs(block["italic"], False)

    def test_numbers_and_choices_come_back_as_numbers(self):
        block = f.parse_form(form_from(tpl({"kind": "text", "text": "x", "size": 14,
                                            "space_after": 30})))["blocks"][0]
        self.assertEqual((block["size"], block["space_after"]), (14, 30))
        self.assertIsInstance(block["size"], int)

    def test_list_fields_split_on_lines_and_drop_blanks(self):
        values = form_from(tpl({"kind": "band", "title": "t", "intro": "i", "items": ["a"]}))
        values["b0_items"] = "  first \r\n\r\n second\n   \nthird"
        self.assertEqual(f.parse_form(values)["blocks"][0]["items"], ["first", "second", "third"])

    def test_the_subject_modes(self):
        preset = tpl(subject={"preset": "hail_alert"})
        self.assertEqual(f.parse_form(form_from(preset))["subject"], {"preset": "hail_alert"})
        text = tpl(subject={"text": "Hello {total}"})
        self.assertEqual(f.parse_form(form_from(text))["subject"], {"text": "Hello {total}"})

    def test_the_subject_form_describes_both(self):
        self.assertEqual(f.subject_form(tpl(subject={"preset": "hail_alert"}))["mode"], "preset")
        self.assertEqual(f.subject_form(tpl(subject={"text": "x"}))["text"], "x")
        for junk in (None, {}, {"subject": "x"}, "x"):
            shown = f.subject_form(junk)
            self.assertEqual(shown["mode"], "text")
            self.assertEqual(shown["presets"], list(b.SUBJECT_PRESETS))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class UnreadableInputTest(unittest.TestCase):
    def test_words_in_a_number_box_are_passed_on_and_refused_by_the_check(self):
        values = form_from(tpl({"kind": "text", "text": "x"}))
        values["b0_space_after"] = "lots"
        self.assertEqual(f.parse_form(values)["blocks"][0]["space_after"], "lots")
        with self.assertRaises(b.BlockError) as ctx:
            b.clean_template(f.parse_form(values))
        self.assertEqual(ctx.exception.problems[0][0], 1)
        self.assertIn("space_after", ctx.exception.problems[0][1])

    def test_a_choice_that_is_not_offered_is_passed_on_and_refused(self):
        values = form_from(tpl({"kind": "text", "text": "x"}))
        values["b0_size"] = "99"
        self.assertEqual(f.parse_form(values)["blocks"][0]["size"], "99")
        with self.assertRaises(b.BlockError):
            b.clean_template(f.parse_form(values))

    def test_numbers_with_spaces_and_signs(self):
        values = form_from(tpl({"kind": "text", "text": "x"}))
        for raw, want in ((" 12 ", 12), ("-4", -4), ("1.5", "1.5"), ("", ""), ("1e3", "1e3"),
                          ("9" * 20, "9" * 20)):
            values["b0_space_after"] = raw
            self.assertEqual(f.parse_form(values)["blocks"][0]["space_after"], want, raw)

    def test_the_block_count_is_clamped_and_junk_means_none(self):
        for raw, count in (("abc", 0), ("", 0), ("-3", 0), ("0", 0), ("2", 2),
                           ("99999", f.MAX_FORM_BLOCKS)):
            self.assertEqual(len(f.parse_form({"n_blocks": raw})["blocks"]), count, raw)
        self.assertEqual(f.parse_form({})["blocks"], [])

    def test_an_oversized_form_is_reported_not_cut(self):
        template = tpl(*[{"kind": "divider"}] * (b.MAX_BLOCKS + 5))
        values = form_from(template)
        self.assertEqual(len(f.parse_form(values)["blocks"]), f.MAX_FORM_BLOCKS)
        with self.assertRaises(b.BlockError) as ctx:
            b.clean_template(f.parse_form(values))
        self.assertIn("limited", str(ctx.exception))

    def test_unknown_kinds_and_unknown_fields_do_not_raise(self):
        got = f.parse_form({"n_blocks": "2", "b0_kind": "script", "b0_text": "x",
                            "b1_kind": "text", "b1_onclick": "1", "b1_text": "y", "evil": "1"})
        self.assertEqual(got["blocks"][0], {"kind": "script"})
        self.assertEqual(got["blocks"][1]["kind"], "text")
        self.assertNotIn("onclick", got["blocks"][1])
        with self.assertRaises(b.BlockError):
            b.clean_template(got)

    def test_a_missing_kind_is_an_unknown_kind(self):
        self.assertEqual(f.parse_form({"n_blocks": "1"})["blocks"], [{"kind": ""}])

    def test_a_missing_field_is_left_for_the_default(self):
        block = f.parse_form({"n_blocks": "1", "b0_kind": "text", "b0_text": "x"})["blocks"][0]
        self.assertNotIn("size", block)
        self.assertEqual(b.clean_block(block)["size"], 16)

    def test_markup_is_not_interpreted_by_the_form(self):
        values = form_from(tpl({"kind": "text", "text": "x"}))
        values["b0_text"] = "{{ 7*7 }} <script>"
        self.assertEqual(f.parse_form(values)["blocks"][0]["text"], "{{ 7*7 }} <script>")
        with self.assertRaises(b.BlockError):
            b.clean_template(f.parse_form(values))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class FieldsForTest(unittest.TestCase):
    def test_values_come_from_the_block_or_the_default(self):
        shown = {x["key"]: x for x in f.fields_for({"kind": "text", "text": "hi", "size": 14}, 3)}
        self.assertEqual(shown["text"]["value"], "hi")
        self.assertEqual(shown["size"]["value"], "14")
        self.assertEqual(shown["color"]["value"], "dark")              # the default
        self.assertEqual(shown["text"]["name"], "b3_text")
        self.assertIs(shown["bold"]["checked"], False)

    def test_a_required_setting_with_no_value_shows_empty(self):
        shown = {x["key"]: x for x in f.fields_for({"kind": "text"}, 0)}
        self.assertEqual(shown["text"]["value"], "")

    def test_lists_show_one_item_per_line(self):
        shown = f.fields_for({"kind": "badges", "images": ["a", "b"]}, 0)
        self.assertEqual(next(x for x in shown if x["key"] == "images")["value"], "a\nb")

    def test_unknown_kinds_show_no_fields(self):
        self.assertEqual(f.fields_for({"kind": "zzz"}, 0), [])
        self.assertEqual(f.fields_for({}, 0), [])

    def test_the_field_description_is_not_changed(self):
        before = copy.deepcopy(f.FIELDS)
        f.fields_for({"kind": "text", "text": "x"}, 0)
        self.assertEqual(f.FIELDS, before)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class DraftOperationsTest(unittest.TestCase):
    def kinds(self, template):
        return [x["kind"] for x in template["blocks"]]

    def base(self):
        return tpl({"kind": "text", "text": "a"}, {"kind": "divider"}, {"kind": "text", "text": "b"})

    def test_a_block_is_added_above_the_footer(self):
        got = f.add_block(self.base(), "heading")
        self.assertEqual(self.kinds(got), ["text", "divider", "text", "heading", "footer"])

    def test_adding_without_a_footer_appends(self):
        got = f.add_block({"blocks": [{"kind": "divider"}]}, "text")
        self.assertEqual(self.kinds(got), ["divider", "text"])

    def test_the_footer_and_unknown_kinds_cannot_be_added(self):
        for kind in ("footer", "zzz", "", None):
            self.assertEqual(f.add_block(self.base(), kind), self.base(), kind)

    def test_the_block_limit_stops_adding(self):
        full = tpl(*[{"kind": "divider"}] * (b.MAX_BLOCKS - 1))
        self.assertEqual(len(full["blocks"]), b.MAX_BLOCKS)
        self.assertEqual(f.add_block(full, "text"), full)
        one_less = tpl(*[{"kind": "divider"}] * (b.MAX_BLOCKS - 2))
        self.assertEqual(len(f.add_block(one_less, "text")["blocks"]), b.MAX_BLOCKS)

    def test_moving_swaps_neighbours(self):
        got = f.move_block(self.base(), 0, 1)
        self.assertEqual(self.kinds(got), ["divider", "text", "text", "footer"])
        self.assertEqual(got["blocks"][1]["text"], "a")
        got = f.move_block(self.base(), 2, -1)
        self.assertEqual(self.kinds(got), ["text", "text", "divider", "footer"])

    def test_nothing_moves_past_the_ends_or_across_the_footer(self):
        base = self.base()
        self.assertEqual(f.move_block(base, 0, -1), base)             # top
        self.assertEqual(f.move_block(base, 2, 1), base)              # onto the footer
        self.assertEqual(f.move_block(base, 3, -1), base)             # the footer itself
        self.assertEqual(f.move_block(base, 3, 1), base)

    def test_bad_indexes_and_steps_change_nothing(self):
        base = self.base()
        for index, step in ((-1, 1), (99, -1), (1, 0), (1, 2), (1, -2), (-5, -1)):
            self.assertEqual(f.move_block(base, index, step), base, (index, step))

    def test_remove(self):
        got = f.remove_block(self.base(), 1)
        self.assertEqual(self.kinds(got), ["text", "text", "footer"])
        self.assertEqual(f.remove_block(self.base(), 3), self.base())     # not the footer
        for index in (-1, 4, 99):
            self.assertEqual(f.remove_block(self.base(), index), self.base(), index)

    def test_no_operation_changes_the_draft_it_is_given(self):
        base = self.base()
        before = copy.deepcopy(base)
        f.add_block(base, "text")
        f.move_block(base, 0, 1)
        f.remove_block(base, 0)
        self.assertEqual(base, before)

    def test_an_edited_draft_still_compiles(self):
        draft = f.starter()
        draft = f.add_block(draft, "text")
        draft = f.move_block(draft, len(draft["blocks"]) - 2, -1)
        draft = f.remove_block(draft, 1)
        self.assertTrue(b.compile_blocks(draft))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class DescribeTest(unittest.TestCase):
    def draft(self):
        return tpl({"kind": "text", "text": "a"}, {"kind": "headline"}, {"kind": "divider"})

    def test_each_block_is_described_in_order(self):
        shown = f.describe(self.draft())
        self.assertEqual([x["kind"] for x in shown], ["text", "headline", "divider", "footer"])
        self.assertEqual([x["number"] for x in shown], [1, 2, 3, 4])
        self.assertEqual([x["index"] for x in shown], [0, 1, 2, 3])
        self.assertEqual(shown[0]["label"], "Paragraph")
        self.assertEqual(shown[0]["fields"][0]["name"], "b0_text")

    def test_system_and_footer_flags(self):
        shown = f.describe(self.draft())
        self.assertEqual([x["system"] for x in shown], [False, True, False, False])
        self.assertEqual([x["footer"] for x in shown], [False, False, False, True])

    def test_move_buttons_follow_the_move_rules(self):
        shown = f.describe(self.draft())
        self.assertEqual([x["can_up"] for x in shown], [False, True, True, False])
        self.assertEqual([x["can_down"] for x in shown], [True, True, False, False])

    def test_every_button_agrees_with_what_move_block_really_does(self):
        draft = self.draft()
        for x in f.describe(draft):
            for step, key in ((-1, "can_up"), (1, "can_down")):
                moved = f.move_block(draft, x["index"], step) != draft
                self.assertEqual(moved, x[key], (x["index"], step))

    def test_problems_attach_to_their_block(self):
        shown = f.describe(self.draft(), [(2, "bad two"), (None, "general"), (2, "also two"),
                                          (9, "nowhere")])
        self.assertEqual(shown[1]["problems"], ["bad two", "also two"])
        self.assertEqual([x["problems"] for x in shown if x["index"] != 1], [[], [], []])

    def test_junk_drafts_describe_as_nothing_or_unknown(self):
        for junk in (None, [], "x", {}, {"blocks": None}, {"blocks": "x"}):
            self.assertEqual(f.describe(junk), [], repr(junk))
        shown = f.describe({"blocks": [None, 5, {"kind": "zzz"}, {}]})
        self.assertEqual([x["label"] for x in shown], ["Unknown block"] * 4)
        self.assertTrue(all(x["fields"] == [] for x in shown))

    def test_the_subject_labels_cover_every_preset(self):
        shown = f.subject_form(tpl())
        for preset in shown["presets"]:
            self.assertIn(preset, shown["labels"])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RichFieldsAndLegendTest(unittest.TestCase):
    def rich_fields(self):
        return sorted((kind, x["key"]) for kind, fields in f.FIELDS.items()
                      for x in fields if x["rich"])

    def test_exactly_the_four_fields_that_take_formatting_are_marked(self):
        self.assertEqual(self.rich_fields(), [("band", "intro"), ("band", "items"),
                                              ("heading", "text"), ("text", "text")])

    def test_every_marked_field_really_takes_formatting(self):
        samples = {("text", "text"): {"kind": "text", "text": "a [b]b[/b] [i]i[/i] {total}"},
                   ("heading", "text"): {"kind": "heading", "text": "a [b=navy]b[/b] {total}"},
                   ("band", "intro"): {"kind": "band", "title": "t", "items": ["x"],
                                       "intro": "[color=red]c[/color] {largest_hail}"},
                   ("band", "items"): {"kind": "band", "title": "t", "intro": "i",
                                       "items": ["[b]one[/b]", "{nearest_miles} away"]}}
        for key, block in samples.items():
            self.assertIn(key, self.rich_fields())
            self.assertTrue(b.clean_block(block), key)

    def test_the_unmarked_text_fields_really_do_not(self):
        for kind, key, block in (
                ("button", "label", {"kind": "button", "url": "https://x.test/", "label": "[b]x[/b]"}),
                ("band", "title", {"kind": "band", "intro": "i", "items": ["x"], "title": "[b]x[/b]"}),
                ("testimonial", "quote", {"kind": "testimonial", "who": "w", "quote": "[b]x[/b]"}),
                ("testimonial", "who", {"kind": "testimonial", "quote": "q", "who": "[b]x[/b]"})):
            marked = next(x for x in f.FIELDS[kind] if x["key"] == key)["rich"]
            self.assertFalse(marked, (kind, key))
            self.assertIn("[b]x[/b]", b.compile_blocks(tpl(block))[1], (kind, key))   # shown as typed

    def test_a_rich_field_points_at_the_guide_and_other_fields_do_not(self):
        for kind, fields in f.FIELDS.items():
            for x in fields:
                if x["rich"] and x["key"] != "items":
                    self.assertEqual(x["help"], f.RICH_HELP)
        self.assertIn("guide at the top of the page", f.RICH_HELP)      # the legend's place on the page
        items = next(x for x in f.FIELDS["band"] if x["key"] == "items")
        self.assertIn("formatting works on each line", items["help"])

    def test_fields_for_carries_the_flag_to_the_page(self):
        shown = {x["key"]: x["rich"] for x in f.fields_for({"kind": "text", "text": "x"}, 0)}
        self.assertEqual((shown["text"], shown["size"], shown["bold"]), (True, False, False))

    # ---- the guide ----

    def test_every_tag_example_compiles(self):
        for code, meaning in f.TAG_GUIDE:
            self.assertTrue(meaning)
            self.assertTrue(b.rich(code, "guide"), code)

    def test_every_colour_the_guide_names_is_in_the_palette(self):
        import re
        for code, _ in f.TAG_GUIDE:
            for name in re.findall(r"=([a-z]+)\]", code):
                if not name.startswith("http"):
                    self.assertIn(name, b.PALETTE, code)

    def test_the_guide_covers_every_kind_of_tag(self):
        import re
        kinds = {re.search(r"\[(b|i|color|link)", code).group(1) for code, _ in f.TAG_GUIDE}
        self.assertEqual(kinds, {"b", "i", "color", "link"})
        self.assertTrue(any("{schedule_url}" in code for code, _ in f.TAG_GUIDE))

    def test_every_merge_field_has_a_meaning_and_no_extras(self):
        self.assertEqual(set(f.MERGE_MEANINGS), set(b.TEXT_TOKENS))
        self.assertTrue(all(f.MERGE_MEANINGS.values()))

    def test_every_merge_field_in_the_legend_is_accepted_by_the_compiler(self):
        for item in f.legend()["merge_fields"]:
            self.assertTrue(b.rich("x " + item["token"], "guide"), item["token"])

    def test_the_legend_lists_the_palette_with_its_colours(self):
        colours = f.legend()["colours"]
        self.assertEqual([c["name"] for c in colours], list(b.PALETTE))
        self.assertEqual({c["name"]: c["hex"] for c in colours}, b.PALETTE)

    def test_the_legend_shows_the_values_of_the_chosen_sample(self):
        values = {x["token"]: x["example"] for x in f.legend("one")["merge_fields"]}
        self.assertEqual(values["{total}"], "1")
        self.assertEqual(values["{storm_dates_label}"], "August 15, 2026")
        self.assertEqual(values["{largest_hail}"], '1.75"')
        values = {x["token"]: x["example"] for x in f.legend("more")["merge_fields"]}
        self.assertEqual(values["{total}"], "18")
        values = {x["token"]: x["example"] for x in f.legend("several")["merge_fields"]}
        self.assertEqual(values["{storm_dates_label}"], "August 15 and August 21, 2026")

    def test_the_legend_works_for_every_situation_and_falls_back_for_a_bad_one(self):
        from hailsys.email import samples
        for key in list(samples.SITUATIONS) + ["nope", None, 5, ["one"]]:
            self.assertEqual(len(f.legend(key)["merge_fields"]), len(b.TEXT_TOKENS), repr(key))
        self.assertEqual(f.legend("nope"), f.legend("one"))

    def test_the_legend_is_in_a_fixed_order_and_does_not_change_between_calls(self):
        first = f.legend()
        self.assertEqual(first, f.legend())
        self.assertEqual([x["token"] for x in first["merge_fields"]],
                         sorted(x["token"] for x in first["merge_fields"]))
        first["tags"].clear()
        self.assertTrue(f.legend()["tags"])


if __name__ == "__main__":
    unittest.main()
