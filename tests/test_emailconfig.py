"""Tests for hailsys/email/emailconfig.py (item 203, piece 1).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_emailconfig

The database tests run in a transaction that is rolled back, so the real settings and images
are never left changed.  Skips without a database.  The missing-image case cannot be made in
the real table (a CHECK stops a required image being retired, and nothing may delete), so it
uses a stand-in connection.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import emailconfig, render
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class _FakeConn:
    """Answers the two queries with the images it is given."""
    def __init__(self, images):
        self.images = images

    def execute(self, sql, params=None):
        if "email_images" in sql:
            return _Result([{"name": n, "url": u, "alt_text": f"Alt {n}"}
                            for n, u in self.images.items()])
        return _Result([{"contact_email": "c@example.com", "schedule_url": "https://x.test/s",
                         "from_name": "N", "from_email": "f@example.com",
                         "reply_to": "r@example.com"}])


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class FakeConnTest(unittest.TestCase):
    URLS = {"logo": "https://x.test/l.png", "badge_cra": "https://x.test/c.png",
            "badge_bbb": "https://x.test/b.png"}

    def test_every_required_image_lands_in_its_field(self):
        got = emailconfig.load_settings(_FakeConn(self.URLS))
        self.assertEqual((got.logo_url, got.badge_cra_url, got.badge_bbb_url),
                         ("https://x.test/l.png", "https://x.test/c.png", "https://x.test/b.png"))
        self.assertEqual((got.contact_email, got.schedule_url),
                         ("c@example.com", "https://x.test/s"))

    def test_every_active_image_is_carried_with_its_alt_text(self):
        urls = {**self.URLS, "spring": "https://x.test/spring.png"}
        got = emailconfig.load_settings(_FakeConn(urls))
        self.assertEqual(got.images["spring"], {"url": "https://x.test/spring.png",
                                                "alt": "Alt spring"})
        self.assertEqual(set(got.images), set(urls))

    def test_a_missing_image_is_loud_and_named(self):
        for gone in self.URLS:
            urls = {k: v for k, v in self.URLS.items() if k != gone}
            with self.assertRaises(render.SettingsError) as ctx:
                emailconfig.load_settings(_FakeConn(urls))
            self.assertIn(gone, str(ctx.exception))

    def test_several_missing_images_are_all_named(self):
        with self.assertRaises(render.SettingsError) as ctx:
            emailconfig.load_settings(_FakeConn({"logo": self.URLS["logo"]}))
        self.assertIn("badge_cra", str(ctx.exception))
        self.assertIn("badge_bbb", str(ctx.exception))

    def test_the_sender_comes_from_the_settings_row(self):
        self.assertEqual(emailconfig.load_sender(_FakeConn(self.URLS)),
                         {"from_name": "N", "from_email": "f@example.com",
                          "reply_to": "r@example.com"})


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        user = self.conn.execute(
            "SELECT emp_id FROM users WHERE role = 'system'").fetchone()["emp_id"]
        # The settings history trigger reads the acting user from this.
        self.conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(user),))

    def set_image(self, name, url):
        self.conn.execute("UPDATE email_images SET url = %s WHERE name = %s", (url, name))

    def test_the_seeded_images_and_settings_load(self):
        got = emailconfig.load_settings(self.conn)
        self.assertTrue(got.logo_url.startswith("https://"))
        self.assertTrue(got.badge_cra_url.startswith("https://"))
        self.assertTrue(got.badge_bbb_url.startswith("https://"))
        self.assertIn("@", got.contact_email)

    def test_an_edited_image_is_what_loads(self):
        self.set_image("logo", "https://images.example.test/new-logo.png")
        self.set_image("badge_bbb", "https://images.example.test/new-bbb.png")
        got = emailconfig.load_settings(self.conn)
        self.assertEqual(got.logo_url, "https://images.example.test/new-logo.png")
        self.assertEqual(got.badge_bbb_url, "https://images.example.test/new-bbb.png")
        self.assertNotEqual(got.badge_cra_url, got.logo_url)

    def test_an_edited_setting_is_what_loads(self):
        self.conn.execute(
            "UPDATE settings SET contact_email = 'edited@example.test', "
            "schedule_url = 'https://example.test/book', from_name = 'Edited Name', "
            "from_email = 'from-edited@example.test', reply_to = 'reply-edited@example.test'")
        got = emailconfig.load_settings(self.conn)
        self.assertEqual((got.contact_email, got.schedule_url),
                         ("edited@example.test", "https://example.test/book"))
        self.assertEqual(emailconfig.load_sender(self.conn),
                         {"from_name": "Edited Name", "from_email": "from-edited@example.test",
                          "reply_to": "reply-edited@example.test"})

    def add_image(self, name, url="https://extra.example.test/x.png", alt="Extra alt"):
        self.conn.execute(
            "INSERT INTO email_images (name, url, alt_text, created_by, updated_by) "
            "SELECT %s, %s, %s, emp_id, emp_id FROM users WHERE role = 'system'",
            (name, url, alt))

    def test_an_extra_image_is_available_to_templates_but_not_a_required_field(self):
        self.add_image("zz-extra")
        got = emailconfig.load_settings(self.conn)
        self.assertEqual(got.images["zz-extra"],
                         {"url": "https://extra.example.test/x.png", "alt": "Extra alt"})
        for field in ("logo_url", "badge_cra_url", "badge_bbb_url"):
            self.assertNotIn("extra.example.test", getattr(got, field))

    def test_a_retired_image_is_not_loaded(self):
        self.add_image("zz-retired")
        self.conn.execute("UPDATE email_images SET is_active = FALSE WHERE name = 'zz-retired'")
        self.assertNotIn("zz-retired", emailconfig.load_settings(self.conn).images)
        self.assertNotIn("zz-retired", emailconfig.load_images(self.conn))

    def test_the_required_images_carry_the_seeded_alt_text(self):
        images = emailconfig.load_images(self.conn)
        self.assertEqual(images["logo"]["alt"], "Roof Brokers Inc.")
        self.assertEqual(images["badge_cra"]["alt"], "CRA Member")
        self.assertEqual(images["badge_bbb"]["alt"], "BBB Accredited")

    def test_the_block_built_email_renders_from_the_database_images(self):
        # the whole path: database images -> settings -> context -> compiled blocks -> HTML
        from datetime import datetime, timezone
        from decimal import Decimal
        from hailsys.email import blocks
        self.add_image("zz-promo", "https://extra.example.test/promo.png", 'Promo "A"')
        settings = emailconfig.load_settings(self.conn)
        matches = [{"listing_id": 1, "address": "1 Main St, Denver, CO",
                    "utc_datetime": datetime(2026, 8, 15, 20, 19, tzinfo=timezone.utc),
                    "magnitude": Decimal("1.75"), "distance_miles": Decimal("0.4")}]
        context = render.build_context("Pat Jones", matches, settings)
        template = {**blocks.HAIL_ALERT_BLOCKS,
                    "blocks": [{"kind": "image", "image": "zz-promo"}]
                              + blocks.HAIL_ALERT_BLOCKS["blocks"]}
        subject, html = blocks.compile_blocks(template)
        out_subject, out_html = render.render(subject, html, context)
        self.assertIn(f'src="{settings.logo_url}"', out_html)
        self.assertIn(f'src="{settings.badge_cra_url}"', out_html)
        self.assertIn('src="https://extra.example.test/promo.png"', out_html)
        self.assertIn('alt="Promo &#34;A&#34;"', out_html)
        self.assertIn("Pat,", out_html)
        self.assertIn("1 Main St", out_subject)

    def test_an_address_changed_on_the_admin_page_reaches_the_email(self):
        from datetime import datetime, timezone
        from decimal import Decimal
        from hailsys.email import blocks
        self.set_image("logo", "https://images.example.test/changed-logo.png")
        context = render.build_context(
            "Pat", [{"listing_id": 1, "address": "1 Main St", "magnitude": Decimal("1"),
                     "utc_datetime": datetime(2026, 8, 15, 20, 19, tzinfo=timezone.utc),
                     "distance_miles": Decimal("1")}], emailconfig.load_settings(self.conn))
        subject, html = blocks.compile_blocks(blocks.HAIL_ALERT_BLOCKS)
        self.assertIn('src="https://images.example.test/changed-logo.png"',
                      render.render(subject, html, context)[1])

    def test_a_free_hosting_address_is_refused_on_load(self):
        # The database accepts any https address; the renderer's rule refuses this one.
        self.set_image("logo", "https://something.azurewebsites.net/logo.png")
        with self.assertRaises(render.SettingsError) as ctx:
            emailconfig.load_settings(self.conn)
        self.assertIn("free-hosting", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
