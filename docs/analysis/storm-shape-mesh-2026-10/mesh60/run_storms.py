"""Steps 4-5: MESH_Max_60min (NOAA S3, hourly files) against the good-fit storms dated on or after 2020-10-14.
Run from ~/hail-analysis:  .venv/bin/python mesh60/run_storms.py
Memory: one CONUS grid at a time (meshgrid.read_crop_pygrib); one worker; crops cached to mesh60/crops/*.npz;
per-storm results checkpointed to mesh60/per_storm/<id>.json and skipped on rerun."""
import csv, json, math, os, sys, collections, datetime as dt, resource, gc
import numpy as np
from pyproj import Transformer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meshgrid as mg

HERE = os.path.dirname(os.path.abspath(__file__))
SS = os.path.join(HERE, "..", "storm_shape")
OUT = os.path.join(HERE, "per_storm"); os.makedirs(OUT, exist_ok=True)

R_M = 5.0 * 1609.344            # today's match radius (settings.default_match_radius_miles = 5.0 per storm_shape/summary.md; no DB here)
FAR_M = 8.0 * 1609.344          # "more than 8 mi from any actionable report"
DOMAIN_M = 30_000.0             # MESH area counted only within 30 km of the storm's reports (other storms share the crop)
NEAR_M = 5_000.0                # per-report max MESH radius
T100, T075 = 25.4, 19.05        # 1.00 in and 0.75 in, mm
FOOTHILLS_LON = -105.0          # cutoff: west of this = foothills (see summary.md for the reasoning and sensitivity)
# actionable floors, from storm_shape/summary.md (report_types on hail-dev; not re-read here)
FLOORS = {"HAIL": 1.0, "TSTM WND GST": 58.0, "TSTM WND DMG": None, "DOWNBURST": None}
START = "2020-10-14"

# ---------------- rebuild storm membership (same code as storm_shape/analyze_shape.py) ----------------
rows = []
for r in csv.DictReader(open(os.path.join(SS, "reports_raw.csv"))):
    t = dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    mag = float(r["magnitude"]) if r["magnitude"] != "" else None
    fl = FLOORS.get(r["report_text"], "n/a")
    rows.append(dict(iem_id=int(r["iem_id"]), type=r["report_text"], mag=mag, t=t.timestamp() / 60.0, utc=t, local=r["local"],
                     lat=float(r["latitude"]), lon=float(r["longitude"]), x=float(r["x"]), y=float(r["y"]),
                     act=r["report_text"] in FLOORS and (fl is None or (mag is not None and mag >= fl))))

def cluster(pts, D, T):
    n = len(pts); p = list(range(n))
    def f(i):
        while p[i] != i:
            p[i] = p[p[i]]; i = p[i]
        return i
    order = sorted(range(n), key=lambda i: pts[i]["t"])
    for a in range(n):
        i = order[a]
        for b in range(a + 1, n):
            j = order[b]
            if pts[j]["t"] - pts[i]["t"] > T: break
            if math.hypot(pts[i]["x"] - pts[j]["x"], pts[i]["y"] - pts[j]["y"]) / 1000.0 <= D:
                p[f(i)] = f(j)
    g = collections.defaultdict(list)
    for i in range(n): g[f(i)].append(pts[i])
    return list(g.values())

cls = sorted(cluster(rows, 30.0, 60.0), key=lambda c: min(r["t"] for r in c))
members = {}
for i, c in enumerate(cls, 1):
    c.sort(key=lambda r: r["t"]); members[f"A{i:05d}"] = c

# verify against storms_all.csv: every cluster's size, start and end must match
sall = {r["storm_id"]: r for r in csv.DictReader(open(os.path.join(SS, "storms_all.csv")))}
assert len(sall) == len(members), (len(sall), len(members))
bad = [k for k, c in members.items() if (str(len(c)), c[0]["local"], c[-1]["local"]) != (sall[k]["n_reports"], sall[k]["start_local"], sall[k]["end_local"])]
assert not bad, f"membership differs from storms_all.csv for {len(bad)} storms, e.g. {bad[:5]}"
print(f"membership rebuilt: {len(members)} clusters, all match storms_all.csv on n_reports/start/end")

shape = {r["storm_id"]: r for r in csv.DictReader(open(os.path.join(SS, "shape_by_storm.csv")))}
SEL = [k for k in shape if shape[k]["start_local"] >= START]
for k in SEL: assert sall[k]["status"] == "ok"
print(f"good-fit storms with start_local >= {START}: {len(SEL)}")

