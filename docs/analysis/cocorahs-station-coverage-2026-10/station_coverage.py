#!/usr/bin/env python3
"""CoCoRaHS stations against the daily radar: where observers are, and what the ones inside radar hail filed

Analysis only (2026-10-23, written by Claude).  Reads public files and the public CoCoRaHS export (one request per
storm day, a pause between them), writes only under --out and the cache folders.  Nothing here touches the database,
RentCast or any email.  Item 253, decision log 2026-10-23 (the hail-likelihood design, point 7).

The question.  A CoCoRaHS *hail* report is only filed when hail fell, so it cannot say where hail did not fall.  But a
station that files a *daily precipitation* report is an observer who was there.  For every station that filed the daily
report covering a storm day, with the daily MESH_Max_1440min within 3, 5 and 8 km of it:
  - did it file a hail report that day, or only rain, or was it dry (0.00)?
  - how does that change with the radar value, and with whether an independent IEM hail report is near?
A dry station inside radar hail is the strongest absence signal there is (hail leaves precipitation).  A rainy station
with no hail report is a weak one (observers do not always file hail).  Also: how much of the radar hail area has any
station at all within 5 km, which says how thin the ground evidence really is.

Timing.  A daily report covers the 24 hours ending about 7 AM on its ObservationDate, so a storm on local day D is in
the report dated D+1 (checked 2026-10-23 on 2024-05-30: evening hail stations read 0.15 to 0.64 in on 5/31 and about
0.00 on 5/30).  The script uses the D+1 report.  Hail from midnight to 7 AM on D is in the D report and is missed here.
"""
import argparse
import collections
import csv
import json
import math
import os
import statistics as st
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from multiprocessing import get_context
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.environ.get("HAIL_REPO", os.path.expanduser("~/Hail-System")))
from hailsys.radar import fetch, mesh          # noqa: E402  (stdlib only)

DENVER = ZoneInfo("America/Denver")
BOX = (41.5, 37.0, -106.3, -101.4)             # lat max, lat min, lon min, lon max: the same box as the other studies
FIRST, LAST = date(2020, 10, 15), date(2026, 10, 21)
FLOOR_MM = 12.7
CEILING_MM = 254.0                             # item 247
IN = 25.4
RADII = (3.0, 5.0, 8.0)
REPORT_KM = 5.0
# NOAA's impossible values (items 247 and 250); a day over the ceiling is also set aside when it is read
SKIP_DAYS = {"2022-01-03", "2022-01-11", "2020-12-23", "2020-12-28"}
EXPORT = "https://data.cocorahs.org/cocorahs/export/exportreports.aspx"
BANDS = ((0.0, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.5), (1.5, 99.0))   # MESH max within 5 km, inches
GRID = 0.1                                     # degrees; larger than the 8 km window in latitude and longitude here


