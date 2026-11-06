"""The release stamp (parking-lot item 289): hailsys/version.py reads hailsys/VERSION, written by
scripts/write_version.sh at deploy, and the footer shows it, or "development" without one.

    docker compose run --rm -v ./tests:/app/tests:ro web python -m unittest tests.test_version
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hailsys import version  # noqa: E402
# FooterTest builds the app, which needs Flask, the web image has it, the host doesn't
try:
    import hailsys.web  #noqa: F410,E402
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)


class ReadVersionTest(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "VERSION"
            if text is not None:
                path.write_text(text, encoding="utf-8")
            return version.read_version(path)

    def test_a_stamp_is_the_describe_and_the_commit(self):
        self.assertEqual(self.read("v0.9.0 abc1234\n"), {"describe": "v0.9.0", "commit": "abc1234"})
        self.assertEqual(self.read("v0.9.0-3-gabc1234-dirty abc1234"),
                         {"describe": "v0.9.0-3-gabc1234-dirty", "commit": "abc1234"})

    def test_no_file_or_anything_else_is_none(self):
        for text in (None, "", "\n", "v0.9.0", "v0.9.0 abc1234 extra", "v0.9.0 <script>", "v0.9.0 aéb"):
            with self.subTest(text=text):
                self.assertIsNone(self.read(text))

    @unittest.skipUnless((ROOT / ".gitignore").exists(), ".gitignore is not mounted here (run on the host to check it)")
    def test_the_stamp_file_is_never_committed(self):
        self.assertIn("hailsys/VERSION", (ROOT / ".gitignore").read_text())

@unittest.skipIf(MISSING, f"missing dependency: {MISSING}")
class FooterTest(unittest.TestCase):
    def page(self, stamp):
        with mock.patch("hailsys.web.jobs.sweep_stale_pulls"), \
                mock.patch.object(version, "read_version", return_value=stamp):
            from hailsys.web import create_app
            app = create_app()
        return app.test_client().get("/login").get_data(as_text=True)

    def test_the_footer_shows_the_release_and_commit(self):
        page = self.page({"describe": "v0.9.0", "commit": "abc1234"})
        self.assertIn('<footer class="site-footer">', page)
        self.assertIn("v0.9.0 &middot; abc1234", page)

    def test_an_unstamped_working_copy_says_development(self):
        page = self.page(None)
        self.assertRegex(page, r'<footer class="site-footer">\s*development\s*</footer>')


if __name__ == "__main__":
    unittest.main()
