"""Tests for two guards no other test reached (parking-lot item 214):  cc_status_runs_guard() (sql/039) and
pull_zips_append_only_guard() (sql/059).  cc_api_calls_guard() (sql/040) is tested in test_cc_budget.py.

The guard tests need a role that can reach the triggers (hail_admin):  hail_app has no DELETE on either table and no
UPDATE on pull_zips, so as hail_app the grant refuses first and the trigger is never reached.  The grant tests switch
to hail_app with SET LOCAL ROLE.  Everything runs in one transaction that is always rolled back:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_log_guards

Under the standard command (hail_app) the guard tests skip.

What they pin:  cc_status_runs refuses DELETE, any change to a finished run, and a change to the id, start time or
person of a running one, and lets a running run record its outcome;  pull_zips refuses every UPDATE and DELETE;  and
the grants:  hail_app may not delete from either table or update pull_zips.
"""

import os
import unittest


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class LogGuardTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.psycopg = psycopg
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        for table in ("cc_status_runs", "pull_zips"):
            if not self.conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (table,)).fetchone()["ok"]:
                self.fail(f"{table} does not exist (sql/039 or sql/059 not applied)")
        # The trigger sits behind the grant; only a role with DELETE on pull_zips reaches it.
        if not self.conn.execute("SELECT has_table_privilege('pull_zips', 'DELETE') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that reaches the triggers (hail_admin)")
        self.emp = self.conn.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]

    def refused(self, sql, args=(), error=None):
        """The statement fails with a restrict_violation (the guard) or the given error; only its savepoint is undone."""
        error = error or self.psycopg.errors.RestrictViolation
        with self.assertRaises(error):
            with self.conn.transaction():
                self.conn.execute(sql, args)

    def running_run(self):
        return self.conn.execute(
            "INSERT INTO cc_status_runs (triggered_by) VALUES (%s) RETURNING run_id", (self.emp,)).fetchone()["run_id"]

    def a_pull_zip(self):
        row = self.conn.execute("SELECT pull_id, zcta5 FROM pull_zips LIMIT 1").fetchone()
        if row is None:
            pull = self.conn.execute(
                "INSERT INTO api_pulls (emp_id, zip_count, estimated_api_calls, api_status) "
                "VALUES (%s, 1, 1, 'complete') RETURNING pull_id", (self.emp,)).fetchone()["pull_id"]
            self.conn.execute("INSERT INTO pull_zips (pull_id, zcta5, basis) VALUES (%s, '80000', 'reports')", (pull,))
            row = {"pull_id": pull, "zcta5": "80000"}
        return row

    # --- cc_status_runs (sql/039) ---

    def test_a_running_run_records_its_outcome(self):
        run = self.running_run()
        self.conn.execute("UPDATE cc_status_runs SET status = 'ok', finished_at = now(), checked = 3, bounced = 1 "
                          "WHERE run_id = %s", (run,))
        row = self.conn.execute("SELECT status, checked FROM cc_status_runs WHERE run_id = %s", (run,)).fetchone()
        self.assertEqual((row["status"], row["checked"]), ("ok", 3))

    def test_a_finished_run_cannot_change(self):
        run = self.running_run()
        self.conn.execute("UPDATE cc_status_runs SET status = 'failed', finished_at = now() WHERE run_id = %s", (run,))
        self.refused("UPDATE cc_status_runs SET error_detail = 'x' WHERE run_id = %s", (run,))
        self.refused("UPDATE cc_status_runs SET status = 'running', finished_at = NULL WHERE run_id = %s", (run,))

    def test_a_running_run_keeps_its_identity(self):
        run = self.running_run()
        self.refused("UPDATE cc_status_runs SET started_at = started_at - interval '1 day' WHERE run_id = %s", (run,))
        other = self.conn.execute("SELECT max(emp_id) AS e FROM users").fetchone()["e"]
        self.assertNotEqual(other, self.emp, "needs two users to change triggered_by")
        self.refused("UPDATE cc_status_runs SET triggered_by = %s WHERE run_id = %s", (other, run))
        self.refused("UPDATE cc_status_runs SET run_id = run_id + 1000000 WHERE run_id = %s", (run,),
                     error=(self.psycopg.errors.RestrictViolation, self.psycopg.errors.GeneratedAlways))

    def test_no_run_can_be_deleted(self):
        run = self.running_run()
        self.refused("DELETE FROM cc_status_runs WHERE run_id = %s", (run,))

    # --- pull_zips (sql/059) ---

    def test_pull_zips_refuses_update(self):
        row = self.a_pull_zip()
        self.refused("UPDATE pull_zips SET basis = basis WHERE pull_id = %s AND zcta5 = %s",
                     (row["pull_id"], row["zcta5"]))

    def test_pull_zips_refuses_delete(self):
        row = self.a_pull_zip()
        self.refused("DELETE FROM pull_zips WHERE pull_id = %s AND zcta5 = %s", (row["pull_id"], row["zcta5"]))

    def test_a_pull_takes_zips_in_the_transaction_that_creates_it(self):
        pull = self.conn.execute(
            "INSERT INTO api_pulls (emp_id, zip_count, estimated_api_calls, api_status) "
            "VALUES (%s, 1, 1, 'running') RETURNING pull_id", (self.emp,)).fetchone()["pull_id"]
        self.conn.execute("INSERT INTO pull_zips (pull_id, zcta5, basis) VALUES (%s, '80001', 'reports')", (pull,))

    def test_an_older_pull_takes_no_more_zips(self):
        # sql/063 (item 278): now() is the transaction's start, so an hour-old pull was made in another one.
        pull = self.conn.execute(
            "INSERT INTO api_pulls (emp_id, zip_count, estimated_api_calls, api_status, started_at) "
            "VALUES (%s, 1, 1, 'complete', now() - interval '1 hour') RETURNING pull_id", (self.emp,)).fetchone()["pull_id"]
        self.refused("INSERT INTO pull_zips (pull_id, zcta5, basis) VALUES (%s, '80002', 'reports')", (pull,))

    # --- grants ---

    def test_the_app_role_cannot_delete_or_rewrite(self):
        run = self.running_run()
        row = self.a_pull_zip()
        denied = self.psycopg.errors.InsufficientPrivilege
        for sql, args in (("DELETE FROM cc_status_runs WHERE run_id = %s", (run,)),
                          ("DELETE FROM pull_zips WHERE pull_id = %s", (row["pull_id"],)),
                          ("UPDATE pull_zips SET basis = basis WHERE pull_id = %s", (row["pull_id"],)),
                          ("TRUNCATE cc_status_runs", ()),
                          ("TRUNCATE pull_zips", ())):
            with self.subTest(sql=sql):
                with self.assertRaises(denied):
                    with self.conn.transaction():
                        self.conn.execute("SET LOCAL ROLE hail_app")
                        self.conn.execute(sql, args)


if __name__ == "__main__":
    unittest.main()
