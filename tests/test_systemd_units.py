"""Checks on the unit files in systemd/ -- the slips that stop a unit starting, found by reading.

    python3 -m unittest tests.test_systemd_units

What they pin, for every unit in the folder: each timer's Unit= names a service file that exists
(with underscores, as the installed name; a hyphen made systemd refuse to arm a timer on 2026-09), a
service runs as the account that owns the repo, the working directory is the repo, a timer has an
OnCalendar and Persistent=true, and a radar unit's command names a mode the script has.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITS = sorted((ROOT / "systemd").glob("*.service")) + sorted((ROOT / "systemd").glob("*.timer"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))


def settings(path):
    """{key: [values]} of the unit file's key=value lines, comments and section names ignored."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.setdefault(key, []).append(value)
    return out


class UnitTest(unittest.TestCase):
    def test_there_are_units_to_check(self):
        self.assertGreaterEqual(len(UNITS), 8)

    def test_a_timer_names_a_service_that_exists(self):
        for path in (p for p in UNITS if p.suffix == ".timer"):
            with self.subTest(path.name):
                (unit,) = settings(path)["Unit"]
                self.assertTrue((ROOT / "systemd" / unit).exists(), unit)
                self.assertEqual(unit, path.stem + ".service")

    def test_a_timer_is_persistent_and_has_a_schedule(self):
        for path in (p for p in UNITS if p.suffix == ".timer"):
            with self.subTest(path.name):
                s = settings(path)
                self.assertEqual(s["Persistent"], ["true"])
                self.assertEqual(len(s["OnCalendar"]), 1)
                self.assertRegex(s["OnCalendar"][0], r"^(\w+ )?\*-\*-\* \d\d:\d\d:\d\d$")

    def test_a_service_runs_as_the_repo_owner_in_the_repo(self):
        for path in (p for p in UNITS if p.suffix == ".service"):
            with self.subTest(path.name):
                s = settings(path)
                self.assertEqual(s["User"], ["hail-user"])
                self.assertEqual(s["Group"], ["hail-user"])
                self.assertEqual([w.rstrip("/") for w in s["WorkingDirectory"]], ["/home/hail-user/hail-system"])
                self.assertEqual(s["Type"], ["oneshot"])

    def test_a_service_runs_a_script_that_exists_in_the_ingest_image(self):
        for path in (p for p in UNITS if p.suffix == ".service"):
            with self.subTest(path.name):
                (command,) = settings(path)["ExecStart"]
                match = re.match(r"^(?:/bin/sh -c ')?/usr/bin/docker compose run --rm ingest python3 (scripts/\w+\.py)\b", command)
                self.assertTrue(match, command)
                self.assertTrue((ROOT / match.group(1)).exists(), match.group(1))

    def test_service_names_use_underscores_like_the_installed_ones(self):
        for path in UNITS:
            self.assertNotIn("-", path.name, path.name)

    def test_the_radar_units_run_modes_the_script_has(self):
        try:
            import radar_ingest
        except ImportError as exc:      # psycopg lives in the app image (item 273)
            self.skipTest(f"missing dependency: {exc}")
        for path in (p for p in UNITS if p.name.startswith("radar_") and p.suffix == ".service"):
            with self.subTest(path.name):
                (command,) = settings(path)["ExecStart"]
                argv = command.split("scripts/radar_ingest.py ")[1].split()
                args = radar_ingest.parse_args(argv)           # exits if the mode or an option is wrong
                self.assertIn(args.mode, ("nightly", "rebuild"))

    def test_the_radar_timers_run_after_the_iem_ones(self):
        def minutes(name):
            h, m = settings(ROOT / "systemd" / name)["OnCalendar"][0].split()[-1].split(":")[:2]
            return int(h) * 60 + int(m)
        self.assertGreater(minutes("radar_ingest.timer"), minutes("iem_ingest.timer"))
        self.assertGreater(minutes("radar_weekly_rebuild.timer"), minutes("iem_weekly_replay.timer"))
        self.assertTrue(settings(ROOT / "systemd" / "radar_weekly_rebuild.timer")["OnCalendar"][0].startswith("Sun"))


if __name__ == "__main__":
    unittest.main()
