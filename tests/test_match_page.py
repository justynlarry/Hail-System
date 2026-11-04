"""The match page (item 201 pass, items 108, 109, 140, 297): /storms/matches with its database reads
stubbed, and sendlist.listing_reason, the rule the page and the send list share.

    docker compose run --rm -v ./tests:/app/tests:ro web python -m unittest tests.test_match_page
"""

import os
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.queries import sendlist
    from hailsys.web import create_app
    from hailsys.web import views
    MISSING = None
except ImportError as exc:                       # pragma: no cover
    MISSING = str(exc)

NOW = datetime.now(timezone.utc)


def listing(n, *, realtor=1, status="Active", seen_days_ago=1, agent="Pat Agent"):
    return {"listing_id": n, "property_address": f"{n} Example St, Denver, CO 80202", "city": "Denver",
            "zip_code": "80202", "property_type": "Single Family", "year_built": 1990,
            "list_price": Decimal("500000"), "list_date": date(2026, 9, 1), "list_mls_number": f"MLS{n}",
            "list_status": status,
            "list_last_seen": None if seen_days_ago is None else NOW - timedelta(days=seen_days_ago),
            "realtor_id": realtor, "agent_name": agent if realtor else None,
            "agent_email": "agent@example.test" if realtor else None, "agent_phone": None,
            "agent_office_name": None, "nearest_miles": Decimal("1.2"), "max_magnitude": Decimal("1.75"),
            "mag_unit": "in", "report_count": 2}


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class ListingReasonTest(unittest.TestCase):
    def test_the_two_listing_rules_in_the_send_lists_order(self):
        fresh_day = date.today() - timedelta(days=7)
        self.assertIsNone(sendlist.listing_reason("Active", NOW, fresh_day))
        self.assertEqual(sendlist.listing_reason("Active", NOW - timedelta(days=30), fresh_day), "stale_listing")
        self.assertEqual(sendlist.listing_reason("Active", None, fresh_day), "stale_listing")
        self.assertEqual(sendlist.listing_reason("Inactive", NOW, fresh_day), "inactive_listing")
        self.assertEqual(sendlist.listing_reason("Inactive", None, fresh_day), "inactive_listing")   # inactive first


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"), "no database or secret key")
class MatchPageTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(conn.close)
        self.users = {}
        for r in conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                              "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        self.rows = [listing(1), listing(2, seen_days_ago=40), listing(3, status="Inactive"),
                     listing(4, realtor=None, seen_days_ago=None)]
        self.zips = [{"zcta5": z} for z in ("80202", "80203", "80204")]
        self.coverage = {"80202": NOW}
        for target, attr, value in ((views.matches, "fetch_match_detail", lambda *a, **k: [dict(r) for r in self.rows]),
                                    (views.storms, "fetch_zips", lambda *a, **k: self.zips),
                                    (views.matches, "fetch_pull_coverage", lambda *a, **k: self.coverage)):
            patcher = mock.patch.object(target, attr, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def page(self, role="admin", query="date=2026-09-22&type=HAIL"):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"], s["role"], s["user_name"] = self.users[role], role, "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        resp = client.get("/storms/matches?" + query)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_listings_that_will_not_be_emailed_are_tagged_not_hidden(self):
        page = self.page()
        for n in (1, 2, 3, 4):
            self.assertIn(f"{n} Example St", page)                       # every row stays (item 140)
        self.assertEqual(page.count('<tr class="not-sent">'), 3)
        self.assertIn(">Not seen recently &middot; last seen", page)
        self.assertIn(">No longer active<", page)
        self.assertRegex(page, r"<strong>3</strong> of these 4 will not be emailed")
        self.assertIn("<strong>4</strong> listings", page)               # the count is unchanged

    def test_a_fresh_active_listing_has_no_tag(self):
        self.rows = [listing(1)]
        page = self.page()
        self.assertNotIn("send-tag", page)
        self.assertNotIn("will not be emailed: RentCast", page)       # the summary line, not the intro

    def test_columns_are_named_not_numbered(self):
        page = self.page()
        for name in ("col-addr", "col-zip", "col-type", "col-built", "col-price", "col-near", "col-max",
                     "col-reports", "col-mls"):
            self.assertIn(f'<th class="{name}', page)
        self.assertIn(">Largest hail</th>", page)
        self.assertIn(">Nearest report</th>", page)

    def test_the_strongest_column_says_hail_only_on_a_hail_page(self):
        page = self.page(query="date=2026-09-22&type=TSTM+WND+GST")
        self.assertIn(">Largest reported</th>", page)

    def test_the_incomplete_box_says_zips_not_zip_s(self):
        page = self.page()
        self.assertIn("touched 3\n        zips, and", page)                # item 297
        self.assertNotIn("zip s", page)

    def test_admins_get_a_button_to_open_send_for_this_day(self):
        page = self.page()
        self.assertIn('href="/send/?mode=reports&amp;day=2026-09-22">Send to these realtors</a>', page)
        if "viewer" in self.users:
            self.assertNotIn("Send to these realtors", self.page("viewer"))

    def test_each_agent_shows_how_many_listings(self):
        page = self.page()
        self.assertIn('<span class="agent-count">3 listings</span>', page)
        self.assertIn('No agent on record <span class="agent-count">1 listing</span>', page)


if __name__ == "__main__":
    unittest.main()
