#!/usr/bin/env python3
"""Item 255 follow-up: why do hidden IEM hail reports miss Agree and One source?

Analysis only (2026-10-25, written by Claude).  Reads points.csv from backtest.py and the stored radar cells; read only
(a temp table is dropped with the connection).  For each IEM point inside its day's crop box: the radar value at the point
and the largest within 3, 5 and 8 km, the report's size and its source.  Then: what the recall would be if a report counted
when radar hail at the threshold lay within 3, 5 or 8 km, instead of exactly at the point.

    python3 misses.py --points points.csv --out OUT [--threshold-in 1.0]
"""
import argparse
import collections
import csv
import sys
from datetime import date
from pathlib import Path

from hailsys.db import get_connection

MM_PER_IN = 25.4

_SQL = """
SELECT p.n, p.size_in, p.level,
       (SELECT max(c.mm) FROM radar_cells c WHERE c.day = p.day
            AND c.geom && ST_Expand(p.geom, 0.0051) AND ST_DWithin(c.geom, p.geom, 0.0071)) AS mm_here,
       (SELECT max(c.mm) FROM radar_cells c WHERE c.day = p.day
            AND c.geom && ST_Expand(p.geom, 0.04) AND ST_DWithin(c.geom::geography, p.geom::geography, 3000)) AS mm_3,
       (SELECT max(c.mm) FROM radar_cells c WHERE c.day = p.day
            AND c.geom && ST_Expand(p.geom, 0.06) AND ST_DWithin(c.geom::geography, p.geom::geography, 5000)) AS mm_5,
       (SELECT max(c.mm) FROM radar_cells c WHERE c.day = p.day
            AND c.geom && ST_Expand(p.geom, 0.09) AND ST_DWithin(c.geom::geography, p.geom::geography, 8000)) AS mm_8
FROM pts p
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--threshold-in", type=float, default=1.0)
    args = ap.parse_args(argv)
    thr = args.threshold_in * MM_PER_IN

    rows = [r for r in csv.DictReader(open(args.points)) if r["hidden_source"] == "iem" and r["inbox"] == "1"]
    with get_connection() as conn:
        conn.execute("CREATE TEMP TABLE pts (n int, day date, size_in float, level text, geom geometry(Point, 4326))")
        for n, r in enumerate(rows):
            conn.execute("INSERT INTO pts VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                         (n, date.fromisoformat(r["day"]), float(r["size_in"]), r["level"], float(r["lon"]), float(r["lat"])))
        got = {r["n"]: r for r in conn.execute(_SQL).fetchall()}
        conn.rollback()

    def band(v):
        return "none" if v is None else ("<0.5" if v < 12.7 else "0.5-" + str(args.threshold_in) if v < thr else ">=thr")

    lines = [f"IEM points inside the crop box: {len(rows)}; radar threshold {args.threshold_in:g} in ({thr:.1f} mm)", ""]
    hit = lambda r: r["level"] in ("agree", "one_source")
    # 1. Recall by size.
    by = collections.defaultdict(lambda: [0, 0])
    for n, r in enumerate(rows):
        s = float(r["size_in"])
        k = "1.00" if s < 1.25 else "1.25-1.75" if s < 2 else "2.00+"
        by[k][0] += 1
        by[k][1] += hit(r)
    lines.append("Recall by reported size")
    for k in sorted(by):
        lines.append(f"  {k:<10}{by[k][0]:>6} points  {by[k][1] / by[k][0]:.1%}")
    # 2. For misses: radar nearby.
    miss = [n for n, r in enumerate(rows) if not hit(r)]
    lines += ["", f"Misses: {len(miss)}.  Largest radar value near each, in bands (mm_here is the cell at the point)"]
    for col in ("mm_here", "mm_3", "mm_5", "mm_8"):
        c = collections.Counter(band(got[n][col]) for n in miss)
        lines.append(f"  {col:<8}" + "  ".join(f"{k} {c[k]}" for k in ("none", "<0.5", "0.5-" + str(args.threshold_in), ">=thr")))
    # 3. Recall if radar hail within d counted.
    lines += ["", "Recall if a point counted when radar at the threshold lay within d (misses with radar >= threshold within d are added)"]
    for col, d in (("mm_here", "at the point"), ("mm_3", "3 km"), ("mm_5", "5 km"), ("mm_8", "8 km")):
        extra = sum(1 for n in miss if got[n][col] is not None and got[n][col] >= thr)
        lines.append(f"  {d:<13}{(len(rows) - len(miss) + extra) / len(rows):.1%}  (+{extra})")
    # 4. By year, as the headline.
    lines += ["", "Of the misses, what radar value was within 5 km, by reported size"]
    for k in sorted(by):
        ns = [n for n in miss if (lambda s: "1.00" if s < 1.25 else "1.25-1.75" if s < 2 else "2.00+")(float(rows[n]["size_in"])) == k]
        c = collections.Counter(band(got[n]["mm_5"]) for n in ns)
        lines.append(f"  {k:<10}{len(ns):>5} misses  " + "  ".join(f"{b} {c[b]}" for b in sorted(c)))
    text = "\n".join(lines)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "misses.txt").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
