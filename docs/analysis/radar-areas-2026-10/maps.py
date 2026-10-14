"""Maps for steps 2 and 3: tiers (core, 3 km margin, warning outer) and the HRRR-stretched fallback (L = 8 km).
Run from ~/hail-analysis: .venv/bin/python radar_areas/maps.py"""
import os, sys, json, math
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "weather_sources"))
import common as C
import partb_warnings as PW
from tiers import components, min_dist_to, ANCHOR_KM
from capsule import capsule_mask
from matplotlib.path import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch, Circle
from matplotlib.lines import Line2D
from pyproj import Transformer

ns = C.load_members(); members = ns["members"]; S = C.per_storm()
tr = Transformer.from_crs(4326, 26913, always_xy=True)
os.makedirs(os.path.join(HERE, "maps"), exist_ok=True)
# tier fills: one sequential hue (core darkest) so "more likely" reads as darker; outer is the lightest step
TIER = ListedColormap(["#e8eef7", "#9ec5f4", "#2a78d6"])
for k in ("A02164", "A02418", "A02156"):
    d = S[k]; c = members[k]; act = [r for r in c if r["act"]]; w0, w1 = C.window_utc(d)
    rr, cc = C.domain(c); xy = np.column_stack([C.GX[rr, cc], C.GY[rr, cc]])
    v = np.nan_to_num(C.mesh_max(d)[rr, cc], nan=-1.0)
    act_xy = np.array([(r["x"], r["y"]) for r in act]); dact = min_dist_to(xy, act_xy)
    mi = np.nonzero(v >= C.T100)[0]; core = np.zeros(rr.size, bool)
    for comp in components([(int(rr[i]), int(cc[i])) for i in mi]):
        cells = mi[comp]
        if (dact[cells] <= ANCHOR_KM * 1000).any(): core[cells] = True
    marg = ~core & (min_dist_to(xy, xy[core]) <= 3000)
    pts = np.column_stack([C.LON2[rr, cc], C.LAT2[rr, cc]]); inpoly = np.zeros(rr.size, bool)
    polys = PW.read_shapefile(os.path.join(C.HERE, "samples", "warnings", "partb", f"wwa_CO_SVTO_{d['start_local'][:4]}.zip"))
    for a, rings in polys:
        b, e = PW.ts(a.get("POLY_BEG")), PW.ts(a.get("POLY_END"))
        if rings and b and e and b < w1 and e > w0:
            for ring in rings: inpoly |= Path(ring).contains_points(pts)
    tier = np.full(rr.size, np.nan); tier[inpoly] = 0; tier[marg] = 1; tier[core] = 2
    grid = np.full(C.GX.shape, np.nan); grid[rr, cc] = tier
    w = json.load(open(os.path.join(C.HERE, "partb", "winds", f"{k}.json"))); rm = w["hrrr"]["toward"]["Bunkers right (approx)"]
    stretched = np.zeros(C.GX.shape, bool); stretched[rr, cc] = capsule_mask(xy, act, rm, C.R_M, 8000.0)
    xs = [r["x"] / 1000 for r in c]; ys = [r["y"] / 1000 for r in c]; pad = 32
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    fig, ax = plt.subplots(figsize=(8.5, 8.5 * (y1 - y0) / (x1 - x0) + 1.0), dpi=130); ax.set_facecolor("white")
    ax.pcolormesh(C.GX / 1000, C.GY / 1000, grid, cmap=TIER, vmin=-0.5, vmax=2.5, shading="nearest", zorder=1)
    ax.contour(C.GX / 1000, C.GY / 1000, stretched.astype(float), levels=[0.5], colors="#2e8b57", linewidths=1.3, linestyles="--", zorder=3)
    for r in act: ax.add_patch(Circle((r["x"] / 1000, r["y"] / 1000), C.R_M / 1000, fill=False, ec="#1b1b1b", lw=0.7, alpha=0.6, zorder=2))
    g = json.load(open(os.path.join(C.HERE, "partb", "ground", f"{k}.json")))["points"]
    for p in g:
        if p["src"] != "cocorahs": continue      # mPING is research-licensed (item 215): validation only, never mapped
        X, Y = tr.transform(p["lon"], p["lat"]); big = (p["size_in"] or 0) >= 1
        ax.plot(X / 1000, Y / 1000, "s", ms=4.5 if big else 3.2, mfc="#7a3db8" if big else "white", mec="#7a3db8", mew=0.9, zorder=5)
    for r in c:
        if r["type"] == "HAIL": ax.plot(r["x"] / 1000, r["y"] / 1000, "o", ms=5, mfc="#1b1b1b" if r["act"] else "white", mec="#1b1b1b", zorder=6)
    cx, cy = np.mean(xs), np.mean(ys)
    ax.annotate("", xy=(cx + 18 * math.sin(math.radians(rm)), cy + 18 * math.cos(math.radians(rm))), xytext=(cx, cy),
                arrowprops=dict(arrowstyle="-|>", color="#2e8b57", lw=2), zorder=7)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    ax.set_xlabel("UTM 13N easting (km)", color="#55534e", fontsize=8); ax.set_ylabel("northing (km)", color="#55534e", fontsize=8)
    ax.tick_params(colors="#8a8780", labelsize=7)
    for sp in ax.spines.values(): sp.set_color("#d6d4ce")
    ax.set_title(f"{k}  {d['start_local'][:16]} MT — radar-shaped tiers and the HRRR-stretched fallback\n"
                 f"core (MESH ≥1 in, anchored to a report within 5 km), 3 km margin, warning-polygon outer tier", fontsize=8.5, loc="left")
    h = [Patch(fc="#2a78d6", label="core"), Patch(fc="#9ec5f4", label="margin (3 km)"), Patch(fc="#e8eef7", ec="#c9c7c1", label="outer (warning polygons)"),
         Line2D([], [], color="#1b1b1b", lw=0.7, label="today's 5 mi circles"), Line2D([], [], color="#2e8b57", lw=1.3, ls="--", label="fallback: circles stretched ±8 km along HRRR"),
         Line2D([], [], color="#2e8b57", lw=2, label="HRRR right-mover direction"),
         Line2D([], [], marker="o", ls="", mfc="#1b1b1b", mec="#1b1b1b", ms=5, label="LSR hail ≥1 in"), Line2D([], [], marker="o", ls="", mfc="white", mec="#1b1b1b", ms=5, label="LSR hail <1 in"),
         Line2D([], [], marker="s", ls="", mfc="#7a3db8", mec="#7a3db8", ms=4.5, label="CoCoRaHS ≥1 in"), Line2D([], [], marker="s", ls="", mfc="white", mec="#7a3db8", ms=3.5, label="CoCoRaHS <1 in")]
    ax.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=3, fontsize=7, frameon=False)
    fig.tight_layout(); p = os.path.join(HERE, "maps", f"{k}.png"); fig.savefig(p, facecolor="white"); plt.close(fig); print("map", p)
