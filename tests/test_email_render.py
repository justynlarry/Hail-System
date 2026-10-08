"""Tests for hailsys/email/render.py -- pure, no database, no network.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_email_render
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.email import render as r
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

SETTINGS_ARGS = ("https://x.test/logo.png", "https://x.test/cra.jpg",
                 "https://x.test/bbb.png", "e@x.invalid",
                 "https://www.roofbrokersinc.com/Orders/Create")
UTC = timezone.utc


def match(lid, address, when, mag, dist):
    return {"listing_id": lid, "address": address, "utc_datetime": when,
            "magnitude": None if mag is None else Decimal(mag),
            "distance_miles": Decimal(dist)}


AUG15 = datetime(2026, 8, 15, 20, 19, tzinfo=UTC)      # 14:19 in Denver
AUG21 = datetime(2026, 8, 21, 18, 0, tzinfo=UTC)
JEBEL = "5825 South Jebel Way, Centennial, CO"

SUBJECT = "{{ total }} near {{ properties[0].address }}"
BODY = ("[[trackingImage]]{{ agent_first_name }}|{{ storm_dates_label }}|"
        "{% for p in properties %}{{ p.address }}"
        "{% for e in p.events %}[{{ e.date }} {{ e.hail_size }} {{ e.miles }}]{% endfor %}"
        "{% endfor %}")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class FormatTest(unittest.TestCase):
    def test_first_name_accepts_people(self):
        for name, want in (("Jane Smith", "Jane"), ("JANE SMITH", "Jane"),
                           ("Mary-Ann O'Neil", "Mary-Ann"), ("  Bob   Jones ", "Bob")):
            with self.subTest(name=name):
                self.assertEqual(r.first_name(name), want)

    def test_first_name_declines_anything_doubtful(self):
        for name in (None, "", "Jane", "Smith, Jane", "The Smith Team", "Smith Realty LLC",
                     "J. Smith", "Jane2 Smith", "Real Estate Group"):
            with self.subTest(name=name):
                self.assertIsNone(r.first_name(name))

    def test_hail_label(self):
        self.assertEqual(r.hail_label(Decimal("1.75")), '1.75"')
        self.assertEqual(r.hail_label(Decimal("1")), '1.00"')
        self.assertEqual(r.hail_label(None), r.NOT_REPORTED)
        self.assertEqual(r.hail_label(Decimal("NaN")), r.NOT_REPORTED)
        self.assertEqual(r.hail_label(Decimal("Infinity")), r.NOT_REPORTED)

    def test_miles_label(self):
        self.assertEqual(r.miles_label(Decimal("3.80")), "3.8 mi")
        with self.assertRaises(r.RenderError):
            r.miles_label(Decimal("NaN"))

    def test_the_storm_day_is_the_denver_day(self):
        late = datetime(2026, 8, 16, 2, 30, tzinfo=UTC)      # 20:30 on Aug 15 in Denver
        self.assertEqual(r.local_date(late), date(2026, 8, 15))
        self.assertEqual(r.local_date(AUG15), date(2026, 8, 15))

    def test_dates_label(self):
        d = date
        self.assertEqual(r.storm_dates_label([d(2026, 8, 15)]), "August 15, 2026")
        self.assertEqual(r.storm_dates_label([d(2026, 8, 21), d(2026, 8, 15)]),
                         "August 15 and August 21, 2026")
        self.assertEqual(r.storm_dates_label([d(2026, 8, 15), d(2026, 8, 21), d(2026, 8, 28)]),
                         "August 15, August 21 and August 28, 2026")
        self.assertEqual(r.storm_dates_label([d(2026, 12, 30), d(2027, 1, 2)]),
                         "December 30, 2026 and January 2, 2027")
        self.assertEqual(r.storm_dates_label([d(2026, 8, 15), d(2026, 8, 15)]),
                         "August 15, 2026")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SettingsTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(r.EmailSettings(*SETTINGS_ARGS).contact_email, "e@x.invalid")

    def test_a_free_hosting_link_is_refused(self):
        args = list(SETTINGS_ARGS)
        args[4] = "https://roofbrokersinc-preview.azurewebsites.net/Orders/Create"
        with self.assertRaises(r.SettingsError):
            r.EmailSettings(*args)

    def test_http_is_refused(self):
        args = list(SETTINGS_ARGS)
        args[0] = "http://x.test/logo.png"
        with self.assertRaises(r.SettingsError):
            r.EmailSettings(*args)

    def test_a_bad_contact_email_is_refused(self):
        args = list(SETTINGS_ARGS)
        args[3] = "not an email"
        with self.assertRaises(r.SettingsError):
            r.EmailSettings(*args)

    def test_from_env_missing_is_loud(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(r.SettingsError) as cm:
                r.EmailSettings.from_env()
        self.assertIn("EMAIL_", str(cm.exception))

    def test_from_env(self):
        env = dict(zip(("EMAIL_LOGO_URL", "EMAIL_BADGE_CRA_URL", "EMAIL_BADGE_BBB_URL",
                        "EMAIL_CONTACT_EMAIL", "EMAIL_SCHEDULE_URL"), SETTINGS_ARGS))
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(r.EmailSettings.from_env().schedule_url, SETTINGS_ARGS[4])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ContextTest(unittest.TestCase):
    def setUp(self):
        self.s = r.EmailSettings(*SETTINGS_ARGS)

    def test_one_storm_one_listing(self):
        c = r.build_context("Jane Smith", [match(1, JEBEL, AUG15, "1.75", "3.80")], self.s)
        self.assertEqual((c["storm_count"], c["total"], c["more_count"]), (1, 1, 0))
        self.assertEqual(c["storm_dates_label"], "August 15, 2026")
        self.assertEqual((c["largest_hail"], c["nearest_miles"]), ('1.75"', "3.8 mi"))
        self.assertEqual(c["agent_first_name"], "Jane")
        self.assertEqual(c["properties"][0]["events"],
                         [{"date": "August 15, 2026", "hail_size": '1.75"', "miles": "3.8 mi"}])

    def test_two_storms_on_one_listing(self):
        c = r.build_context(None, [match(1, JEBEL, AUG21, "1.00", "4.60"),
                                   match(1, JEBEL, AUG15, "1.75", "3.80")], self.s)
        self.assertEqual(c["storm_count"], 2)
        self.assertEqual(c["storm_dates_label"], "August 15 and August 21, 2026")
        self.assertEqual([e["date"] for e in c["properties"][0]["events"]],
                         ["August 15, 2026", "August 21, 2026"])       # chronological
        self.assertEqual((c["largest_hail"], c["nearest_miles"]), ('1.75"', "3.8 mi"))
        self.assertEqual(c["agent_first_name"], "")

    def test_listings_are_ordered_by_reports_then_hail(self):
        c = r.build_context("A B", [match(1, "A St", AUG15, "1.00", "1.00"),
                                    match(2, "B St", AUG15, "2.00", "1.00"),
                                    match(3, "C St", AUG15, "1.50", "1.00"),
                                    match(3, "C St", AUG21, "1.00", "1.00")], self.s)
        self.assertEqual([p["address"] for p in c["properties"]],
                         ["C St", "B St", "A St"])

    def test_the_cap_counts_the_rest_and_figures_cover_everything(self):
        matches = [match(i, f"{i} St", AUG15, "1.00", "5.00") for i in range(1, 21)]
        matches.append(match(99, "Hidden St", AUG15, "3.00", "0.25"))   # ranks last, hidden
        c = r.build_context("A B", matches, self.s, max_listings=15)
        self.assertEqual((len(c["properties"]), c["more_count"], c["total"]), (15, 6, 21))
        self.assertEqual((c["largest_hail"], c["nearest_miles"]), ('3.00"', "0.2 mi"))

    def test_unreported_sizes_are_handled(self):
        c = r.build_context("A B", [match(1, "A St", AUG15, None, "1.00"),
                                    match(2, "B St", AUG15, "1.25", "2.00")], self.s)
        self.assertEqual(c["largest_hail"], '1.25"')
        sizes = {p["address"]: p["events"][0]["hail_size"] for p in c["properties"]}
        self.assertEqual(sizes["A St"], r.NOT_REPORTED)

    def test_all_sizes_missing(self):
        c = r.build_context("A B", [match(1, "A St", AUG15, None, "1.00")], self.s)
        self.assertEqual(c["largest_hail"], r.NOT_REPORTED)

    def test_no_matches_is_an_error(self):
        with self.assertRaises(r.RenderError):
            r.build_context("A B", [], self.s)

    def test_the_context_has_exactly_the_allowed_variables(self):
        c = r.build_context("A B", [match(1, "A St", AUG15, "1.00", "1.00")], self.s)
        self.assertEqual(set(c), set(r.ALLOWED_VARS))


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RenderTest(unittest.TestCase):
    def setUp(self):
        self.s = r.EmailSettings(*SETTINGS_ARGS)

    def ctx(self, address=JEBEL):
        return r.build_context("Jane Smith", [match(1, address, AUG15, "1.75", "3.80")], self.s)

    def test_renders_body_and_subject(self):
        subject, html = r.render(SUBJECT, BODY, self.ctx())
        self.assertEqual(subject, f"1 near {JEBEL}")
        self.assertEqual(html, f'[[trackingImage]]Jane|August 15, 2026|{JEBEL}'
                               '[August 15, 2026 1.75&#34; 3.8 mi]')

    def test_the_body_escapes_and_the_subject_does_not(self):
        subject, html = r.render(SUBJECT, BODY, self.ctx("5 Smith & Sons <b>Way</b>"))
        self.assertIn("5 Smith & Sons <b>Way</b>", subject)
        self.assertIn("5 Smith &amp; Sons &lt;b&gt;Way&lt;/b&gt;", html)
        self.assertNotIn("<b>", html)

    def test_a_line_break_cannot_add_a_header(self):
        subject, _ = r.render(SUBJECT, BODY, self.ctx("9 Bad St\r\nBcc: x@evil.example"))
        self.assertNotIn("\n", subject)
        self.assertNotIn("\r", subject)

    def test_a_long_subject_is_cut(self):
        subject, _ = r.render(SUBJECT, BODY, self.ctx("x" * 400))
        self.assertLessEqual(len(subject), r.MAX_SUBJECT)
        self.assertTrue(subject.endswith("..."))

    def test_an_undefined_placeholder_is_an_error_without_data(self):
        with self.assertRaises(r.RenderError) as cm:
            r.render("{{ nope }}", BODY, self.ctx())
        self.assertNotIn(JEBEL, str(cm.exception))

    def test_the_body_must_keep_the_tracking_tag(self):
        with self.assertRaises(r.RenderError):
            r.render(SUBJECT, "no tag {{ total }}", self.ctx())


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ValidateTest(unittest.TestCase):
    def test_a_good_template_passes(self):
        r.validate_template(SUBJECT, BODY)

    def test_every_problem_is_reported_together(self):
        with self.assertRaises(r.RenderError) as cm:
            r.validate_template("{{ bogus }}", "{{ also_bogus }} <a href=\"http://x.test\">")
        text = str(cm.exception)
        for part in ("bogus", "also_bogus", "trackingImage", "https"):
            self.assertIn(part, text)

    def test_syntax_errors_are_caught(self):
        with self.assertRaises(r.RenderError):
            r.validate_template("{% if %}", BODY)

    def test_a_free_hosting_link_is_refused(self):
        body = BODY + '<a href="https://roofbrokersinc-preview.azurewebsites.net/x">'
        with self.assertRaises(r.RenderError):
            r.validate_template(SUBJECT, body)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class RealTemplateTest(unittest.TestCase):
    """The real template files, once they are in the repo; skips until then."""

    BODY_FILE = ROOT / "hailsys/email/templates/hail_alert.html"
    SUBJECT_FILE = ROOT / "hailsys/email/templates/hail_alert_subject.txt"

    def setUp(self):
        if not (self.BODY_FILE.exists() and self.SUBJECT_FILE.exists()):
            self.skipTest("template files are not in the repo yet")
        self.body = self.BODY_FILE.read_text()
        self.subject = self.SUBJECT_FILE.read_text().strip()

    def test_it_validates_and_renders_for_one_and_two_storms(self):
        r.validate_template(self.subject, self.body)
        s = r.EmailSettings(*SETTINGS_ARGS)
        one = r.build_context("Jane Smith", [match(1, JEBEL, AUG15, "1.75", "3.80")], s)
        two = r.build_context("Jane Smith", [match(1, JEBEL, AUG15, "1.75", "3.80"),
                                             match(1, JEBEL, AUG21, "1.00", "4.60")], s)
        for ctx in (one, two):
            subject, html = r.render(self.subject, self.body, ctx)
            self.assertNotIn("{{", html)
            self.assertNotIn("{%", html)
            self.assertIn(JEBEL, subject)
        self.assertTrue(r.render(self.subject, self.body, one)[0].startswith("A hail storm was"))
        self.assertTrue(r.render(self.subject, self.body, two)[0].startswith("Hail storms were"))


if __name__ == "__main__":
    unittest.main()
