"""Tests for the by-level report (Phase 5c step 4, piece 5):  hailsys/queries/levelreport.py, /send/levels and
send_levels.html.

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    FK=$(grep '^FLASK_SECRET_KEY=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_levelreport

The query tests make their own emails in a transaction that is always rolled back.  The page tests read the real
database and replace the query where they need known numbers.

What they pin:  only emails that reached Constant Contact count;  an email counts once, under its best level and
its set, with its bases;  bounces and complaints come from send_log;  an unsubscribe is credited to the latest email
to that address sent before it was recorded, and never to a later one, a removed one or a legacy import;  report
emails are one comparison row;  the summary orders newest set first and Agree before One source;  the page is admin
only, shows counts beside rates, and writes nothing.
"""

import contextlib
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg
from psycopg.rows import dict_row

from hailsys.queries import levelreport

NOW = datetime.now(timezone.utc)


class SummarizeTest(unittest.TestCase):
    def row(self, kind="zone", set_id=1, level="agree", bases=("radar",), **flags):
        r = {"kind": kind, "set_id": set_id, "level": level, "bases": list(bases),
             "bounced": False, "complained": False, "unsubscribed": False}
        r.update(flags)
        return r

    def test_zone_emails_are_grouped_by_set_and_level_and_by_set_and_bases(self):
        got = levelreport.summarize([
            self.row(level="one_source"), self.row(level="agree", bounced=True),
            self.row(level="agree", bases=("both", "radar"), unsubscribed=True),
            self.row(set_id=2, level="agree", complained=True)])
        self.assertEqual([(r["set_id"], r["level"], r["emails"], r["bounced"], r["complained"], r["unsubscribed"])
                          for r in got["by_level"]],
                         [(2, "agree", 1, 0, 1, 0), (1, "agree", 2, 1, 0, 1), (1, "one_source", 1, 0, 0, 0)])
        self.assertEqual([(r["set_id"], r["bases"], r["emails"]) for r in got["by_basis"]],
                         [(2, "radar", 1), (1, "both + radar", 1), (1, "radar", 2)])

    def test_report_emails_are_one_row_and_not_in_the_zone_tables(self):
        got = levelreport.summarize([self.row(kind="report", set_id=None, level=None, bases=("report",), bounced=True),
                                     self.row(kind="report", set_id=None, level=None, bases=("report",))])
        self.assertEqual(got["report"], {"emails": 2, "bounced": 1, "complained": 0, "unsubscribed": 0})
        self.assertEqual((got["by_level"], got["by_basis"]), ([], []))

    def test_nothing_gives_empty_tables_and_a_zero_row(self):
        self.assertEqual(levelreport.summarize([]), {"by_level": [], "by_basis": [], "report_by_level": [],
                                                    "report": {"emails": 0, "bounced": 0, "complained": 0,
                                                               "unsubscribed": 0}})


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class FetchEmailsTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('send_log', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs hail_admin")
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]
        self.set_id = self.conn.execute("SELECT max(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        self.sync = self.conn.execute("SELECT min(run_id) AS r FROM cc_sync_runs").fetchone()["r"]
        self.zone_tpl = self.conn.execute(
            "INSERT INTO email_templates (template_name, subject, body, created_by, match_kind) "
            "VALUES (%s, 's', 'b', %s, 'zone') RETURNING template_id",
            (f"lr-{uuid.uuid4().hex[:8]}", self.emp)).fetchone()["template_id"]
        self.report_tpl = self.conn.execute(
            "SELECT template_id FROM email_templates WHERE match_kind = 'report' ORDER BY template_id LIMIT 1"
        ).fetchone()["template_id"]
        self.iem = self.conn.execute("SELECT iem_id FROM iem_data WHERE report_text = 'HAIL' LIMIT 1").fetchone()["iem_id"]
        self.conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(self.emp),))
        self.conn.execute("UPDATE settings SET zone_observer_wording_approved = true")
        self.n = 0
        self.mine = []
        self.realtors = {}

    def match(self, *, basis="radar", level="agree"):
        self.n += 1
        rid = f"LR-{uuid.uuid4().hex[:10]}"
        self.conn.execute("INSERT INTO properties (rentcast_id, property_address) VALUES (%s, %s)",
                          (rid, f"{self.n} Level St, Calhan, CO 80808"))
        listing = self.conn.execute("INSERT INTO listings (rentcast_id, list_status, list_date) "
                                    "VALUES (%s, 'Active', now()) RETURNING listing_id", (rid,)).fetchone()["listing_id"]
        if basis == "report":
            return self.conn.execute(
                "INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used, storm_date) "
                "VALUES (%s, %s, 1, 5, current_date) RETURNING match_id", (self.iem, listing)).fetchone()["match_id"]
        return self.conn.execute(
            "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
            "VALUES (%s, current_date, %s, %s, 'core', %s) RETURNING match_id",
            (listing, basis, level, self.set_id)).fetchone()["match_id"]

    def email(self, to="agent@example.test", *, matches=(("radar", "agree"),), status="sent", sent=None):
        """One email with one send_log row per (basis, level) match, all with this status."""
        report = matches[0][0] == "report"
        sent = sent or NOW - timedelta(days=2)
        key = to.strip().lower()            # one realtor per address:  realtors.email_norm is unique
        if key not in self.realtors:
            self.realtors[key] = self.conn.execute(
                "INSERT INTO realtors (agent_name, agent_email) VALUES ('Agent', %s) RETURNING realtor_id",
                (to,)).fetchone()["realtor_id"]
        realtor = self.realtors[key]
        email_id = self.conn.execute(
            "INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, sync_run_id, "
            "permission_asserted, created_by, subject, html_body, created_at) "
            "VALUES (%s, %s, %s, %s, %s, 'none', %s, 's', 'b', %s) RETURNING email_id",
            (uuid.uuid4(), realtor, to, self.report_tpl if report else self.zone_tpl, self.sync, self.emp, sent)
        ).fetchone()["email_id"]
        for basis, level in matches:
            self.conn.execute(
                "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by, email_id, "
                "send_status, sent_at, basis, level, zone_set_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (realtor, to, self.match(basis=basis, level=level), self.report_tpl if report else self.zone_tpl, self.emp,
                 email_id, status, None if status in ("queued", "failed") else sent, basis,
                 None if report else level, None if report else self.set_id))
        self.mine.append(email_id)
        return email_id

    def unsubscribe(self, address, *, at=None, source="unsubscribe", removed=False):
        self.conn.execute(
            "INSERT INTO dnc_list (email_raw, added_by, source, added_at, removed_at, removed_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (address, self.emp, source, at or NOW, NOW if removed else None, self.emp if removed else None))

    def rows(self):
        return {r["email_id"]: r for r in levelreport.fetch_emails(self.conn) if r["email_id"] in self.mine}

    def test_only_emails_that_reached_constant_contact_count(self):
        sent = self.email("a@example.test", status="sent")
        bounced = self.email("b@example.test", status="bounced")
        complained = self.email("c@example.test", status="complained")
        self.email("d@example.test", status="queued")
        self.email("e@example.test", status="failed")
        got = self.rows()
        self.assertEqual(set(got), {sent, bounced, complained})
        self.assertEqual((got[bounced]["bounced"], got[bounced]["complained"]), (True, False))
        self.assertEqual((got[complained]["bounced"], got[complained]["complained"]), (False, True))
        self.assertEqual((got[sent]["bounced"], got[sent]["complained"]), (False, False))

    def test_one_bounced_row_makes_the_email_bounced(self):
        # The bounce check moves rows from sent to bounced, so an email's rows can differ for a moment.
        e = self.email("mixed-status@example.test", matches=(("radar", "agree"), ("radar", "agree")))
        self.conn.execute("UPDATE send_log SET send_status = 'bounced', status_updated_at = now() "
                          "WHERE send_id = (SELECT min(send_id) FROM send_log WHERE email_id = %s)", (e,))
        got = self.rows()[e]
        self.assertEqual((got["bounced"], got["complained"]), (True, False))

    # The page hides the basis table when it is empty;  it is empty exactly when the level table is (every zone
    # email is in both), so which of the two the test reads cannot be told apart and is not pinned.

    def test_an_email_counts_once_under_its_best_level_with_its_set_and_bases(self):
        both = self.email(matches=(("radar", "one_source"), ("both", "agree"), ("radar", "agree")))
        one = self.email("o@example.test", matches=(("observer", "one_source"),))
        got = self.rows()
        self.assertEqual((got[both]["kind"], got[both]["level"], got[both]["set_id"], got[both]["bases"]),
                         ("zone", "agree", self.set_id, ["both", "radar"]))
        self.assertEqual((got[one]["level"], got[one]["bases"]), ("one_source", ["observer"]))

    def test_a_report_email_is_report_kind_with_no_level(self):
        e = self.email("r@example.test", matches=(("report", None),))
        got = self.rows()[e]
        self.assertEqual((got["kind"], got["level"], got["set_id"], got["bases"]), ("report", None, None, ["report"]))

    def test_an_unsubscribe_is_credited_to_the_latest_email_before_it(self):
        old = self.email("u@example.test", sent=NOW - timedelta(days=10))
        latest = self.email("u@example.test", sent=NOW - timedelta(days=5))
        after = self.email("u@example.test", sent=NOW - timedelta(days=1))
        self.unsubscribe("u@example.test", at=NOW - timedelta(days=3))
        got = self.rows()
        self.assertEqual({e: got[e]["unsubscribed"] for e in (old, latest, after)},
                         {old: False, latest: True, after: False})

    def test_the_address_match_ignores_case_and_spaces(self):
        e = self.email(" Mixed@Example.Test ")
        self.unsubscribe("mixed@example.test")
        self.assertTrue(self.rows()[e]["unsubscribed"])

    def test_an_unsubscribe_before_any_email_is_credited_to_nothing(self):
        e = self.email("early@example.test", sent=NOW - timedelta(days=1))
        self.unsubscribe("early@example.test", at=NOW - timedelta(days=2))
        self.assertFalse(self.rows()[e]["unsubscribed"])

    def test_a_removed_unsubscribe_or_another_source_is_not_an_unsubscribe(self):
        removed = self.email("removed@example.test")
        legacy = self.email("legacy@example.test")
        manual = self.email("manual@example.test")
        self.unsubscribe("removed@example.test", removed=True)
        self.unsubscribe("legacy@example.test", source="legacy_import")
        self.unsubscribe("manual@example.test", source="manual")
        got = self.rows()
        self.assertEqual([got[e]["unsubscribed"] for e in (removed, legacy, manual)], [False, False, False])

    def test_the_query_writes_nothing(self):
        self.email()
        before = self.conn.execute("SELECT (SELECT count(*) FROM send_log) AS s, (SELECT count(*) FROM dnc_list) AS d"
                                   ).fetchone()
        levelreport.fetch_emails(self.conn)
        after = self.conn.execute("SELECT (SELECT count(*) FROM send_log) AS s, (SELECT count(*) FROM dnc_list) AS d"
                                  ).fetchone()
        self.assertEqual(before, after)


SUMMARY = {"by_level": [{"set_id": 3, "level": "agree", "emails": 4, "bounced": 1, "complained": 0, "unsubscribed": 2},
                        {"set_id": 3, "level": "one_source", "emails": 2, "bounced": 0, "complained": 0,
                         "unsubscribed": 0}],
           "by_basis": [{"set_id": 3, "bases": "both + radar", "emails": 6, "bounced": 1, "complained": 0,
                         "unsubscribed": 2}],
           "report": {"emails": 5, "bounced": 1, "complained": 0, "unsubscribed": 0},
           "report_by_level": [
               {"set_id": 3, "level": "agree", "emails": 1, "bounced": 0, "complained": 0, "unsubscribed": 0},
               {"set_id": 3, "level": "none", "emails": 2, "bounced": 1, "complained": 0, "unsubscribed": 0},
               {"set_id": None, "level": None, "emails": 2, "bounced": 0, "complained": 0, "unsubscribed": 0}]}


@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class LevelsPageTest(unittest.TestCase):
    def setUp(self):
        from hailsys.web import create_app
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        with psycopg.connect(row_factory=dict_row) as conn:
            self.users = {}
            for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                  "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
                self.users.setdefault(r["role"], r["emp_id"])

    def client_as(self, role="admin"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s.update(emp_id=self.users[role], role=role, user_name="test",
                     issued_at=datetime.now(timezone.utc).isoformat())
        return client

    def html(self, summary=None):
        from hailsys.web import send as screen
        with contextlib.ExitStack() as stack:
            if summary is not None:
                stack.enter_context(mock.patch.object(screen.levelreport, "summarize", return_value=summary))
            resp = self.client_as().get("/send/levels")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_only_an_admin_may_open_it(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                with self.subTest(role=role):
                    self.assertNotEqual(self.client_as(role).get("/send/levels").status_code, 200)

    def test_the_real_page_opens(self):
        html = self.html()
        self.assertIn("For comparison: NWS report emails", html)
        self.assertIn("parking-lot item 266", html)
        self.assertIn("Replies arrive in RBI's inbox", html)

    def test_rows_show_counts_beside_rates(self):
        html = " ".join(self.html(SUMMARY).split())
        self.assertIn("<td>3</td><td>Agree</td> <td>4</td> <td>1 (25%)</td> <td>0 (0%)</td> <td>2 (50%)</td>", html)
        self.assertIn("<td>3</td><td>One source</td> <td>2</td>", html)
        self.assertIn("<td>both + radar</td> <td>6</td>", html)
        self.assertIn("All report emails</td> <td>5</td> <td>1 (20%)</td>", html)
        # Report emails by the level at their houses (item 266), each labelled, "not recorded" last.
        self.assertIn("<td>3</td><td>Agree</td> <td>1</td>", html)
        self.assertIn("<td>3</td><td>Outside the zones</td> <td>2</td> <td>1 (50%)</td>", html)
        self.assertIn("<td></td><td>Not recorded</td> <td>2</td>", html)
        self.assertLess(html.index("Outside the zones</td>"), html.index("Not recorded</td>"))

    def test_no_zone_email_says_so_and_a_zero_row_has_no_rate(self):
        empty = {"by_level": [], "by_basis": [], "report_by_level": [],
                 "report": {"emails": 0, "bounced": 0, "complained": 0, "unsubscribed": 0}}
        html = " ".join(self.html(empty).split())
        self.assertIn("No zone email has been sent yet.", html)
        self.assertNotIn("Zone emails by basis", html)
        self.assertIn("All report emails</td> <td>0</td> <td>0</td>", html)

    def test_the_batches_and_zone_pages_link_here(self):
        for url in ("/send/batches", "/send/?mode=zones"):
            with self.subTest(url=url):
                self.assertIn('href="/send/levels"', self.client_as().get(url).get_data(as_text=True))

    def test_the_page_writes_nothing(self):
        with psycopg.connect(row_factory=dict_row) as conn:
            q = ("SELECT (SELECT count(*) FROM send_log) AS s, (SELECT count(*) FROM dnc_list) AS d, "
                 "(SELECT count(*) FROM sent_emails) AS e")
            before = conn.execute(q).fetchone()
            conn.rollback()
            self.html()
            self.assertEqual(conn.execute(q).fetchone(), before)


if __name__ == "__main__":
    unittest.main()
