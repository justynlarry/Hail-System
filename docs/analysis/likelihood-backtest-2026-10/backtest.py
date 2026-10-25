#!/usr/bin/env python3
"""Backtest of the hail-likelihood zones: hide one source, rebuild, see where its big hail landed.

Analysis only (2026-10-25, written by Claude).  Phase 5c step 1, decision log 2026-10-23 (bar about 80%).
Writes nothing to the production tables: every day is built inside a transaction that is rolled back, so the
stored zones and builds are exactly as they were.  Only --out is written.  Nothing here sends, pulls or matches.

The question.  For each source (IEM hail reports, CoCoRaHS filings) and each storm day with at least one of
its points of --min-in or more: rebuild the day with that source hidden from the anchors (the other source and
the radar still count), then ask whether each hidden point sits inside Agree or One source.  The share that do
is the recall; the bar is about 80%.  A point inside the radar layers lands in One source at worst (rules), so
what a miss means is a point with no radar behind it and no other report within 5 miles, or inside Context.

Run (needs a role that can write the likelihood tables, hail_admin; rolled back, so safe to re-run):
    python3 backtest.py --out OUT [--from 2020-10-15] [--to 2026-10-21] [--min-in 1.0]
        [--region front_range=-105.05,-104.5] [--region east=-103.9,]

A region is NAME=LON_MIN,LON_MAX with either side optional.  Reports are assigned by longitude.
"""
import argparse
import collections
import csv
import sys
from datetime import date
from pathlib import Path

from hailsys.db import get_connection
from hailsys.likelihood import build
from hailsys.radar.fetch import day_window
from hailsys.radar.mesh import STEP
from hailsys.tuning import DISPLAY_TZ

HIDE = {
    # An always-false clause on the anchor insert is the whole "hide".  Everything else in build_day is untouched.
    "iem": ("_IEM_SQL", " AND false"),
    "cocorahs": ("_COCORAHS_SQL", " AND false"),
}

_POINTS = {
    "iem": """
        SELECT i.geom, i.magnitude::float AS size_in
        FROM iem_data i
        WHERE i.utc_datetime >= %(start)s AND i.utc_datetime < %(end)s
            AND i.report_text = 'HAIL' AND i.magnitude >= %(min_in)s
            AND i.report_source_norm IS DISTINCT FROM 'COCORAHS'""",
    "cocorahs": """
        SELECT c.geom, coalesce(c.largest_in, c.average_in)::float AS size_in
        FROM cocorahs_hail_reports c
        WHERE c.observed_at >= %(start)s AND c.observed_at < %(end)s
            AND coalesce(c.largest_in, c.average_in) >= %(min_in)s""",
}

_DAYS = """
SELECT d.day FROM radar_days d
WHERE d.status = 'loaded' AND d.day BETWEEN %(first)s AND %(last)s
ORDER BY d.day
"""

# Zones of different levels do not overlap, so a point has at most one level (two on a shared edge:
# the best wins).
_LANDING = """
WITH pts AS (""" + "{points}" + """)
SELECT ST_X(p.geom) AS lon, ST_Y(p.geom) AS lat, p.size_in,
       coalesce((SELECT z.level FROM likelihood_zones z
                 WHERE z.day = %(day)s AND z.set_id = %(set_id)s AND ST_Intersects(z.geom, p.geom)
                 ORDER BY CASE z.level WHEN 'agree' THEN 0 WHEN 'one_source' THEN 1 ELSE 2 END
                 LIMIT 1), 'none') AS level,
       -- the crop box the day's radar was cut to: outside it no layer can exist, so a miss there is out of scope
       (SELECT ST_Intersects(p.geom, ST_MakeEnvelope(d.box_lon_min - %(half)s, d.box_lat_min - %(half)s,
                                                     d.box_lon_max + %(half)s, d.box_lat_max + %(half)s, 4326))
        FROM radar_days d WHERE d.day = %(day)s) AS inbox
FROM pts p
"""


def parse_regions(items):
    out = {}
    for item in items:
        name, _, span = item.partition("=")
        lo, _, hi = span.partition(",")
        out[name] = (float(lo) if lo else None, float(hi) if hi else None)
    return out


