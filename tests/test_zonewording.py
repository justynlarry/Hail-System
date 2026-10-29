"""Tests for hailsys/email/zonewording.py and the draft zone template (Phase 5c step 4, piece 1).

Pure:  no database, nothing sent.

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_zonewording

What they pin:  the radar sentence says "hail of at least <the set's threshold> within about <the set's radius in
miles> of this address" with both numbers taken from the parameter set;  the sentences and the CoCoRaHS attribution
by basis;  every banned claim is refused and the shipped marketing copy, which says "damaged" and "80%", is not;
and the draft zone template renders for every basis with no hail size, no distance and no National Weather Service,
carries the attribution only when an observer is behind the email, escapes what it is given, and uses exactly the
variables a zone-kind template is allowed.
"""

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

from hailsys.email import zonewording as zw

TEMPLATES = Path(__file__).resolve().parent.parent / "hailsys" / "email" / "templates"


def source(name):
    return (TEMPLATES / name).read_text()


class NumbersTest(unittest.TestCase):
    def test_inches_read_naturally(self):
        for value, want in ((1, "1 inch"), ("1.00", "1 inch"), (Decimal("1.00"), "1 inch"), (1.25, "1.25 inches"),
                            (Decimal("1.50"), "1.5 inches"), (2, "2 inches"), (Decimal("0.75"), "0.75 inches"),
                            (10, "10 inches")):
            with self.subTest(value=value):
                self.assertEqual(zw.inches_label(value), want)

    def test_the_radius_is_whole_miles_and_at_least_one(self):
        for km, want in ((5.0, "3 miles"), (Decimal("5.0"), "3 miles"), (8.0, "5 miles"), (3.0, "2 miles"),
                         (1.6, "1 mile"), (0.1, "1 mile"), (16.0, "10 miles")):
            with self.subTest(km=km):
                self.assertEqual(zw.miles_label(km), want)


class SentencesTest(unittest.TestCase):
    RADAR = "Weather radar indicated hail of at least 1 inch within about 3 miles of this address."

    def test_the_radar_sentence_is_the_designed_wording(self):
        self.assertEqual(zw.radar_sentence(Decimal("1.00"), Decimal("5.0")), self.RADAR)

    def test_the_numbers_follow_the_parameter_set(self):
        self.assertEqual(zw.radar_sentence(Decimal("1.25"), Decimal("8.0")),
                         "Weather radar indicated hail of at least 1.25 inches within about 5 miles of this address.")
        self.assertEqual(zw.radar_sentence(2, 1.0),
                         "Weather radar indicated hail of at least 2 inches within about 1 mile of this address.")

    def test_each_basis_says_what_it_rests_on(self):
        self.assertEqual(zw.sentences(["radar"], 1, 5), [self.RADAR])
        self.assertEqual(zw.sentences(["observer"], 1, 5), [zw.OBSERVER_SENTENCE])
        self.assertEqual(zw.sentences(["both"], 1, 5), [self.RADAR, zw.OBSERVER_SENTENCE])

    def test_a_mixed_email_says_each_thing_once_radar_first(self):
        want = [self.RADAR, zw.OBSERVER_SENTENCE]
        for bases in (["radar", "observer"], ["observer", "radar"], ["radar", "radar", "observer", "observer"],
                      ["both", "radar", "observer"], {"radar", "both"} | {"observer"}):
            with self.subTest(bases=bases):
                self.assertEqual(zw.sentences(bases, 1, 5), want)

    def test_no_bases_no_sentences(self):
        self.assertEqual(zw.sentences([], 1, 5), [])

    def test_an_unknown_basis_is_refused(self):
        for bad in (["report"], ["radar", "guess"], [""], ["RADAR"]):
            with self.subTest(bad=bad):
                with self.assertRaises(zw.WordingError):
                    zw.sentences(bad, 1, 5)

    def test_the_observer_sentence_names_no_station_and_no_place(self):
        low = zw.OBSERVER_SENTENCE.lower()
        for word in ("station", "cocorahs", "county", "street", "near you", "your"):
            self.assertNotIn(word, low)

    def test_the_attribution_is_there_exactly_when_an_observer_is(self):
        for bases, want in ((["radar"], ""), (["observer"], zw.ATTRIBUTION), (["both"], zw.ATTRIBUTION),
                            (["radar", "observer"], zw.ATTRIBUTION), ([], "")):
            with self.subTest(bases=bases):
                self.assertEqual(zw.attribution_for(bases), want)
        self.assertIn("CC BY 4.0", zw.ATTRIBUTION)
        self.assertIn("Community Collaborative Rain, Hail and Snow Network", zw.ATTRIBUTION)
        self.assertNotIn("teh ", zw.ATTRIBUTION)

    def test_every_sentence_passes_the_check_for_a_range_of_parameter_sets(self):
        for hail in (Decimal("0.50"), Decimal("1.00"), Decimal("1.25"), Decimal("2.50"), Decimal("4.00")):
            for km in (Decimal("0"), Decimal("1.0"), Decimal("5.0"), Decimal("12.5"), Decimal("50")):
                for bases in (["radar"], ["observer"], ["both"], ["radar", "observer"]):
                    for s in zw.sentences(bases, hail, km) + [zw.attribution_for(bases)]:
                        with self.subTest(hail=hail, km=km, bases=bases):
                            zw.check(s)


