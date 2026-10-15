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


if __name__ == "__main__":
    unittest.main()