def region_of(lon, regions):
    names = [n for n, (lo, hi) in regions.items()
             if (lo is None or lon >= lo) and (hi is None or lon < hi)]
    return names[0] if names else "other"


def hidden_build(conn, day, source, min_in, set_id):
    """Rebuild 'day' with 'source' hidden, read where its points landed, roll back."""
    attr, clause = HIDE[source]
    original = getattr(build, attr)
    setattr(build, attr, original + clause)
    try:
        result = build.build_day(conn, day, DISPLAY_TZ, set_id=set_id)
        start, end = day_window(day, DISPLAY_TZ)
        sql = _LANDING.replace("{points}", _POINTS[source])
        rows = conn.execute(sql, {"start": start, "end": end, "min_in": min_in, "day": day,
                                  "set_id": result["set_id"], "half": STEP / 2}).fetchall()
    finally:
        setattr(build, attr, original)
        conn.rollback()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--from", dest="first", type=date.fromisoformat, default=date(2020, 10, 15))
    ap.add_argument("--to", dest="last", type=date.fromisoformat, default=date(2026, 10, 21))
    ap.add_argument("--min-in", type=float, default=1.0)
    ap.add_argument("--set-id", type=int, default=None)
    ap.add_argument("--region", action="append", default=[])
    ap.add_argument("--limit", type=int, default=None, help="stop after this many days (a trial run)")
    args = ap.parse_args(argv)
    regions = parse_regions(args.region)
    args.out.mkdir(parents=True, exist_ok=True)

    per_point = []
    with get_connection() as conn:
        days = [r["day"] for r in conn.execute(_DAYS, {"first": args.first, "last": args.last}).fetchall()]
        for source in ("iem", "cocorahs"):
            done = 0
            for day in days:
                start, end = day_window(day, DISPLAY_TZ)
                n = conn.execute("SELECT count(*) AS n FROM (" + _POINTS[source] + ") q",
                                 {"start": start, "end": end, "min_in": args.min_in}).fetchone()["n"]
                conn.rollback()
                if not n:
                    continue
                for r in hidden_build(conn, day, source, args.min_in, args.set_id):
                    per_point.append((source, day, r["lon"], r["lat"], r["size_in"], r["level"], r["inbox"]))
                done += 1
                if args.limit and done >= args.limit:
                    break
            print(f"{source}: {done} days rebuilt", file=sys.stderr)

    with open(args.out / "points.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["hidden_source", "day", "lon", "lat", "size_in", "level", "inbox", "region"])
        for s, d, lon, lat, size, level, inbox in per_point:
            w.writerow([s, d, f"{lon:.5f}", f"{lat:.5f}", size, level, int(inbox), region_of(lon, regions)])

    def table(title, key, only_inbox=True):
        groups = collections.defaultdict(collections.Counter)
        for s, d, lon, lat, size, level, inbox in per_point:
            if only_inbox and not inbox:
                continue
            groups[key(s, d, lon)][level] += 1
        lines = [title, f"{'group':<28}{'points':>7}{'agree':>8}{'one_src':>9}{'context':>9}{'none':>7}{'recall':>9}"]
        for k in sorted(groups):
            c = groups[k]
            n = sum(c.values())
            hit = c["agree"] + c["one_source"]
            lines.append(f"{k:<28}{n:>7}{c['agree']:>8}{c['one_source']:>9}{c['context']:>9}{c['none']:>7}"
                         f"{hit / n:>9.1%}")
        return "\n".join(lines)

    text = "\n\n".join([
        f"Backtest, points of {args.min_in:g} in or more, days {args.first} to {args.last}",
        "Points inside each day's radar crop box (the denominator; outside it no layer can exist).",
        table("Overall", lambda s, d, lon: s),
        table("Overall, every point including outside the box", lambda s, d, lon: s, only_inbox=False),
        table("By year", lambda s, d, lon: f"{s} {d.year}"),
        table("By region", lambda s, d, lon: f"{s} {region_of(lon, regions)}"),
    ])
    (args.out / "summary.txt").write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
