#!/usr/bin/env python3
"""Storm-track reconstruction. Stdlib only. Throwaway analysis, not project code.
Reads reports_raw.csv (from q_extract.sql), shells out to psql for geometry (read-only SELECTs)."""
import csv, math, json, subprocess, collections, datetime as dt, statistics as st, sys

BUF_KM, D_KM, T_MIN = 50.0, 30.0, 60.0
MIN_N, MIN_SPAN = 5, 30.0
FOOT_BUF_M = 8047.0   # 5 miles, same as the zip/match radius default (decision log 2026-09-10)
PSQL = ["docker", "exec", "-i", "hail-system-postgis-1", "psql", "-U", "hail_admin",
        "-d", "weather-property", "-At", "-F", "|"]

def psql(sql):
    r = subprocess.run(PSQL, input=sql, capture_output=True, text=True)
    if r.returncode or "ERROR" in r.stderr:
        sys.exit("psql failed: " + r.stderr)
    return r.stdout

rows = []
for r in csv.DictReader(open("reports_raw.csv")):
    t = dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    rows.append(dict(
        iem_id=int(r["iem_id"]), type=r["report_text"],
        mag=float(r["magnitude"]) if r["magnitude"] != "" else None,
        t=t.timestamp() / 60.0,           # minutes since epoch (UTC)
        local=r["local"], utc=r["utc"], lat=float(r["latitude"]), lon=float(r["longitude"]),
        x=float(r["x"]) / 1000.0, y=float(r["y"]) / 1000.0, km=float(r["km_to_cov"]),
        zone="inside" if float(r["km_to_cov"]) == 0 else "buffer",
        src=r["report_source_norm"], county=r["county"]))

def cluster(pts, D, T):
    """Single-linkage: link two reports if within D km AND T minutes. Union-find."""
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
            if math.hypot(pts[i]["x"] - pts[j]["x"], pts[i]["y"] - pts[j]["y"]) <= D:
                p[f(i)] = f(j)
    g = collections.defaultdict(list)
    for i in range(n): g[f(i)].append(pts[i])
    return list(g.values())

def qualifies(c):
    return len(c) >= MIN_N and (max(r["t"] for r in c) - min(r["t"] for r in c)) >= MIN_SPAN

def counts(pts, D, T):
    cl = cluster(pts, D, T)
    return len(cl), sum(qualifies(c) for c in cl)

def sel(buf, types=None):
    return [r for r in rows if r["km"] <= buf and (types is None or r["type"] in types)]

HAIL = {"HAIL"}; WIND = {"TSTM WND GST", "TSTM WND DMG", "DOWNBURST"}
out = {}
base = sel(BUF_KM)
out["inventory"] = collections.Counter((r["type"], r["zone"] if r["km"] <= BUF_KM else "outside") for r in rows)

# --- sensitivity -------------------------------------------------------
out["sens"] = []
for D in (D_KM / 2, D_KM, D_KM * 2):
    for T in (T_MIN / 2, T_MIN, T_MIN * 2):
        n, q = counts(base, D, T); out["sens"].append((D, T, n, q))
out["sens_buf"] = [(b, len(sel(b)), *counts(sel(b), D_KM, T_MIN)) for b in (25, 50, 100, 1e9)]
out["pair"] = {k: (len(v), *counts(v, D_KM, T_MIN)) for k, v in
               {"hail+wind": base, "hail only": sel(BUF_KM, HAIL), "wind only": sel(BUF_KM, WIND)}.items()}
out["pair_sens"] = {k: [(D, T, *counts(v, D, T)) for D in (15, 30, 60) for T in (30, 60, 120)]
                    for k, v in {"hail only": sel(BUF_KM, HAIL), "hail+wind": base}.items()}

# --- storms ------------------------------------------------------------
cls = sorted(cluster(base, D_KM, T_MIN), key=lambda c: min(r["t"] for r in c))
storms, sreports = [], []
def fit(c):
    t0 = min(r["t"] for r in c)
    ts = [(r["t"] - t0) / 60.0 for r in c]           # hours
    tm = sum(ts) / len(ts); stt = sum((t - tm) ** 2 for t in ts)
    res = {}
    ssr = sst = 0.0
    for k in ("x", "y"):
        vals = [r[k] for r in c]; vm = sum(vals) / len(vals)
        slope = sum((t - tm) * (v - vm) for t, v in zip(ts, vals)) / stt
        icpt = vm - slope * tm
        ssr += sum((v - (icpt + slope * t)) ** 2 for t, v in zip(ts, vals))
        sst += sum((v - vm) ** 2 for v in vals)
        res[k] = (slope, icpt)
    vx, vy = res["x"][0], res["y"][0]
    return dict(vx=vx, vy=vy, speed=math.hypot(vx, vy),
                bearing=(math.degrees(math.atan2(vx, vy)) + 360) % 360,   # direction moving TOWARD, clockwise from N
                r2=(1 - ssr / sst) if sst > 0 else float("nan"), t0=t0, res=res,
                tend=(max(r["t"] for r in c) - t0) / 60.0)
