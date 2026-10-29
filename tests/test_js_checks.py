"""Runs the Node checks in tests/js/ against the real scripts in hailsys/web/static/ (item 222).

    docker compose --profile test run --rm -T test

Each check is a node script that prints ok/FAIL lines and exits 1 on any failure.  Without Node
(the host, the web container) these tests skip and say why, rather than pass unnoticed.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "hailsys" / "web" / "static"
CHECKS = sorted((ROOT / "tests" / "js").glob("*_check.js"))
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "no node here: run the test service (docker compose --profile test run --rm -T test)")
class JsCheckTest(unittest.TestCase):
    def test_there_are_checks_to_run(self):
        # A renamed or unmounted folder would otherwise run nothing and pass.
        self.assertGreaterEqual(len(CHECKS), 2)

    def test_each_check_passes(self):
        for path in CHECKS:
            with self.subTest(path.name):
                run = subprocess.run([NODE, str(path), str(STATIC)],
                                     capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)