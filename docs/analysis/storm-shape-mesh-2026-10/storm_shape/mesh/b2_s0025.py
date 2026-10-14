#!/usr/bin/env python3
"""B2: S0025 (2026-06-08) against MRMS MESH. Throwaway analysis. Stdlib only; geometry via read-only PostGIS SELECTs.
Needs the crops written by fetch_window.py. Two report sets: PIECE (22-report west cell) and WHOLE (34 reports)."""
import csv, json, math, os, struct, subprocess, sys, collections, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SCR = "/tmp/claude-1000/-home-hail-user-hail-system/161af0a4-2bd0-4c46-bcb9-1b5f0cd7675f/scratchpad"
CROPS = f"{SCR}/mesh_crops"
LAT_MAX, LAT_MIN, LON_MIN, LON_MAX, D = 42.0, 37.5, -107.0, -102.0, 0.01
NR, NC = 451, 501
R_M = 8046.72; EXT_KM = 5.0; THRESH_MM = {"1.00in": 25.4, "0.75in": 19.05}
PSQL = ["docker", "exec", "-i", "hail-system-postgis-1", "psql", "-U", "hail_admin", "-d", "weather-property", "-At"]
def psql(sql):
    r = subprocess.run(PSQL, input=sql, capture_output=True, text=True)
    if r.returncode or "ERROR" in r.stderr: sys.exit("psql failed: " + r.stderr[:1500])
    return r.stdout

# ---- reports ----
ids = {int(r["iem_id"]) for r in csv.DictReader(open("../../storm_tracks/storm_reports.csv")) if r["storm_id"] == "S0025"}
FL = {"HAIL": 1.0, "TSTM WND GST": 58.0, "TSTM WND DMG": None, "DOWNBURST": None}
reps = []
for r in csv.DictReader(open("../reports_raw.csv")):
    if int(r["iem_id"]) in ids:
        t = dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        mag = float(r["magnitude"]) if r["magnitude"] else None
        reps.append(dict(id=int(r["iem_id"]), type=r["report_text"], mag=mag, t=t, tm=t.timestamp() / 60, lat=float(r["latitude"]), lon=float(r["longitude"]),
                         x=float(r["x"]), y=float(r["y"]), act=(FL[r["report_text"]] is None) or (mag is not None and mag >= FL[r["report_text"]]), local=r["local"]))
