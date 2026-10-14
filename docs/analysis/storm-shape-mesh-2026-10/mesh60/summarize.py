"""Steps 6-7: tables, summary numbers and maps from mesh60/per_storm/*.json. Recomputes nothing from MESH except the map grids
(read from the crop cache, one storm at a time). Run from ~/hail-analysis: .venv/bin/python mesh60/summarize.py"""
import csv, glob, json, os, sys, math, statistics as st
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
S = {d["storm_id"]: d for d in (json.load(open(p)) for p in glob.glob(os.path.join(HERE, "per_storm", "*.json")))}
SEL = sorted(k for k, d in S.items() if d["in_selection"])
REF = ["REF_S0025_whole34", "REF_S0025_piece22"]
assert len(SEL) == 60, len(SEL)
MIN_AREA = 10.0          # km2: storms with less MESH >= 1.00 in than this are not ranked on coverage (one or two cells)
CUTOFFS = (-104.9, -105.0, -105.1)
MAIN_CUT = -105.0

def pct(v, q):
    v = sorted(v); k = (len(v) - 1) * q; f = int(k); c = min(f + 1, len(v) - 1); return v[f] + (v[c] - v[f]) * (k - f)
def med(v): return f"{100 * pct(v, .5):.0f}% [{100 * pct(v, .25):.0f}, {100 * pct(v, .75):.0f}]" if v else "n/a"
def zone(d, cut=MAIN_CUT): return "foothills" if d["centroid_lon"] < cut else "plains"

# ---------------- tables ----------------
COLS = ["storm_id", "in_selection", "start_local", "end_local", "n_reports", "n_actionable", "n_actionable_hail", "max_hail_reported_in",
        "centroid_lat", "centroid_lon", "zone_lon_-105.0", "n_files", "domain_km2", "domain_max_mesh_mm",
        "mesh100_km2", "mesh100_in_circles_km2", "share100_covered", "mesh100_beyond8mi_km2", "share100_beyond8mi",
        "mesh075_km2", "share075_covered", "share075_beyond8mi", "circles_km2", "precision100",
        "n_act_hail_mesh_ge100", "n_act_hail_mesh_ge075", "report_hit_rate100", "rank_worst_coverage"]
rankable = sorted([k for k in SEL if S[k]["mesh100_km2"] >= MIN_AREA], key=lambda k: (S[k]["share100_covered"], -S[k]["mesh100_km2"]))
rank = {k: i + 1 for i, k in enumerate(rankable)}
def r4(v): return "" if v is None else (round(v, 4) if isinstance(v, float) else v)
with open(os.path.join(HERE, "per_storm.csv"), "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(COLS)
    for k in SEL + REF:
        d = S[k]
        w.writerow([r4(d.get(c)) for c in COLS[:10]] + [zone(d), len(d["files"])] + [r4(d.get(c)) for c in COLS[12:-1]] + [rank.get(k, "")])
with open(os.path.join(HERE, "per_report.csv"), "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["storm_id", "iem_id", "local", "lat", "lon", "reported_in", "max_mesh_5km_mm", "max_mesh_5km_in",
                                    "mesh_ge_1.00in", "mesh_ge_0.75in", "foothills_lon_-105.0"])
    for k in SEL + REF:
        for p in S[k]["reports"]:
            mm = p["max_mesh_5km_mm"]
            w.writerow([k, p["iem_id"], p["local"], p["lat"], p["lon"], p["reported_in"], r4(mm), round(p["max_mesh_5km_in"], 3),
                        mm is not None and mm >= 25.4, mm is not None and mm >= 19.05, p["foothills"]])

# ---------------- numbers ----------------
def block(keys):
    ks = [k for k in keys if S[k]["mesh100_km2"] > 0]
    ka = [k for k in keys if S[k]["mesh100_km2"] >= MIN_AREA]
    A = sum(S[k]["mesh100_km2"] for k in keys)
    reps = [p for k in keys for p in S[k]["reports"]]
    return dict(
        storms=len(keys), storms_mesh100_zero=sum(1 for k in keys if S[k]["mesh100_km2"] == 0),
        storms_mesh100_ge10=len(ka),
        mesh100_km2_total=round(A), covered_pooled=(sum(S[k]["mesh100_in_circles_km2"] for k in keys) / A) if A else None,
        beyond8_pooled=(sum(S[k]["mesh100_beyond8mi_km2"] for k in keys) / A) if A else None,
        covered_median_iqr=med([S[k]["share100_covered"] for k in ka]), beyond8_median_iqr=med([S[k]["share100_beyond8mi"] for k in ka]),
        storms_covered_0=sum(1 for k in ka if S[k]["share100_covered"] == 0),
        storms_covered_lt25=sum(1 for k in ka if S[k]["share100_covered"] < .25),
        storms_beyond8_gt50=sum(1 for k in ka if S[k]["share100_beyond8mi"] > .5),
        covered075_pooled=(sum(S[k]["mesh075_in_circles_km2"] for k in keys) / sum(S[k]["mesh075_km2"] for k in keys)) if sum(S[k]["mesh075_km2"] for k in keys) else None,
        precision100_median=med([S[k]["precision100"] for k in keys if S[k]["precision100"] is not None]),
        act_hail_reports=len(reps), reports_mesh_ge100=sum(1 for p in reps if (p["max_mesh_5km_mm"] or -1) >= 25.4),
        reports_mesh_ge075=sum(1 for p in reps if (p["max_mesh_5km_mm"] or -1) >= 19.05),
        reports_mesh_none=sum(1 for p in reps if (p["max_mesh_5km_mm"] or -1) <= 0),
        report_mesh_median_in=round(st.median([p["max_mesh_5km_in"] for p in reps]), 2) if reps else None,
        report_reported_median_in=round(st.median([p["reported_in"] for p in reps]), 2) if reps else None)

