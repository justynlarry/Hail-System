"""The jurisdiction panel and check form on Address Search (hailsys/web/search.py, item 190 step 1).

    docker compose --profile test run --rm -T test python -m unittest tests.test_search_jurisdiction

Runs on one connection that every get_connection() in search.py and auth.py is handed (TestConn, as
in test_sign_in_limits.py):  the searches, geocode_cache rows and checks a test writes are rolled
back at the end.  The boundaries are tests/test_jurisdiction.py's Pacific squares, in TEMPORARY
tables that shadow the real ones on that connection, so nothing real is read or touched.  The
Census geocoder is replaced by a stub that puts each address at a chosen point.  Runs as hail_app,
so its grants on jurisdiction_checks are what is tested.

What they pin:  every role sees the panel for a matched search, with the city or "Unincorporated"
and the county, the nearest line, and the amber warning when near;  only an admin gets the form,
for the search just made;  empty boundaries say "not loaded" and offer no form;  a search with no
match shows no panel.  Recording:  an agreeing check stores the system's answer as looked up again
from the search's point, never from the form, with no cause;  a differing one needs the book's
answer and a reason;  the near flag is stored and agrees with the stored distance;  a second check
of one search, a search with no location, unloaded boundaries and a bad search id are each refused
with nothing written;  a viewer or sender is refused;  the check is sent back to an empty search.
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app, auth, search as search_web
    from tests.test_jurisdiction import TESTA, TESTB, XVILLE
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

FAR_INSIDE = (-150.07, 10.07)       # in Xville, its west line about 1,096 m away
NEAR_OUTSIDE = (-150.0805, 10.05)   # unincorporated Testa, about 55 m from Xville's line


class TestConn:
    """The test's one connection, handed to every get_connection() call.  A commit moves a savepoint
    forward and a rollback goes back to it;  the whole test is rolled back at the end."""
    def __init__(self, conn):
        self._c, self._n = conn, 0
        self._savepoint()

    def _savepoint(self):
        self._n += 1
        self._c.execute(f"SAVEPOINT t{self._n}")

    def commit(self):
        self._savepoint()

    def rollback(self):
        self._c.execute(f"ROLLBACK TO SAVEPOINT t{self._n}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        return False

    def __getattr__(self, name):
        return getattr(self._c, name)


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class SearchJurisdictionTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.raw = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.raw.close)
        self.addCleanup(self.raw.rollback)
        row = self.raw.execute("SELECT emp_id FROM users WHERE role = 'admin' AND is_active "
                               "ORDER BY emp_id LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no active admin")
        self.admin = row["emp_id"]
        self.near_m = self.raw.execute("SELECT jurisdiction_near_boundary_m AS m FROM settings "
                                       "WHERE id = 1").fetchone()["m"]
        self.raw.execute("CREATE TEMPORARY TABLE municipal_boundaries ("
                         " place_fips CHAR(5), name TEXT, geom geometry(MultiPolygon, 4326),"
                         " loaded_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        self.raw.execute("CREATE TEMPORARY TABLE county_boundaries ("
                         " county_fips CHAR(5), name TEXT, geom geometry(MultiPolygon, 4326))")
        for row in (TESTA, TESTB):
            self.raw.execute("INSERT INTO county_boundaries VALUES (%s, %s, ST_GeomFromText(%s, 4326))", row)
        self.conn = TestConn(self.raw)
        for module in (search_web, auth):
            patcher = mock.patch.object(module, "get_connection", return_value=self.conn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tag = uuid.uuid4().hex[:8]
        self.n = 0

    # ---- helpers ----

    def load_cities(self):
        self.raw.execute("INSERT INTO municipal_boundaries (place_fips, name, geom) "
                         "VALUES (%s, %s, ST_GeomFromText(%s, 4326))", XVILLE)

    def client(self, role="admin"):
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.admin, role, "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def search(self, point, role="admin", found=True):
        """(page text, search_id) for one search of a new address placed at point (lon, lat)."""
        self.n += 1
        address = f"{100 + self.n} Check{self.tag} St, Denver, CO 80202"
        lon, lat = point
        result = ([{"matched_address": address.upper(), "latitude": lat, "longitude": lon,
                    "tiger_line_id": None}] if found else [])
        with mock.patch.object(search_web.geocode, "geocode", return_value=result):
            resp = self.client(role).get("/search/", query_string={"address": address})
        self.assertEqual(resp.status_code, 200)
        search_id = self.raw.execute("SELECT max(search_id) AS id FROM address_searches").fetchone()["id"]
        self.conn.commit()          # so a refused check's rollback keeps the search
        return resp.get_data(as_text=True), search_id

    def check(self, search_id, role="admin", **form):
        """(response, flashed messages) for one POST of the check form."""
        client = self.client(role)
        resp = client.post("/search/check", data={"search_id": str(search_id), **form})
        with client.session_transaction() as s:
            flashed = [m for _, m in s.get("_flashes", [])]
        return resp, flashed

    def checks(self, search_id):
        return self.raw.execute("SELECT * FROM jurisdiction_checks WHERE search_id = %s",
                                (search_id,)).fetchall()

    # ---- the panel ----

    def test_a_viewer_sees_the_panel_and_no_form(self):
        self.load_cities()
        page, _ = self.search(FAR_INSIDE, role="viewer")
        flat = " ".join(page.split())
        self.assertIn("<h3>Jurisdiction</h3>", page)
        self.assertIn("<strong>Xville</strong>, Testa County", flat)
        self.assertIn("Nearest city or county line: Xville city line,", flat)
        self.assertNotIn("Near a boundary", page)
        self.assertNotIn('action="/search/check"', page)

    def test_a_sender_sees_no_form(self):
        self.load_cities()
        page, _ = self.search(FAR_INSIDE, role="sender")
        self.assertIn("<h3>Jurisdiction</h3>", page)
        self.assertNotIn('action="/search/check"', page)

    def test_an_admin_gets_the_form_for_the_search_just_made(self):
        self.load_cities()
        page, search_id = self.search(FAR_INSIDE)
        flat = " ".join(page.split())
        self.assertIn('action="/search/check"', page)
        self.assertIn(f'name="search_id" value="{search_id}"', flat)
        for cause in search_web.CHECK_CAUSES:
            self.assertIn(f'<option value="{cause}">', page)

    def test_near_a_line_shows_the_warning(self):
        self.load_cities()
        page, _ = self.search(NEAR_OUTSIDE)
        flat = " ".join(page.split())
        self.assertIn("<strong>Unincorporated</strong>, Testa County", flat)
        self.assertIn('class="note warning"', page)
        self.assertRegex(flat, r"Near a boundary: \d+ m from the Xville city line\. Check the book\.")

    def test_unloaded_boundaries_say_so_and_offer_no_form(self):
        page, _ = self.search(FAR_INSIDE)
        self.assertIn("City boundaries aren't loaded", page)
        self.assertNotIn('action="/search/check"', page)

    def test_a_search_with_no_match_shows_no_panel(self):
        self.load_cities()
        page, _ = self.search(FAR_INSIDE, found=False)
        self.assertIn("Address not found", page)
        self.assertNotIn("<h3>Jurisdiction</h3>", page)

    # ---- recording ----

    def test_an_agreeing_check_stores_the_systems_answer_and_no_cause(self):
        self.load_cities()
        _, search_id = self.search(FAR_INSIDE)
        resp, flashed = self.check(search_id, verdict="agrees", cause="geocoder", note=" fine ")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/search/"))
        self.assertTrue(any(m.startswith("Check recorded for ") and "says the same" in m for m in flashed))
        [row] = self.checks(search_id)
        self.assertEqual((row["place_count"], row["place_fips"], row["place_name"]), (1, "99901", "Xville"))
        self.assertEqual((row["county_fips"], row["county_name"]), ("99001", "Testa"))
        self.assertEqual((row["nearest_line_kind"], row["nearest_line_name"]), ("municipal", "Xville"))
        self.assertTrue(1085 <= row["nearest_line_m"] <= 1105)
        self.assertFalse(row["near_boundary"])
        self.assertEqual(row["near_boundary_m"], self.near_m)
        self.assertEqual((row["verdict"], row["cause"], row["note"]), ("agrees", None, "fine"))
        self.assertEqual(row["checked_by"], self.admin)

    def test_the_systems_answer_is_never_taken_from_the_form(self):
        self.load_cities()
        _, search_id = self.search(FAR_INSIDE)
        self.check(search_id, verdict="agrees", place_name="Fakeville", place_fips="00000",
                   near_boundary="true", nearest_line_m="1")
        [row] = self.checks(search_id)
        self.assertEqual((row["place_name"], row["place_fips"]), ("Xville", "99901"))
        self.assertFalse(row["near_boundary"])

    def test_a_differing_check_stores_the_books_answer_and_reason(self):
        self.load_cities()
        _, search_id = self.search(FAR_INSIDE)
        _, flashed = self.check(search_id, verdict="differs", book_answer=" Testa County ",
                                cause="book_outdated", new_build="1")
        [row] = self.checks(search_id)
        self.assertEqual((row["verdict"], row["book_answer"], row["cause"], row["new_build"]),
                         ("differs", "Testa County", "book_outdated", True))
        self.assertTrue(any("says something else (the book is out of date" in m for m in flashed))

    def test_a_differing_check_needs_the_books_answer_and_a_reason(self):
        self.load_cities()
        _, search_id = self.search(FAR_INSIDE)
        for form in ({"verdict": "differs", "cause": "boundary"},
                     {"verdict": "differs", "book_answer": "Testa County"},
                     {"verdict": "differs", "book_answer": "Testa County", "cause": "made-up"},
                     {"book_answer": "Testa County", "cause": "boundary"}):
            with self.subTest(form=form):
                resp, flashed = self.check(search_id, **form)
                self.assertEqual(resp.status_code, 302)
                self.assertIn("address=", resp.headers["Location"])     # searched again
                self.assertTrue(any("Nothing was recorded" in m for m in flashed))
        self.assertEqual(self.checks(search_id), [])

    def test_the_near_flag_is_stored_and_agrees_with_the_stored_distance(self):
        self.load_cities()
        _, search_id = self.search(NEAR_OUTSIDE)
        self.check(search_id, verdict="agrees")
        [row] = self.checks(search_id)
        self.assertEqual(row["place_count"], 0)
        self.assertTrue(row["near_boundary"])
        self.assertLessEqual(row["nearest_line_m"], row["near_boundary_m"])

    def test_a_second_check_of_one_search_is_refused(self):
        self.load_cities()
        _, search_id = self.search(FAR_INSIDE)
        self.check(search_id, verdict="agrees")
        _, flashed = self.check(search_id, verdict="differs", book_answer="Testb County", cause="unknown")
        self.assertTrue(any("already checked" in m for m in flashed))
        [row] = self.checks(search_id)
        self.assertEqual(row["verdict"], "agrees")

    def test_a_search_with_no_location_is_refused(self):
        self.load_cities()
        _, search_id = self.search(FAR_INSIDE, found=False)
        _, flashed = self.check(search_id, verdict="agrees")
        self.assertTrue(any("no location to check" in m for m in flashed))
        self.assertEqual(self.checks(search_id), [])

    def test_unloaded_boundaries_are_refused(self):
        _, search_id = self.search(FAR_INSIDE)
        _, flashed = self.check(search_id, verdict="agrees")
        self.assertTrue(any("aren't loaded" in m for m in flashed))
        self.assertEqual(self.checks(search_id), [])

    def test_a_bad_search_id_is_refused(self):
        resp = self.client().post("/search/check", data={"search_id": "abc", "verdict": "agrees"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/search/"))

    def test_a_viewer_or_sender_is_refused(self):
        self.load_cities()
        _, search_id = self.search(FAR_INSIDE)
        for role in ("viewer", "sender"):
            with self.subTest(role=role):
                resp, _ = self.check(search_id, role=role, verdict="agrees")
                self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.checks(search_id), [])


if __name__ == "__main__":
    unittest.main()
