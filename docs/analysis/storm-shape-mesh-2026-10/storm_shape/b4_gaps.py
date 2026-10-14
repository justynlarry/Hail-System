#!/usr/bin/env python3
"""B4: how many stored properties sit in the GAPS of today's circle footprint, for the 180 good-fit storms. Report data only (no MESH).
Gap = corridor minus union of R-circles, where corridor = every point within R of the fitted track line between the first and last report
projections (flat ends). That bounds what gap-filling alone would add, independent of the width question.
Same clustering/fit/ok filter as analyze_shape.py (copied, not imported: that script writes files on import). Read-only PostGIS."""
import csv, math, subprocess, sys, collections, datetime as dt, statistics as st, json

D_KM, T_MIN, MIN_N, MIN_SPAN, OK_R2 = 30.0, 60.0, 5, 30.0, 0.5
PSQL = ["docker", "exec", "-i", "hail-system-postgis-1", "psql", "-U", "hail_admin", "-d", "weather-property", "-At", "-F", "|"]
def psql(sql):
    r = subprocess.run(PSQL, input=sql, capture_output=True, text=True)
    if r.returncode or "ERROR" in r.stderr: sys.exit("psql failed: " + r.stderr[:1500])
    return r.stdout
R_M = float(psql("select default_match_radius_miles from settings").strip()) * 1609.344
FLOORS = {l.split("|")[0]: (float(l.split("|")[1]) if l.split("|")[1] else None) for l in psql("select report_text, min_magnitude from report_types where roof_relevant").splitlines()}

rows = []
for r in csv.DictReader(open("reports_raw.csv")):
    t = dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc); mag = float(r["magnitude"]) if r["magnitude"] else None; fl = FLOORS.get(r["report_text"])
    rows.append(dict(type=r["report_text"], t=t.timestamp() / 60, local=r["local"], x=float(r["x"]) / 1000, y=float(r["y"]) / 1000,
                     act=r["report_text"] in FLOORS and (fl is None or (mag is not None and mag >= fl))))
def cluster(pts, D, T):
    n = len(pts); p = list(range(n))
    def f(i):
        while p[i] != i: p[i] = p[p[i]]; i = p[i]
        return i
    order = sorted(range(n), key=lambda i: pts[i]["t"])
    for a in range(n):
        i = order[a]
        for b in range(a + 1, n):
            j = order[b]
            if pts[j]["t"] - pts[i]["t"] > T: break
            if math.hypot(pts[i]["x"] - pts[j]["x"], pts[i]["y"] - pts[j]["y"]) <= D: p[f(i)] = f(j)
    g = collections.defaultdict(list)
    for i in range(n): g[f(i)].append(pts[i])
    return sorted(g.values(), key=lambda c: min(r["t"] for r in c))
def fit(c):
    t0 = min(r["t"] for r in c); ts = [(r["t"] - t0) / 60 for r in c]; n = len(c); tm = sum(ts) / n; stt = sum((t - tm) ** 2 for t in ts); ssr = sst = 0; sl = {}
    for k in ("x", "y"):
        v = [r[k] for r in c]; vm = sum(v) / n; s = sum((t - tm) * (a - vm) for t, a in zip(ts, v)) / stt; ic = vm - s * tm; sl[k] = s
        ssr += sum((a - (ic + s * t)) ** 2 for t, a in zip(ts, v)); sst += sum((a - vm) ** 2 for a in v)
    return sl["x"], sl["y"], (1 - ssr / sst) if sst > 0 else float("nan")

storms = []
for i, c in enumerate(cluster(rows, D_KM, T_MIN), 1):
    c.sort(key=lambda r: r["t"]); dur = c[-1]["t"] - c[0]["t"]
    if len(c) < MIN_N or dur < MIN_SPAN: continue
    vx, vy, r2 = fit(c)
    if not r2 >= OK_R2: continue
    sp = math.hypot(vx, vy); ux, uy = vx / sp, vy / sp; n = len(c); cx = sum(r["x"] for r in c) / n; cy = sum(r["y"] for r in c) / n
    al = [(r["x"] - cx) * ux + (r["y"] - cy) * uy for r in c]
    storms.append(dict(id=f"A{i:05d}", c=c, cx=cx, cy=cy, ux=ux, uy=uy, smin=min(al), smax=max(al), start=c[0]["local"], year=int(c[0]["local"][:4])))