out = dict(overall=block(SEL), by_zone={}, by_report_lon={}, ref={})
for cut in CUTOFFS:
    out["by_zone"][str(cut)] = {z: block([k for k in SEL if zone(S[k], cut) == z]) for z in ("foothills", "plains")}
    reps = [p for k in SEL for p in S[k]["reports"]]
    out["by_report_lon"][str(cut)] = {z: dict(n=len(g), mesh_ge100=sum(1 for p in g if (p["max_mesh_5km_mm"] or -1) >= 25.4),
                                              mesh_ge075=sum(1 for p in g if (p["max_mesh_5km_mm"] or -1) >= 19.05),
                                              median_mesh_in=round(st.median([p["max_mesh_5km_in"] for p in g]), 2) if g else None)
                                      for z, g in (("west (foothills)", [p for p in reps if p["lon"] < cut]), ("east (plains)", [p for p in reps if p["lon"] >= cut]))}
cov = sorted(S[k]["share100_covered"] for k in rankable); b8 = sorted(S[k]["share100_beyond8mi"] for k in rankable)
for k in REF:
    d = S[k]
    out["ref"][k] = dict(mesh100_km2=round(d["mesh100_km2"], 1), covered=d["share100_covered"], beyond8=d["share100_beyond8mi"],
                         n_files=len(d["files"]), act_hail=len(d["reports"]), act_hail_mesh_ge100=d["n_act_hail_mesh_ge100"],
                         storms_with_lower_or_equal_coverage=sum(1 for v in cov if v <= d["share100_covered"]),
                         storms_with_higher_or_equal_beyond8=sum(1 for v in b8 if v >= d["share100_beyond8mi"]), of=len(cov))
out["worst5"] = [dict(storm_id=k, start=S[k]["start_local"], mesh100_km2=round(S[k]["mesh100_km2"]), covered=round(S[k]["share100_covered"], 3),
                      beyond8=round(S[k]["share100_beyond8mi"], 3), zone=zone(S[k]), centroid=(round(S[k]["centroid_lat"], 3), round(S[k]["centroid_lon"], 3)),
                      n_act=S[k]["n_actionable"], n_act_hail=S[k]["n_actionable_hail"], hail_hits=f"{S[k]['n_act_hail_mesh_ge100']}/{S[k]['n_actionable_hail']}")
                 for k in rankable[:5]]
out["zero_mesh_storms"] = [dict(storm_id=k, start=S[k]["start_local"], n_act_hail=S[k]["n_actionable_hail"], max_hail_in=S[k]["max_hail_reported_in"],
                                domain_max_mesh_in=round(S[k]["domain_max_mesh_mm"] / 25.4, 2), zone=zone(S[k]))
                           for k in SEL if S[k]["mesh100_km2"] < MIN_AREA]