# reference (not in the 60): S0025 = the 2026-06-08 cluster holding the 12-month run's S0025 reports
s25 = {int(r["iem_id"]) for r in csv.DictReader(open(os.path.join(HERE, "..", "storm_tracks", "storm_reports.csv"))) if r["storm_id"] == "S0025"}
host = [k for k, c in members.items() if s25 & {r["iem_id"] for r in c}]
assert len(host) == 1 and {r["iem_id"] for r in members[host[0]]} == s25, host
REF = {f"REF_S0025_whole34": members[host[0]]}
pcs = sorted(cluster(members[host[0]], 15.0, 30.0), key=lambda c: -len(c))
assert len(pcs[0]) == 22
REF["REF_S0025_piece22"] = sorted(pcs[0], key=lambda r: r["t"])
print(f"S0025 is {host[0]} ({sall[host[0]]['status']}, {sall[host[0]]['reason']}, R2 {sall[host[0]]['fit_r2']}); run as reference only")

# ---------------- grid geometry (computed once) ----------------
LATS, LONS = mg.lat_lon_axes()
LON2, LAT2 = np.meshgrid(LONS, LATS)
# reports' x/y came from PostGIS 4269->26913; cells are WGS84 (4326). NAD83 vs WGS84 here is ~1 m, far below the 1 km cells.
GX, GY = Transformer.from_crs(4326, 26913, always_xy=True).transform(LON2, LAT2)
GX = GX.astype(np.float64); GY = GY.astype(np.float64)
Rau = 6371.0072                     # authalic radius, km
CELL_KM2 = (Rau * math.radians(mg.DD)) ** 2 * np.cos(np.radians(LAT2))   # ~0.94 km2 at 39.5 N
del LON2

def min_dist(sel, pts):
    """Distance (m) from each selected cell to the nearest point; inf if no points."""
    d = np.full(sel[0].size, np.inf)
    for p in pts:
        d = np.minimum(d, np.hypot(GX[sel] - p["x"], GY[sel] - p["y"]))
    return d

def hours_for(c):
    """Top-of-hour UTC stamps whose 60-min window (H-60, H] overlaps the storm's first-to-last report time."""
    a, b = c[0]["utc"], c[-1]["utc"]
    h = a.replace(minute=0, second=0) + (dt.timedelta(hours=1) if (a.minute or a.second) else dt.timedelta(0))
        # a report exactly at HH:00 falls in the file stamped HH:00
    end = b.replace(minute=0, second=0) + (dt.timedelta(hours=1) if (b.minute or b.second) else dt.timedelta(0))
    out = []
    while h <= end: out.append(h); h += dt.timedelta(hours=1)
    return out

_listing = {}
def stamp_for(h):
    """The file stamped exactly HH0000, else the nearest later one within 10 min (recorded as a substitution)."""
    day = h.strftime("%Y%m%d")
    if day not in _listing: _listing[day] = mg.list_day(day)
    want = h.strftime("%H%M%S")
    if want in _listing[day]: return day, want, 0
    later = [s for s in _listing[day] if want < s <= (h + dt.timedelta(minutes=10)).strftime("%H%M%S")]
    if later: return day, later[0], (int(later[0][2:4]) - int(want[2:4]))
    return day, None, None

def storm_max_grid(c):
    """Per-cell max over the storm's hourly files, plus file bookkeeping. Holds one crop at a time plus the running max."""
    mx = None; files = []
    for h in hours_for(c):
        day, st, sub = stamp_for(h)
        if st is None:
            files.append(dict(hour=h.isoformat(), file=None, status="MISSING")); continue
        crop, how = mg.cached_crop(day, st)
        files.append(dict(hour=h.isoformat(), file=f"{day}-{st}", status=how, substituted_min=sub))
        mx = crop.copy() if mx is None else np.fmax(mx, crop)
        del crop
    return mx, files

