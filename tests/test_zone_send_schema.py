"""Tests for sql/060_zone_sends.sql -- what a zone send needs from the database (Phase 5c step 4, piece 2).

Needs a role that can insert into send_log, email_templates and storm_listing_matches (hail_admin);  everything runs in a
transaction that is always rolled back, so nothing is sent and nothing is kept:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -v ./tests:/app/tests:ro web python -m unittest tests.test_zone_send_schema

What they pin:  a zone match is sent only under a zone-kind template and a report match only under a report-kind one, the
send record carries the match's own basis, level and set, observer wording is refused until the setting is true (and
turning it on is logged), the new columns and the template kind are frozen, the existing send code (which names no new
column) still inserts a report send, and everything that was frozen or append-only before still is.
"""

import os
import unittest

SEND = ("INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by, email_id, basis, level, "
        "zone_set_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class ZoneSendSchemaTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('send_log', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert send_log rows (hail_admin)")
        if self.conn.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_name = 'email_templates' "
                             "AND column_name = 'match_kind'").fetchone()["n"] == 0:
            self.fail("sql/060_zone_sends.sql is not applied")
        self.old = self.conn.execute("SELECT * FROM send_log ORDER BY send_id LIMIT 1").fetchone()
        if self.old is None:
            self.skipTest("no send_log row to copy")
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]
        self.set_id = self.conn.execute("SELECT min(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]
        self.report_tpl = self.conn.execute(
            "SELECT template_id FROM email_templates WHERE match_kind = 'report' ORDER BY template_id LIMIT 1").fetchone()["template_id"]
        self.zone_tpl = self.conn.execute(
            "INSERT INTO email_templates (template_name, subject, body, created_by, match_kind) "
            "VALUES ('zone test', 's', 'b', %s, 'zone') RETURNING template_id", (self.emp,)).fetchone()["template_id"]
        listings = self.conn.execute("SELECT listing_id FROM listings ORDER BY listing_id LIMIT 4").fetchall()
        self.matches = {}
        for basis, row in zip(("radar", "observer", "both"), listings):
            self.matches[basis] = self.conn.execute(
                "INSERT INTO storm_listing_matches (listing_id, storm_date, basis, level, layer, set_id) "
                "VALUES (%s, '1999-06-09', %s, 'agree', 'core', %s) RETURNING match_id",
                (row["listing_id"], basis, self.set_id)).fetchone()["match_id"]

    def send(self, match, template, basis="report", level=None, zone_set=None):
        return self.conn.execute(SEND, (self.old["realtor_id"], self.old["recipient_email"], match, template,
                                        self.old["sent_by"], self.old["email_id"], basis, level, zone_set))

    def zone_send(self, basis="radar", **kw):
        kw.setdefault("level", "agree")
        kw.setdefault("zone_set", self.set_id)
        return self.send(self.matches[basis], self.zone_tpl, basis, **kw)

    def refused(self, fn, message=None, kinds=None):
        import psycopg
        kinds = kinds or (psycopg.errors.RestrictViolation, psycopg.errors.CheckViolation,
                          psycopg.errors.ForeignKeyViolation, psycopg.errors.NotNullViolation)
        self.conn.execute("SAVEPOINT t")
        with self.assertRaises(kinds) as ctx:
            fn()
        self.conn.execute("ROLLBACK TO SAVEPOINT t")
        if message:
            self.assertIn(message, str(ctx.exception))

    def approve(self):
        self.conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(self.emp),))
        self.conn.execute("UPDATE settings SET zone_observer_wording_approved = true")

    # -- what was there before is still there -----------------------------------------------------

    def test_every_existing_template_and_send_reads_report(self):
        kinds = {r["match_kind"] for r in self.conn.execute("SELECT DISTINCT match_kind FROM email_templates")}
        self.assertTrue(kinds <= {"report", "zone"})
        old = self.conn.execute("SELECT basis, level, zone_set_id FROM send_log WHERE send_id = %s",
                                (self.old["send_id"],)).fetchone()
        self.assertEqual((old["basis"], old["level"], old["zone_set_id"]), ("report", None, None))

    def test_the_setting_starts_false(self):
        self.conn.rollback()
        flag = self.conn.execute("SELECT zone_observer_wording_approved AS a FROM settings").fetchone()["a"]
        self.assertIs(flag, False)

    def test_the_existing_send_code_which_names_no_new_column_still_inserts_a_report_send(self):
        self.conn.execute(
            "INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by, email_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)", (self.old["realtor_id"], self.old["recipient_email"],
                                                 self.old["match_id"], self.report_tpl, self.old["sent_by"],
                                                 self.old["email_id"]))
        row = self.conn.execute("SELECT basis, level, zone_set_id FROM send_log ORDER BY send_id DESC LIMIT 1").fetchone()
        self.assertEqual((row["basis"], row["level"], row["zone_set_id"]), ("report", None, None))

    def test_a_report_send_under_a_report_template_is_accepted(self):
        self.send(self.old["match_id"], self.report_tpl)

    # -- the pairing ------------------------------------------------------------------------------

    def test_a_zone_match_under_a_report_template_is_refused(self):
        self.refused(lambda: self.send(self.matches["radar"], self.report_tpl, "radar", "agree", self.set_id),
                     "zone-kind template")

    def test_a_report_match_under_a_zone_template_is_refused(self):
        self.refused(lambda: self.send(self.old["match_id"], self.zone_tpl), "report-kind template")

    def test_a_radar_zone_send_under_a_zone_template_is_accepted(self):
        self.zone_send("radar")

    def test_the_record_must_say_what_the_match_says(self):
        radar, tpl, set_id = self.matches["radar"], self.zone_tpl, self.set_id
        # A real parameter set that is not the match's own (never committed:  its 20 rules are only checked at commit).
        other = self.conn.execute(
            "INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi) "
            "SELECT 'other set', radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi "
            "FROM likelihood_parameter_sets WHERE set_id = %s RETURNING set_id", (set_id,)).fetchone()["set_id"]
        cases = {"basis report": lambda: self.send(radar, tpl, "report", None, None),
                 "wrong basis": lambda: self.send(radar, tpl, "both", "agree", set_id),
                 "wrong level": lambda: self.send(radar, tpl, "radar", "one_source", set_id),
                 "a set that does not exist": lambda: self.send(radar, tpl, "radar", "agree", 987654321),
                 "a different real set": lambda: self.send(radar, tpl, "radar", "agree", other),
                 "no set": lambda: self.send(radar, tpl, "radar", "agree", None),
                 "no level": lambda: self.send(radar, tpl, "radar", None, set_id)}
        for label, fn in cases.items():
            with self.subTest(case=label):
                self.refused(fn)

    def test_a_report_match_is_recorded_as_a_report_only(self):
        self.refused(lambda: self.send(self.old["match_id"], self.report_tpl, "radar", "agree", self.set_id),
                     "basis report")

    def test_a_bad_snapshot_is_refused_by_the_table_too(self):
        # With no trigger in the way (a report match), the CHECKs still hold on their own.
        for label, basis, level, zone_set in (("unknown basis", "guess", None, None),
                                              ("a zone basis with no level", "radar", None, self.set_id),
                                              ("a zone basis with no set", "radar", "agree", None),
                                              ("a report with a made-up level", "report", "maybe", None)):
            with self.subTest(case=label):
                self.refused(lambda: self.send(self.old["match_id"], self.report_tpl, basis, level, zone_set))

    def test_a_report_send_may_carry_the_level_looked_up_at_send_time(self):
        for level in ("agree", "one_source", "context", "none", None):
            with self.subTest(level=level):
                self.send(self.old["match_id"], self.report_tpl, "report", level, self.set_id if level else None)

    # -- the observer-wording gate ------------------------------------------------------------------

    def test_observer_and_both_sends_are_refused_while_the_setting_is_false(self):
        for basis in ("observer", "both"):
            with self.subTest(basis=basis):
                self.refused(lambda: self.zone_send(basis), "not approved in settings")

    def test_a_radar_send_does_not_need_the_setting(self):
        self.zone_send("radar")

    def test_once_the_setting_is_true_observer_and_both_sends_are_accepted(self):
        self.approve()
        self.zone_send("observer")
        self.zone_send("both")

    def test_turning_the_setting_on_is_logged_with_who_did_it(self):
        before = self.conn.execute("SELECT count(*) AS n FROM settings_history").fetchone()["n"]
        self.approve()
        row = self.conn.execute("SELECT zone_observer_wording_approved AS a, changed_by FROM settings_history "
                                "ORDER BY history_id DESC LIMIT 1").fetchone()
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM settings_history").fetchone()["n"], before + 1)
        self.assertEqual((row["a"], row["changed_by"]), (True, self.emp))

    def test_changing_another_setting_still_logs_the_flag_as_it_stands(self):
        self.conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(self.emp),))
        self.conn.execute("UPDATE settings SET email_cap_days = email_cap_days + 1")
        row = self.conn.execute("SELECT zone_observer_wording_approved AS a FROM settings_history "
                                "ORDER BY history_id DESC LIMIT 1").fetchone()
        self.assertIs(row["a"], False)

    # -- frozen after insert ------------------------------------------------------------------------

    def test_the_snapshot_columns_cannot_change_after_the_send(self):
        self.zone_send("radar")
        send_id = self.conn.execute("SELECT max(send_id) AS m FROM send_log").fetchone()["m"]
        for column, value in (("basis", "'report'"), ("level", "'one_source'"), ("zone_set_id", "NULL")):
            with self.subTest(column=column):
                self.refused(lambda: self.conn.execute(f"UPDATE send_log SET {column} = {value} WHERE send_id = %s",
                                                       (send_id,)), "only provider status columns")

    def test_provider_status_still_updates_and_a_send_still_cannot_be_deleted(self):
        self.zone_send("radar")
        send_id = self.conn.execute("SELECT max(send_id) AS m FROM send_log").fetchone()["m"]
        self.conn.execute("UPDATE send_log SET send_status = 'failed' WHERE send_id = %s", (send_id,))
        # hail_app has no DELETE, so its refusal would be the grant's, not the trigger's (item 241).
        if not self.conn.execute("SELECT has_table_privilege('send_log', 'DELETE') AS ok").fetchone()["ok"]:
            self.skipTest("the delete half needs a role that reaches the trigger (hail_admin)")
        self.refused(lambda: self.conn.execute("DELETE FROM send_log WHERE send_id = %s", (send_id,)), "append-only")

    def test_a_template_kind_cannot_change_and_the_old_freezes_still_hold(self):
        for label, sql, message in (
                ("kind", "UPDATE email_templates SET match_kind = 'report' WHERE template_id = %s", "never edited"),
                ("body", "UPDATE email_templates SET body = 'x' WHERE template_id = %s", "never edited"),
                ("subject", "UPDATE email_templates SET subject = 'x' WHERE template_id = %s", "never edited"),
                ("delete", "DELETE FROM email_templates WHERE template_id = %s", "cannot be deleted")):
            with self.subTest(case=label):
                # As above: without DELETE the grant refuses before the trigger can (item 241).
                if label == "delete" and not self.conn.execute(
                        "SELECT has_table_privilege('email_templates', 'DELETE') AS ok").fetchone()["ok"]:
                    self.skipTest("needs a role that reaches the delete trigger (hail_admin)")
                self.refused(lambda: self.conn.execute(sql, (self.zone_tpl,)), message)

    def test_an_unused_templates_description_is_editable_and_retiring_is_one_way(self):
        self.conn.execute("UPDATE email_templates SET description = 'note' WHERE template_id = %s", (self.zone_tpl,))
        self.conn.execute("UPDATE email_templates SET is_active = false WHERE template_id = %s", (self.zone_tpl,))
        self.refused(lambda: self.conn.execute("UPDATE email_templates SET is_active = true WHERE template_id = %s",
                                               (self.zone_tpl,)), "cannot be reactivated")

    def test_an_unknown_template_kind_is_refused(self):
        self.refused(lambda: self.conn.execute(
            "INSERT INTO email_templates (template_name, subject, body, created_by, match_kind) "
            "VALUES ('x', 's', 'b', %s, 'guess')", (self.emp,)), "email_templates_match_kind_known")

    def test_the_old_blanket_guard_is_gone_and_the_pairing_trigger_is_there(self):
        names = {r["tgname"] for r in self.conn.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'send_log'::regclass AND NOT tgisinternal")}
        self.assertIn("trg_send_log_kind_pairing", names)
        self.assertNotIn("trg_send_log_report_match_only", names)


if __name__ == "__main__":
    unittest.main()
