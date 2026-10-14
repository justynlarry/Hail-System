"""Tests for hailsys/queries/emailadmin.py, on the real database (item 203, piece 1).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_emailadmin

Everything runs in a transaction that is rolled back, so the real images and settings are never
left changed.  Skips without a database.
"""

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.email import emailconfig
    from hailsys.queries import emailadmin as ea
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

URL = "https://images.example.test/a.png"


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class EmailAdminTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.system = self.one("SELECT emp_id FROM users WHERE role = 'system'")["emp_id"]
        self.admin = self.one("SELECT emp_id FROM users WHERE role = 'admin' AND is_active "
                              "ORDER BY emp_id LIMIT 1")["emp_id"]
        self.name = f"zz-{uuid.uuid4().hex[:12]}"

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def image(self, name):
        return self.one("SELECT * FROM email_images WHERE name = %s", (name,))

    def settings(self):
        return self.one("SELECT contact_email, schedule_url, from_name, from_email, reply_to "
                        "FROM settings")

    def add(self, name=None, url=URL, alt="An image"):
        ea.add_image(self.conn, self.system, name or self.name, url, alt)
        return self.image(name or self.name)

    # ---- fetch_images ----

    def test_the_required_images_are_listed_active_first_then_by_name(self):
        self.add()
        retired = self.add(f"{self.name}-old")
        ea.set_image_active(self.conn, self.system, retired["image_id"], False)
        rows = ea.fetch_images(self.conn)
        names = [r["name"] for r in rows]
        for required in emailconfig.REQUIRED_IMAGES:
            self.assertIn(required, names)
        active = [r["is_active"] for r in rows]
        self.assertEqual(active, sorted(active, reverse=True))
        self.assertEqual(names[-1], f"{self.name}-old")
        live = [r["name"] for r in rows if r["is_active"]]
        self.assertEqual(live, sorted(live, key=str.lower))

    def test_a_listed_image_carries_who_updated_it(self):
        self.add()
        row = next(r for r in ea.fetch_images(self.conn) if r["name"] == self.name)
        self.assertTrue(row["updated_by_name"])

    # ---- add_image ----

    def test_an_added_image_is_stored_stripped_and_active(self):
        ea.add_image(self.conn, self.admin, f"  {self.name} ", f"  {URL}  ", "  Alt words  ")
        row = self.image(self.name)
        self.assertEqual((row["url"], row["alt_text"], row["is_active"]), (URL, "Alt words", True))
        self.assertEqual((row["created_by"], row["updated_by"]), (self.admin, self.admin))

    def test_bad_names_are_refused(self):
        for bad in ("", "   ", "two words", "a/b", "x" * 41, "café"):
            with self.assertRaises(ea.EmailAdminError, msg=repr(bad)):
                ea.add_image(self.conn, self.system, bad, URL, "x")

    def test_a_forty_character_name_is_allowed(self):
        name = "n" * 40
        ea.add_image(self.conn, self.system, name, URL, "x")
        self.assertIsNotNone(self.image(name))

    def test_bad_addresses_are_refused_and_nothing_is_stored(self):
        for bad in ("", "http://images.example.test/a.png", "images.example.test/a.png",
                    "https://has space.example.test/a.png",
                    "https://something.azurewebsites.net/a.png",
                    "https://images.example.test/" + "a" * 500):
            with self.assertRaises(ea.EmailAdminError, msg=bad[:40]):
                ea.add_image(self.conn, self.system, self.name, bad, "x")
        self.assertIsNone(self.image(self.name))

    def test_alt_text_is_required_and_limited(self):
        for bad in ("", "   ", "a" * 201):
            with self.assertRaises(ea.EmailAdminError, msg=repr(bad[:10])):
                ea.add_image(self.conn, self.system, self.name, URL, bad)
        self.assertIsNone(self.image(self.name))
        ea.add_image(self.conn, self.system, self.name, URL, "a" * 200)
        self.assertIsNotNone(self.image(self.name))

    def test_a_name_that_differs_only_in_case_is_refused_and_the_connection_stays_usable(self):
        self.add(self.name.lower())
        with self.assertRaises(ea.EmailAdminError) as ctx:
            ea.add_image(self.conn, self.system, self.name.upper(), URL, "x")
        self.assertIn("already", str(ctx.exception))
        self.assertTrue(ea.fetch_images(self.conn))     # not left in a failed transaction

    # ---- update_image ----

    def test_an_edit_changes_the_address_and_alt_text_and_who_updated_it(self):
        row = self.add()
        ea.update_image(self.conn, self.admin, row["image_id"],
                        "https://images.example.test/b.png", " New alt ")
        after = self.image(self.name)
        self.assertEqual((after["url"], after["alt_text"]),
                         ("https://images.example.test/b.png", "New alt"))
        self.assertEqual((after["created_by"], after["updated_by"]), (self.system, self.admin))
        self.assertEqual(after["name"], self.name)

    def test_a_refused_edit_changes_nothing(self):
        row = self.add()
        for url, alt in (("http://images.example.test/b.png", "x"), (URL, ""),
                         ("https://x.azurewebsites.net/b.png", "x")):
            with self.assertRaises(ea.EmailAdminError):
                ea.update_image(self.conn, self.admin, row["image_id"], url, alt)
        after = self.image(self.name)
        self.assertEqual((after["url"], after["alt_text"]), (URL, "An image"))

    def test_editing_an_unknown_image_is_refused(self):
        with self.assertRaises(ea.EmailAdminError) as ctx:
            ea.update_image(self.conn, self.admin, -1, URL, "x")
        self.assertIn("does not exist", str(ctx.exception))

    def test_a_required_image_can_be_edited_and_the_edit_is_what_a_send_loads(self):
        logo = self.image("logo")
        ea.update_image(self.conn, self.admin, logo["image_id"],
                        "https://images.example.test/new-logo.png", "New logo")
        self.assertEqual(emailconfig.load_settings(self.conn).logo_url,
                         "https://images.example.test/new-logo.png")

    # ---- set_image_active ----

    def test_an_optional_image_can_be_retired_and_reactivated(self):
        row = self.add()
        ea.set_image_active(self.conn, self.admin, row["image_id"], False)
        retired = self.image(self.name)
        self.assertFalse(retired["is_active"])
        self.assertEqual(retired["updated_by"], self.admin)
        ea.set_image_active(self.conn, self.admin, row["image_id"], True)
        self.assertTrue(self.image(self.name)["is_active"])

    def test_a_required_image_cannot_be_retired_and_the_connection_stays_usable(self):
        for name in emailconfig.REQUIRED_IMAGES:
            image_id = self.image(name)["image_id"]
            with self.assertRaises(ea.EmailAdminError) as ctx:
                ea.set_image_active(self.conn, self.admin, image_id, False)
            self.assertIn("built-in", str(ctx.exception))
            self.assertTrue(self.image(name)["is_active"])

    def test_retiring_an_unknown_image_is_refused(self):
        with self.assertRaises(ea.EmailAdminError) as ctx:
            ea.set_image_active(self.conn, self.admin, -1, False)
        self.assertIn("does not exist", str(ctx.exception))

    # ---- save_email_settings ----

    VALID = dict(contact_email="contact@example.test", schedule_url="https://example.test/book",
                 from_name="Test Sender", from_email="from@example.test",
                 reply_to="reply@example.test")

    def save(self, **over):
        values = {**self.VALID, **over}
        ea.save_email_settings(self.conn, self.admin, **values)

    def test_the_five_values_are_saved_stripped(self):
        self.save(contact_email="  contact@example.test ", from_name="  Test Sender ")
        self.assertEqual(self.settings(), self.VALID)

    def test_a_save_is_logged_to_the_history_with_who_and_the_new_values(self):
        self.save()
        hist = self.one("SELECT * FROM settings_history WHERE contact_email = %s "
                        "ORDER BY history_id DESC LIMIT 1", (self.VALID["contact_email"],))
        self.assertEqual(hist["changed_by"], self.admin)
        for field, value in self.VALID.items():
            self.assertEqual(hist[field], value, field)

    def test_what_is_saved_is_what_a_send_loads(self):
        self.save()
        self.assertEqual(emailconfig.load_sender(self.conn),
                         {"from_name": "Test Sender", "from_email": "from@example.test",
                          "reply_to": "reply@example.test"})
        loaded = emailconfig.load_settings(self.conn)
        self.assertEqual((loaded.contact_email, loaded.schedule_url),
                         ("contact@example.test", "https://example.test/book"))

    def test_each_bad_value_is_refused_and_nothing_changes(self):
        before = self.settings()
        bad = {"contact_email": ("", "not an email", "a@b@c.test", "two words@x.test"),
               "schedule_url": ("", "http://example.test/book", "example.test/book",
                                "https://x.azurewebsites.net/book"),
               "from_name": ("", "   ", "n" * 101),
               "from_email": ("", "nobody", "a b@x.test"),
               "reply_to": ("", "nobody", "a b@x.test")}
        for field, values in bad.items():
            for value in values:
                with self.assertRaises(ea.EmailAdminError, msg=f"{field}={value!r}"):
                    self.save(**{field: value})
        self.assertEqual(self.settings(), before)

    def test_a_refusal_names_the_field(self):
        for field, word in (("contact_email", "contact"), ("schedule_url", "schedule"),
                            ("from_email", "sender"), ("reply_to", "reply-to")):
            with self.assertRaises(ea.EmailAdminError) as ctx:
                self.save(**{field: "bad"})
            self.assertIn(word, str(ctx.exception).lower(), field)

    def test_a_hundred_character_sender_name_is_allowed(self):
        self.save(from_name="n" * 100)
        self.assertEqual(len(self.settings()["from_name"]), 100)


if __name__ == "__main__":
    unittest.main()