def analyse(sid, c, mx):
    act = [r for r in c if r["act"]]
    act_hail = [r for r in act if r["type"] == "HAIL"]
    # domain: cells within 30 km of any report, found inside the reports' bbox + 30 km
    x0, x1 = min(r["x"] for r in c) - DOMAIN_M, max(r["x"] for r in c) + DOMAIN_M
    y0, y1 = min(r["y"] for r in c) - DOMAIN_M, max(r["y"] for r in c) + DOMAIN_M
    box = (GX >= x0) & (GX <= x1) & (GY >= y0) & (GY <= y1)
    sel = np.nonzero(box)
    dom = min_dist(sel, c) <= DOMAIN_M
    rr, cc = sel[0][dom], sel[1][dom]
    assert rr.min() > 0 and cc.min() > 0 and rr.max() < mg.NR - 1 and cc.max() < mg.NC - 1, "30 km domain touches crop edge"
    v = np.nan_to_num(mx[rr, cc], nan=-1.0); area = CELL_KM2[rr, cc]
    dact = min_dist((rr, cc), act)
    res = dict(storm_id=sid, start_local=c[0]["local"], end_local=c[-1]["local"], n_reports=len(c), n_actionable=len(act),
               n_actionable_hail=len(act_hail), n_hail=sum(r["type"] == "HAIL" for r in c),
               max_hail_reported_in=max([r["mag"] for r in c if r["type"] == "HAIL" and r["mag"] is not None], default=None),
               centroid_lat=float(np.mean([r["lat"] for r in c])), centroid_lon=float(np.mean([r["lon"] for r in c])),
               share_reports_west_of_cutoff=sum(r["lon"] < FOOTHILLS_LON for r in c) / len(c),
               domain_km2=float(area.sum()), domain_max_mesh_mm=float(v.max()))
    for tag, th in (("100", T100), ("075", T075)):
        m = v >= th
        a = float(area[m].sum())
        res[f"mesh{tag}_km2"] = a
        res[f"mesh{tag}_in_circles_km2"] = float(area[m & (dact <= R_M)].sum())
        res[f"mesh{tag}_beyond8mi_km2"] = float(area[m & (dact > FAR_M)].sum())
        res[f"share{tag}_covered"] = res[f"mesh{tag}_in_circles_km2"] / a if a > 0 else None
        res[f"share{tag}_beyond8mi"] = res[f"mesh{tag}_beyond8mi_km2"] / a if a > 0 else None
    res["circles_km2"] = float(area[dact <= R_M].sum())      # circle area inside the domain (all of it: circles sit within 30 km of reports)
    res["precision100"] = res["mesh100_in_circles_km2"] / res["circles_km2"] if res["circles_km2"] > 0 else None
    reps = []
    for r in act_hail:
        near = np.hypot(GX[rr, cc] - r["x"], GY[rr, cc] - r["y"]) <= NEAR_M
        mm = float(v[near].max()) if near.any() else None
        reps.append(dict(iem_id=r["iem_id"], local=r["local"], lat=r["lat"], lon=r["lon"], reported_in=r["mag"],
                         max_mesh_5km_mm=mm, max_mesh_5km_in=(mm / 25.4 if mm is not None and mm > 0 else 0.0),
                         foothills=r["lon"] < FOOTHILLS_LON))
    res["reports"] = reps
    res["n_act_hail_mesh_ge100"] = sum(1 for p in reps if p["max_mesh_5km_mm"] is not None and p["max_mesh_5km_mm"] >= T100)
    res["n_act_hail_mesh_ge075"] = sum(1 for p in reps if p["max_mesh_5km_mm"] is not None and p["max_mesh_5km_mm"] >= T075)
    res["report_hit_rate100"] = res["n_act_hail_mesh_ge100"] / len(reps) if reps else None
    return res

def rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

jobs = [(k, members[k]) for k in SEL] + list(REF.items())
for n, (sid, c) in enumerate(jobs, 1):
    path = os.path.join(OUT, f"{sid}.json")
    if os.path.exists(path):
        print(f"[{n}/{len(jobs)}] {sid} done, skipped"); continue
    mx, files = storm_max_grid(c)
    if mx is None or any(f["status"] == "MISSING" for f in files):
        # loud: write nothing, so a rerun retries, and say which hour was missing
        print(f"[{n}/{len(jobs)}] {sid} INCOMPLETE: {[f for f in files if f['status'] == 'MISSING']}"); continue
    res = analyse(sid, c, mx); res["files"] = files; res["in_selection"] = sid in SEL
    with open(path + ".tmp", "w") as fh: json.dump(res, fh, indent=1)
    os.replace(path + ".tmp", path)
    sh = res["share100_covered"]
    print(f"[{n}/{len(jobs)}] {sid} {res['start_local']} files {len(files)} | MESH>=1in {res['mesh100_km2']:.0f} km2, "
          f"covered {('%.0f%%' % (100 * sh)) if sh is not None else 'n/a'} | peak RSS {rss():.0f} MB", flush=True)
    del mx; gc.collect()
print("done; peak RSS %.0f MB" % rss())
