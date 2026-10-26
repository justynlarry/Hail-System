"""Nothing CoCoRaHS-derived reaches an email, an export, a pull, the matcher or a send (Phase 5c, step 1a).

The decision log waived step 1 only (the ingest, likelihood zones, a staff-only map layer, a backtest).  Pulls from
the zones, matches with a basis and sends by level each need their own waiver entry first.  These tests are the
guard until then.  They read source text and parse imports, so they need no database:

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_cocorahs_isolation

What they pin:  no file under hailsys/ outside hailsys/cocorahs/ names the CoCoRaHS ingest (its package, tables or script), and
no script but its own ingest does;  the CoCoRaHS code imports nothing from the email, matching, RentCast, Constant
Contact or web packages;  its ingest script imports none of them either;  and the one table the code writes
(cocorahs_*) is not named by the matcher or the export queries.

When step 1 adds the staff-only map layer, the new module and view go in ALLOWED below, in the same commit, and the
decision log entry is the reason.  Steps 2 to 4 get no allowance without their own waiver.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Our names, not the bare word:  IEM's own report source is labelled COCORAHS (a colour in map.js, a note in
# load_reference.sh), and that is old IEM data, not this ingest.
MENTION = re.compile(r"hailsys[./]cocorahs|cocorahs_|import[^\n]*\bcocorahs\b|scripts/cocorahs", re.I)

# The only places allowed to name CoCoRaHS.  Paths relative to the repo root; a folder ends with "/".
# hailsys/likelihood/ reads the CoCoRaHS table to build the zones (step 1b, decision log 2026-10-23 "Phase 5c starts").
# hailsys/queries/likelihood_map.py and the map panel show the staff-only layer and name the size floor column.
# hailsys/queries/likelihoodadmin.py and the admin page's hail-zone section edit the parameter set (slice 7),
# whose size floor column is cocorahs_min_in;  they read and write sets only, no reports.
ALLOWED = ("hailsys/cocorahs/", "hailsys/likelihood/", "hailsys/queries/likelihood_map.py",
           "hailsys/queries/likelihoodadmin.py", "hailsys/web/templates/_map_panel.html",
           "hailsys/web/templates/_hail_zones.html", "scripts/cocorahs_ingest.py")

# Packages the CoCoRaHS code must never import (it has no business there, and nothing there may be reached from it).
FORBIDDEN = ("hailsys.email", "hailsys.matching", "hailsys.rentcast", "hailsys.constantcontact", "hailsys.web",
             "hailsys.queries")


def source_files(*patterns):
    out = []
    for pattern in patterns:
        out += [p for p in sorted(ROOT.glob(pattern)) if p.is_file() and "__pycache__" not in p.parts]
    return out


def relative(path):
    return str(path.relative_to(ROOT))


def allowed(path):
    rel = relative(path)
    return any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in ALLOWED)


def imported_modules(path):
    mods = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            mods.append(base)
            mods += [f"{base}.{a.name}" for a in node.names]
    return mods


class IsolationTest(unittest.TestCase):
    def test_the_sweep_actually_finds_files(self):
        # A glob that matches nothing would pass every test below for the wrong reason.
        self.assertGreater(len(source_files("hailsys/**/*.py")), 40)
        self.assertGreater(len(source_files("hailsys/web/templates/**/*.html")), 5)
        self.assertGreater(len(source_files("hailsys/cocorahs/*.py")), 3)

    def test_no_file_in_the_app_outside_the_cocorahs_package_names_cocorahs(self):
        for path in source_files("hailsys/**/*"):
            if allowed(path) or path.suffix in (".pyc", ".png", ".jpg", ".ico", ".woff", ".woff2"):
                continue
            with self.subTest(path=relative(path)):
                self.assertIsNone(MENTION.search(path.read_text(errors="ignore")))

    def test_no_script_but_its_own_names_cocorahs(self):
        for path in source_files("scripts/*.py", "scripts/*.sh"):
            if allowed(path) or path.name == "status.sh":      # status.sh only reads the run table to report on it
                continue
            with self.subTest(path=relative(path)):
                self.assertIsNone(MENTION.search(path.read_text(errors="ignore")))

    def test_the_email_matching_rentcast_and_send_code_never_names_it(self):
        found = source_files("hailsys/email/**/*", "hailsys/matching/**/*", "hailsys/rentcast/**/*",
                             "hailsys/constantcontact/**/*", "hailsys/web/sendjobs.py", "hailsys/web/send.py",
                             "hailsys/web/drip.py", "hailsys/web/emailtpl.py")
        self.assertGreater(len(found), 10)
        for path in found:
            with self.subTest(path=relative(path)):
                self.assertIsNone(MENTION.search(path.read_text(errors="ignore")))

    def test_the_export_and_match_queries_never_name_it(self):
        for path in source_files("hailsys/queries/*.py"):
            if allowed(path):
                continue
            with self.subTest(path=relative(path)):
                self.assertIsNone(MENTION.search(path.read_text()))

    def test_the_cocorahs_code_imports_nothing_from_the_send_side_or_the_web(self):
        for path in source_files("hailsys/cocorahs/*.py", "hailsys/likelihood/*.py", "scripts/cocorahs_ingest.py"):
            with self.subTest(path=relative(path)):
                for mod in imported_modules(path):
                    self.assertFalse(any(mod == bad or mod.startswith(bad + ".") for bad in FORBIDDEN), mod)

    def test_the_check_would_catch_a_mention(self):
        # The guard itself:  a sample that should trip it does.
        self.assertTrue(MENTION.search("SELECT * FROM cocorahs_hail_reports"))
        self.assertTrue(MENTION.search("from hailsys.CoCoRaHS import load"))
        self.assertTrue(MENTION.search("from hailsys import cocorahs"))
        self.assertTrue(MENTION.search("python3 scripts/cocorahs_ingest.py nightly"))
        self.assertFalse(MENTION.search("SELECT * FROM iem_data"))
        self.assertFalse(MENTION.search("'COCORAHS': '#2ca02c',"))      # IEM's source label
        self.assertTrue(allowed(ROOT / "hailsys/cocorahs/load.py"))
        self.assertFalse(allowed(ROOT / "hailsys/matching/matcher.py"))
        self.assertTrue(any(m.startswith("hailsys.email") for m in imported_modules_of("from hailsys.email import x")))


def imported_modules_of(text):
    """Same walk as imported_modules, for a string."""
    mods = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom):
            mods += [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
    return mods


if __name__ == "__main__":
    unittest.main()