class CheckTest(unittest.TestCase):
    REFUSED = {
        "a radar technical term": ["The MESH value was high", "55 dBZ over the area", "Radar reflectivity was strong"],
        "a radar size or estimate": ["radar estimated size 1.5 in", "The estimated hail size was large",
                                     "maximum estimated size", "radar-measured hail", "radar confirmed hail",
                                     "Radar detected hail nearby", "measured size 2 in"],
        "a claim about the person's own property": [
            "Hail fell at your property", "hail on your roof", "over your house", "damage in your yard",
            "A storm hit your home", "hail damaged your roof", "Your roof was hit", "your home has suffered damage",
            "your property took a beating", "This property was struck by hail", "this roof has been damaged"],
        "a probability": ["a 60% chance of damage", "chance of hail", "the probability of damage", "likely had hail",
                          "probably damaged", "Definitely was hit", "80 percent chance", "odds of a claim"],
        "a guarantee or a confirmation": ["We guarantee a pass", "guaranteed savings", "confirmed hail",
                                          "damage verified", "proven damage", "proof of hail"],
    }

    def test_each_banned_claim_is_refused_with_its_kind(self):
        for kind, texts in self.REFUSED.items():
            for text in texts:
                with self.subTest(text=text):
                    found = zw.banned_claims(text)
                    self.assertIn(kind, [label for label, _ in found], f"{text!r} -> {found}")
                    with self.assertRaises(zw.WordingError):
                        zw.check(text)

    def test_the_refusal_names_what_was_found_and_where(self):
        with self.assertRaises(zw.WordingError) as ctx:
            zw.check("We guarantee hail damaged your roof.", where="the body of template 'x'")
        msg = str(ctx.exception)
        self.assertIn("the body of template 'x'", msg)
        self.assertIn("guarantee", msg)
        self.assertIn("(a guarantee or a confirmation)", msg)
        self.assertIn("damaged your", msg)

    def test_case_does_not_matter(self):
        for text in ("GUARANTEED", "Radar Estimated Size", "AT YOUR PROPERTY", "Mesh"):
            with self.assertRaises(zw.WordingError):
                zw.check(text)

    def test_the_shipped_marketing_words_are_allowed(self):
        for text in ("This is based on weather observations, not an inspection. While it's not confirmation that the "
                     "roof was damaged, it may be worth having a licensed roofer come out to check for damage.",
                     "80% of homes are certifiable as-is or with minor repair work, and most hail repairs come in "
                     "under the insurance deductible.",
                     "Hail activity near your listing at 123 Main St", "Find out now, while you have time to deal with it.",
                     "hail of at least 1 inch within about 3 miles of this address",
                     "A trained volunteer weather observer reported hail nearby.",
                     "The National Weather Service logged a hail report", "Largest reported hail size",
                     "Recent Hail Activity Near Your Listings", "Schedule a new inspection",
                     "Most of These Are Small", "certified roofs since 1992", "Already have an account?"):
            with self.subTest(text=text):
                self.assertEqual(zw.banned_claims(text), [])
                self.assertEqual(zw.check(text), text)

    def test_the_whole_shipped_report_template_passes(self):
        # The check is for zone wording, but it must not be so loud that it flags the copy already in use.
        self.assertEqual(zw.banned_claims(source("hail_alert.html")), [])
        self.assertEqual(zw.banned_claims(source("hail_alert_subject.txt")), [])

    def test_check_returns_the_text_unchanged(self):
        text = "<p>Hello &amp; welcome</p>"
        self.assertIs(zw.check(text), text)


