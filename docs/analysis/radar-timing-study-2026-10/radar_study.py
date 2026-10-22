#!/usr/bin/env python3
"""Radar against hail reports on many days, with timing: the large version of the 2026-10-22 checks.

For each local day it downloads the 24 hourly NOAA MESH_Max_60min files (a file stamped H covers the hour before H),
and for every hail report that day records the largest radar value within 3, 5 and 8 km in the report's own hour and
in the hours either side.  It also follows the main swath hour to hour to get a storm motion.  Standard library
only (the repo's hailsys.radar decoder), one process per worker, one JSON file per day, so a stopped run resumes.

    python3 radar_study.py --reports reports.csv --days days.txt --out out/ --workers 5
    python3 radar_study.py --summarize out/               # tables from the day files already there

reports.csv: day,utc_time,magnitude,longitude,latitude  (day = the Denver local date; see README.md for the export)
days.txt:    one local date (YYYY-MM-DD) per line

Written in-session by Claude (not keyed in); analysis only, it reads public NOAA data and writes only to --out.
Run it with `nice -n 10 nohup ... &` on a machine you are also using.  Worker processes are started with 'spawn' so
Python 3.12 and 3.14 behave the same (3.14 changed the default).
"""

import argparse
import collections
import csv
import json
import math
import os
import statistics as st
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from hailsys.radar import fetch, mesh          # noqa: E402  (stdlib only)

UTC = timezone.utc
BOX = (41.5, 37.0, -106.3, -101.4)             # lat max, lat min, lon min, lon max: the Front Range with margin
FLOOR_MM = 12.7                                # keep cells from 0.5 in
CEILING_MM = 254.0                             # a value above 10 in is NOAA's processing, not hail (item 247)
IN = 25.4
RADII_KM = (3.0, 5.0, 8.0)
MIN_SWATH_CELLS = 15


