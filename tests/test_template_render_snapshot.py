"""The render snapshot for the template editor remodel (parking-lot item 294).

The remodel changes how the editor looks, never what an email says.  This pins that:  every saved
template version, the two built-in template files, the block starter and the editor preview are
rendered with every sample storm and fixed stand-in settings, and each (subject, html) is compared
with tests/snapshots/template_render.json, by SHA-256.  Any difference is a failure.

Stand-in settings, not the admin page's, so changing the contact email or an image address does
not fail the test:  only the template and the code that compiles and renders it can.

A version saved after the snapshot was taken is not in it, so it is listed as new and not failed.
A snapshot entry that can no longer be rendered (a version gone, which the database forbids) fails.

Run (needs the database for the saved versions; without it only the file templates are checked):

    docker compose run --rm -v ./tests:/app/tests:ro web \
        python -m unittest tests.test_template_render_snapshot

Write a new snapshot (only before a slice starts, never to make a failing slice pass):

    docker compose run --rm -v ./tests:/app/tests web \
        python -m tests.test_template_render_snapshot --write
"""

import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "template_render.json"

import re
MARK = re.compile(r' data-block="\d+"')          # the editor preview's block marks (item 294, slice 7)

try:
    from hailsys.email import blocks, preview, render, samples, templatestore, zonecontext
    MISSING = None
except ImportError as exc:
    MISSING = str(exc)

# The radar sentence of a zone email reads these from the hail-zone set; fixed here.  The stand-in
# realtor is a copy of the zone send page's (hailsys/web/sendzone.py), kept here so a change there
# does not move the snapshot.
ZONE_WORDING = {"hail_in": Decimal("1.00"), "radius_km": Decimal("2.0")}
ZONE_AGENT = "Pat Example"
ZONE_EVENTS = [{"listing_id": 0, "address": "123 Example St, Denver, CO 80202",
                "utc_datetime": datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc),
                "bases": ["radar"]}]


def _settings(names=()):
    """Stand-in email settings, with a stand-in for every image a template names."""
    images = {n: {"url": f"https://example.test/img/{n}.png", "alt": f"{n} image"}
              for n in {"logo", "badge_cra", "badge_bbb", *names}}
    return render.EmailSettings(
        "https://example.test/img/logo.png", "https://example.test/img/badge_cra.png",
        "https://example.test/img/badge_bbb.png", "snapshot@example.test",
        "https://example.test/schedule", images=images)


def _digest(subject, html):
    return hashlib.sha256((subject + "\n\x00\n" + html).encode("utf-8")).hexdigest()


def _render_report(subject_src, body_src, settings):
    out = {}
    for key in samples.SITUATIONS:
        agent, matches = samples.situation(key)
        context = render.build_context(agent, matches, settings)
        out[key] = _digest(*render.render(subject_src, body_src, context))
    return out


def _render_zone(subject_src, body_src, settings):
    context = zonecontext.build_context(ZONE_AGENT, ZONE_EVENTS, settings, ZONE_WORDING)
    return {"zone": _digest(*render.render(subject_src, body_src, context, kind="zone"))}


def _from_blocks(template, label):
    """Digests for a block template:  compiled and rendered, and through the editor preview."""
    names = blocks.images_used(template)
    settings = _settings(names)
    cleaned = blocks.clean_template(template)
    subject_src, body_src = blocks.compile_blocks(cleaned)
    out = {f"{label}|compiled|{k}": v for k, v in _render_report(subject_src, body_src, settings).items()}
    for key in samples.SITUATIONS:
        p = preview.build_preview(template, settings, key)
        if not p["ok"]:
            raise AssertionError(f"{label}: the editor preview failed: {p['problems']}")
        out[f"{label}|preview|{key}"] = _digest(p["subject"], p["html"])
        # The on-screen preview carries data-block marks (slice 7).  With them removed it must be
        # the unmarked preview exactly, so the marks are the only difference.
        marked = preview.build_preview(template, settings, key, mark_blocks=True)
        if not marked["ok"] or MARK.sub("", marked["html"]) != p["html"] or marked["size"] != p["size"]:
            raise AssertionError(f"{label}|{key}: the marked preview differs by more than its marks")
    return out


def compute(conn=None):
    """{key: sha256} for everything the snapshot covers.  conn None: the files only."""
    out = {}
    settings = _settings()
    for name, (_, _, kind) in templatestore._BUILTIN.items():
        subject_src, body_src = templatestore.load_files(name)
        rendered = (_render_zone if kind == "zone" else _render_report)(subject_src, body_src, settings)
        out.update({f"file:{name}|{k}": v for k, v in rendered.items()})
    out.update(_from_blocks(blocks.HAIL_ALERT_BLOCKS, "starter"))
    if conn is None:
        return out
    rows = conn.execute(
        "SELECT template_id, template_name, match_kind, subject, body, blocks "
        "FROM email_templates ORDER BY template_id").fetchall()
    conn.rollback()
    for row in rows:
        label = f"saved:{row['template_id']}:{row['template_name']}"
        names = blocks.images_used(row["blocks"]) if row["blocks"] else ()
        render_fn = _render_zone if row["match_kind"] == "zone" else _render_report
        rendered = render_fn(row["subject"], row["body"], _settings(names))
        out.update({f"{label}|stored|{k}": v for k, v in rendered.items()})
        if row["blocks"]:
            out.update(_from_blocks(row["blocks"], label))
    return out


def _connect():
    try:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(row_factory=dict_row, connect_timeout=3)
    except Exception:
        return None


@unittest.skipIf(MISSING, f"hailsys not importable: {MISSING}")
class RenderSnapshotTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not SNAPSHOT.exists():
            raise unittest.SkipTest(f"no snapshot at {SNAPSHOT}; write one with --write")
        cls.snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        cls.conn = _connect()

    @classmethod
    def tearDownClass(cls):
        if cls.conn is not None:
            cls.conn.close()

    def test_every_rendering_matches_the_snapshot(self):
        now = compute(self.conn)
        expected = self.snapshot if self.conn is not None else {
            k: v for k, v in self.snapshot.items() if not k.startswith("saved:")}
        changed = sorted(k for k in expected if k in now and now[k] != expected[k])
        gone = sorted(k for k in expected if k not in now)
        new = sorted(k for k in now if k not in self.snapshot)
        if new:
            print(f"\n{len(new)} rendering(s) not in the snapshot (saved after it was taken): "
                  + ", ".join(sorted({k.split('|')[0] for k in new})))
        self.assertEqual(changed, [], "these renderings changed")
        self.assertEqual(gone, [], "these snapshot entries can no longer be rendered")

    def test_the_snapshot_covers_the_files_and_the_starter(self):
        prefixes = {k.split("|")[0] for k in self.snapshot}
        for name in templatestore._BUILTIN:
            self.assertIn(f"file:{name}", prefixes)
        self.assertIn("starter", prefixes)


if __name__ == "__main__":
    if "--write" in sys.argv:
        conn = _connect()
        if conn is None:
            sys.exit("no database: a snapshot without the saved versions is not worth writing")
        data = compute(conn)
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {len(data)} renderings to {SNAPSHOT}")
    else:
        unittest.main()
