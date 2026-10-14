"""Part B summary numbers and maps, from the partb/ checkpoints. Run from ~/hail-analysis:
    .venv/bin/python weather_sources/summarize_partb.py"""
import os, sys, json, glob, math, statistics as st, collections, datetime as dt
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

P = os.path.join(C.HERE, "partb")
def load(d): return {x["storm_id"]: x for x in (json.load(open(p)) for p in sorted(glob.glob(os.path.join(P, d, "A*.json"))))}
W, G, V = load("warnings"), load("ground"), load("winds")
assert len(W) == 60 and len(G) == 60 and len(V) == 180, (len(W), len(G), len(V))
out = {}

def q(v):
    v = sorted(v); n = len(v)
    return dict(n=n, median=round(st.median(v), 3), q1=round(v[n // 4], 3), q3=round(v[(3 * n) // 4], 3)) if n else dict(n=0)

# ---- warnings ----
Rw = list(W.values()); S = lambda k: sum(r[k] for r in Rw); M = S("mesh100_km2")
ka = [r for r in Rw if r["mesh100_km2"] >= 10]
jac = lambda a, b, i: i / (a + b - i) if (a + b - i) > 0 else 0
out["warnings"] = dict(
    storms=len(Rw), storms_with_polygon=sum(1 for r in Rw if r["n_polygons"]),
    polygons_per_storm=q([r["n_polygons"] for r in Rw]),
    pooled=dict(mesh100_km2=round(M), recall_polygons=S("mesh100_in_poly_km2") / M, recall_circles=S("mesh100_in_circle_km2") / M,
                recall_either=S("mesh100_in_either_km2") / M, poly_km2=round(S("poly_km2")), circle_km2=round(S("circle_km2")),
                precision_polygons=S("mesh100_in_poly_km2") / S("poly_km2"), precision_circles=S("mesh100_in_circle_km2") / S("circle_km2"),
                poly_share_of_domain=S("poly_km2") / S("domain_km2"),
                poly_unsupported_share=S("poly_no_mesh075_far_km2") / S("poly_km2")),
    per_storm=dict(recall_polygons=q([r["mesh100_in_poly_km2"] / r["mesh100_km2"] for r in ka]),
                   recall_circles=q([r["mesh100_in_circle_km2"] / r["mesh100_km2"] for r in ka]),
                   precision_polygons=q([r["mesh100_in_poly_km2"] / r["poly_km2"] for r in ka if r["poly_km2"]]),
                   jaccard_polygons=q([jac(r["poly_km2"], r["mesh100_km2"], r["mesh100_in_poly_km2"]) for r in ka]),
                   jaccard_circles=q([jac(r["circle_km2"], r["mesh100_km2"], r["mesh100_in_circle_km2"]) for r in ka]),
                   poly_to_circle_area=q([r["poly_km2"] / r["circle_km2"] for r in Rw if r["circle_km2"]])))
tg = [(r["max_hailtag_in"], r["max_mesh_in_poly_in"], r["max_reported_hail_in"]) for r in Rw if r["max_hailtag_in"]]
a = np.array([(x[0], x[1]) for x in tg]); b = np.array([(x[0], x[2]) for x in tg if x[2]])
out["warnings"]["hailtag"] = dict(n=len(a), corr_with_mesh_max=float(np.corrcoef(a.T)[0, 1]), corr_with_reported_max=float(np.corrcoef(b.T)[0, 1]),
                                  median_tag=float(np.median(a[:, 0])), median_mesh_max=float(np.median(a[:, 1])), median_reported_max=float(np.median(b[:, 1])))

# ---- ground (CoCoRaHS, mPING) ----
out["ground"] = {}
for src in ("cocorahs", "mping"):
    Pts = [dict(p, storm=k) for k, r in G.items() for p in r["points"] if p["src"] == src]
    sz = [p for p in Pts if p["size_in"] is not None]
    big = [p for p in sz if p["size_in"] >= 1.0]; small = [p for p in sz if p["size_in"] < 0.75]
    m1 = lambda p: p["mesh5km_mm"] >= C.T100
    xs = np.array([(p["size_in"], max(p["mesh5km_mm"], 0) / 25.4) for p in sz]) if sz else np.zeros((0, 2))
    out["ground"][src] = dict(points=len(Pts), storms_with_points=len({p["storm"] for p in Pts}), sized=len(sz), ge_1in=len(big), lt_075in=len(small),
                              ge1_with_mesh100_5km=sum(map(m1, big)), ge1_with_mesh075_5km=sum(1 for p in big if p["mesh5km_mm"] >= C.T075),
                              ge1_no_mesh100=sum(1 for p in big if not m1(p)), ge1_no_mesh100_outside_circles=sum(1 for p in big if not m1(p) and not p["in_circle"]),
                              ge1_outside_circles=sum(1 for p in big if not p["in_circle"]), ge1_outside_circles_on_mesh100=sum(1 for p in big if m1(p) and not p["in_circle"]),
                              small_with_mesh100_5km=sum(map(m1, small)), small_with_mesh100_5km_in_circles=sum(1 for p in small if m1(p) and p["in_circle"]),
                              corr_size_mesh5km=float(np.corrcoef(xs.T)[0, 1]) if len(xs) > 2 else None,
                              median_size=float(np.median(xs[:, 0])) if len(xs) else None, median_mesh5km_in=float(np.median(xs[:, 1])) if len(xs) else None)

# ---- winds ----
def ang(src, keep=lambda r: True):
    res = {}
    rows = [r for r in V.values() if r[src] and keep(r)]
    preds = sorted({k for r in rows for k in r[src]["err"]})
    for p in preds:
        e = [r[src]["err"][p] for r in rows if p in r[src]["err"]]
        res[p] = dict(n=len(e), median_abs=round(st.median([abs(x) for x in e]), 1), median_signed=round(st.median(e), 1),
                      within_30=round(sum(abs(x) <= 30 for x in e) / len(e), 3), within_45=round(sum(abs(x) <= 45 for x in e) / len(e), 3))
    return res
out["winds"] = dict(hrrr=ang("hrrr"), sounding=ang("sounding"),
                    hrrr_r2_ge_0_8=ang("hrrr", lambda r: r["r2"] >= 0.8), sounding_r2_ge_0_8=ang("sounding", lambda r: r["r2"] >= 0.8),
                    hrrr_no_chain=ang("hrrr", lambda r: r["chain_suspect"] != "True"))
both = [r for r in V.values() if r["hrrr"] and r["sounding"]]
out["winds"]["hrrr_vs_sounding"] = {p: dict(n=len(d), median_abs_diff=round(st.median([abs(x) for x in d]), 1))
                                    for p in ("500 mb", "700 mb", "mean 700-300", "Bunkers right (approx)")
                                    for d in [[(r["hrrr"]["toward"][p] - r["sounding"]["toward"][p] + 180) % 360 - 180 for r in both
                                               if p in r["hrrr"]["toward"] and p in r["sounding"]["toward"]]] if d}
out["winds"]["coverage"] = dict(hrrr=sum(1 for r in V.values() if r["hrrr"]), sounding=sum(1 for r in V.values() if r["sounding"]),
                                either=sum(1 for r in V.values() if r["hrrr"] or r["sounding"]), both=len(both))
json.dump(out, open(os.path.join(C.HERE, "partb_numbers.json"), "w"), indent=1, default=float)
print(json.dumps(out, indent=1, default=float))

# ---- maps ----
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Circle, Patch, Polygon as MPoly
from matplotlib.lines import Line2D
from pyproj import Transformer
import partb_warnings as PW
ns = C.load_members(); members = ns["members"]; Sx = C.per_storm()
tr = Transformer.from_crs(4326, 26913, always_xy=True)
polys = []
for y in sorted({W[k]["start_local"][:4] for k in W}):
    polys += PW.read_shapefile(os.path.join(C.HERE, "samples", "warnings", "partb", f"wwa_CO_SVTO_{y}.zip"))
byprod = collections.defaultdict(list)
for a_, rings in polys: byprod[a_.get("PROD_ID")].append((a_, rings))
CM = ListedColormap(["#b7d3f6", "#6da7ec", "#2a78d6", "#104281"]); NORM = BoundaryNorm([19.05, 25.4, 38.1, 50.8, 1e9], CM.N)
POLY_C = "#d9822b"              # one categorical hue for warnings; MESH keeps the blue ramp
GX, GY = C.GX / 1000, C.GY / 1000
os.makedirs(os.path.join(C.HERE, "maps"), exist_ok=True)
for k in ["A01973", "A02418", "A02164", "A02156", "A02215"]:
    d = Sx[k]; c = members[k]; mx = C.mesh_max(d)
    xs = [r["x"] / 1000 for r in c]; ys = [r["y"] / 1000 for r in c]; pad = 32
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    fig, ax = plt.subplots(figsize=(8.5, 8.5 * (y1 - y0) / (x1 - x0) + 1.1), dpi=130); ax.set_facecolor("white")
    ax.pcolormesh(GX, GY, np.where(np.nan_to_num(mx, nan=-1) >= 19.05, mx, np.nan), cmap=CM, norm=NORM, shading="nearest", zorder=1)
    # same selection rule as partb_warnings.py: polygon time overlaps the MESH window and contains a domain cell centre
    w0, w1 = C.window_utc(d); rr, cc = C.domain(c)
    dpts = np.column_stack([C.LON2[rr, cc], C.LAT2[rr, cc]])
    from matplotlib.path import Path as MPath
    for a_, rings in polys:
        b_, e_ = PW.ts(a_.get("POLY_BEG")), PW.ts(a_.get("POLY_END"))
        if not rings or b_ is None or e_ is None or not (b_ < w1 and e_ > w0): continue
        if not any(MPath(ring).contains_points(dpts).any() for ring in rings): continue
        for ring in rings:
            px, py = tr.transform(ring[:, 0], ring[:, 1])
            ax.add_patch(MPoly(np.column_stack([px / 1000, py / 1000]), closed=True, fill=False, ec=POLY_C, lw=1.0,
                               ls="-" if a_["PHENOM"] == "SV" else (0, (2, 2)), zorder=2))
    for r in c:
        if r["act"]: ax.add_patch(Circle((r["x"] / 1000, r["y"] / 1000), 8.04672, fill=False, ec="#1b1b1b", lw=1.0, zorder=3))
    for r in c:
        X, Y = r["x"] / 1000, r["y"] / 1000
        if r["type"] == "HAIL": ax.plot(X, Y, "o", ms=5.5, mfc="#1b1b1b" if r["act"] else "white", mec="#1b1b1b", mew=1, zorder=6)
        else: ax.plot(X, Y, "^", ms=6, mfc="#1b1b1b" if r["act"] else "white", mec="#1b1b1b", mew=1, zorder=6)
    for p in G[k]["points"]:
        X, Y = tr.transform(p["lon"], p["lat"]); big = (p["size_in"] or 0) >= 1.0
        mk = "s" if p["src"] == "cocorahs" else "D"
        ax.plot(X / 1000, Y / 1000, mk, ms=5 if big else 3.6, mfc="#7a3db8" if big else "white", mec="#7a3db8", mew=1, zorder=5)
    # motion arrows from the storm centroid
    cx, cy = np.mean(xs), np.mean(ys); v = V[k]; L = 18
    def arrow(deg, col, ls):
        ax.annotate("", xy=(cx + L * math.sin(math.radians(deg)), cy + L * math.cos(math.radians(deg))), xytext=(cx, cy),
                    arrowprops=dict(arrowstyle="-|>", color=col, lw=2, ls=ls), zorder=7)
    arrow(v["storm_toward"], "#1b1b1b", "-")
    if v["hrrr"]:
        if "500 mb" in v["hrrr"]["toward"]: arrow(v["hrrr"]["toward"]["500 mb"], "#2e8b57", "--")
        if "Bunkers right (approx)" in v["hrrr"]["toward"]: arrow(v["hrrr"]["toward"]["Bunkers right (approx)"], "#2e8b57", ":")
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    ax.set_xlabel("UTM 13N easting (km)", color="#55534e", fontsize=8); ax.set_ylabel("northing (km)", color="#55534e", fontsize=8)
    ax.tick_params(colors="#8a8780", labelsize=7)
    for sp in ax.spines.values(): sp.set_color("#d6d4ce")
    w = W[k]; g = G[k]; e = (v["hrrr"] or {}).get("err", {})
    ax.set_title(f"{k}  {d['start_local'][:16]} to {d['end_local'][11:16]} MT\n"
                 f"MESH ≥1 in: {w['mesh100_km2']:.0f} km² — in warnings {100 * w['mesh100_in_poly_km2'] / max(w['mesh100_km2'], 1e-9):.0f}%, in circles {100 * w['mesh100_in_circle_km2'] / max(w['mesh100_km2'], 1e-9):.0f}%   "
                 f"warnings {w['n_polygons']} (hail tag max {w['max_hailtag_in']} in)\n"
                 f"CoCoRaHS {g['n_cocorahs']}, mPING {g['n_mping']} points   storm → {v['storm_toward']:.0f}°, HRRR 500 mb error {e.get('500 mb', 'n/a')}°, Bunkers error {e.get('Bunkers right (approx)', 'n/a')}°",
                 fontsize=8.2, loc="left", color="#1b1b1b")
    h = [Patch(fc="#b7d3f6", label="MESH 0.75–1.00 in"), Patch(fc="#6da7ec", label="1.00–1.50"), Patch(fc="#2a78d6", label="1.50–2.00"), Patch(fc="#104281", label="> 2.00"),
         Line2D([], [], color=POLY_C, lw=1, label="SVR warning"), Line2D([], [], color=POLY_C, lw=1, ls=(0, (2, 2)), label="TOR warning"),
         Line2D([], [], color="#1b1b1b", lw=1, label="5 mi circle (actionable)"),
         Line2D([], [], marker="o", ls="", mfc="#1b1b1b", mec="#1b1b1b", ms=5, label="LSR hail ≥1 in"),
         Line2D([], [], marker="o", ls="", mfc="white", mec="#1b1b1b", ms=5, label="LSR hail <1 in"),
         Line2D([], [], marker="^", ls="", mfc="#1b1b1b", mec="#1b1b1b", ms=5, label="LSR wind (filled = actionable)"),
         Line2D([], [], marker="s", ls="", mfc="#7a3db8", mec="#7a3db8", ms=5, label="CoCoRaHS ≥1 in"),
         Line2D([], [], marker="s", ls="", mfc="white", mec="#7a3db8", ms=4, label="CoCoRaHS <1 in"),
         Line2D([], [], marker="D", ls="", mfc="#7a3db8", mec="#7a3db8", ms=5, label="mPING ≥1 in"),
         Line2D([], [], marker="D", ls="", mfc="white", mec="#7a3db8", ms=4, label="mPING <1 in"),
         Line2D([], [], color="#1b1b1b", lw=2, label="storm motion (fit)"), Line2D([], [], color="#2e8b57", lw=2, ls="--", label="HRRR 500 mb"),
         Line2D([], [], color="#2e8b57", lw=2, ls=":", label="Bunkers right (approx)")]
    ax.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=4, fontsize=7, frameon=False)
    fig.tight_layout(); pth = os.path.join(C.HERE, "maps", f"{k}.png"); fig.savefig(pth, facecolor="white"); plt.close(fig); print("map", pth)
    del mx