print("ok storms:", len(storms), "R =", R_M, "m")
ref = {r["storm_id"]: r for r in csv.DictReader(open("shape_by_storm.csv"))}
assert {s["id"] for s in storms} == set(ref), "ok-storm set differs from Part A"

vals = []
for s in storms:
    c = s["c"]; mp = ",".join(f"({r['x']*1000:.1f} {r['y']*1000:.1f})" for r in c); act = [r for r in c if r["act"]]
    mpa = "NULL" if not act else "ST_GeomFromText('MULTIPOINT(" + ",".join(f"({r['x']*1000:.1f} {r['y']*1000:.1f})" for r in act) + ")',26913)"
    a = (s["cx"] + s["ux"] * s["smin"], s["cy"] + s["uy"] * s["smin"]); b = (s["cx"] + s["ux"] * s["smax"], s["cy"] + s["uy"] * s["smax"])
    vals.append(f"('{s['id']}', ST_GeomFromText('MULTIPOINT({mp})',26913), {mpa}, ST_GeomFromText('LINESTRING({a[0]*1000:.1f} {a[1]*1000:.1f},{b[0]*1000:.1f} {b[1]*1000:.1f})',26913))")
sql = f"""
WITH v(id, mp, mpa, ln) AS (VALUES {','.join(vals)}),
sh AS MATERIALIZED (
  SELECT id, ST_Buffer(mp, {R_M}, 'quad_segs=16') c,
         CASE WHEN mpa IS NULL THEN ST_GeomFromText('POLYGON EMPTY',26913) ELSE ST_Buffer(mpa, {R_M}, 'quad_segs=16') END a,
         ST_Buffer(ln, {R_M}, 'endcap=flat quad_segs=16') corr FROM v),
g AS MATERIALIZED (
  SELECT id, c, a, corr, ST_Difference(corr, c) gap, ST_Difference(corr, a) gap_a,
         ST_Transform(c,4326) c4, ST_Transform(a,4326) a4, ST_Transform(corr,4326) corr4,
         ST_Transform(ST_Difference(corr, c),4326) gap4, ST_Transform(ST_Difference(corr, a),4326) gap_a4 FROM sh),
pc AS MATERIALIZED (   -- every (storm, property) pair with the property inside the corridor, with membership flags
  SELECT g.id, p.rentcast_id,
         ST_Intersects(p.geom, g.c4) in_c, ST_Intersects(p.geom, g.a4) in_a,
         ST_Intersects(p.geom, g.gap4) in_gap, ST_Intersects(p.geom, g.gap_a4) in_gap_a
  FROM g JOIN properties p ON p.geom && g.corr4 AND ST_Intersects(p.geom, g.corr4)),
pcirc AS MATERIALIZED (  -- properties inside the circles anywhere (corridor or not)
  SELECT g.id, p.rentcast_id, ST_Intersects(p.geom, g.a4) in_a
  FROM g JOIN properties p ON p.geom && g.c4 AND ST_Intersects(p.geom, g.c4))
SELECT 'S', g.id, ST_Area(g.corr)/1e6, ST_Area(g.c)/1e6, ST_Area(g.gap)/1e6, ST_Area(g.gap_a)/1e6, ST_Area(g.a)/1e6,
  (SELECT count(*) FROM pc WHERE pc.id=g.id), (SELECT count(*) FROM pc WHERE pc.id=g.id AND in_gap), (SELECT count(*) FROM pc WHERE pc.id=g.id AND in_gap_a),
  (SELECT count(*) FROM pcirc WHERE pcirc.id=g.id), (SELECT count(*) FROM pcirc WHERE pcirc.id=g.id AND in_a),
  ST_NumGeometries(g.gap)
FROM g
UNION ALL
SELECT 'G', 'distinct', 0,0,0,0,0,
  (SELECT count(DISTINCT rentcast_id) FROM pc WHERE in_gap),
  (SELECT count(DISTINCT rentcast_id) FROM pc WHERE in_gap AND rentcast_id NOT IN (SELECT rentcast_id FROM pcirc)),
  (SELECT count(DISTINCT rentcast_id) FROM pc WHERE in_gap_a),
  (SELECT count(DISTINCT rentcast_id) FROM pcirc),
  (SELECT count(DISTINCT rentcast_id) FROM pc WHERE in_gap_a AND rentcast_id NOT IN (SELECT rentcast_id FROM pcirc WHERE in_a)), 0
ORDER BY 1, 2;"""
res = [l.split("|") for l in psql(sql).splitlines()]
S = [r for r in res if r[0] == "S"]; G = [r for r in res if r[0] == "G"][0]
with open("gap_fill.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow("storm_id era start_local n_reports corridor_km2 circle_km2 gap_km2 gap_share_of_corridor circle_actionable_km2 gap_actionable_km2 gap_parts props_in_corridor props_in_gap props_in_gap_actionable props_in_circle props_in_circle_actionable".split())
    for r in S:
        sid = r[1]; s = next(x for x in storms if x["id"] == sid); era = "2004-2012" if s["year"] <= 2012 else ("2013-2019" if s["year"] <= 2019 else "2020-present")
        corr, circ, gap, gapa, circa = (float(x) for x in r[2:7])
        w.writerow([sid, era, s["start"], len(s["c"]), round(corr, 1), round(circ, 1), round(gap, 1), round(gap / corr, 3) if corr else "", round(circa, 1), round(gapa, 1), r[12],
                    r[7], r[8], r[9], r[10], r[11]])
rows_g = list(csv.DictReader(open("gap_fill.csv")))
f = lambda k: [float(x[k]) for x in rows_g]; ii = lambda k: [int(x[k]) for x in rows_g]
num = dict(
    n=len(rows_g), R_m=R_M,
    corridor_km2=sum(f("corridor_km2")), gap_km2=sum(f("gap_km2")), gap_act_km2=sum(f("gap_actionable_km2")), circle_km2=sum(f("circle_km2")), circle_act_km2=sum(f("circle_actionable_km2")),
    gap_share_median=st.median(f("gap_share_of_corridor")), gap_share_q=st.quantiles(f("gap_share_of_corridor"), n=4),
    props_corridor=sum(ii("props_in_corridor")), props_gap=sum(ii("props_in_gap")), props_gap_act=sum(ii("props_in_gap_actionable")),
    props_circle=sum(ii("props_in_circle")), props_circle_act=sum(ii("props_in_circle_actionable")),
    storms_gap_gt0=sum(1 for x in ii("props_in_gap") if x > 0), storms_gap_act_gt0=sum(1 for x in ii("props_in_gap_actionable") if x > 0),
    gap_per_storm_median=st.median(ii("props_in_gap")), gap_per_storm_q=st.quantiles(ii("props_in_gap"), n=4), gap_act_per_storm_median=st.median(ii("props_in_gap_actionable")),
    gap_vs_circle_ratio_median=st.median([int(x["props_in_gap"]) / int(x["props_in_circle"]) for x in rows_g if int(x["props_in_circle"]) > 0]),
    storms_with_no_props_anywhere=sum(1 for x in rows_g if int(x["props_in_corridor"]) == 0 and int(x["props_in_circle"]) == 0),
    gap_parts_dist=dict(collections.Counter(min(int(x["gap_parts"]), 5) for x in rows_g)),
    distinct=dict(gap=int(G[7]), gap_never_in_any_circle=int(G[8]), gap_act=int(G[9]), in_any_circle=int(G[10]), gap_act_never_in_any_actionable_circle=int(G[11])))
by_era = {}
for e in ("2004-2012", "2013-2019", "2020-present"):
    sub = [x for x in rows_g if x["era"] == e]
    by_era[e] = dict(n=len(sub), props_gap=sum(int(x["props_in_gap"]) for x in sub), props_circle=sum(int(x["props_in_circle"]) for x in sub), gap_km2=sum(float(x["gap_km2"]) for x in sub))
num["by_era"] = by_era
json.dump(num, open("b4_numbers.json", "w"), indent=1, default=str)
print(json.dumps(num, indent=1, default=str))
