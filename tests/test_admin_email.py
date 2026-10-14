"""Tests for the email routes on the admin page (hailsys/web/admin.py).

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_admin_email

The writes (hailsys/queries/emailadmin.py, tested on the real database in test_emailadmin) are
replaced in every test, so nothing here changes an image or a setting.  The admin page itself
is the real one, which only reads, with the image list replaced so the page is predictable.
"""

import contextlib
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
    from hailsys.web import create_app, admin as admin_web
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


def image(image_id, name, active=True):
    return {"image_id": image_id, "name": name, "url": f"https://img.example.test/{name}.png",
            "alt_text": f"Alt of {name}", "is_active": active,
            "updated_at": datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
            "updated_by_name": "someone"}


IMAGES = [image(901, "logo"), image(902, "badge_cra"), image(903, "badge_bbb"),
          image(904, "spring-sale"), image(905, "old-banner", active=False)]

SETTINGS_FORM = dict(contact_email="c@example.test", schedule_url="https://example.test/s",
                     from_name="Sender", from_email="f@example.test", reply_to="r@example.test")


@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
@unittest.skipUnless(os.environ.get("PGHOST") and os.environ.get("FLASK_SECRET_KEY"),
                     "no database or secret key")
class AdminEmailTest(unittest.TestCase):
    def setUp(self):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"):
            self.app = create_app()
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.users = {}
        for r in self.conn.execute("SELECT emp_id, role FROM users WHERE is_active "
                                   "AND role IN ('admin', 'sender', 'viewer') ORDER BY emp_id"):
            self.users.setdefault(r["role"], r["emp_id"])
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.fns = {name: stack.enter_context(
            mock.patch.object(admin_web.emailadmin, name))
            for name in ("save_email_settings", "add_image", "update_image", "set_image_active")}
        stack.enter_context(mock.patch.object(admin_web.emailadmin, "fetch_images",
                                              return_value=IMAGES))

    def client_as(self, role):
        if role not in self.users:
            self.skipTest(f"no active {role} user in this database")
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["emp_id"] = self.users[role]
            s["role"] = role
            s["user_name"] = "test"
            s["issued_at"] = datetime.now(timezone.utc).isoformat()
        return client

    def post(self, path, role="admin", **data):
        self.app.config["WTF_CSRF_ENABLED"] = False
        return self.client_as(role).post(path, data=data, follow_redirects=True)

    def writes_called(self):
        return [name for name, fn in self.fns.items() if fn.called]

    PATHS = ("/admin/email-settings", "/admin/images", "/admin/images/904",
             "/admin/images/904/retire", "/admin/images/905/reactivate")

    # ---- who may write ----

    def test_every_write_needs_a_token(self):
        client = self.client_as("admin")
        for path in self.PATHS:
            self.assertEqual(client.post(path, data=SETTINGS_FORM).status_code, 400, path)
        self.assertEqual(self.writes_called(), [])

    def test_every_write_is_post_only(self):
        client = self.client_as("admin")
        for path in self.PATHS:
            self.assertEqual(client.get(path).status_code, 405, path)

    def test_signed_out_goes_to_login_and_writes_nothing(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        for path in self.PATHS:
            resp = self.app.test_client().post(path, data=SETTINGS_FORM)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(self.writes_called(), [])

    def test_only_admin_may_write(self):
        for role in ("viewer", "sender"):
            if role in self.users:
                for path in self.PATHS:
                    self.assertEqual(self.post(path, role, **SETTINGS_FORM).status_code, 403,
                                     f"{role} {path}")
        self.assertEqual(self.writes_called(), [])

    # ---- what each route calls ----

    def test_saving_the_email_settings_passes_the_form_and_the_admin(self):
        resp = self.post("/admin/email-settings", **SETTINGS_FORM)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Email settings updated.", resp.get_data(as_text=True))
        args = self.fns["save_email_settings"].call_args.args
        self.assertEqual(args[1:], (self.users["admin"], "c@example.test",
                                    "https://example.test/s", "Sender", "f@example.test",
                                    "r@example.test"))

    def test_adding_an_image_passes_name_address_and_alt_text(self):
        resp = self.post("/admin/images", name="spring", url="https://x.example.test/s.png",
                         alt_text="Spring")
        self.assertIn("Image added.", resp.get_data(as_text=True))
        self.assertEqual(self.fns["add_image"].call_args.args[1:],
                         (self.users["admin"], "spring", "https://x.example.test/s.png",
                          "Spring"))

    def test_editing_an_image_passes_its_id_address_and_alt_text(self):
        resp = self.post("/admin/images/904", url="https://x.example.test/n.png", alt_text="N")
        self.assertIn("Image updated.", resp.get_data(as_text=True))
        self.assertEqual(self.fns["update_image"].call_args.args[1:],
                         (self.users["admin"], 904, "https://x.example.test/n.png", "N"))

    def test_retire_and_reactivate_pass_the_right_flag(self):
        self.assertIn("Image retired.",
                      self.post("/admin/images/904/retire").get_data(as_text=True))
        self.assertEqual(self.fns["set_image_active"].call_args.args[1:],
                         (self.users["admin"], 904, False))
        self.assertIn("Image reactivated.",
                      self.post("/admin/images/905/reactivate").get_data(as_text=True))
        self.assertEqual(self.fns["set_image_active"].call_args.args[1:],
                         (self.users["admin"], 905, True))

    def test_a_refused_value_is_shown_not_an_error_page(self):
        for fn, path in (("save_email_settings", "/admin/email-settings"),
                         ("add_image", "/admin/images"),
                         ("update_image", "/admin/images/904"),
                         ("set_image_active", "/admin/images/904/retire")):
            self.fns[fn].side_effect = admin_web.emailadmin.EmailAdminError(f"Refused: {fn}")
            resp = self.post(path, **SETTINGS_FORM)
            self.assertEqual(resp.status_code, 200, path)
            text = resp.get_data(as_text=True)
            self.assertIn(f"Refused: {fn}", text)

    def test_a_refusal_does_not_also_report_success(self):
        self.fns["add_image"].side_effect = admin_web.emailadmin.EmailAdminError("Refused")
        text = self.post("/admin/images", name="x", url="u", alt_text="a").get_data(as_text=True)
        self.assertNotIn("Image added.", text)

    # ---- the page ----

    def page(self):
        resp = self.client_as("admin").get("/admin/")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_the_page_lists_every_image_with_a_thumbnail_and_the_sender_warning(self):
        text = self.page()
        for i in IMAGES:
            self.assertIn(f'src="{i["url"]}"', text, i["name"])
            self.assertIn(f'form="img-{i["image_id"]}"', text, i["name"])
        self.assertIn("verified sender in Constant Contact", text)
        self.assertIn("Save email settings", text)

    def test_the_page_shows_the_current_email_values(self):
        row = self.conn.execute("SELECT contact_email, schedule_url, from_name, from_email, "
                                "reply_to FROM settings").fetchone()
        text = self.page()
        for value in row.values():
            self.assertIn(value, text)

    def test_required_images_have_no_retire_button_and_optional_ones_do(self):
        text = self.page()
        for required in (901, 902, 903):
            self.assertNotIn(f"/admin/images/{required}/retire", text)
        self.assertIn("/admin/images/904/retire", text)
        self.assertNotIn("/admin/images/904/reactivate", text)

    def test_a_retired_image_offers_reactivate_not_retire(self):
        text = self.page()
        self.assertIn("/admin/images/905/reactivate", text)
        self.assertNotIn("/admin/images/905/retire", text)
        self.assertIn("(retired)", text)


if __name__ == "__main__":
    unittest.main()
