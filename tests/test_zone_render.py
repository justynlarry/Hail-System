"""Tests for hailsys/email/zonecontext.py and the zone kind of render.py (Phase 5c step 4, piece 3b).

Pure:  no database, nothing sent.

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_zone_render

What they pin:  the context a zone email gets (no hail size, no distance, evidence made by the wording
module from the set's numbers, attribution only when an observer is behind it);  a report template is
refused as a zone template and the other way round;  banned claims are refused at save and again at
render, in the template text and the evidence text but not in an address; and the shipped zone
template renders from a zone context for every basis.
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from hailsys.email import render as r, zonecontext as zc, zonewording as zw

TEMPLATES = Path(__file__).resolve().parent.parent / "hailsys" / "email" / "templates"
UTC = timezone.utc
SETTINGS = r.EmailSettings("https://x.test/logo.png", "https://x.test/cra.jpg", "https://x.test/bbb.png",
                           "e@x.invalid", "https://www.roofbrokersinc.com/Orders/Create")
WORDING = {"hail_in": 1, "radius_km": 5.0}
AUG15 = datetime(2026, 8, 15, 20, 19, tzinfo=UTC)
AUG21 = datetime(2026, 8, 21, 18, 0, tzinfo=UTC)


def event(lid, address, when, bases=("radar",)):
    return {"listing_id": lid, "address": address, "utc_datetime": when, "bases": list(bases)}


def shipped(name):
    return (TEMPLATES / name).read_text()


ZONE_SUBJECT = shipped("hail_alert_zone_subject.txt")
ZONE_BODY = shipped("hail_alert_zone.html")
REPORT_SUBJECT = shipped("hail_alert_subject.txt")
REPORT_BODY = shipped("hail_alert.html")


class ContextTest(unittest.TestCase):
    def ctx(self, events, **kw):
        return zc.build_context("Jane Smith", events, SETTINGS, WORDING, **kw)

    def test_the_variables_are_exactly_the_zone_kinds_allowed_set(self):
        got = self.ctx([event(1, "1 A St", AUG15)])
        self.assertEqual(set(got), set(r.ALLOWED_VARS_ZONE))

    def test_no_hail_size_and_no_distance_anywhere(self):
        got = self.ctx([event(1, "1 A St", AUG15)])
        self.assertNotIn("nearest_miles", got)
        self.assertNotIn("biggest_hail", got)
        for p in got["properties"]:
            for e in p["events"]:
                self.assertEqual(set(e), {"date"})

    def test_each_setting_lands_in_its_own_variable(self):
        s = r.EmailSettings("https://x.test/logo.png", "https://x.test/cra.jpg", "https://x.test/bbb.png",
                            "contact@x.invalid", "https://x.test/schedule")
        got = zc.build_context("Jane Smith", [event(1, "1 A St", AUG15)], s, WORDING)
        self.assertEqual((got["logo_url"], got["badge_cra_url"], got["badge_bbb_url"],
                          got["contact_email"], got["schedule_url"]),
                         ("https://x.test/logo.png", "https://x.test/cra.jpg", "https://x.test/bbb.png",
                          "contact@x.invalid", "https://x.test/schedule"))

    def test_no_events_is_refused(self):
        with self.assertRaises(r.RenderError):
            self.ctx([])

    def test_the_agent_name_the_counts_and_the_dates(self):
        got = self.ctx([event(1, "1 A St", AUG15), event(2, "2 B St", AUG15), event(3, "3 C St", AUG21)])
        self.assertEqual(got["agent_first_name"], "Jane")
        self.assertEqual((got["total"], got["more_count"], got["storm_count"]), (3, 0, 2))
        self.assertEqual(got["storm_dates_label"],
                         r.storm_dates_label([r.local_date(AUG15), r.local_date(AUG21)]))

    def test_a_doubtful_name_gets_no_first_name(self):
        got = zc.build_context("The Smith Team", [event(1, "1 A St", AUG15)], SETTINGS, WORDING)
        self.assertEqual(got["agent_first_name"], "")

    def test_a_house_with_two_days_is_one_property_with_two_dates_and_comes_first(self):
        got = self.ctx([event(1, "1 A St", AUG15), event(2, "2 B St", AUG15), event(2, "2 B St", AUG21)])
        self.assertEqual([p["address"] for p in got["properties"]], ["2 B St", "1 A St"])
        self.assertEqual(len(got["properties"][0]["events"]), 2)
        self.assertEqual(got["total"], 2)

    def test_ties_sort_by_address(self):
        got = self.ctx([event(1, "9 Z St", AUG15), event(2, "1 A St", AUG15)])
        self.assertEqual([p["address"] for p in got["properties"]], ["1 A St", "9 Z St"])

    def test_the_list_is_capped_and_the_rest_counted(self):
        events = [event(i, f"{i} A St", AUG15) for i in range(1, 8)]
        got = self.ctx(events, max_listings=3)
        self.assertEqual((len(got["properties"]), got["more_count"], got["total"]), (3, 4, 7))

    def test_evidence_follows_the_bases_and_the_sets_numbers(self):
        radar = self.ctx([event(1, "1 A St", AUG15, ["radar"])])
        self.assertEqual(radar["evidence"], zw.sentences(["radar"], 1, 5.0))
        self.assertEqual(radar["attribution"], "")
        both = self.ctx([event(1, "1 A St", AUG15, ["radar", "observer"])])
        self.assertEqual(both["evidence"], zw.sentences({"radar", "observer"}, 1, 5.0))
        self.assertEqual(both["attribution"], zw.ATTRIBUTION)

    def test_the_radar_sentence_uses_the_wording_numbers(self):
        got = zc.build_context("Jane Smith", [event(1, "1 A St", AUG15)], SETTINGS,
                               {"hail_in": 1.25, "radius_km": 8.0})
        self.assertIn("1.25 inches", " ".join(got["evidence"]))
        self.assertIn("5 miles", " ".join(got["evidence"]))

    def test_observer_alone_carries_the_attribution_and_no_radar_sentence(self):
        got = self.ctx([event(1, "1 A St", AUG15, ["observer"])])
        self.assertEqual(got["attribution"], zw.ATTRIBUTION)
        self.assertNotIn("radar", " ".join(got["evidence"]).lower())

    def test_images_are_copied_not_shared(self):
        s = r.EmailSettings(*[getattr(SETTINGS, f) for f in
                              ("logo_url", "badge_cra_url", "badge_bbb_url", "contact_email", "schedule_url")],
                            images={"x": {"url": "https://x.test/i.png", "alt": "i"}})
        got = zc.build_context("Jane Smith", [event(1, "1 A St", AUG15)], s, WORDING)
        got["images"]["x"]["alt"] = "changed"
        self.assertEqual(s.images["x"]["alt"], "i")


class KindValidationTest(unittest.TestCase):
    def test_the_shipped_pairs_are_valid_for_their_own_kind(self):
        r.validate_template(REPORT_SUBJECT, REPORT_BODY)
        r.validate_template(REPORT_SUBJECT, REPORT_BODY, kind="report")
        r.validate_template(ZONE_SUBJECT, ZONE_BODY, kind="zone")

    def test_a_report_template_is_refused_as_a_zone_template(self):
        with self.assertRaises(r.RenderError) as ctx:
            r.validate_template(REPORT_SUBJECT, REPORT_BODY, kind="zone")
        self.assertIn("unknown placeholder", str(ctx.exception))

    def test_a_zone_template_is_refused_as_a_report_template(self):
        with self.assertRaises(r.RenderError) as ctx:
            r.validate_template(ZONE_SUBJECT, ZONE_BODY, kind="report")
        self.assertIn("unknown placeholder", str(ctx.exception))

    def test_an_unknown_kind_is_refused(self):
        for kind in ("", "Zone", "both", None):
            with self.subTest(kind=kind), self.assertRaises(r.RenderError):
                r.validate_template(ZONE_SUBJECT, ZONE_BODY, kind=kind)

    def test_hail_size_and_distance_are_not_zone_variables(self):
        for var in ("nearest_miles", "biggest_hail", "magnitude"):
            with self.subTest(var=var), self.assertRaises(r.RenderError):
                r.validate_template(ZONE_SUBJECT, ZONE_BODY + "{{ " + var + " }}", kind="zone")

    def test_a_banned_claim_in_the_body_is_refused_at_save(self):
        body = ZONE_BODY + "<p>Your roof was damaged by hail.</p>"
        with self.assertRaises(r.RenderError) as ctx:
            r.validate_template(ZONE_SUBJECT, body, kind="zone")
        self.assertIn("wording rules", str(ctx.exception))

    def test_a_banned_claim_in_the_subject_is_refused_at_save(self):
        with self.assertRaises(r.RenderError) as ctx:
            r.validate_template("Confirmed hail at your home", ZONE_BODY, kind="zone")
        self.assertIn("the subject", str(ctx.exception))

    def test_the_wording_rules_do_not_apply_to_a_report_template(self):
        # The report path must not move:  its copy is the marketing team's and is not linted here.
        r.validate_template(REPORT_SUBJECT, REPORT_BODY + "<p>Guaranteed.</p>", kind="report")

    def test_every_problem_is_listed_together(self):
        with self.assertRaises(r.RenderError) as ctx:
            r.validate_template("{{ nearest_miles }}", "<p>Guaranteed</p>", kind="zone")
        text = str(ctx.exception)
        for word in ("unknown placeholder", "trackingImage", "wording rules"):
            self.assertIn(word, text)


class KindRenderTest(unittest.TestCase):
    def ctx(self, bases=("radar",), address="1 A St"):
        return zc.build_context("Jane Smith", [event(1, address, AUG15, bases)], SETTINGS, WORDING)

    def test_the_shipped_zone_template_renders_for_every_basis(self):
        for bases in (["radar"], ["observer"], ["both"], ["radar", "observer"]):
            with self.subTest(bases=bases):
                subject, html = r.render(ZONE_SUBJECT, ZONE_BODY, self.ctx(bases), kind="zone")
                self.assertIn("1 A St", subject)
                self.assertIn("1 A St", html)
                self.assertIn("[[trackingImage]]", html)         # the provider's tag survives rendering
                for sentence in self.ctx(bases)["evidence"]:
                    self.assertIn(sentence, html)

    def test_the_attribution_shows_only_with_an_observer(self):
        _, radar = r.render(ZONE_SUBJECT, ZONE_BODY, self.ctx(["radar"]), kind="zone")
        _, observer = r.render(ZONE_SUBJECT, ZONE_BODY, self.ctx(["observer"]), kind="zone")
        self.assertNotIn(zw.ATTRIBUTION, radar)
        self.assertIn(zw.ATTRIBUTION[:30], observer)

    def test_an_address_that_reads_like_a_claim_does_not_stop_the_email(self):
        for address in ("12 Proof Rd", "5 Guaranteed Way", "9 Confirmed Ct"):
            with self.subTest(address=address):
                _, html = r.render(ZONE_SUBJECT, ZONE_BODY, self.ctx(address=address), kind="zone")
                self.assertIn(address, html)

    def test_a_banned_claim_in_the_template_is_refused_at_render(self):
        body = ZONE_BODY + "<p>Your roof was damaged.</p>"
        with self.assertRaises(r.RenderError) as ctx:
            r.render(ZONE_SUBJECT, body, self.ctx(), kind="zone")
        self.assertIn("wording rules", str(ctx.exception))

    def test_a_banned_claim_in_the_subject_is_refused_at_render(self):
        with self.assertRaises(r.RenderError):
            r.render("Probability of hail at your home", ZONE_BODY, self.ctx(), kind="zone")

    def test_a_banned_claim_in_the_evidence_text_is_refused(self):
        ctx = self.ctx()
        ctx["evidence"] = ["Radar estimated size 2 inches."]
        with self.assertRaises(r.RenderError) as ctx_err:
            r.render(ZONE_SUBJECT, ZONE_BODY, ctx, kind="zone")
        self.assertIn("the evidence text", str(ctx_err.exception))

    def test_a_banned_claim_in_the_attribution_is_refused(self):
        ctx = self.ctx(["observer"])
        ctx["attribution"] = "Confirmed by observers."
        with self.assertRaises(r.RenderError):
            r.render(ZONE_SUBJECT, ZONE_BODY, ctx, kind="zone")

    def test_the_report_path_does_not_look_at_the_evidence_or_the_wording(self):
        ctx = {"agent_first_name": "J", "evidence": ["guaranteed"], "total": 1}
        subject, html = r.render("{{ total }}", "<p>Guaranteed {{ agent_first_name }}</p>[[trackingImage]]", ctx)
        self.assertEqual((subject, html), ("1", "<p>Guaranteed J</p>[[trackingImage]]"))

    def test_a_name_is_escaped_in_the_body(self):
        _, html = r.render(ZONE_SUBJECT, ZONE_BODY, self.ctx(address="1 <b>A</b> St"), kind="zone")
        self.assertIn("1 &lt;b&gt;A&lt;/b&gt; St", html)
        self.assertNotIn("<b>A</b>", html)


if __name__ == "__main__":
    unittest.main()