assert len(reps) == 34, len(reps)
def cluster(pts, Dk, T):
    n = len(pts); p = list(range(n))
    def f(i):
        while p[i] != i: p[i] = p[p[i]]; i = p[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if abs(pts[i]["tm"] - pts[j]["tm"]) <= T and math.hypot(pts[i]["x"] - pts[j]["x"], pts[i]["y"] - pts[j]["y"]) / 1000 <= Dk: p[f(i)] = f(j)
    g = collections.defaultdict(list)
    for i in range(n): g[f(i)].append(pts[i])
    return sorted(g.values(), key=lambda c: -len(c))
PIECE = cluster(reps, 15, 30)[0]
assert len(PIECE) == 22, len(PIECE)
SETS = {"piece22": PIECE, "whole34": reps}

# ---- fit and axis (same method as Part A) ----
def fit_axis(c):
    t0 = min(r["tm"] for r in c); ts = [(r["tm"] - t0) / 60 for r in c]; n = len(c)
    tm = sum(ts) / n; stt = sum((t - tm) ** 2 for t in ts); ssr = sst = 0; sl = {}
    for k in ("x", "y"):
        v = [r[k] / 1000 for r in c]; vm = sum(v) / n; s = sum((t - tm) * (a - vm) for t, a in zip(ts, v)) / stt; ic = vm - s * tm; sl[k] = s
        ssr += sum((a - (ic + s * t)) ** 2 for t, a in zip(ts, v)); sst += sum((a - vm) ** 2 for a in v)
    vx, vy = sl["x"], sl["y"]; sp = math.hypot(vx, vy); ux, uy = vx / sp, vy / sp
    cx, cy = sum(r["x"] for r in c) / n / 1000, sum(r["y"] for r in c) / n / 1000
    al = [(r["x"] / 1000 - cx) * ux + (r["y"] / 1000 - cy) * uy for r in c]; cr = [-(r["x"] / 1000 - cx) * uy + (r["y"] / 1000 - cy) * ux for r in c]
    v = sorted(abs(a) for a in cr); k = (n - 1) * .9; f = int(k); p90 = v[f] + (v[min(f + 1, n - 1)] - v[f]) * (k - f)
    return dict(bearing=(math.degrees(math.atan2(vx, vy)) + 360) % 360, speed=sp, r2=1 - ssr / sst, cx=cx, cy=cy, ux=ux, uy=uy, smin=min(al), smax=max(al), p90=p90, length=max(al) - min(al))

def pca_axis(c, f):
    """Spatial major axis (needs no time fit). Used because the piece's time fit is meaningless (R2 ~0.05); sign chosen to match the time-fit direction."""
    n = len(c); cx, cy = f["cx"], f["cy"]
    xs = [r["x"] / 1000 - cx for r in c]; ys = [r["y"] / 1000 - cy for r in c]
    cxx = sum(a * a for a in xs) / (n - 1); cyy = sum(b * b for b in ys) / (n - 1); cxy = sum(a * b for a, b in zip(xs, ys)) / (n - 1)
    th = 0.5 * math.atan2(2 * cxy, cxx - cyy); ux, uy = math.cos(th), math.sin(th)
    if ux * f["ux"] + uy * f["uy"] < 0: ux, uy = -ux, -uy
    al = [a * ux + b * uy for a, b in zip(xs, ys)]; cr = [-a * uy + b * ux for a, b in zip(xs, ys)]
    v = sorted(abs(a) for a in cr); k = (n - 1) * .9; fl = int(k); p90 = v[fl] + (v[min(fl + 1, n - 1)] - v[fl]) * (k - fl)
    g = dict(f); g.update(ux=ux, uy=uy, smin=min(al), smax=max(al), p90=p90, length=max(al) - min(al), bearing=(math.degrees(math.atan2(ux, uy)) + 360) % 360)
    mid = (cxx + cyy) / 2; rad = math.sqrt(((cxx - cyy) / 2) ** 2 + cxy ** 2); g["pca_elong"] = math.sqrt(mid + rad) / math.sqrt(max(mid - rad, 1e-12))
    return g

# ---- MESH max grid over a window ----
def mesh_max(t_from, t_to):
    mx = [0] * (NR * NC); n = 0; names = []
    for fn in sorted(os.listdir(CROPS)):
        day, hm = fn[:-4].split("-"); t = dt.datetime.strptime(day + hm, "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        if t_from <= t <= t_to:
            v = struct.unpack(">%dH" % (NR * NC), open(f"{CROPS}/{fn}", "rb").read()); n += 1; names.append(fn)
            mx = [a if a > b else b for a, b in zip(mx, v)]
    return mx, n, names
def cell_center(i):    # index -> (lat, lon)
    r, c = divmod(i, NC); return LAT_MAX + 0.005 - 0.01 * (r + 0.5) + 0.0, LON_MIN + 0.005 + 0.01 * c - 0.0

def cell_lat_lon(i):
    r, c = divmod(i, NC)
    return 42.005 - 0.01 * r - 0.01 * 0 - 0.0, -106.995 + 0.01 * c       # pixel centers (crop top row = 42.005 per mesh_lib.lat_top)

out = {}
for name, c in [(f"{n}|{m}", s_) for n, s_ in SETS.items() for m in ("pca", "time")]:
    nm, mode = name.split("|")
    f = fit_axis(c)
    if mode == "pca": f = pca_axis(c, f)
    t0 = min(r["t"] for r in c) - dt.timedelta(minutes=20); t1 = max(r["t"] for r in c) + dt.timedelta(minutes=20)
    mx, nfiles, names = mesh_max(t0, t1)
    # sanity: crop top-left center must be (42.005, -106.995); mesh_lib.read_crop returned lat_top=42.005 lon_left=-106.995
    cells = {k: [i for i, v in enumerate(mx) if v >= th * 10] for k, th in THRESH_MM.items()}
    def vals(k):
        rows = []
        for i in cells[k]:
            la, lo = cell_lat_lon(i); rows.append(f"({lo-0.005:.4f},{la-0.005:.4f},{lo+0.005:.4f},{la+0.005:.4f})")
        return ",".join(rows) if rows else "(0,0,0,0)"
    mp = ",".join(f"({r['x']:.1f} {r['y']:.1f})" for r in c)
    act = [r for r in c if r["act"]]; mpa = ",".join(f"({r['x']:.1f} {r['y']:.1f})" for r in act)
    ex = EXT_KM
    A = (f["cx"] + f["ux"] * (f["smin"] - ex), f["cy"] + f["uy"] * (f["smin"] - ex)); B = (f["cx"] + f["ux"] * (f["smax"] + ex), f["cy"] + f["uy"] * (f["smax"] + ex))
    A0 = (f["cx"] + f["ux"] * f["smin"], f["cy"] + f["uy"] * f["smin"]); B0 = (f["cx"] + f["ux"] * f["smax"], f["cy"] + f["uy"] * f["smax"])
    ls = lambda a, b: f"LINESTRING({a[0]*1000:.1f} {a[1]*1000:.1f},{b[0]*1000:.1f} {b[1]*1000:.1f})"
    def mesh_sql(k):
        return f"ST_Transform(ST_Union(ARRAY(SELECT ST_MakeEnvelope(a,b,c,d,4326) FROM (VALUES {vals(k)}) t(a,b,c,d))),26913)"
    sql = f"""
WITH s AS MATERIALIZED (SELECT
  ST_Buffer(ST_GeomFromText('MULTIPOINT({mp})',26913), {R_M}, 'quad_segs=16') c,
  ST_Buffer(ST_GeomFromText('MULTIPOINT({mpa})',26913), {R_M}, 'quad_segs=16') a,
  ST_Buffer(ST_GeomFromText('{ls(A,B)}',26913), {f['p90']*1000:.1f}, 'quad_segs=16') k,
  ST_Buffer(ST_GeomFromText('{ls(A,B)}',26913), {f['p90']*1000 + R_M:.1f}, 'quad_segs=16') w,
  ST_Buffer(ST_GeomFromText('{ls(A0,B0)}',26913), {R_M}, 'endcap=flat quad_segs=16') corr,
  ST_Buffer(ST_GeomFromText('MULTIPOINT({mp})',26913), 30000) dom),
mm AS MATERIALIZED (SELECT s.*, ST_Intersection({mesh_sql('1.00in')}, s.dom) m25, ST_Intersection({mesh_sql('0.75in')}, s.dom) m19 FROM s),
g AS MATERIALIZED (SELECT *, ST_Difference(corr, c) gap, ST_Difference(corr, a) gap_a FROM mm)
SELECT json_build_object(
 'area', json_build_object('circle',ST_Area(c)/1e6,'circle_act',ST_Area(a)/1e6,'cap_p90',ST_Area(k)/1e6,'cap_wide',ST_Area(w)/1e6,'corridor',ST_Area(corr)/1e6,
        'gap',ST_Area(gap)/1e6,'gap_act',ST_Area(gap_a)/1e6,'mesh25',ST_Area(m25)/1e6,'mesh19',ST_Area(m19)/1e6,'domain',ST_Area(dom)/1e6),
 'mesh25_in', json_build_object('circle',ST_Area(ST_Intersection(m25,c))/1e6,'circle_act',ST_Area(ST_Intersection(m25,a))/1e6,'cap_p90',ST_Area(ST_Intersection(m25,k))/1e6,
        'cap_wide',ST_Area(ST_Intersection(m25,w))/1e6,'corridor',ST_Area(ST_Intersection(m25,corr))/1e6,'gap',ST_Area(ST_Intersection(m25,gap))/1e6,'gap_act',ST_Area(ST_Intersection(m25,gap_a))/1e6),
 'mesh19_in', json_build_object('circle',ST_Area(ST_Intersection(m19,c))/1e6,'cap_p90',ST_Area(ST_Intersection(m19,k))/1e6,'cap_wide',ST_Area(ST_Intersection(m19,w))/1e6,
        'corridor',ST_Area(ST_Intersection(m19,corr))/1e6,'gap',ST_Area(ST_Intersection(m19,gap))/1e6),
 'gap_parts', ST_NumGeometries(gap), 'circle_parts', ST_NumGeometries(c),
 'wkt', json_build_object('circle',ST_AsText(ST_Transform(c,4326),5),'circle_act',ST_AsText(ST_Transform(a,4326),5),'cap_p90',ST_AsText(ST_Transform(k,4326),5),
        'cap_wide',ST_AsText(ST_Transform(w,4326),5),'gap',ST_AsText(ST_Transform(gap,4326),5),'corridor',ST_AsText(ST_Transform(corr,4326),5),'domain',ST_AsText(ST_Transform(dom,4326),5)))
FROM g;"""
    res = json.loads(psql(sql).strip())
    # per-report MESH check: max MESH within 3x3 cells (~1 km) and within ~5 km of each report
    def local_max(lat, lon, rad):
        r = int(round((42.005 - lat) / 0.01)); cc = int(round((lon + 106.995) / 0.01)); m = 0
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                rr, c2 = r + dr, cc + dc
                if 0 <= rr < NR and 0 <= c2 < NC: m = max(m, mx[rr * NC + c2])
        return m / 10
    rep_rows = [dict(id=r["id"], type=r["type"], mag=r["mag"], local=r["local"], lat=r["lat"], lon=r["lon"], act=r["act"], mesh_1km_mm=local_max(r["lat"], r["lon"], 1), mesh_5km_mm=local_max(r["lat"], r["lon"], 5)) for r in c]
    res.update(name=name, mode=mode, n=len(c), n_act=len(act), fit={k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.items()}, window_utc=[t0.isoformat(), t1.isoformat()],
               n_files=nfiles, n_cells_25=len(cells["1.00in"]), n_cells_19=len(cells["0.75in"]), max_mesh_mm=max(mx) / 10, reports=rep_rows,
               cells25=[cell_lat_lon(i) + (mx[i] / 10,) for i in cells["1.00in"]], cells19=[cell_lat_lon(i) + (mx[i] / 10,) for i in cells["0.75in"]])
    # gap hail cells: list MESH>=25.4 cells that sit in the gap region (point-in-gap test in PostGIS)
    if res["cells25"] or res["cells19"]:
        pts = ",".join(f"({lo:.4f} {la:.4f})" for la, lo, v in (res["cells19"] or res["cells25"]))
    out[name] = res
    # keep the full max grid for plotting
    open(f"{nm}_meshmax.bin", "wb").write(struct.pack(">%dH" % (NR * NC), *mx))
json.dump(out, open("b2_numbers.json", "w"), default=str)
for k, v in out.items():
    print(k, v["n"], "reports; files", v["n_files"], "; fit", v["fit"]); print("  area", {a: round(b, 1) for a, b in v["area"].items()})
    print("  mesh>=1in inside", {a: round(b, 1) for a, b in v["mesh25_in"].items()}); print("  mesh>=0.75in inside", {a: round(b, 1) for a, b in v["mesh19_in"].items()})
    print("  cells>=1in", v["n_cells_25"], "max mm", v["max_mesh_mm"], "gap parts", v["gap_parts"], "circle parts", v["circle_parts"])
