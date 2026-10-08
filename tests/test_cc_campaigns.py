"""Tests for hailsys/constantcontact/campaigns.py, with a fake in place of api.request.

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_cc_campaigns

The fake replies from a script and records every call, so the tests check what would be sent
to Constant Contact: the path, the body, and which statuses are accepted.  A call the script
did not expect fails the test.  No network, no database.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from hailsys.constantcontact import api, campaigns as cp
    from hailsys.constantcontact.api import ApiError
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


class Fake:
    def __init__(self, *replies):
        self.replies = list(replies)          # (status, doc) or an exception to raise
        self.calls = []

    def __call__(self, method, path, *, body=None, query=None, expect=(200, 201, 202, 204)):
        self.calls.append({"method": method, "path": path, "body": body,
                           "query": query, "expect": expect})
        if not self.replies:
            raise AssertionError(f"unscripted call: {method} {path}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class CampaignsTest(unittest.TestCase):
    def run_with(self, replies, fn, *args, **kw):
        fake = Fake(*replies)
        with mock.patch.object(api, "request", fake):
            result = fn(*args, **kw)
        self.assertEqual(fake.replies, [], "a scripted reply was never used")
        return result, fake.calls

    # ---- contacts ----

    def test_find_contact_returns_the_exact_address(self):
        other = {"contact_id": "c-other", "email_address": {"address": "other@x.invalid"}}
        mine = {"contact_id": "c-1", "email_address": {"address": " A@X.invalid ",
                                                       "permission_to_send": "implicit"}}
        found, calls = self.run_with([(200, {"contacts": [other, mine]})],
                                     cp.find_contact, "a@x.invalid")
        self.assertEqual(found, {"contact_id": "c-1", "permission": "implicit"})
        self.assertEqual(calls[0]["query"], {"email": "a@x.invalid", "status": "all"})

    def test_find_contact_returns_none_when_absent(self):
        found, _ = self.run_with([(200, {"contacts": []})], cp.find_contact, "a@x.invalid")
        self.assertIsNone(found)

    def test_find_contact_ignores_a_different_address(self):
        other = {"contact_id": "c-9", "email_address": {"address": "someone@x.invalid"}}
        found, _ = self.run_with([(200, {"contacts": [other]})], cp.find_contact, "a@x.invalid")
        self.assertIsNone(found)

    def test_create_contact_is_implicit_and_on_the_list(self):
        cid, calls = self.run_with([(201, {"contact_id": "c-1"})],
                                   cp.create_contact, "a@x.invalid", "Jane", "L1")
        self.assertEqual(cid, "c-1")
        call = calls[0]
        self.assertEqual((call["method"], call["path"], call["expect"]), ("POST", "/contacts", (201,)))
        self.assertEqual(call["body"]["email_address"],
                         {"address": "a@x.invalid", "permission_to_send": "implicit"})
        self.assertEqual(call["body"]["list_memberships"], ["L1"])
        self.assertEqual(call["body"]["first_name"], "Jane")

    def test_create_contact_without_a_name_sends_no_name(self):
        _, calls = self.run_with([(201, {"contact_id": "c-1"})],
                                 cp.create_contact, "a@x.invalid", None, "L1")
        self.assertNotIn("first_name", calls[0]["body"])

    def test_an_existing_contact_is_a_409_and_is_not_overwritten(self):
        with self.assertRaises(ApiError) as caught:
            self.run_with([ApiError("exists", status=409)], cp.create_contact,
                          "a@x.invalid", None, "L1")
        self.assertEqual(caught.exception.status, 409)

    def test_add_to_list_starts_the_bulk_job_then_waits_for_it(self):
        replies = [(201, {"activity_id": "act-1"}),
                   (200, {"state": "initialized"}), (200, {"state": "completed"})]
        _, calls = self.run_with(replies, cp.add_to_list, "c-1", "L1", sleep=lambda s: None)
        self.assertEqual(calls[0]["path"], "/activities/add_list_memberships")
        self.assertEqual(calls[0]["body"], {"source": {"contact_ids": ["c-1"]}, "list_ids": ["L1"]})
        self.assertEqual([c["path"] for c in calls[1:]], ["/activities/act-1"] * 2)

    # ---- lists and campaigns ----

    def test_create_list_returns_the_id(self):
        list_id, calls = self.run_with([(201, {"list_id": "L1"})], cp.create_list, "hail-1")
        self.assertEqual(list_id, "L1")
        self.assertEqual(calls[0]["body"]["name"], "hail-1")

    def test_create_campaign_picks_the_primary_email_by_role(self):
        doc = {"campaign_id": "camp-1", "campaign_activities": [
            {"role": "permalink", "campaign_activity_id": "act-permalink"},
            {"role": "primary_email", "campaign_activity_id": "act-primary"}]}
        got, calls = self.run_with([(200, doc)], cp.create_campaign, "n", {"subject": "s"})
        self.assertEqual(got, {"campaign_id": "camp-1", "activity_id": "act-primary"})
        self.assertEqual(calls[0]["body"],
                         {"name": "n", "email_campaign_activities": [{"subject": "s"}]})

    def test_create_campaign_without_a_primary_activity_fails(self):
        doc = {"campaign_id": "camp-1",
               "campaign_activities": [{"role": "permalink", "campaign_activity_id": "a"}]}
        with self.assertRaises(ApiError):
            self.run_with([(200, doc)], cp.create_campaign, "n", {})

    def test_campaign_fields_are_custom_html(self):
        fields = cp.campaign_fields(subject="S", html="<p>x</p>", from_name="RBI",
                                    from_email="f@x.invalid", reply_to="r@x.invalid")
        self.assertEqual(fields, {"format_type": 5, "from_name": "RBI",
                                  "from_email": "f@x.invalid", "reply_to_email": "r@x.invalid",
                                  "subject": "S", "html_content": "<p>x</p>"})

    def test_update_campaign_attaches_the_list(self):
        _, calls = self.run_with([(200, None)], cp.update_campaign, "act-1", {"subject": "S"}, "L1")
        self.assertEqual((calls[0]["method"], calls[0]["path"]),
                         ("PUT", "/emails/activities/act-1"))
        self.assertEqual(calls[0]["body"], {"subject": "S", "contact_list_ids": ["L1"]})

    def test_schedule_sends_now_and_only_accepts_201(self):
        _, calls = self.run_with([(201, [])], cp.schedule, "act-1")
        self.assertEqual(calls[0]["path"], "/emails/activities/act-1/schedules")
        self.assertEqual(calls[0]["body"], {"scheduled_date": "0"})
        self.assertEqual(calls[0]["expect"], (201,))

    def test_delete_list(self):
        _, calls = self.run_with([(202, None)], cp.delete_list, "L1")
        self.assertEqual((calls[0]["method"], calls[0]["path"]), ("DELETE", "/contact_lists/L1"))

    # ---- waiting ----

    def test_wait_until_done_polls_until_done(self):
        replies = [(200, {"current_status": "DRAFT"}), (200, {"current_status": "EXECUTING"}),
                   (200, {"current_status": "Done"})]
        slept = []
        self.run_with(replies, cp.wait_until_done, "act-1", sleep=slept.append)
        self.assertEqual(len(slept), 2)

    def test_wait_until_done_fails_on_an_error_status(self):
        with self.assertRaises(ApiError):
            self.run_with([(200, {"current_status": "ERROR"})], cp.wait_until_done, "act-1",
                          sleep=lambda s: None)

    def test_wait_until_done_gives_up_at_the_deadline(self):
        ticks = iter([0, 1, 11])                      # deadline 10: the second look is too late
        replies = [(200, {"current_status": "SCHEDULED"})] * 2
        with self.assertRaises(ApiError):
            self.run_with(replies, cp.wait_until_done, "act-1", timeout=10,
                          sleep=lambda s: None, clock=lambda: next(ticks))

    # ---- safety ----

    def test_an_id_that_could_steer_a_url_is_refused(self):
        for bad in ("../x", "a/b", "", None, 5, "x" * 65):
            with self.assertRaises(ApiError, msg=repr(bad)):
                self.run_with([(201, {"list_id": bad})], cp.create_list, "n")

    def test_the_error_message_never_holds_the_value(self):
        with self.assertRaises(ApiError) as caught:
            self.run_with([(201, {"list_id": "../secret"})], cp.create_list, "n")
        self.assertNotIn("secret", str(caught.exception))

    def test_unknown_outcome(self):
        for status, expected in ((None, True), (500, True), (503, True), (200, True),
                                 (201, True), (400, False), (404, False), (409, False),
                                 (422, False), (401, False)):
            self.assertEqual(cp.is_unknown_outcome(ApiError("x", status=status)), expected,
                             msg=str(status))

    def test_stops_batch(self):
        for status, expected in ((401, True), (403, True), (429, True), (400, False),
                                 (409, False), (500, False), (None, False)):
            self.assertEqual(cp.stops_batch(ApiError("x", status=status)), expected,
                             msg=str(status))


if __name__ == "__main__":
    unittest.main()
