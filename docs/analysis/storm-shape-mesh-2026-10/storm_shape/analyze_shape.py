#!/usr/bin/env python3
"""Part A: storm shape, all years. Stdlib only. Throwaway analysis, not project code.
Method reused from ../storm_tracks/analyze.py (same types, UTC->Denver, UTM 13N, single-linkage 30 km / 60 min).
Geometry comes from PostGIS via read-only SELECTs (no temp tables, nothing written to the DB)."""
import csv, math, json, subprocess, collections, datetime as dt, statistics as st, sys, os

D_KM, T_MIN, MIN_N, MIN_SPAN, OK_R2 = 30.0, 60.0, 5, 30.0, 0.5
EXT_KM = float(os.environ.get("EXT_KM", 5.0))        # capsule extension past first/last report (explicit parameter)
PSQL = ["docker", "exec", "-i", "hail-system-postgis-1", "psql", "-U", "hail_admin", "-d", "weather-property", "-At", "-F", "|"]

def psql(sql):
    r = subprocess.run(PSQL, input=sql, capture_output=True, text=True)
    if r.returncode or "ERROR" in r.stderr:
        sys.exit("psql failed: " + r.stderr[:2000])
    return r.stdout

# live radius + floors, read from the DB (settings table is the source of truth, not tuning.py)
R_MI = float(psql("select default_match_radius_miles from settings").strip())
R_M = R_MI * 1609.344
FLOORS = {l.split("|")[0]: (float(l.split("|")[1]) if l.split("|")[1] else None)
          for l in psql("select report_text, min_magnitude from report_types where roof_relevant").splitlines()}
print("R =", R_MI, "mi =", R_M, "m; floors:", {k: v for k, v in FLOORS.items() if k in ("HAIL", "TSTM WND GST", "TSTM WND DMG", "DOWNBURST")})

rows = []
for r in csv.DictReader(open("reports_raw.csv")):
    t = dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    mag = float(r["magnitude"]) if r["magnitude"] != "" else None
    fl = FLOORS.get(r["report_text"])
    rows.append(dict(iem_id=int(r["iem_id"]), type=r["report_text"], mag=mag, t=t.timestamp() / 60.0, local=r["local"], utc=r["utc"],
                     lat=float(r["latitude"]), lon=float(r["longitude"]), x=float(r["x"]) / 1000.0, y=float(r["y"]) / 1000.0,
                     zone=r["zone"], src=r["report_source_norm"], county=r["county"],
                     # same test as matcher.py: roof_relevant AND (no floor OR magnitude >= floor); NULL mag fails a floor
                     act=r["report_text"] in FLOORS and (fl is None or (mag is not None and mag >= fl))))
print("reports:", len(rows))

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
            if math.hypot(pts[i]["x"] - pts[j]["x"], pts[i]["y"] - pts[j]["y"]) <= D:
                p[f(i)] = f(j)
    g = collections.defaultdict(list)
    for i in range(n): g[f(i)].append(pts[i])
    return list(g.values())

def span(c): return max(r["t"] for r in c) - min(r["t"] for r in c)
def storm_like(c): return len(c) >= MIN_N and span(c) >= MIN_SPAN

def fit(c):
    t0 = min(r["t"] for r in c)
    ts = [(r["t"] - t0) / 60.0 for r in c]
    tm = sum(ts) / len(ts); stt = sum((t - tm) ** 2 for t in ts)
    ssr = sst = 0.0; sl = {}
    for k in ("x", "y"):
        vals = [r[k] for r in c]; vm = sum(vals) / len(vals)
        s = sum((t - tm) * (v - vm) for t, v in zip(ts, vals)) / stt
        ic = vm - s * tm; sl[k] = s
        ssr += sum((v - (ic + s * t)) ** 2 for t, v in zip(ts, vals)); sst += sum((v - vm) ** 2 for v in vals)
    vx, vy = sl["x"], sl["y"]
    return dict(vx=vx, vy=vy, speed=math.hypot(vx, vy), bearing=(math.degrees(math.atan2(vx, vy)) + 360) % 360,
                r2=(1 - ssr / sst) if sst > 0 else float("nan"))