class PlainTest(unittest.TestCase):
    def test_tags_comments_and_entities_are_dealt_with(self):
        self.assertEqual(zw.plain("<p>It&rsquo;s <b>fine</b></p><!-- guaranteed -->"), "It’s fine")
        self.assertEqual(zw.plain("{# guaranteed #}ok {{ x }}"), "ok {{ x }}")
        self.assertEqual(zw.plain("<style>.a{color:red}</style>hello"), "hello")

    def test_a_claim_in_a_hidden_comment_is_not_what_a_reader_sees(self):
        self.assertEqual(zw.banned_claims("<!-- guaranteed -->{# at your roof #}fine"), [])

    def test_a_comment_with_a_greater_than_sign_inside_is_still_hidden(self):
        # The tag stripper alone would end such a comment at its first '>' and leave the rest showing.
        self.assertEqual(zw.banned_claims("<!-- a > guaranteed -->fine"), [])
        self.assertEqual(zw.banned_claims("{# a > guaranteed #}fine"), [])

    def test_a_claim_in_an_image_description_is_seen(self):
        # alt and title text is read by people (image-blocking mail clients, screen readers), so it counts.
        for attr in ("alt", "title", "aria-label"):
            with self.subTest(attr=attr):
                found = zw.banned_claims(f'<img src="x.png" {attr}="Hail damaged your roof" />')
                self.assertTrue(found, f"a claim in {attr} was not seen")