COMP = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
def compass(b): return COMP[int(((b + 22.5) % 360) // 45)]

for i, c in enumerate(cls, 1):
    sid = f"S{i:04d}"; q = qualifies(c)
    tmin, tmax = min(r["t"] for r in c), max(r["t"] for r in c)
    first = min(c, key=lambda r: r["t"]); last = max(c, key=lambda r: r["t"])
    hs = [r["mag"] for r in c if r["type"] == "HAIL" and r["mag"] is not None]
    ws = [r["mag"] for r in c if r["type"] == "TSTM WND GST" and r["mag"] is not None]
    s = dict(storm_id=sid, qualifies=q, reports=c,
             start_local=min(c, key=lambda r: r["t"])["local"], end_local=max(c, key=lambda r: r["t"])["local"],
             dur=tmax - tmin, n=len(c),
             n_hail=sum(r["type"] == "HAIL" for r in c), n_gst=sum(r["type"] == "TSTM WND GST" for r in c),
             n_dmg=sum(r["type"] == "TSTM WND DMG" for r in c), n_dbr=sum(r["type"] == "DOWNBURST" for r in c),
             n_in=sum(r["zone"] == "inside" for r in c), n_buf=sum(r["zone"] == "buffer" for r in c),
             max_hail=max(hs) if hs else None, max_wind=max(ws) if ws else None,
             clat=sum(r["lat"] for r in c) / len(c), clon=sum(r["lon"] for r in c) / len(c),
             n_times=len({r["t"] for r in c}))
    if q:
        f = fit(c); s.update(f)
        s["compass"] = compass(f["bearing"])
        s["conf"] = "ok" if f["r2"] >= 0.5 else ("weak" if f["r2"] >= 0.25 else "poor")
    storms.append(s)
    for r in c: sreports.append((sid, r))

# --- geometry from PostGIS (read-only) ---------------------------------
vals = []
for s in storms:
    if not s["qualifies"]: continue
    pts = ",".join(f"{r['x']*1000:.1f} {r['y']*1000:.1f}" for r in s["reports"])
    f = s; res = f["res"]
    xa, ya = res["x"][1], res["y"][1]; xb, yb = xa + res["x"][0] * f["tend"], ya + res["y"][0] * f["tend"]
    tl = f"LINESTRING({xa*1000:.1f} {ya*1000:.1f},{xb*1000:.1f} {yb*1000:.1f})"
    s["track_km"] = math.hypot(xb - xa, yb - ya)
    vals.append(f"('{s['storm_id']}', ST_GeomFromText('MULTIPOINT({','.join('('+p+')' for p in pts.split(','))})',26913), ST_GeomFromText('{tl}',26913))")
sql = f"""WITH v(id,mp,tl) AS (VALUES {','.join(vals)})
SELECT id, ST_AsGeoJSON(ST_Transform(ST_Buffer(ST_ConvexHull(mp),{FOOT_BUF_M}),4326),5),
       ST_AsGeoJSON(ST_Transform(tl,4326),5),
       round((ST_Area(ST_Transform(ST_Buffer(ST_ConvexHull(mp),{FOOT_BUF_M}),4326)::geography)/1e6)::numeric,0)
FROM v ORDER BY id;"""
geo = {}
for ln in psql(sql).splitlines():
    sid, foot, trk, area = ln.split("|"); geo[sid] = (json.loads(foot), json.loads(trk), float(area))
cov_gj = psql("""WITH cov AS (SELECT ST_Union(z.geom) g FROM coverage_zips c JOIN zcta_boundaries z USING (zcta5) WHERE c.removed_at IS NULL)
SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(g,0.004),4) FROM cov;""").strip()
buf_gj = psql(f"""WITH cov AS (SELECT ST_Union(ST_Transform(z.geom,26913)) g FROM coverage_zips c JOIN zcta_boundaries z USING (zcta5) WHERE c.removed_at IS NULL)
SELECT ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(ST_Buffer(g,{BUF_KM*1000}),1500),4326),4) FROM cov;""").strip()

# --- write outputs -----------------------------------------------------
def r2(v, n=2): return "" if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, n)
with open("storms.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow("storm_id qualifies start_local end_local duration_min n_reports n_hail n_tstm_wnd_gst n_tstm_wnd_dmg n_downburst n_inside_coverage n_buffer max_hail_in max_wind_mph centroid_lat centroid_lon bearing_toward_deg compass_toward speed_kmh speed_mph fit_r2 direction_confidence track_length_km footprint_km2".split())
    for s in storms:
        q = s["qualifies"]
        w.writerow([s["storm_id"], q, s["start_local"], s["end_local"], round(s["dur"]), s["n"], s["n_hail"], s["n_gst"], s["n_dmg"], s["n_dbr"],
                    s["n_in"], s["n_buf"], s["max_hail"] if s["max_hail"] is not None else "", s["max_wind"] if s["max_wind"] is not None else "",
                    round(s["clat"], 4), round(s["clon"], 4),
                    r2(s["bearing"], 0) if q else "", s.get("compass", ""), r2(s["speed"], 1) if q else "", r2(s["speed"] * 0.621371, 1) if q else "",
                    r2(s["r2"]) if q else "", s.get("conf", ""), r2(s["track_km"], 1) if q else "", geo[s["storm_id"]][2] if q else ""])
with open("storm_reports.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow("storm_id iem_id report_text magnitude local_time_denver utc_time latitude longitude zone km_to_coverage source county".split())
    for sid, r in sorted(sreports, key=lambda a: (a[0], a[1]["t"])):
        w.writerow([sid, r["iem_id"], r["type"], r["mag"] if r["mag"] is not None else "", r["local"], r["utc"], r["lat"], r["lon"], r["zone"], r["km"], r["src"], r["county"]])
feats = []
for s in storms:
    if not s["qualifies"]: continue
    p = dict(storm_id=s["storm_id"], start_local=s["start_local"], end_local=s["end_local"], n_reports=s["n"],
             bearing_toward_deg=round(s["bearing"]), speed_kmh=round(s["speed"], 1), fit_r2=round(s["r2"], 2), max_hail_in=s["max_hail"], max_wind_mph=s["max_wind"])
    feats.append(dict(type="Feature", properties=dict(p, kind="footprint"), geometry=geo[s["storm_id"]][0]))
    feats.append(dict(type="Feature", properties=dict(p, kind="track"), geometry=geo[s["storm_id"]][1]))
json.dump(dict(type="FeatureCollection", features=feats), open("storms.geojson", "w"))

# --- map ---------------------------------------------------------------
mapdata = dict(
    cov=json.loads(cov_gj), buf=json.loads(buf_gj),
    storms=[dict(id=s["storm_id"], q=s["qualifies"], start=s["start_local"], end=s["end_local"], n=s["n"],
                 bearing=round(s["bearing"]) if s["qualifies"] else None, speed=round(s["speed"], 1) if s["qualifies"] else None,
                 r2=round(s["r2"], 2) if s["qualifies"] else None, mh=s["max_hail"], mw=s["max_wind"],
                 trk=geo[s["storm_id"]][1]["coordinates"] if s["qualifies"] else None) for s in storms],
    rep=[dict(s=sid, t=r["type"], m=r["mag"], lt=r["local"], la=r["lat"], lo=r["lon"]) for sid, r in sreports])
html = open("map_template.html").read().replace("/*DATA*/null", json.dumps(mapdata, separators=(",", ":")))
open("map.html", "w").write(html)

# --- numbers for summary.md --------------------------------------------
Q = [s for s in storms if s["qualifies"]]
def binsum(lst):
    c = collections.Counter(s["compass"] for s in lst); return [(k, c.get(k, 0)) for k in COMP]
sp = [s["speed"] for s in Q]
spbins = collections.Counter(("<20" if v < 20 else "20-40" if v < 40 else "40-60" if v < 60 else "60-90" if v < 90 else "90+") for v in sp)
res = dict(
    inventory={f"{k[0]}|{k[1]}": v for k, v in out["inventory"].items()},
    sens=out["sens"], sens_buf=out["sens_buf"], pair=out["pair"], pair_sens=out["pair_sens"],
    n_clusters=len(storms), n_q=len(Q), rose_all=binsum(Q), rose_ok=binsum([s for s in Q if s["conf"] == "ok"]),
    n_ok=sum(s["conf"] == "ok" for s in Q), n_weak=sum(s["conf"] == "weak" for s in Q), n_poor=sum(s["conf"] == "poor" for s in Q),
    spbins=dict(spbins), sp_med=st.median(sp), sp_q=st.quantiles(sp, n=4) if len(sp) > 3 else None,
    r2_med=st.median([s["r2"] for s in Q]),
    reports_in_storms=len(sreports), singletons=sum(s["n"] == 1 for s in storms))
# wind-vs-hail gap analysis on qualifying paired storms
gap = collections.Counter()
for s in Q:
    h = [r for r in s["reports"] if r["type"] == "HAIL"]; w = [r for r in s["reports"] if r["type"] != "HAIL"]
    hq = len(h) >= MIN_N and (max(r["t"] for r in h) - min(r["t"] for r in h)) >= MIN_SPAN if h else False
    gap["hail-sufficient" if hq else ("wind-only" if not h else "needs-wind")] += 1
    s["gap"] = "hail-sufficient" if hq else ("wind-only" if not h else "needs-wind")
res["gap"] = dict(gap)
top = sorted(Q, key=lambda s: -s["n"])[:5]
res["top"] = [{k: v for k, v in s.items() if k not in ("reports", "res")} | dict(
    counties=collections.Counter(r["county"] for r in s["reports"]).most_common(4),
    foot=geo[s["storm_id"]][2]) for s in top]
res["months"] = collections.Counter(s["start_local"][:7] for s in Q)
json.dump(res, open("numbers.json", "w"), indent=1, default=str)
print("done", len(storms), len(Q))