def era(year): return "2004-2012" if year <= 2012 else ("2013-2019" if year <= 2019 else "2020-present")
COMP = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
def compass(b): return COMP[int(((b + 22.5) % 360) // 45)]

# ---------------- A1: cluster all years ----------------
cls = sorted(cluster(rows, D_KM, T_MIN), key=lambda c: min(r["t"] for r in c))
storms = []
for i, c in enumerate(cls, 1):
    c.sort(key=lambda r: r["t"])
    s = dict(storm_id=f"A{i:05d}", reports=c, n=len(c), dur=span(c), start=c[0]["local"], end=c[-1]["local"],
             year=int(c[0]["local"][:4]), n_hail=sum(r["type"] == "HAIL" for r in c),
             n_wind=sum(r["type"] != "HAIL" for r in c), n_in=sum(r["zone"] == "inside" for r in c))
    s["era"] = era(s["year"])
    hs = [r["mag"] for r in c if r["type"] == "HAIL" and r["mag"] is not None]
    ws = [r["mag"] for r in c if r["type"] == "TSTM WND GST" and r["mag"] is not None]
    s["max_hail"], s["max_wind"] = (max(hs) if hs else None), (max(ws) if ws else None)
    s["clat"], s["clon"] = sum(r["lat"] for r in c) / len(c), sum(r["lon"] for r in c) / len(c)
    s["n_times"] = len({r["t"] for r in c})
    if len(c) < MIN_N: s["status"], s["reason"] = "excluded", "too_few_reports"
    elif s["dur"] < MIN_SPAN: s["status"], s["reason"] = "excluded", "too_short"
    else:
        s.update(fit(c))
        if s["r2"] >= OK_R2: s["status"], s["reason"] = "ok", ""
        elif s["r2"] >= 0.25: s["status"], s["reason"] = "excluded", "fit_weak"
        else: s["status"], s["reason"] = "excluded", "fit_poor"
    storms.append(s)
OK = [s for s in storms if s["status"] == "ok"]
print("clusters", len(storms), "storm-like", sum(1 for s in storms if s["dur"] >= MIN_SPAN and s["n"] >= MIN_N), "ok", len(OK))

# chaining check: re-cluster each storm-like cluster ALONE at half thresholds
chain = []
for s in storms:
    if not (s["n"] >= MIN_N and s["dur"] >= MIN_SPAN): continue
    pieces = cluster(s["reports"], D_KM / 2, T_MIN / 2)
    big = [p for p in pieces if len(p) >= MIN_N]
    big_q = [p for p in pieces if storm_like(p)]
    s["pieces"], s["pieces_ge5"], s["pieces_q"] = len(pieces), len(big), len(big_q)
    s["chain_suspect"] = len(big) >= 2           # >=2 pieces that are each storm-sized: probably separate cells merged
    chain.append(s)
with open("chaining_check.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow("storm_id start_local n_reports status pieces_at_half pieces_ge5_reports pieces_storm_like chain_suspect piece_sizes".split())
    for s in chain:
        sizes = sorted((len(p) for p in cluster(s["reports"], D_KM / 2, T_MIN / 2)), reverse=True)
        w.writerow([s["storm_id"], s["start"], s["n"], s["status"], s["pieces"], s["pieces_ge5"], s["pieces_q"], s["chain_suspect"], " ".join(map(str, sizes[:8]))])

# S0025 (2026-06-08) from the 12-month run: find its cluster here by report id
s25 = {int(r["iem_id"]) for r in csv.DictReader(open("../storm_tracks/storm_reports.csv")) if r["storm_id"] == "S0025"}
host = [s for s in storms if s25 & {r["iem_id"] for r in s["reports"]}]
s25info = []
for h in host:
    ids = {r["iem_id"] for r in h["reports"]}
    pcs = cluster(h["reports"], D_KM / 2, T_MIN / 2)
    s25info.append(dict(storm_id=h["storm_id"], n=h["n"], overlap_with_S0025=len(s25 & ids), S0025_size=len(s25),
                        pieces=[dict(n=len(p), start=min(p, key=lambda r: r["t"])["local"], end=max(p, key=lambda r: r["t"])["local"],
                                     clat=round(sum(r["lat"] for r in p) / len(p), 3), clon=round(sum(r["lon"] for r in p) / len(p), 3),
                                     n_hail=sum(r["type"] == "HAIL" for r in p)) for p in sorted(pcs, key=lambda p: -len(p))]))
# also S0025 alone (exactly its 34 reports), as the brief asked
alone = [r for h in host for r in h["reports"] if r["iem_id"] in s25]
pcs = cluster(alone, D_KM / 2, T_MIN / 2)
s25_alone = [dict(n=len(p), start=min(p, key=lambda r: r["t"])["local"], end=max(p, key=lambda r: r["t"])["local"],
                  clat=round(sum(r["lat"] for r in p) / len(p), 3), clon=round(sum(r["lon"] for r in p) / len(p), 3),
                  hail=sum(r["type"] == "HAIL" for r in p)) for p in sorted(pcs, key=lambda p: -len(p))]

# ---------------- A3: shape ----------------
def pct(vals, q):          # linear interpolation percentile
    v = sorted(vals); k = (len(v) - 1) * q; f = int(k); c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)
def angdiff(a, b):         # axes (mod 180)
    d = abs(a - b) % 180; return min(d, 180 - d)
for s in OK:
    c = s["reports"]; n = len(c)
    cx, cy = sum(r["x"] for r in c) / n, sum(r["y"] for r in c) / n      # OLS line passes through the mean position
    sp = s["speed"]; ux, uy = s["vx"] / sp, s["vy"] / sp; nx, ny = -uy, ux
    al = [(r["x"] - cx) * ux + (r["y"] - cy) * uy for r in c]
    cr = [(r["x"] - cx) * nx + (r["y"] - cy) * ny for r in c]
    s.update(cx=cx, cy=cy, ux=ux, uy=uy, smin=min(al), smax=max(al),
             length=max(al) - min(al), sd_along=st.stdev(al), sd_cross=st.stdev(cr), p90_cross=pct([abs(v) for v in cr], 0.9),
             cross_ext=max(cr) - min(cr))
    s["aspect_ext"] = s["length"] / (2 * s["p90_cross"]) if s["p90_cross"] > 0 else float("nan")
    s["aspect_sd"] = s["sd_along"] / s["sd_cross"] if s["sd_cross"] > 0 else float("nan")
    cxx = sum((r["x"] - cx) ** 2 for r in c) / (n - 1); cyy = sum((r["y"] - cy) ** 2 for r in c) / (n - 1)
    cxy = sum((r["x"] - cx) * (r["y"] - cy) for r in c) / (n - 1)
    mid = (cxx + cyy) / 2; rad = math.sqrt(((cxx - cyy) / 2) ** 2 + cxy ** 2)
    l1, l2 = mid + rad, max(mid - rad, 1e-12)
    th = 0.5 * math.atan2(2 * cxy, cxx - cyy)                 # major axis angle from +x (east)
    s["pca_bearing_axis"] = (90 - math.degrees(th)) % 180
    s["pca_elong"] = math.sqrt(l1 / l2)
    s["axis_diff"] = angdiff(s["bearing"] % 180, s["pca_bearing_axis"])
    s["axis_disagree"] = s["axis_diff"] > 30 and s["pca_elong"] >= 1.5   # PCA axis only meaningful if the cloud is actually elongated
    s["compass"] = compass(s["bearing"])
    s["wind_share"] = s["n_wind"] / n

# ---------------- outputs: storms_all / excluded ----------------
def rr(v, n=2): return "" if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, n)
HDR = "storm_id era start_local end_local duration_min n_reports n_hail n_wind n_inside_coverage max_hail_in max_wind_mph centroid_lat centroid_lon status reason bearing_toward_deg speed_kmh fit_r2 pieces_at_half_thresholds chain_suspect".split()
def srow(s):
    return [s["storm_id"], s["era"], s["start"], s["end"], round(s["dur"]), s["n"], s["n_hail"], s["n_wind"], s["n_in"],
            s["max_hail"] if s["max_hail"] is not None else "", s["max_wind"] if s["max_wind"] is not None else "",
            round(s["clat"], 4), round(s["clon"], 4), s["status"], s["reason"],
            rr(s.get("bearing"), 0), rr(s.get("speed"), 1), rr(s.get("r2")), s.get("pieces", ""), s.get("chain_suspect", "")]
with open("storms_all.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(HDR); [w.writerow(srow(s)) for s in storms]
with open("excluded_storms.csv", "w", newline="") as fh:   # storm-sized clusters only; the <5-report fragments are in storms_all.csv
    w = csv.writer(fh); w.writerow(HDR); [w.writerow(srow(s)) for s in storms if s["status"] == "excluded" and s["n"] >= MIN_N]
reason_counts = collections.Counter(s["reason"] for s in storms if s["status"] == "excluded")
reason_by_era = collections.Counter((s["era"], s["reason"] or "ok") for s in storms)
with open("shape_by_storm.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow("storm_id era start_local n_reports n_hail n_wind duration_min bearing_toward_deg speed_kmh fit_r2 along_length_km sd_along_km sd_cross_km p90_cross_km cross_extent_km aspect_extent aspect_sd pca_bearing_axis pca_elongation axis_diff_deg axis_disagree chain_suspect max_hail_in".split())
    for s in OK:
        w.writerow([s["storm_id"], s["era"], s["start"], s["n"], s["n_hail"], s["n_wind"], round(s["dur"]), round(s["bearing"]), rr(s["speed"], 1), rr(s["r2"]),
                    rr(s["length"], 1), rr(s["sd_along"], 1), rr(s["sd_cross"], 1), rr(s["p90_cross"], 1), rr(s["cross_ext"], 1), rr(s["aspect_ext"]), rr(s["aspect_sd"]),
                    round(s["pca_bearing_axis"]), rr(s["pca_elong"]), rr(s["axis_diff"], 0), s["axis_disagree"], s.get("chain_suspect", ""), s["max_hail"] if s["max_hail"] is not None else ""])

# ---------------- A4: eras ----------------
def kw(groups):
    allv = sorted((v, gi) for gi, g in enumerate(groups) for v in g)
    N = len(allv); ranks = [0.0] * N; i = 0; ties = 0
    while i < N:
        j = i
        while j + 1 < N and allv[j + 1][0] == allv[i][0]: j += 1
        for k in range(i, j + 1): ranks[k] = (i + j) / 2 + 1
        t = j - i + 1; ties += t ** 3 - t; i = j + 1
    rs = collections.defaultdict(float)
    for (v, gi), rk in zip(allv, ranks): rs[gi] += rk
    H = 12 / (N * (N + 1)) * sum(rs[g] ** 2 / len(groups[g]) for g in range(len(groups))) - 3 * (N + 1)
    H /= (1 - ties / (N ** 3 - N)) if N > 1 else 1
    return H, math.exp(-H / 2)       # chi-square sf for df=2 (3 groups)
ERAS = ["2004-2012", "2013-2019", "2020-present"]
METRICS = [("n_reports", "n"), ("duration (min)", "dur"), ("along length (km)", "length"), ("cross SD (km)", "sd_cross"),
           ("cross p90 (km)", "p90_cross"), ("aspect (length / 2*p90)", "aspect_ext"), ("aspect (SD along / SD cross)", "aspect_sd"),
           ("PCA elongation", "pca_elong"), ("wind share of reports", "wind_share")]
def q(vals): return f"{pct(vals,.5):.1f} [{pct(vals,.25):.1f}, {pct(vals,.75):.1f}]" if len(vals) >= 2 else "n/a"
era_n = {e: [s for s in OK if s["era"] == e] for e in ERAS}
yrs = {"2004-2012": 9, "2013-2019": 7, "2020-present": 6.78}   # 2020-01-01 to 2026-10-12
eras_all = {e: [s for s in storms if s["era"] == e] for e in ERAS}
rep_by_era = {e: sum(s["n"] for s in eras_all[e]) for e in ERAS}
md = ["# Shape by era", "", "Local start year decides the era. `ok` = at least 5 reports, at least 30 min, linear-fit R² >= 0.5. Cells are median [Q1, Q3].", "",
      "## Volume", "", "| Era | years | analysed reports | reports/yr | clusters | storm-sized (>=5 rep, >=30 min) | ok fits | ok share of storm-sized | median reports/ok storm |", "|---|---|---|---|---|---|---|---|---|"]
for e in ERAS:
    sl = [s for s in eras_all[e] if s["n"] >= MIN_N and s["dur"] >= MIN_SPAN]
    md.append(f"| {e} | {yrs[e]:.1f} | {rep_by_era[e]} | {rep_by_era[e]/yrs[e]:.0f} | {len(eras_all[e])} | {len(sl)} | {len(era_n[e])} | {len(era_n[e])/max(len(sl),1):.0%} | {st.median([s['n'] for s in era_n[e]]):.0f} |")
md += ["", "## Shape distributions per era (ok-fit storms)", "", "| Metric | " + " | ".join(f"{e} (n={len(era_n[e])})" for e in ERAS) + " | Kruskal-Wallis H | p |", "|---|---|---|---|---|---|"]
kwres = {}
for label, key in METRICS:
    gs = [[s[key] for s in era_n[e] if not (isinstance(s[key], float) and math.isnan(s[key]))] for e in ERAS]
    H, p = kw(gs); kwres[label] = (H, p)
    md.append(f"| {label} | " + " | ".join(q(g) for g in gs) + f" | {H:.1f} | {p:.3f} |")
# storm size confound: aspect/length depend on report count. Compare eras within n-bins.
md += ["", "## Report-count confound (aspect and length grow with the number of reports)", "",
       "Spearman-style check without scipy: median aspect (length / 2*p90) by report-count bin, pooled and by era.", "",
       "| reports | pooled n | pooled median aspect | " + " | ".join(f"{e} n / median" for e in ERAS) + " |", "|---|---|---|---|---|---|"]
for lo, hi in ((5, 7), (8, 12), (13, 25), (26, 10 ** 6)):
    sel = [s for s in OK if lo <= s["n"] <= hi]
    cells = []
    for e in ERAS:
        x = [s["aspect_ext"] for s in sel if s["era"] == e and not math.isnan(s["aspect_ext"])]
        cells.append(f"{len(x)} / {pct(x,.5):.2f}" if x else "0 / n/a")
    lab = f"{lo}-{hi}" if hi < 10 ** 6 else f"{lo}+"
    allx = [s["aspect_ext"] for s in sel if not math.isnan(s["aspect_ext"])]
    md.append(f"| {lab} | {len(allx)} | {pct(allx,.5):.2f} | " + " | ".join(cells) + " |")
open("shape_by_era.md", "w").write("\n".join(md) + "\n")

# ---------------- A5: circle vs capsule (PostGIS) ----------------
def a5(ext_km, props):
    vals = []
    for s in OK:
        c = s["reports"]
        mp = ",".join(f"({r['x']*1000:.1f} {r['y']*1000:.1f})" for r in c)
        act = [r for r in c if r["act"]]
        mpa = "NULL" if not act else "ST_GeomFromText('MULTIPOINT(" + ",".join(f"({r['x']*1000:.1f} {r['y']*1000:.1f})" for r in act) + ")',26913)"
        a = (s["cx"] + s["ux"] * (s["smin"] - ext_km), s["cy"] + s["uy"] * (s["smin"] - ext_km))
        b = (s["cx"] + s["ux"] * (s["smax"] + ext_km), s["cy"] + s["uy"] * (s["smax"] + ext_km))
        ln = f"LINESTRING({a[0]*1000:.1f} {a[1]*1000:.1f},{b[0]*1000:.1f} {b[1]*1000:.1f})"
        vals.append(f"('{s['storm_id']}', ST_GeomFromText('MULTIPOINT({mp})',26913), {mpa}, ST_GeomFromText('{ln}',26913), {s['p90_cross']*1000:.1f})")
    prop_cols = """
 , count(p.*) FILTER (WHERE ST_Intersects(p.geom, c4)) pc
 , count(p.*) FILTER (WHERE ST_Intersects(p.geom, k4)) pk
 , count(p.*) FILTER (WHERE ST_Intersects(p.geom, w4)) pw
 , count(p.*) FILTER (WHERE ST_Intersects(p.geom, c4) AND ST_Intersects(p.geom, k4)) pck
 , count(p.*) FILTER (WHERE ST_Intersects(p.geom, c4) AND ST_Intersects(p.geom, w4)) pcw
 , count(p.*) FILTER (WHERE ST_Intersects(p.geom, a4)) pa""" if props else ", 0,0,0,0,0,0"
    join = "LEFT JOIN properties p ON p.geom && ST_Envelope(ST_Collect(ARRAY[c4,k4,w4]))" if False else ""
    sql = f"""
WITH v(id, mp, mpa, ln, hw) AS (VALUES {','.join(vals)}),
sh AS MATERIALIZED (
  SELECT id,
    ST_Buffer(mp, {R_M}, 'quad_segs=16') c,
    CASE WHEN mpa IS NULL THEN NULL ELSE ST_Buffer(mpa, {R_M}, 'quad_segs=16') END a,
    ST_Buffer(ln, hw, 'quad_segs=16') k,
    ST_Buffer(ln, hw + {R_M}, 'quad_segs=16') w
  FROM v),
m AS MATERIALIZED (
  SELECT id, c, a, k, w,
    ST_Area(c)/1e6 ac, COALESCE(ST_Area(a),0)/1e6 aa, ST_Area(k)/1e6 ak, ST_Area(w)/1e6 aw,
    ST_Area(ST_Intersection(c,k))/1e6 ick, ST_Area(ST_Intersection(c,w))/1e6 icw,
    ST_Transform(c,4326) c4, ST_Transform(k,4326) k4, ST_Transform(w,4326) w4,
    COALESCE(ST_Transform(a,4326), ST_GeomFromText('POLYGON EMPTY',4326)) a4
  FROM sh)
SELECT id, ac, aa, ak, aw, ick, icw {('''
 , (SELECT count(*) FROM properties p WHERE p.geom && c4 AND ST_Intersects(p.geom,c4))
 , (SELECT count(*) FROM properties p WHERE p.geom && k4 AND ST_Intersects(p.geom,k4))
 , (SELECT count(*) FROM properties p WHERE p.geom && w4 AND ST_Intersects(p.geom,w4))
 , (SELECT count(*) FROM properties p WHERE p.geom && k4 AND ST_Intersects(p.geom,c4) AND ST_Intersects(p.geom,k4))
 , (SELECT count(*) FROM properties p WHERE p.geom && w4 AND ST_Intersects(p.geom,c4) AND ST_Intersects(p.geom,w4))
 , (SELECT count(*) FROM properties p WHERE p.geom && a4 AND ST_Intersects(p.geom,a4))''' if props else ', 0,0,0,0,0,0')}, ST_NumGeometries(c)
FROM m ORDER BY id;"""
    out = {}
    for ln in psql(sql).splitlines():
        f = ln.split("|"); out[f[0]] = [float(x) for x in f[1:]]
    return out

main = a5(EXT_KM, True)
sens = {e: a5(e, False) for e in (0.0, 10.0)}
with open("circle_vs_capsule.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(("storm_id era start_local n_reports along_length_km p90_cross_km ext_km "
                "area_circle_km2 area_circle_actionable_km2 "
                "area_capsule_p90_km2 overlap_p90_km2 circle_only_p90_km2 capsule_only_p90_km2 jaccard_p90 "
                "area_capsule_wide_km2 overlap_wide_km2 circle_only_wide_km2 capsule_only_wide_km2 jaccard_wide "
                "props_circle props_circle_actionable props_capsule_p90 props_both_p90 props_circle_only_p90 props_capsule_only_p90 "
                "props_capsule_wide props_both_wide props_circle_only_wide props_capsule_only_wide circle_parts").split())
    for s in OK:
        ac, aa, ak, aw, ick, icw, pc, pk, pw, pck, pcw, pa, parts = main[s["storm_id"]]
        w.writerow([s["storm_id"], s["era"], s["start"], s["n"], rr(s["length"], 1), rr(s["p90_cross"], 1), EXT_KM,
                    rr(ac, 1), rr(aa, 1), rr(ak, 1), rr(ick, 1), rr(ac - ick, 1), rr(ak - ick, 1), rr(ick / (ac + ak - ick), 3),
                    rr(aw, 1), rr(icw, 1), rr(ac - icw, 1), rr(aw - icw, 1), rr(icw / (ac + aw - icw), 3),
                    int(pc), int(pa), int(pk), int(pck), int(pc - pck), int(pk - pck), int(pw), int(pcw), int(pc - pcw), int(pw - pcw), int(parts)])

# footprints geojson: circle + capsules per storm, simplified 100 m
vals = []
for s in OK:
    c = s["reports"]; mp = ",".join(f"({r['x']*1000:.1f} {r['y']*1000:.1f})" for r in c)
    a = (s["cx"] + s["ux"] * (s["smin"] - EXT_KM), s["cy"] + s["uy"] * (s["smin"] - EXT_KM)); b = (s["cx"] + s["ux"] * (s["smax"] + EXT_KM), s["cy"] + s["uy"] * (s["smax"] + EXT_KM))
    vals.append(f"('{s['storm_id']}', ST_GeomFromText('MULTIPOINT({mp})',26913), ST_GeomFromText('LINESTRING({a[0]*1000:.1f} {a[1]*1000:.1f},{b[0]*1000:.1f} {b[1]*1000:.1f})',26913), {s['p90_cross']*1000:.1f})")
sql = f"""WITH v(id,mp,ln,hw) AS (VALUES {','.join(vals)})
SELECT id, 'circle', ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(ST_Buffer(mp,{R_M},'quad_segs=16'),100),4326),5) FROM v
UNION ALL SELECT id, 'capsule_p90', ST_AsGeoJSON(ST_Transform(ST_Buffer(ln,hw,'quad_segs=16'),4326),5) FROM v
UNION ALL SELECT id, 'capsule_wide', ST_AsGeoJSON(ST_Transform(ST_Buffer(ln,hw+{R_M},'quad_segs=16'),4326),5) FROM v ORDER BY 1,2;"""
byid = {s["storm_id"]: s for s in OK}
feats = []
for ln in psql(sql).splitlines():
    sid, kind, gj = ln.split("|", 2); s = byid[sid]
    feats.append(dict(type="Feature", geometry=json.loads(gj), properties=dict(
        storm_id=sid, shape=kind, start_local=s["start"], n_reports=s["n"], bearing_toward_deg=round(s["bearing"]), along_km=round(s["length"], 1),
        p90_cross_km=round(s["p90_cross"], 1), ext_km=EXT_KM, radius_m=round(R_M, 2))))
json.dump(dict(type="FeatureCollection", features=feats), open("footprints.geojson", "w"))

# ---------------- numbers for the summary ----------------
def dist(vals): return dict(n=len(vals), median=pct(vals, .5), q1=pct(vals, .25), q3=pct(vals, .75), p10=pct(vals, .1), p90=pct(vals, .9), min=min(vals), max=max(vals)) if vals else {}
fin = lambda k, S=None: [s[k] for s in (S or OK) if not (isinstance(s[k], float) and math.isnan(s[k]))]

# ---------------- controls: is the elongation real, or made by selection + clustering? ----------------
import random
random.seed(20261014)
def axes_for(c, vx, vy):
    n = len(c); cx, cy = sum(r["x"] for r in c) / n, sum(r["y"] for r in c) / n
    sp = math.hypot(vx, vy)
    if sp == 0: return None
    ux, uy = vx / sp, vy / sp
    al = [(r["x"] - cx) * ux + (r["y"] - cy) * uy for r in c]; cr = [-(r["x"] - cx) * uy + (r["y"] - cy) * ux for r in c]
    p90 = pct([abs(v) for v in cr], .9)
    return (max(al) - min(al)), p90
def pca_el(xs, ys):
    n = len(xs); mx, my = sum(xs) / n, sum(ys) / n
    cxx = sum((a - mx) ** 2 for a in xs) / (n - 1); cyy = sum((b - my) ** 2 for b in ys) / (n - 1); cxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n - 1)
    mid = (cxx + cyy) / 2; rad = math.sqrt(((cxx - cyy) / 2) ** 2 + cxy ** 2)
    return math.sqrt((mid + rad) / max(mid - rad, 1e-12))
null_pass = null_tot = 0; null_asp = []; null_asp_all = []
SL = [s for s in storms if s["n"] >= MIN_N and s["dur"] >= MIN_SPAN]     # null runs over ALL storm-sized clusters, not just the ones that passed
for s in SL:
    c = s["reports"]
    for _ in range(200):
        ts = [r["t"] for r in c]; random.shuffle(ts)
        cc = [dict(r, t=t) for r, t in zip(c, ts)]
        if len({r["t"] for r in cc}) < 2: continue
        f = fit(cc); null_tot += 1
        res = axes_for(cc, f["vx"], f["vy"])
        if res and res[1] > 0:
            null_asp_all.append(res[0] / (2 * res[1]))
            if f["r2"] >= OK_R2:
                null_pass += 1; null_asp.append(res[0] / (2 * res[1]))
gauss_el = []
for s in OK:
    for _ in range(100):
        xs = [random.gauss(0, 1) for _ in range(s["n"])]; ys = [random.gauss(0, 1) for _ in range(s["n"])]
        gauss_el.append(pca_el(xs, ys))
# axis agreement on the storms the fit filter threw out (time axis unreliable there)
exc = []
for s in storms:
    if s["status"] == "excluded" and s["reason"] in ("fit_weak", "fit_poor"):
        n = len(s["reports"]); c = s["reports"]; cx = sum(r["x"] for r in c) / n; cy = sum(r["y"] for r in c) / n
        cxx = sum((r["x"] - cx) ** 2 for r in c) / (n - 1); cyy = sum((r["y"] - cy) ** 2 for r in c) / (n - 1); cxy = sum((r["x"] - cx) * (r["y"] - cy) for r in c) / (n - 1)
        th = 0.5 * math.atan2(2 * cxy, cxx - cyy); pb = (90 - math.degrees(th)) % 180
        exc.append((s["reason"], angdiff(s["bearing"] % 180, pb), pca_el([r["x"] for r in c], [r["y"] for r in c])))
CTL = dict(
    null_perms=null_tot, null_clusters=len(SL), observed_pass_rate=len(OK)/len(SL), null_pass_rate=null_pass / max(null_tot, 1), null_aspect_ext_given_pass=dist(null_asp) if null_asp else {},
    null_aspect_ext_all=dist(null_asp_all), gauss_pca_elong=dist(gauss_el),
    excluded_axis_diff={k: dist([e[1] for e in exc if e[0] == k]) for k in ("fit_weak", "fit_poor")},
    excluded_axis_gt30={k: sum(1 for e in exc if e[0] == k and e[1] > 30 and e[2] >= 1.5) for k in ("fit_weak", "fit_poor")},
    excluded_pca_elong=dist([e[2] for e in exc]),
    p90_lt_1km=sum(1 for s in OK if s["p90_cross"] < 1.0), p90_lt_2km=sum(1 for s in OK if s["p90_cross"] < 2.0),
    ok_n5=sum(1 for s in OK if s["n"] == 5),
    ok_ge8_aspect=dist([s["aspect_ext"] for s in OK if s["n"] >= 8]), ok_ge8_pca=dist([s["pca_elong"] for s in OK if s["n"] >= 8]),
    ok_ge8_n=sum(1 for s in OK if s["n"] >= 8))
rows_cc = list(csv.DictReader(open("circle_vs_capsule.csv")))
fl = lambda k: [float(r[k]) for r in rows_cc]
num = dict(
    controls=CTL, R_mi=R_MI, R_m=R_M, EXT_KM=EXT_KM, n_reports=len(rows), n_inside=sum(r["zone"] == "inside" for r in rows), n_clusters=len(storms),
    n_storm_like=sum(1 for s in storms if s["n"] >= MIN_N and s["dur"] >= MIN_SPAN), n_ok=len(OK), reasons=dict(reason_counts),
    reasons_ge5=dict(collections.Counter(s["reason"] for s in storms if s["status"] == "excluded" and s["n"] >= MIN_N)),
    chain_total=len(chain), chain_split=sum(s["pieces"] > 1 for s in chain), chain_suspect=sum(s["chain_suspect"] for s in chain),
    chain_ok=sum(1 for s in OK if s.get("chain_suspect")),
    chain_pieces_dist=dict(collections.Counter(min(s["pieces"], 6) for s in chain)),
    s25_host=s25info, s25_alone=s25_alone,
    shape=dict(length=dist(fin("length")), sd_cross=dist(fin("sd_cross")), p90=dist(fin("p90_cross")), aspect_ext=dist(fin("aspect_ext")),
               aspect_sd=dist(fin("aspect_sd")), pca_elong=dist(fin("pca_elong")), axis_diff=dist(fin("axis_diff")), n=dist(fin("n"))),
    shape_nochain=dict(length=dist(fin("length", [s for s in OK if not s.get("chain_suspect")])), p90=dist(fin("p90_cross", [s for s in OK if not s.get("chain_suspect")])),
                       aspect_ext=dist(fin("aspect_ext", [s for s in OK if not s.get("chain_suspect")])), n=len([s for s in OK if not s.get("chain_suspect")])),
    frac_aspect_gt = {str(t): sum(1 for s in OK if s["aspect_ext"] > t) / len(OK) for t in (1.0, 1.5, 2.0, 3.0)},
    n_disagree=sum(s["axis_disagree"] for s in OK), disagree=[(s["storm_id"], s["start"], s["n"], round(s["axis_diff"]), round(s["pca_elong"], 2), round(s["r2"], 2)) for s in OK if s["axis_disagree"]][:40],
    axis_diff_gt30=sum(s["axis_diff"] > 30 for s in OK), axis_diff_gt45=sum(s["axis_diff"] > 45 for s in OK),
    kw={k: (round(v[0], 2), round(v[1], 4)) for k, v in kwres.items()},
    area=dict(circle=dist(fl("area_circle_km2")), cap_p90=dist(fl("area_capsule_p90_km2")), cap_wide=dist(fl("area_capsule_wide_km2")), circ_act=dist(fl("area_circle_actionable_km2")),
              jac_p90=dist(fl("jaccard_p90")), jac_wide=dist(fl("jaccard_wide")),
              tot_circle=sum(fl("area_circle_km2")), tot_p90=sum(fl("area_capsule_p90_km2")), tot_wide=sum(fl("area_capsule_wide_km2")),
              tot_ov_p90=sum(fl("overlap_p90_km2")), tot_ov_wide=sum(fl("overlap_wide_km2")),
              circ_only_p90=sum(fl("circle_only_p90_km2")), cap_only_p90=sum(fl("capsule_only_p90_km2")),
              circ_only_wide=sum(fl("circle_only_wide_km2")), cap_only_wide=sum(fl("capsule_only_wide_km2"))),
    props=dict(storms_with_any=sum(1 for r in rows_cc if int(r["props_circle"]) + int(r["props_capsule_wide"]) + int(r["props_capsule_p90"]) > 0),
               tot_circle=sum(int(r["props_circle"]) for r in rows_cc), tot_circle_act=sum(int(r["props_circle_actionable"]) for r in rows_cc),
               tot_p90=sum(int(r["props_capsule_p90"]) for r in rows_cc), tot_wide=sum(int(r["props_capsule_wide"]) for r in rows_cc),
               both_p90=sum(int(r["props_both_p90"]) for r in rows_cc), both_wide=sum(int(r["props_both_wide"]) for r in rows_cc),
               circ_only_p90=sum(int(r["props_circle_only_p90"]) for r in rows_cc), cap_only_p90=sum(int(r["props_capsule_only_p90"]) for r in rows_cc),
               circ_only_wide=sum(int(r["props_circle_only_wide"]) for r in rows_cc), cap_only_wide=sum(int(r["props_capsule_only_wide"]) for r in rows_cc)),
    ext_sens={str(e): dict(tot_cap_p90=sum(v[2] for v in d.values()), tot_cap_wide=sum(v[3] for v in d.values()),
                            ov_p90=sum(v[4] for v in d.values()), ov_wide=sum(v[5] for v in d.values()), tot_circle=sum(v[0] for v in d.values()))
              for e, d in {**sens, EXT_KM: {k: v[:7] for k, v in main.items()}}.items()},
    top_aspect=[(s["storm_id"], s["start"], s["n"], round(s["length"], 1), round(s["p90_cross"], 1), round(s["aspect_ext"], 1), round(s["r2"], 2)) for s in sorted(OK, key=lambda s: -s["aspect_ext"])[:8]],
    bearing_rose=dict(collections.Counter(s["compass"] for s in OK)), speed=dist(fin("speed")), r2=dist(fin("r2")),
    wind_share_era={e: round(sum(s["n_wind"] for s in eras_all[e]) / max(rep_by_era[e], 1), 3) for e in ERAS})
json.dump(num, open("numbers.json", "w"), indent=1, default=str)
print("done; ok storms", len(OK))
