"""Tests for hailsys/queries/workstate.py -- work-state keys, labels and classes.

Run from the repo root:
    python3 -m unittest tests.test_workstate

Pure: no Flask, no database.  Reads style.css and _status_cell.html as text,
because the bug these guard against (parking-lot item 48) lived in the gap
between the Python, the template and the stylesheet.
"""

import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hailsys.queries import workstate as ws

KEYS = {ws.NOT_PULLED, ws.PULLING, ws.PULLED, ws.MATCHED, ws.MATCHED_NONE, ws.SENT}
CSS = (ROOT / "hailsys/web/static/style.css").read_text()
CELL = (ROOT / "hailsys/web/templates/_status_cell.html").read_text()

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def row(**kw):
    base = dict(pulled=False, matched=False, sent=False, match_ran=False,
                running=False, running_since=None, last_pulled_at=None)
    base.update(kw)
    return base


class TablesTest(unittest.TestCase):
    def test_every_key_has_a_label_and_a_class_and_nothing_else_does(self):
        self.assertEqual(set(ws.LABELS), KEYS)
        self.assertEqual(set(ws.CSS_CLASSES), KEYS)

    def test_labels_and_classes_are_distinct(self):
        self.assertEqual(len(set(ws.LABELS.values())), len(KEYS))
        self.assertEqual(len(set(ws.CSS_CLASSES.values())), len(KEYS))

    def test_keys_are_not_label_text(self):
        # A key that equals its label would let someone treat the two as one
        # thing again.
        for key, label in ws.LABELS.items():
            self.assertNotEqual(key, label)


class StyleSheetTest(unittest.TestCase):
    def test_every_class_has_a_rule(self):
        for key, cls in ws.CSS_CLASSES.items():
            with self.subTest(key=key):
                self.assertRegex(CSS, rf"\.{re.escape(cls)}(?![\w-])",
                                 f"{cls} has no rule in style.css")


class StatusCellTest(unittest.TestCase):
    def test_no_label_text_in_the_template(self):
        for label in ws.LABELS.values():
            with self.subTest(label=label):
                self.assertNotIn(label, CELL)

    def test_template_does_not_read_the_label_to_decide_anything(self):
        self.assertNotIn("work_state.state", CELL)
        # The label is only ever displayed.
        for use in re.findall(r"work_state\.label[^}]*", CELL):
            self.assertNotRegex(use, r"==|\bin\b")

    def test_every_key_the_template_names_is_a_real_key(self):
        branches = re.findall(r"work_state\.key\s*(?:==|not in|in)\s*([^%]+)%", CELL)
        self.assertTrue(branches, "template no longer branches on work_state.key")
        for expr in branches:
            for literal in re.findall(r"'([^']+)'", expr):
                with self.subTest(literal=literal):
                    self.assertIn(literal, KEYS)


class KeyOrderTest(unittest.TestCase):
    """The precedence _key() encodes: running, sent, matched, ran-and-pulled,
    pulled, nothing."""

    def key(self, **kw):
        return ws._key(row(**kw), NOW)

    def test_nothing(self):
        self.assertEqual(self.key(), ws.NOT_PULLED)

    def test_pulled(self):
        self.assertEqual(self.key(pulled=True), ws.PULLED)

    def test_matched_and_sent(self):
        self.assertEqual(self.key(pulled=True, matched=True, match_ran=True), ws.MATCHED)
        self.assertEqual(self.key(pulled=True, matched=True, sent=True), ws.SENT)

    def test_matched_outranks_zero_new_rows(self):
        # matched reflects rows in storm_listing_matches, not matches_created
        # (parking-lot item 89), so a re-run that created nothing still matches.
        self.assertEqual(self.key(pulled=True, matched=True, match_ran=True), ws.MATCHED)

    def test_ran_and_pulled_with_no_matches_is_none_in_range(self):
        self.assertEqual(self.key(pulled=True, match_ran=True), ws.MATCHED_NONE)

    def test_a_match_run_without_a_pull_is_not_none_in_range(self):
        self.assertEqual(self.key(match_ran=True), ws.NOT_PULLED)

    def test_running_outranks_everything_while_recent(self):
        recent = NOW - timedelta(minutes=1)
        self.assertEqual(self.key(running=True, running_since=recent, sent=True,
                                  matched=True, pulled=True), ws.PULLING)

    def test_a_dead_running_pull_stops_reading_pulling(self):
        old = NOW - ws.PULL_STALE_AFTER - timedelta(seconds=1)
        self.assertEqual(self.key(running=True, running_since=old, pulled=True), ws.PULLED)


class EntryTest(unittest.TestCase):
    def test_default_for_a_day_with_no_activity(self):
        today = date(2026, 10, 1)
        got = ws.state_for({}, date(2026, 9, 30), "HAIL", today)
        self.assertEqual(got["key"], ws.NOT_PULLED)
        self.assertEqual(got["label"], "Not pulled")
        self.assertEqual(got["css_class"], "badge-not-pulled")
        self.assertFalse(got["is_stale"])
        self.assertIsNone(got["last_pulled_at"])

    def test_old_day_with_no_activity_is_stale(self):
        today = date(2026, 10, 1)
        got = ws.state_for({}, today - timedelta(days=ws.CLAIM_WINDOW_DAYS + 1), "HAIL", today)
        self.assertTrue(got["is_stale"])

    def test_found_entry_is_returned_unchanged(self):
        entry = ws._entry(ws.SENT, is_stale=False, last_pulled_at=NOW)
        self.assertIs(ws.state_for({(date(2026, 9, 30), "HAIL"): entry},
                                   date(2026, 9, 30), "HAIL", date(2026, 10, 1)), entry)

    def test_both_builders_produce_the_same_fields(self):
        a = ws._entry(ws.PULLED, is_stale=False, last_pulled_at=None)
        b = ws.state_for({}, date(2026, 10, 1), "HAIL", date(2026, 10, 1))
        self.assertEqual(set(a), set(b))


if __name__ == "__main__":
    unittest.main()