def hav(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def components(cells):
    seen, out = set(), []
    for start in cells:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    n = (r + dr, c + dc)
                    if n in cells and n not in seen:
                        seen.add(n)
                        stack.append(n)
        out.append(comp)
    return out


def slope(xs, ys):
    mx, my = st.fmean(xs), st.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0


def rank_corr(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):                      # average ranks for ties
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2
            i = j + 1
        return out
    a, b = ranks(xs), ranks(ys)
    ma, mb = st.fmean(a), st.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def ll(r, c):
    return mesh.LA1 - r * mesh.STEP, mesh.LO1 + c * mesh.STEP


def day_stamps(day, tz):
    start = fetch.day_window(day, tz)[0].astimezone(UTC)
    return [start + timedelta(hours=h) for h in range(1, 25)]


def load_hour(stamp, folder, keep, attempts=6):
    """(status, cells): status 'ok', 'missing' or 'suspect'; cells [(lat, lon, mm)] from 0.5 in."""
    name = f"MRMS_MESH_Max_60min_00.50_{stamp:%Y%m%d-%H%M%S}.grib2.gz"
    path = os.path.join(folder, name)
    try:
        if not os.path.exists(path):
            fetch.download(f"{fetch.BUCKET}/CONUS/MESH_Max_60min_00.50/{stamp:%Y%m%d}/{name}", path, attempts=attempts)
        got = mesh.read_cells(path, BOX, FLOOR_MM)
    except fetch.NotAvailable:
        return "missing", []
    finally:
        if not keep and os.path.exists(path):
            os.remove(path)
    if got["max_mm"] is not None and got["max_mm"] > CEILING_MM:
        return "suspect", []
    return "ok", [(*ll(r, c), v) for r, c, v in got["cells"]]


def window_max(cells, lat, lon, radius_km):
    """Largest value within radius_km of the point, in mm (0.0 if none)."""
    dlat, dlon = radius_km / 110.0, radius_km / 85.0
    best = 0.0
    for la, lo, v in cells:
        if v > best and abs(la - lat) <= dlat and abs(lo - lon) <= dlon and hav(la, lo, lat, lon) <= radius_km:
            best = v
    return best


def process_day(job):
    """One local day.  Top level and picklable so a spawned worker can run it.  Returns the day's record."""
    day_iso, reports, cache, out_dir, tz_name, keep = job
    tz = ZoneInfo(tz_name)
    day = date.fromisoformat(day_iso)
    stamps = day_stamps(day, tz)
    hours = {}
    for stamp in stamps:
        hours[stamp] = load_hour(stamp, cache, keep)
        time.sleep(0.1)                                           # be polite to the open bucket
    # ---- every swath followed hour to hour as its own chain (so two storms in one evening are two tracks)
    chains, active = [], []                       # active: chains whose last hour was the previous hour
    for stamp in stamps:
        status, cells = hours[stamp]
        big = {}
        for la, lo, v in cells:
            if v >= IN:
                big[(round((mesh.LA1 - la) / mesh.STEP), round((lo - mesh.LO1) / mesh.STEP))] = v
        comps = sorted((c for c in components(big) if len(c) >= MIN_SWATH_CELLS), key=len, reverse=True)
        taken, now_active = set(), []
        for c in comps:
            pts = [ll(*x) for x in c]
            here = (st.fmean(p[0] for p in pts), st.fmean(p[1] for p in pts))
            best = None
            for k, ch in enumerate(active):
                if k in taken:
                    continue
                d = hav(ch["pts"][-1][1], ch["pts"][-1][2], here[0], here[1])
                if d <= 100 and (best is None or d < best[0]):
                    best = (d, k)
            if best:
                taken.add(best[1])
                ch = active[best[1]]
            else:
                ch = {"pts": [], "cells": 0}
                chains.append(ch)
            ch["pts"].append((stamp.timestamp() / 3600, here[0], here[1], len(c), max(p[1] for p in pts)))
            ch["cells"] += len(c)
            now_active.append(ch)
        active = now_active
    tracks = []
    for ch in chains:
        series = ch["pts"]
        if len(series) >= 3 and series[-1][0] - series[0][0] >= 2:
            ts = [x[0] for x in series]
            u = slope(ts, [x[2] for x in series]) * 111.32 * math.cos(math.radians(st.fmean(x[1] for x in series)))
            v = slope(ts, [x[1] for x in series]) * 110.95
            tracks.append({"u_kmh": u, "v_kmh": v, "bearing": (math.degrees(math.atan2(u, v)) + 360) % 360,
                           "hours": len(series), "cells": ch["cells"], "first_lon": series[0][2], "last_lon": series[-1][2]})
    # ---- each report against the radar, in its own hour and the hours either side
    rows = []
    for t_iso, mag, lon, lat in reports:
        t = datetime.fromisoformat(t_iso).replace(tzinfo=UTC)
        stamp = t if (t.minute == 0 and t.second == 0) else t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        row = {"t": t_iso, "mag": mag, "lon": lon, "lat": lat}
        for label, offset in (("prev", -1), ("own", 0), ("next", 1)):
            status, cells = hours.get(stamp + timedelta(hours=offset), ("missing", []))
            for radius in RADII_KM:
                row[f"{label}_{int(radius)}"] = None if status != "ok" else round(window_max(cells, lat, lon, radius), 1)
        rows.append(row)
    record = {"day": day_iso, "files": len(stamps), "missing": sum(1 for s in hours.values() if s[0] == "missing"),
              "suspect": sum(1 for s in hours.values() if s[0] == "suspect"),
              "cells_1in_hours": sum(1 for s in hours.values() if any(c[2] >= IN for c in s[1])),
              "tracks": tracks, "reports": rows}
    tmp = os.path.join(out_dir, "days", day_iso + ".json.part")
    with open(tmp, "w") as f:
        json.dump(record, f)
    os.replace(tmp, os.path.join(out_dir, "days", day_iso + ".json"))
    return day_iso, len(reports), tracks


# ------------------------------------------------------------------------------------------- summary

def pct(n, d):
    return f"{100 * n / d:3.0f}%" if d else "  --"


def summarize(out_dir):
    records = [json.load(open(p)) for p in sorted(Path(out_dir, "days").glob("*.json"))]
    lines = []
    say = lines.append
    say(f"== Radar against hail reports: {len(records)} local days")
    say(f"   hourly files: {sum(r['files'] for r in records)};  missing at NOAA: {sum(r['missing'] for r in records)};  "
        f"set aside as implausible (> 10 in): {sum(r['suspect'] for r in records)}")
    reports = [dict(row, day=r["day"]) for r in records for row in r["reports"]]
    reports = [r for r in reports if r["own_5"] is not None]          # reports whose own hour has a file
    big = [r for r in reports if r["mag"] >= 1.0]
    say(f"   hail reports: {len(reports)} with a radar file for their hour; {len(big)} of 1.0 in or more")

    say("\n== A. Timing: reports of 1.0 in or more, radar within 5 km (in = radar max value in inches)")
    cats = collections.Counter()
    for r in big:
        own, near = r["own_5"] / IN, max((r["prev_5"] or 0), (r["next_5"] or 0)) / IN
        cats["own hour >= 1.00" if own >= 1.0 else "own hour 0.75-1.00" if own >= 0.75 else "own hour 0.50-0.75" if own >= 0.5
             else "neighbouring hour only >= 0.75" if near >= 0.75 else "none >= 0.75 within +-1 h"] += 1
    for k in ("own hour >= 1.00", "own hour 0.75-1.00", "own hour 0.50-0.75", "neighbouring hour only >= 0.75", "none >= 0.75 within +-1 h"):
        say(f"   {k:34s} {cats[k]:6d} {pct(cats[k], len(big))}")
    say("   share of reports of 1.0 in or more caught in their own hour, by threshold and distance:")
    say(f"   {'T (in)':>7s} " + " ".join(f"{'R=' + str(int(k)) + ' km':>8s}" for k in RADII_KM))
    for T in (0.5, 0.75, 1.0, 1.25):
        say(f"   {T:7.2f} " + " ".join(f"{100 * sum(r[f'own_{int(k)}'] / IN >= T for r in big) / len(big):7.0f}%" for k in RADII_KM))

    say("\n== B. Size: radar max within 5 km in the report's own hour against the reported size")
    pairs = [(r["mag"], r["own_5"] / IN) for r in reports]
    say(f"   radar says AT LEAST t inches -> nearby ground reports (n = {len(pairs)} hail reports of any size)")
    say(f"   {'t (in)':>7s} {'reports':>8s} {'median reported':>15s} {'reported >= t':>13s} {'reported >= t-0.5':>17s} {'reported >= 1.0':>15s}")
    for t in (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        sel = [p for p in pairs if p[1] >= t]
        if len(sel) >= 5:
            say(f"   {t:7.2f} {len(sel):8d} {st.median(p[0] for p in sel):15.2f} {pct(sum(p[0] >= t for p in sel), len(sel)):>13s} "
                f"{pct(sum(p[0] >= t - 0.5 for p in sel), len(sel)):>17s} {pct(sum(p[0] >= 1.0 for p in sel), len(sel)):>15s}")
    say(f"   reported >= s inches -> radar in the same hour:")
    say(f"   {'s (in)':>7s} {'reports':>8s} {'radar>=0.5':>10s} {'radar>=0.75':>11s} {'radar>=1.0':>10s} {'radar>=s':>9s} {'median radar':>12s}")
    for s in (1.0, 1.5, 2.0, 2.5, 3.0):
        sel = [p for p in pairs if p[0] >= s]
        if len(sel) >= 5:
            say(f"   {s:7.2f} {len(sel):8d} {pct(sum(p[1] >= 0.5 for p in sel), len(sel)):>10s} {pct(sum(p[1] >= 0.75 for p in sel), len(sel)):>11s} "
                f"{pct(sum(p[1] >= 1.0 for p in sel), len(sel)):>10s} {pct(sum(p[1] >= s for p in sel), len(sel)):>9s} {st.median(p[1] for p in sel):12.2f}")
    near = [p for p in pairs if p[1] > 0 and p[0] > 0]
    if near:
        ratio = [p[1] / p[0] for p in near]
        say(f"   with radar hail near: median radar/reported {st.median(ratio):.2f}; radar higher on {pct(sum(x > 1 for x in ratio), len(ratio)).strip()}, "
            f"within +-25% on {pct(sum(0.75 <= x <= 1.25 for x in ratio), len(ratio)).strip()}; rank correlation {rank_corr([p[0] for p in near], [p[1] for p in near]):.2f} (n = {len(near)})")

    say("\n== C. By region (longitude), reports of 1.0 in or more, radar >= 1.00 / >= 0.75 in within 5 km in the own hour")
    for name, lo, hi in (("west of -105.05", -999, -105.05), ("-105.05 to -104.5", -105.05, -104.5),
                         ("-104.5 to -103.9", -104.5, -103.9), ("east of -103.9", -103.9, 999)):
        sel = [r for r in big if lo <= r["lon"] < hi]
        say(f"   {name:20s} {len(sel):5d}   {pct(sum(r['own_5'] / IN >= 1.0 for r in sel), len(sel))}   {pct(sum(r['own_5'] / IN >= 0.75 for r in sel), len(sel))}")

    say("\n== D. By month, same measure")
    by_month = collections.defaultdict(list)
    for r in big:
        by_month[int(r["t"][5:7])].append(r)
    for m in sorted(by_month):
        sel = by_month[m]
        say(f"   month {m:2d} {len(sel):5d}   {pct(sum(r['own_5'] / IN >= 1.0 for r in sel), len(sel))}   {pct(sum(r['own_5'] / IN >= 0.75 for r in sel), len(sel))}")

    say("\n== E. Storm motion (each swath followed hour to hour; a track is at least 3 hours spanning 2 h or more)")
    tracks = [(r["day"], t) for r in records for t in r.get("tracks", [])]
    days_with = len({d for d, _ in tracks})
    say(f"   tracks: {len(tracks)} on {days_with} of {len([r for r in records if r['cells_1in_hours']])} days with radar hail of 1 in or more")
    moving = [(d, t) for d, t in tracks if math.hypot(t["u_kmh"], t["v_kmh"]) >= 15]
    if moving:
        east = sum(t["u_kmh"] > 0 for _, t in moving)
        es = sum(60 <= t["bearing"] <= 180 for _, t in moving)
        say(f"   moving 15 km/h or more: {len(moving)};  eastward {pct(east, len(moving)).strip()};  heading E to S (60-180 deg) {pct(es, len(moving)).strip()}")
        spd = [math.hypot(t["u_kmh"], t["v_kmh"]) for _, t in moving]
        quart = [round(x) for x in st.quantiles(spd, n=4)] if len(spd) >= 2 else spd
        say(f"   median speed {st.median(spd):.0f} km/h ({st.median(spd) * 0.621:.0f} mph), quartiles {quart};  median heading {st.median(t['bearing'] for _, t in moving):.0f} deg")
        compass = collections.Counter()
        for _, t in moving:
            b = t["bearing"]
            compass[("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int(((b + 22.5) % 360) // 45)]] += 1
        say("   compass: " + ", ".join(f"{k} {compass[k]}" for k in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")))
        by_year = collections.defaultdict(list)
        for d, t in moving:
            by_year[d[:4]].append(t)
        for y in sorted(by_year):
            ts = by_year[y]
            say(f"   {y}: {len(ts):3d} storms, eastward {pct(sum(t['u_kmh'] > 0 for t in ts), len(ts)).strip()}, median heading {st.median(t['bearing'] for t in ts):.0f} deg")
    say(f"   slow or stalled (< 15 km/h): {len(tracks) - len(moving)}")

    text = "\n".join(lines)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(text + "\n")
    with open(os.path.join(out_dir, "reports_timing.csv"), "w", newline="") as f:
        cols = ["day", "t", "mag", "lon", "lat"] + [f"{h}_{int(k)}" for h in ("prev", "own", "next") for k in RADII_KM]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in [dict(row, day=rec["day"]) for rec in records for row in rec["reports"]]:
            w.writerow({c: r.get(c) for c in cols})
    return text


# ----------------------------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reports"), ap.add_argument("--days"), ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="do at most this many days (for a trial)")
    ap.add_argument("--tz", default="America/Denver", help="the zone the report days are dates in")
    ap.add_argument("--keep", action="store_true", help="keep the downloaded files (about 100 KB each)")
    ap.add_argument("--cache", help="a folder for the downloaded files; given, they are kept and reused on a re-run")
    ap.add_argument("--summarize", action="store_true", help="only print the summary of what is in --out")
    args = ap.parse_args(argv)
    out = Path(args.out)
    (out / "days").mkdir(parents=True, exist_ok=True)
    if args.summarize:
        print(summarize(out))
        return 0
    if not args.reports or not args.days:
        ap.error("--reports and --days are required to run")
    by_day = collections.defaultdict(list)
    for d, t, mag, lon, lat in csv.reader(open(args.reports)):
        by_day[d].append((t, float(mag), float(lon), float(lat)))
    days = [l.strip() for l in open(args.days) if l.strip()]
    todo = [d for d in sorted(set(days)) if not (out / "days" / f"{d}.json").exists()]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(days)} days listed, {len(todo)} to do ({len(set(days)) - len(todo)} already done); {args.workers} workers", flush=True)
    cache = args.cache or tempfile.mkdtemp(prefix="radar_study_")
    os.makedirs(cache, exist_ok=True)
    jobs = [(d, by_day.get(d, []), cache, str(out), args.tz, args.keep or bool(args.cache)) for d in todo]
    started = time.time()
    done = 0
    with get_context("spawn").Pool(args.workers, maxtasksperchild=10) as pool:
        for day, n, track in pool.imap_unordered(process_day, jobs):
            done += 1
            eta = (time.time() - started) / done * (len(jobs) - done)
            motion = (f"{len(track)} track(s), longest u={max(track, key=lambda t: t['hours'])['u_kmh']:+.0f} "
                      f"v={max(track, key=lambda t: t['hours'])['v_kmh']:+.0f} km/h") if track else "no track"
            print(f"[{done}/{len(jobs)}] {day} reports={n} {motion}  (~{eta / 60:.0f} min left)", flush=True)
    print("\n" + summarize(out))
    print("\nDONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
