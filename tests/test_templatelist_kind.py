"""Tests for the kind filter in hailsys/queries/templatelist.py and its report callers (item 265, step 4 piece 4b-1).

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -e FLASK_SECRET_KEY="$FK" -v ./tests:/app/tests:ro web python -m unittest tests.test_templatelist_kind

The query tests run in a transaction that is rolled back.  The page tests cannot see a template made in that
transaction (the app has its own connections), so they check that every list call on the report send page and
the editor asks for the report kind:  once by reading the code, once by watching the real /send page.

What they pin:  a kind lists only that kind, None lists both, an unknown kind is refused;  a name of the other kind
is not found;  the page count counts only the kind asked for;  and the report send page, its follow-up list, its
confirm step and the block editor never list a zone template.
"""

import ast
import os
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import templatestore as ts
    from hailsys.queries import templatelist as tl
    MISSING = None
except ImportError as exc:      # psycopg and jinja2 live in the app image; the host run skips (item 273)
    MISSING = str(exc)

ROOT = Path(__file__).resolve().parent.parent
REPORT_SUBJECT = "Hail near {{ total }} of your listings"
REPORT_BODY = "<p>Hello {{ agent_first_name }} {{ nearest_miles }}</p>[[trackingImage]]"
ZONE_SUBJECT = "Hail near {{ total }} listings"
ZONE_BODY = "<p>{{ agent_first_name }}</p>{% for e in evidence %}<p>{{ e }}</p>{% endfor %}[[trackingImage]]"


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class KindFilterTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        user = self.conn.execute("SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        prefix = f"zz-kind-{uuid.uuid4().hex[:8]}"
        self.report = f"{prefix}-report"
        self.zone = f"{prefix}-zone"
        ts.ensure_current(self.conn, created_by=user, name=self.report,
                          subject_src=REPORT_SUBJECT, body_src=REPORT_BODY)
        ts.ensure_current(self.conn, created_by=user, name=self.zone,
                          subject_src=ZONE_SUBJECT, body_src=ZONE_BODY, match_kind="zone")

    def names(self, rows):
        return {r["template_name"] for r in rows} & {self.report, self.zone}

    def active(self, kind=None):
        sql = "SELECT count(*) AS n FROM email_templates WHERE is_active"
        if kind:
            return self.conn.execute(sql + " AND match_kind = %s", (kind,)).fetchone()["n"]
        return self.conn.execute(sql).fetchone()["n"]

    def test_fetch_all_lists_only_the_kind_asked_for(self):
        self.assertEqual(self.names(tl.fetch_all(self.conn, kind="report")), {self.report})
        self.assertEqual(self.names(tl.fetch_all(self.conn, kind="zone")), {self.zone})
        self.assertEqual(self.names(tl.fetch_all(self.conn)), {self.report, self.zone})

    def test_fetch_one_finds_a_name_only_under_its_own_kind(self):
        self.assertIsNone(tl.fetch_one(self.conn, self.zone, kind="report"))
        self.assertIsNone(tl.fetch_one(self.conn, self.report, kind="zone"))
        self.assertEqual(tl.fetch_one(self.conn, self.zone, kind="zone")["template_name"], self.zone)
        self.assertEqual(tl.fetch_one(self.conn, self.report, kind="report")["template_name"], self.report)
        self.assertEqual(tl.fetch_one(self.conn, self.zone)["template_name"], self.zone)

    def test_the_page_count_counts_only_the_kind(self):
        for kind in ("report", "zone", None):
            with self.subTest(kind=kind):
                page = tl.fetch_page(self.conn, 1, per_page=1000, kind=kind)
                self.assertEqual(page["total"], self.active(kind))
                self.assertEqual(len(page["rows"]), self.active(kind))
        page = tl.fetch_page(self.conn, 1, per_page=1, kind="zone")
        self.assertEqual((page["total"], page["pages"]), (self.active("zone"), self.active("zone")))

    def test_every_page_holds_only_the_kind(self):
        seen = set()
        page = tl.fetch_page(self.conn, 1, per_page=3, kind="report")
        for n in range(1, page["pages"] + 1):
            seen |= {r["template_name"] for r in tl.fetch_page(self.conn, n, per_page=3, kind="report")["rows"]}
        self.assertIn(self.report, seen)
        self.assertNotIn(self.zone, seen)

    def test_an_unknown_kind_is_refused(self):
        for kind in ("", "Zone", "both", "report "):
            with self.subTest(kind=kind):
                for call in (lambda: tl.fetch_all(self.conn, kind=kind),
                             lambda: tl.fetch_one(self.conn, self.zone, kind=kind),
                             lambda: tl.fetch_page(self.conn, 1, kind=kind)):
                    with self.assertRaises(ValueError):
                        call()


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ReportCallersTest(unittest.TestCase):
    """Every templatelist call on the report send page and in the block editor asks for 'report'."""

    def calls(self, relpath):
        tree = ast.parse((ROOT / relpath).read_text())
        out = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "templatelist"):
                kind = [k.value for k in node.keywords if k.arg == "kind"]
                out.append((node.func.attr, node.lineno,
                            kind[0].value if kind and isinstance(kind[0], ast.Constant) else None))
        return out

    def test_the_report_send_page_and_the_editor_ask_for_report(self):
        for relpath, at_least in (("hailsys/web/send.py", 6), ("hailsys/web/emailtpl.py", 2)):
            found = self.calls(relpath)
            self.assertGreaterEqual(len(found), at_least, relpath)
            for name, line, kind in found:
                with self.subTest(file=relpath, line=line, call=name):
                    self.assertEqual(kind, "report")


@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class SendPageAsksForReportTest(unittest.TestCase):
    def setUp(self):
        from hailsys.web import create_app
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        with psycopg.connect(row_factory=dict_row) as conn:
            row = conn.execute("SELECT emp_id FROM users WHERE is_active AND role = 'admin' "
                               "ORDER BY emp_id LIMIT 1").fetchone()
        if row is None:
            self.skipTest("no active admin")
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s.update(emp_id=row["emp_id"], role="admin", user_name="test",
                     issued_at=datetime.now(timezone.utc).isoformat())

    def test_the_send_page_and_the_editor_list_report_templates_only(self):
        kinds = []

        def spy(real):
            def wrapped(*a, **kw):
                kinds.append(kw.get("kind"))
                return real(*a, **kw)
            return wrapped

        from hailsys.web import send as screen
        with mock.patch.object(tl, "fetch_page", spy(tl.fetch_page)), \
                mock.patch.object(tl, "fetch_one", spy(tl.fetch_one)), \
                mock.patch.object(tl, "fetch_all", spy(tl.fetch_all)), \
                mock.patch.object(screen.dripscreen, "due_steps", return_value=[]):
            self.assertEqual(self.client.get("/send/").status_code, 200)
            self.assertEqual(self.client.get("/templates/").status_code, 200)
            screen._campaign_templates()
            screen._template_available(f"nope-{uuid.uuid4().hex[:6]}")
        self.assertTrue(kinds)
        self.assertEqual(set(kinds), {"report"})


if __name__ == "__main__":
    unittest.main()