def hav(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def in_box(lat, lon):
    return BOX[1] <= lat <= BOX[0] and BOX[2] <= lon <= BOX[3]


def num(text):
    """A finite float or None.  Decimal accepts 'NaN' and 'Infinity' (CLAUDE.md trap), so check is_finite."""
    try:
        d = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        return None
    return float(d) if d.is_finite() else None


def precip(text):
    """Daily precipitation in inches: None for NA or blank (no value), 0.001 for T (a trace is wet)."""
    t = (text or "").strip().upper()
    if t in ("", "NA"):
        return None
    if t == "T":
        return 0.001
    return num(t)


class Grid:
    """Points in 0.1 degree buckets, so a few-km window looks at nine buckets instead of every point."""

    def __init__(self, points):
        self.b = collections.defaultdict(list)
        for p in points:                       # p = (lat, lon, payload)
            self.b[(int(p[0] // GRID), int(p[1] // GRID))].append(p)

    def near(self, lat, lon, km):
        i, j = int(lat // GRID), int(lon // GRID)
        dlat, dlon = km / 110.0, km / 85.0
        for a in (i - 1, i, i + 1):
            for b in (j - 1, j, j + 1):
                for p in self.b.get((a, b), ()):
                    if abs(p[0] - lat) <= dlat and abs(p[1] - lon) <= dlon and hav(p[0], p[1], lat, lon) <= km:
                        yield p

    def any(self, lat, lon, km, pred=None):
        return any(pred is None or pred(p) for p in self.near(lat, lon, km))

    def max_value(self, lat, lon, km):
        return max((p[2] for p in self.near(lat, lon, km)), default=0.0)


def load_hail(paths):
    """CoCoRaHS hail filings by local date: {day: {station: largest_in or None}} plus where each station is."""
    by_day, where, dropped = collections.defaultdict(dict), {}, collections.Counter()
    for p in paths:
        with open(p, newline="") as f:
            for row in csv.DictReader(f, skipinitialspace=True):
                lat, lon = num(row.get("Latitude")), num(row.get("Longitude"))
                try:
                    day = date.fromisoformat(row["ObservationDate"].strip())
                except (KeyError, ValueError, AttributeError):
                    dropped["bad date"] += 1
                    continue
                if lat is None or lon is None or not in_box(lat, lon) or not (FIRST <= day <= LAST):
                    dropped["no position, outside box or dates"] += 1
                    continue
                s = (row.get("StationNumber") or "").strip()
                # the largest stone, else the average when only that was filed (many forms carry only one of them)
                size = num(row.get("LargestSize"))
                by_day[day.isoformat()][s] = size if size is not None else num(row.get("AverageSize"))
                where[s] = (lat, lon)
    return by_day, where, dropped


def load_iem(path):
    """Independent hail reports (not CoCoRaHS-sourced) by local day, as (lat, lon, magnitude)."""
    indep, dropped = collections.defaultdict(list), collections.Counter()
    with open(path, newline="") as f:
        reader = csv.reader(f)
        head = next(reader)
        for row in reader:
            if len(row) != len(head):          # unquoted commas in CITY (CLAUDE.md trap): the fields are unrecoverable
                dropped["field_count_mismatch"] += 1
                continue
            r = dict(zip(head, row))
            if r["TYPETEXT"].strip() != "HAIL" or r["SOURCE"].strip().upper() == "COCORAHS":
                continue
            lat, lon, mag = num(r["LAT"]), num(r["LON"]), num(r["MAG"])   # MAG 'None' is IEM's null (trap)
            if lat is None or lon is None or not in_box(lat, lon):
                continue
            t = datetime.strptime(r["VALID"], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            indep[t.astimezone(DENVER).date().isoformat()].append((lat, lon, mag))
    return indep, dropped


def daily_path(cache, report_day):
    return Path(cache, f"daily_CO_{report_day.isoformat()}.csv")


def download_daily(report_day, cache, pause, contact):
    """The Colorado daily reports dated report_day, cached.  One request, a pause after it, loud on anything odd."""
    dest = daily_path(cache, report_day)
    if dest.exists():
        return dest
    q = urllib.parse.urlencode({"ReportType": "Daily", "Format": "csv", "State": "CO", "ReportDateType": "reportdate",
                                "StartDate": f"{report_day.month}/{report_day.day}/{report_day.year}",
                                "EndDate": f"{report_day.month}/{report_day.day}/{report_day.year}",
                                "TimesInGMT": "False", "dtf": "1"})
    req = urllib.request.Request(EXPORT + "?" + q, headers={
        "User-Agent": "hail-system-analysis (research; " + (contact or "no contact given") + ")"})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read()
            if not body.startswith(b"ObservationDate,"):
                raise ValueError("unexpected export header: " + repr(body[:80]))
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(body)
            tmp.replace(dest)
            time.sleep(pause)
            return dest
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if attempt == 3:
                raise RuntimeError(f"daily export for {report_day} failed: {exc}") from exc
            time.sleep(5 * attempt)


def read_daily(path):
    """Stations in the box with their daily precipitation: [(lat, lon, station, precip_in_or_None)]."""
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f, skipinitialspace=True):
            lat, lon = num(row.get("Latitude")), num(row.get("Longitude"))
            if lat is None or lon is None or not in_box(lat, lon):
                continue
            out.append((lat, lon, (row.get("StationNumber") or "").strip(), precip(row.get("TotalPrecipAmt"))))
    return out


def process_day(job):
    day_iso, hail_day, hail_where, reports, mesh_cache, daily_cache, out_dir = job
    out = Path(out_dir, "days", day_iso + ".json")
    if out.exists():
        return json.loads(out.read_text())
    day = date.fromisoformat(day_iso)
    rec = {"day": day_iso, "status": "ok", "stations": [], "area": {}}
    try:
        path, stamp, key = fetch.fetch_day(day, DENVER, mesh_cache)
        got = mesh.read_cells(path, BOX, FLOOR_MM)
    except fetch.NotAvailable:
        rec["status"] = "missing"
        got = None
    if got is not None and got["max_mm"] is not None and got["max_mm"] > CEILING_MM:
        rec["status"] = "suspect"
    if rec["status"] != "ok":
        out.write_text(json.dumps(rec))
        return rec
    cells = Grid((mesh.LA1 - r * mesh.STEP, mesh.LO1 + c * mesh.STEP, v) for r, c, v in got["cells"])
    daily = read_daily(daily_path(daily_cache, day + timedelta(days=1)))
    seen = {s for _, _, s, _ in daily}
    # a station that filed hail but no daily report is still a station that was there:  keep it, precipitation unknown
    extra = [(hail_where[s][0], hail_where[s][1], s, None) for s in hail_day if s not in seen and s in hail_where]
    stations = daily + extra
    rep_all = Grid((a, b, m) for a, b, m in reports)
    rep_big = Grid((a, b, m) for a, b, m in reports if m is not None and m >= 1.0)
    for lat, lon, s, p in stations:
        rec["stations"].append({
            "s": s, "lat": lat, "lon": lon, "p": p, "daily": s in seen,
            "hail": s in hail_day, "size": hail_day.get(s),
            "m3": round(cells.max_value(lat, lon, 3.0), 1), "m5": round(cells.max_value(lat, lon, 5.0), 1),
            "m8": round(cells.max_value(lat, lon, 8.0), 1),
            "rn": rep_all.any(lat, lon, REPORT_KM), "rn1": rep_big.any(lat, lon, REPORT_KM)})
    # area: each 0.01 degree cell at or above T, with or without an independent report within 5 km, and what ground
    # observers are within 5 km of it:  any station that reported, a wet one, one that filed hail
    obs = Grid((x["lat"], x["lon"], x) for x in rec["stations"] if x["p"] is not None or x["hail"])
    for T in (0.75, 1.0):
        a = collections.Counter()
        for la, lo, v in (p for ps in cells.b.values() for p in ps):
            if v < T * IN:
                continue
            kind = "report" if rep_all.any(la, lo, REPORT_KM) else "radar_only"
            a[kind] += 1
            near = list(obs.near(la, lo, REPORT_KM))
            if near:
                a[kind + "_station"] += 1
                if any((x[2]["p"] or 0) > 0 for x in near):
                    a[kind + "_wet"] += 1
                if any(x[2]["hail"] for x in near):
                    a[kind + "_hail"] += 1
        rec["area"][str(T)] = dict(a)
    out.write_text(json.dumps(rec))
    return rec


def pct(a, b):
    return f"{100 * a / b:4.0f}%" if b else "   --"


def outcome(x):
    """hail filed / wet, no hail / dry (0.00) / no precipitation value."""
    if x["hail"]:
        return "hail"
    if x["p"] is None:
        return "none"
    return "wet" if x["p"] > 0 else "dry"


def band_row(name, sel):
    c = collections.Counter(outcome(x) for x in sel)
    n = len(sel)
    wet_or_hail = c["hail"] + c["wet"]
    return (f"   {name:34s} {n:7d}  dry {pct(c['dry'], n)}  wet no hail {pct(c['wet'], n)}  hail {pct(c['hail'], n)}"
            f"   hail among wet or hail: {pct(c['hail'], wet_or_hail)}  (n {wet_or_hail})")


def summarize(out_dir):
    recs = [json.loads(p.read_text()) for p in sorted(Path(out_dir, "days").glob("*.json"))]
    ok = [r for r in recs if r["status"] == "ok"]
    sd = [dict(x, day=r["day"]) for r in ok for x in r["stations"]]
    L = []
    say = L.append
    say(f"== CoCoRaHS stations against the daily MESH, {FIRST} to {LAST}, coverage box")
    say(f"   days: {len(recs)} ({dict(collections.Counter(r['status'] for r in recs))});  station-days {len(sd)} from "
        f"{len({x['s'] for x in sd})} stations;  hail filings among them: {sum(x['hail'] for x in sd)}")
    per_day = [sum(1 for x in r['stations'] if x['p'] is not None) for r in ok]
    say(f"   stations with a daily precipitation value per day: median {st.median(per_day) if per_day else 0:.0f}, "
        f"min {min(per_day, default=0)}, max {max(per_day, default=0)}")
    say(f"   hail filed by a station with no daily report that day: {sum(x['hail'] and not x['daily'] for x in sd)}")

    say("\n== A. Radar hail area and the observers near it (cells 0.01 deg, about 0.94 km2; observers within 5 km)")
    for T in ("1.0", "0.75"):
        a = collections.Counter()
        for r in ok:
            a.update(r["area"].get(T, {}))
        for kind, label in (("report", "with an independent report within 5 km"), ("radar_only", "radar only")):
            n = a[kind]
            say(f"   MESH >= {float(T):.2f} in, {label:38s} cells {n:8d}   any reporting station {pct(a[kind + '_station'], n)}"
                f"   a wet station {pct(a[kind + '_wet'], n)}   a hail filing {pct(a[kind + '_hail'], n)}")

    say("\n== B. Station-days by the largest MESH within 5 km: dry, wet with no hail, or hail filed")
    say(f"   {'MESH within 5 km (in)':34s} {'days':>7s}")
    for lo, hi in BANDS:
        sel = [x for x in sd if x["p"] is not None or x["hail"]]
        sel = [x for x in sel if lo * IN <= x["m5"] < hi * IN] if hi < 99 else [x for x in sel if x["m5"] >= lo * IN]
        say(band_row(f"{lo:.2f} to {hi:.2f}" if hi < 99 else f"{lo:.2f} or more", sel))

    for T in (1.0, 0.75):
        say(f"\n== C. Station-days with MESH >= {T:.2f} in within 5 km, by whether an independent IEM report is within 5 km")
        have = [x for x in sd if (x["p"] is not None or x["hail"]) and x["m5"] >= T * IN]
        say(band_row("radar and report", [x for x in have if x["rn"]]))
        say(band_row("radar only", [x for x in have if not x["rn"]]))
        for k in (3, 8):
            alt = [x for x in sd if (x["p"] is not None or x["hail"]) and x[f"m{k}"] >= T * IN and not x["rn"]]
            say(band_row(f"radar only, MESH within {k} km", alt))

    say("\n== D. Radar only (MESH >= 1.00 in within 5 km, no independent report within 5 km), by year and region")
    ro = [x for x in sd if (x["p"] is not None or x["hail"]) and x["m5"] >= IN and not x["rn"]]
    for y in sorted({x["day"][:4] for x in sd}):
        say(band_row(y, [x for x in ro if x["day"][:4] == y]))
    say(band_row("Front Range band (lon -105.05 to -104.5)", [x for x in ro if -105.05 <= x["lon"] < -104.5]))
    say(band_row("east of lon -103.9", [x for x in ro if x["lon"] >= -103.9]))
    say(band_row("Dec to Mar", [x for x in ro if x["day"][5:7] in ("12", "01", "02", "03")]))

    say("\n== E. Hail filings (largest stone) against the radar at the station")
    big = [x for x in sd if x["hail"] and x["size"] is not None and x["size"] >= 1.0]
    say(f"   hail filings of 1 in or more: {len(big)};  MESH >= 1.00 in within 3/5/8 km: "
        + " / ".join(pct(sum(x[f'm{int(k)}'] >= IN for x in big), len(big)).strip() for k in RADII))
    text = "\n".join(L) + "\n"
    Path(out_dir, "summary.txt").write_text(text)
    with open(Path(out_dir, "station_days.csv"), "w", newline="") as f:
        cols = ["day", "s", "lat", "lon", "p", "daily", "hail", "size", "m3", "m5", "m8", "rn", "rn1"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for x in sd:
            if x["m8"] >= FLOOR_MM or x["hail"]:      # the rest is the dry-country baseline: 600,000 rows of zeros
                w.writerow({k: x[k] for k in cols})
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hail", nargs="+", required=True, help="CoCoRaHS hail export CSVs (hail_CO_*.csv)")
    ap.add_argument("--iem", required=True, help="IEM LSR CSV (iem_lsr_CO.csv)")
    ap.add_argument("--days", required=True, help="local days to read, one YYYY-MM-DD per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mesh-cache", required=True, help="folder for the daily MESH files (about 100 KB each, kept)")
    ap.add_argument("--daily-cache", required=True, help="folder for the CoCoRaHS daily exports (about 90 KB each, kept)")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds to wait after each CoCoRaHS request")
    ap.add_argument("--contact", default="", help="put in the User-Agent so CoCoRaHS can reach whoever runs this")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--summarize", action="store_true")
    a = ap.parse_args()
    for d in (Path(a.out, "days"), Path(a.mesh_cache), Path(a.daily_cache)):
        d.mkdir(parents=True, exist_ok=True)
    if a.summarize:
        print(summarize(a.out))
        return
    hail, where, hdrop = load_hail(a.hail)
    indep, idrop = load_iem(a.iem)
    days = sorted({d.strip() for d in open(a.days) if d.strip()} - SKIP_DAYS)
    days = [d for d in days if FIRST <= date.fromisoformat(d) <= LAST]
    if a.limit:
        days = days[:a.limit]
    print(f"{len(days)} days;  hail filings on {len(hail)} days (dropped {dict(hdrop)});  "
          f"independent IEM hail {sum(map(len, indep.values()))} (dropped {dict(idrop)})", flush=True)
    todo = [d for d in days if not daily_path(a.daily_cache, date.fromisoformat(d) + timedelta(days=1)).exists()]
    print(f"{len(todo)} CoCoRaHS daily exports to download, one every {a.pause:g} s", flush=True)
    for i, d in enumerate(todo, 1):          # serial on purpose: one polite request at a time
        download_daily(date.fromisoformat(d) + timedelta(days=1), a.daily_cache, a.pause, a.contact)
        if i % 25 == 0 or i == len(todo):
            print(f"  downloaded {i}/{len(todo)}", flush=True)
    jobs = [(d, hail.get(d, {}), where, indep.get(d, []), a.mesh_cache, a.daily_cache, a.out) for d in days]
    with get_context("spawn").Pool(a.workers) as pool:
        for i, rec in enumerate(pool.imap_unordered(process_day, jobs), 1):
            if i % 25 == 0 or i == len(jobs):
                print(f"[{i}/{len(jobs)}] {rec['day']} {rec['status']}", flush=True)
    print(summarize(a.out))


if __name__ == "__main__":
    main()