out["no_actionable_storms"] = [k for k in SEL if S[k]["n_actionable"] == 0]
json.dump(out, open(os.path.join(HERE, "summary_numbers.json"), "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str))

# ---------------- maps: 5 worst coverage ----------------
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Circle, Patch
from matplotlib.lines import Line2D
from pyproj import Transformer
import meshgrid as mg
LATS, LONS = mg.lat_lon_axes(); LON2, LAT2 = np.meshgrid(LONS, LATS)
GX, GY = Transformer.from_crs(4326, 26913, always_xy=True).transform(LON2, LAT2); GX /= 1000; GY /= 1000
to_utm = Transformer.from_crs(4326, 26913, always_xy=True)
rows = {}
for r in csv.DictReader(open(os.path.join(HERE, "..", "storm_shape", "reports_raw.csv"))): rows[int(r["iem_id"])] = r
# storm membership is not in the json (only actionable hail is); rebuild it the same way run_storms.py does, quietly
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()):
    sys.argv = ["x"]; import importlib.util
    spec = importlib.util.spec_from_file_location("rs_members", os.path.join(HERE, "run_storms.py"))
    src = open(os.path.join(HERE, "run_storms.py")).read().split("# ---------------- grid geometry")[0]
    ns = {"__file__": os.path.join(HERE, "run_storms.py")}; exec(compile(src, "run_storms_members", "exec"), ns)
members = ns["members"]

BOUNDS = [19.05, 25.4, 38.1, 50.8, 1e9]
CMAP = ListedColormap(["#b7d3f6", "#6da7ec", "#2a78d6", "#104281"]); NORM = BoundaryNorm(BOUNDS, CMAP.N)   # one-hue sequential, light->dark
os.makedirs(os.path.join(HERE, "maps"), exist_ok=True)
for i, k in enumerate(rankable[:5], 1):
    d = S[k]; c = members[k]
    mx = None
    for f in d["files"]:
        a = np.load(os.path.join(mg.CACHE, f["file"] + ".npz"))["mm"]; mx = a if mx is None else np.fmax(mx, a)
    xs = [float(r["x"]) / 1000 for r in c]; ys = [float(r["y"]) / 1000 for r in c]
    pad = 32
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    fig, ax = plt.subplots(figsize=(8.5, 8.5 * (y1 - y0) / (x1 - x0) + 0.9), dpi=130)
    ax.set_facecolor("white")
    m = np.where(np.nan_to_num(mx, nan=-1) >= 19.05, mx, np.nan)
    ax.pcolormesh(GX, GY, m, cmap=CMAP, norm=NORM, shading="nearest", zorder=1)
    # 30 km domain (union of discs around all reports) drawn as faint dashed discs
    for x, y in zip(xs, ys): ax.add_patch(Circle((x, y), 30, fill=False, ec="#c9c7c1", lw=0.6, ls=(0, (3, 3)), zorder=2))
    for r in c:
        if r["iem_id"] in {p["iem_id"] for p in d["reports"]} or (r["act"]):
            ax.add_patch(Circle((r["x"] / 1000, r["y"] / 1000), 8.04672, fill=False, ec="#1b1b1b", lw=1.1, zorder=3))
    for r in c:
        X, Y = r["x"] / 1000, r["y"] / 1000
        if r["type"] == "HAIL" and r["act"]: ax.plot(X, Y, "o", ms=6, mfc="#1b1b1b", mec="white", mew=1, zorder=5)
        elif r["type"] == "HAIL": ax.plot(X, Y, "o", ms=6, mfc="white", mec="#1b1b1b", mew=1.2, zorder=5)
        else: ax.plot(X, Y, "^", ms=6.5, mfc="#1b1b1b" if r["act"] else "white", mec="#1b1b1b", mew=1, zorder=5)
    # longitude cutoff line
    lat_line = np.linspace(LATS.min(), LATS.max(), 50); lx, ly = to_utm.transform(np.full(50, MAIN_CUT), lat_line)
    ax.plot(lx / 1000, ly / 1000, color="#8a8780", lw=1, ls=(0, (6, 3)), zorder=2)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    ax.set_xlabel("UTM 13N easting (km)", color="#55534e", fontsize=8); ax.set_ylabel("northing (km)", color="#55534e", fontsize=8)
    ax.tick_params(colors="#8a8780", labelsize=7)
    for sp in ax.spines.values(): sp.set_color("#d6d4ce")
    ax.set_title(f"{k}  {d['start_local'][:16]} to {d['end_local'][11:16]} MT  ({zone(d)})\n"
                 f"MESH ≥1.00 in within 30 km: {d['mesh100_km2']:.0f} km²   covered by circles: {100 * d['share100_covered']:.0f}%   "
                 f">8 mi from actionable report: {100 * d['share100_beyond8mi']:.0f}%\n"
                 f"actionable hail reports with MESH ≥1.00 in within 5 km: {d['n_act_hail_mesh_ge100']} of {d['n_actionable_hail']}   "
                 f"(worst coverage rank {i} of {len(rankable)})", fontsize=8.5, loc="left", color="#1b1b1b")
    h = [Patch(fc="#b7d3f6", label="MESH 0.75–1.00 in"), Patch(fc="#6da7ec", label="1.00–1.50 in"), Patch(fc="#2a78d6", label="1.50–2.00 in"),
         Patch(fc="#104281", label="> 2.00 in"),
         Line2D([], [], color="#1b1b1b", lw=1.1, label="5 mi circle, actionable report"),
         Line2D([], [], color="#c9c7c1", lw=0.8, ls=(0, (3, 3)), label="30 km domain"),
         Line2D([], [], color="#8a8780", lw=1, ls=(0, (6, 3)), label=f"lon {MAIN_CUT} (foothills cutoff)"),
         Line2D([], [], marker="o", ls="", mfc="#1b1b1b", mec="white", ms=6, label="hail ≥1.00 in (actionable)"),
         Line2D([], [], marker="o", ls="", mfc="white", mec="#1b1b1b", ms=6, label="hail < 1.00 in"),
         Line2D([], [], marker="^", ls="", mfc="#1b1b1b", mec="#1b1b1b", ms=6, label="wind, actionable"),
         Line2D([], [], marker="^", ls="", mfc="white", mec="#1b1b1b", ms=6, label="wind, not actionable")]
    ax.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=4, fontsize=7, frameon=False)
    fig.tight_layout()
    p = os.path.join(HERE, "maps", f"worst{i}_{k}.png"); fig.savefig(p, facecolor="white"); plt.close(fig)
    print("map", p)
    del mx, m
