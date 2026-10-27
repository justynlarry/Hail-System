"""The web container logs how long each request took (decision log 2026-10-27, after a click that felt slow).

The command in docker-compose.yml turns on gunicorn's access log with the request time and no query string, so a date or a
token in a URL never lands in the log.  Needs the file mounted:

    docker compose run --rm -T --no-deps -v ./tests:/app/tests:ro -v ./docker-compose.yml:/app/docker-compose.yml:ro \\
        web python -m unittest tests.test_compose_web_log
"""

import re
import unittest
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


@unittest.skipUnless(COMPOSE.exists(), "docker-compose.yml is not mounted")
class ComposeWebLogTest(unittest.TestCase):
    def command(self):
        text = COMPOSE.read_text()
        m = re.search(r"^  web:\n(?:    .*\n|\n)*?    command: (.+)$", text, re.M)
        self.assertIsNotNone(m, "no web command found")
        return m.group(1)

    def test_the_access_log_goes_to_stdout_with_the_request_time(self):
        cmd = self.command()
        self.assertIn("--access-logfile -", cmd)
        self.assertIn("--access-logformat", cmd)
        self.assertIn("%(L)s", cmd)                       # request time in seconds

    def test_the_log_line_has_the_path_and_never_the_query_string(self):
        cmd = self.command()
        self.assertIn("%(U)s", cmd)                       # the path alone
        self.assertNotIn("%(r)s", cmd)                    # the request line carries the query string
        self.assertNotIn("%(q)s", cmd)
        self.assertNotIn("%(f)s", cmd)                    # the referer carries one too

    def test_the_server_command_itself_is_unchanged(self):
        cmd = self.command()
        self.assertTrue(cmd.startswith("gunicorn --bind 0.0.0.0:8000 --workers 2"))
        self.assertTrue(cmd.endswith("hailsys.wsgi:app"))


if __name__ == "__main__":
    unittest.main()