# jinja2 lives in the app image; the other classes are pure Python and run on the host too (item 273).
@unittest.skipIf(importlib.util.find_spec("jinja2") is None, "jinja2 not installed")
class ZoneTemplateTest(unittest.TestCase):
    """The draft hail_alert_zone template and its subject, rendered the way hailsys/email/render.py does."""

    ALLOWED = {"logo_url", "badge_cra_url", "badge_bbb_url", "contact_email", "schedule_url", "agent_first_name",
               "storm_count", "storm_dates_label", "evidence", "attribution", "properties", "more_count"}

    def setUp(self):
        from jinja2 import StrictUndefined
        from jinja2.sandbox import SandboxedEnvironment
        self.body = SandboxedEnvironment(autoescape=True, undefined=StrictUndefined)
        self.subject = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)

    def context(self, bases=("radar",), properties=None, days=("August 15, 2026",), more=0, name="Sam"):
        properties = properties if properties is not None else [{"address": "123 Main St, Calhan, CO",
                                                                  "events": [{"date": days[0]}]}]
        return {"logo_url": "https://img.example.test/logo.png", "badge_cra_url": "https://img.example.test/cra.png",
                "badge_bbb_url": "https://img.example.test/bbb.png", "contact_email": "info@example.test",
                "schedule_url": "https://example.test/schedule", "agent_first_name": name,
                "storm_count": len(days), "storm_dates_label": " and ".join(days),
                "evidence": zw.sentences(bases, Decimal("1.00"), Decimal("5.0")),
                "attribution": zw.attribution_for(bases), "properties": properties, "more_count": more}

    def render(self, ctx):
        return self.body.from_string(source("hail_alert_zone.html")).render(**ctx)

    def test_it_needs_exactly_the_allowed_variables(self):
        from jinja2 import meta
        needed = meta.find_undeclared_variables(self.body.parse(source("hail_alert_zone.html")))
        self.assertEqual(set(needed), self.ALLOWED)
        self.assertEqual(meta.find_undeclared_variables(self.subject.parse(source("hail_alert_zone_subject.txt"))),
                         {"total", "properties"})

    def test_a_missing_variable_is_an_error_not_a_blank(self):
        from jinja2 import UndefinedError
        ctx = self.context()
        del ctx["evidence"]
        with self.assertRaises(UndefinedError):
            self.render(ctx)

    def test_no_report_facts_leak_into_a_zone_email(self):
        for bases in (["radar"], ["observer"], ["both"], ["radar", "observer"]):
            out = zw.plain(self.render(self.context(bases)))
            low = out.lower()
            with self.subTest(bases=bases):
                for phrase in ("largest", "nearest", "miles away", "hail size", "national weather service",
                               "size not reported", "storm report", "reported hail size"):
                    self.assertNotIn(phrase, low)

    def test_the_evidence_sentences_appear_once_each(self):
        for bases in (["radar"], ["observer"], ["both"], ["radar", "observer"]):
            ctx = self.context(bases)
            out = zw.plain(self.render(ctx))
            with self.subTest(bases=bases):
                for s in ctx["evidence"]:
                    self.assertEqual(out.count(s), 1, s)

    def test_a_radar_only_email_has_no_observer_words_and_no_attribution(self):
        out = zw.plain(self.render(self.context(["radar"])))
        for word in ("volunteer", "CoCoRaHS", "CC BY", "observer"):
            self.assertNotIn(word, out)
        self.assertIn("Weather radar indicated hail of at least 1 inch within about 3 miles of this address.", out)

    def test_an_observer_email_has_the_attribution_and_no_radar_words(self):
        out = zw.plain(self.render(self.context(["observer"])))
        self.assertIn(zw.ATTRIBUTION, out)
        self.assertIn(zw.OBSERVER_SENTENCE, out)
        for word in ("radar", "inch", "mile"):
            self.assertNotIn(word, out.lower())

    def test_the_attribution_block_is_only_in_an_email_with_an_observer_behind_it(self):
        # Not just the words:  a radar-only email has no empty footer row either.
        self.assertNotIn("DATA ATTRIBUTION", self.render(self.context(["radar"])))
        for bases in (["observer"], ["both"], ["radar", "observer"]):
            self.assertIn("DATA ATTRIBUTION", self.render(self.context(bases)))

    def test_a_both_email_has_both_and_the_attribution(self):
        out = zw.plain(self.render(self.context(["both"])))
        self.assertIn(zw.ATTRIBUTION, out)
        self.assertIn("Weather radar indicated", out)
        self.assertIn("volunteer weather observer", out)

    def test_every_rendering_passes_the_wording_check(self):
        for bases in (["radar"], ["observer"], ["both"], ["radar", "observer"]):
            for days in (("August 15, 2026",), ("August 15, 2026", "August 21, 2026")):
                with self.subTest(bases=bases, days=days):
                    zw.check(self.render(self.context(bases, days=days)), "the rendered email")

    def test_the_template_source_passes_the_wording_check(self):
        zw.check(source("hail_alert_zone.html"), "hail_alert_zone.html")
        zw.check(source("hail_alert_zone_subject.txt"), "the subject")

    def test_what_it_is_given_is_escaped(self):
        props = [{"address": "<script>alert(1)</script> & Co", "events": [{"date": "<b>x</b>"}]}]
        out = self.render(self.context(properties=props, name="<i>Sam</i>"))
        self.assertNotIn("<script>alert", out)
        self.assertNotIn("<i>Sam</i>", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co", out)

    def test_one_listing_one_storm_reads_as_a_single_row(self):
        out = zw.plain(self.render(self.context()))
        self.assertIn("Recent Hail Activity Near Your Listing", out)
        self.assertIn("123 Main St, Calhan, CO", out)
        self.assertIn("Hello,", zw.plain(self.render(self.context(name=""))))
        self.assertIn("Sam,", out)

    def test_several_listings_and_storms_each_show_their_dates(self):
        props = [{"address": "1 First St", "events": [{"date": "August 15, 2026"}, {"date": "August 21, 2026"}]},
                 {"address": "2 Second St", "events": [{"date": "August 21, 2026"}]}]
        out = zw.plain(self.render(self.context(properties=props, days=("August 15, 2026", "August 21, 2026"), more=3)))
        for want in ("Recent Hail Activity Near Your Listings", "August 15, 2026 and August 21, 2026"):
            self.assertIn(want, out)
        # Each address is followed by its own dates, in order, and the rest are counted.
        self.assertIn("1 First St August 15, 2026 August 21, 2026 2 Second St August 21, 2026 and 3 more", out)

    def test_the_parts_that_make_it_a_deliverable_advertisement_are_kept(self):
        raw = source("hail_alert_zone.html")
        self.assertIn("[[trackingImage]]", raw)
        out = zw.plain(self.render(self.context()))
        self.assertIn("This is an advertisement.", out)
        self.assertIn("info@example.test", self.render(self.context()))
        self.assertIn("SCHEDULE A FREE INSPECTION", out)

    def test_the_subject_for_one_listing_and_for_several(self):
        one = self.subject.from_string(source("hail_alert_zone_subject.txt")).render(
            total=1, properties=[{"address": "123 Main St"}])
        many = self.subject.from_string(source("hail_alert_zone_subject.txt")).render(
            total=4, properties=[{"address": "123 Main St"}])
        self.assertEqual(one.strip(), "Recent hail activity near your listing at 123 Main St")
        self.assertEqual(many.strip(), "Recent hail activity near several of your listings")
        for subject in (one, many):
            self.assertLessEqual(len(subject), 150)
            zw.check(subject, "the subject")


if __name__ == "__main__":
    unittest.main()
