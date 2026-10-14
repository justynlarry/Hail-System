"""Step 2 (item 206): radar-shaped storm areas on the 60 MESH storms, three tiers.
Run from ~/hail-analysis: .venv/bin/python radar_areas/tiers.py
- core:   connected MESH >= 1.00 in patches (8-connected) with an actionable report within ANCHOR_KM (presence, not value)
- margin: cells within MARGIN_KM of the core, not in it (placeholder width; sensitivity 1/3/5 km)
- outer:  warning polygons (SVR/TOR overlapping the MESH window), not in core or margin
- rest:   the rest of the 30 km domain
Independent check: mPING + CoCoRaHS points (weather_sources/partb/ground), which neither MESH nor the reports used.
Today's circles (5 mi around actionable reports) are cross-tabulated against the tiers. Checkpoint per storm."""
import os, sys, json, glob, collections
import numpy as np
from matplotlib.path import Path
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "weather_sources"))
import common as C
import partb_warnings as PW

OUT = os.path.join(HERE, "per_storm"); os.makedirs(OUT, exist_ok=True)
ANCHOR_KM, MARGINS_KM, MAIN_MARGIN = 5.0, (1.0, 3.0, 5.0), 3.0
GROUND = os.path.join(C.HERE, "partb", "ground")

def components(mask_rc):
    """8-connected components of a set of (row, col) cells. Returns list of arrays of indices into mask_rc."""
    idx = {rc: i for i, rc in enumerate(mask_rc)}; seen = np.zeros(len(mask_rc), bool); comps = []
    for s in range(len(mask_rc)):
        if seen[s]: continue
        stack = [s]; seen[s] = True; comp = []
        while stack:
            i = stack.pop(); comp.append(i); r, c = mask_rc[i]
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    j = idx.get((r + dr, c + dc))
                    if j is not None and not seen[j]: seen[j] = True; stack.append(j)
        comps.append(np.array(comp))
    return comps

def min_dist_to(cells_xy, targets_xy, chunk=4000):
    d = np.full(len(cells_xy), np.inf)
    if len(targets_xy) == 0: return d
    for k in range(0, len(targets_xy), chunk):
        t = targets_xy[k:k + chunk]
        d = np.minimum(d, np.sqrt(((cells_xy[:, None, :] - t[None, :, :]) ** 2).sum(-1)).min(1))
    return d

def main():
    ns = C.load_members(); members = ns["members"]; S = C.per_storm()
    SEL = sorted(k for k, d in S.items() if d["in_selection"])
    polys = []
    for y in sorted({S[k]["start_local"][:4] for k in SEL}):
        polys += PW.read_shapefile(os.path.join(C.HERE, "samples", "warnings", "partb", f"wwa_CO_SVTO_{y}.zip"))
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 26913, always_xy=True)
    for k in SEL:
        jp = os.path.join(OUT, f"{k}.json")
        if os.path.exists(jp): continue
        d = S[k]; c = members[k]; w0, w1 = C.window_utc(d)
        rr, cc = C.domain(c); n = rr.size
        xy = np.column_stack([C.GX[rr, cc], C.GY[rr, cc]]); area = C.CELL_KM2[rr, cc]
        v = np.nan_to_num(C.mesh_max(d)[rr, cc], nan=-1.0)
        act = [r for r in c if r["act"]]
        act_xy = np.array([(r["x"], r["y"]) for r in act]) if act else np.zeros((0, 2))
        dact = min_dist_to(xy, act_xy)
        # core: MESH >= 1 in components that come within ANCHOR_KM of an actionable report
        m = v >= C.T100; mi = np.nonzero(m)[0]
        core = np.zeros(n, bool); comps_all = comps_kept = 0
        if mi.size:
            for comp in components([(int(rr[i]), int(cc[i])) for i in mi]):
                comps_all += 1; cells = mi[comp]
                if (dact[cells] <= ANCHOR_KM * 1000).any(): core[cells] = True; comps_kept += 1
        dcore = min_dist_to(xy, xy[core])
        # outer: warning polygons (same selection rule as partb_warnings)
        pts = np.column_stack([C.LON2[rr, cc], C.LAT2[rr, cc]]); inpoly = np.zeros(n, bool)
        for a, rings in polys:
            b, e = PW.ts(a.get("POLY_BEG")), PW.ts(a.get("POLY_END"))
            if not rings or b is None or e is None or not (b < w1 and e > w0): continue
            for ring in rings: inpoly |= Path(ring).contains_points(pts)
        circ = dact <= C.R_M
        # independent ground points -> cell index
        g = json.load(open(os.path.join(GROUND, f"{k}.json")))["points"]
        gp = []
        for p in g:
            x, y = tr.transform(p["lon"], p["lat"])
            i = int(np.argmin((xy[:, 0] - x) ** 2 + (xy[:, 1] - y) ** 2))
            gp.append(dict(src=p["src"], size=p["size_in"], i=i))
        res = dict(storm_id=k, start_local=d["start_local"], mesh100_components=comps_all, core_components=comps_kept,
                   mesh100_km2=float(area[m].sum()), circle_km2=float(area[circ].sum()), domain_km2=float(area.sum()), margins={})
        for mk in MARGINS_KM:
            marg = (~core) & (dcore <= mk * 1000)
            outer = inpoly & ~core & ~marg
            rest = ~(core | marg | outer)
            tiers = dict(core=core, margin=marg, outer=outer, rest=rest)
            t = {}
            for name, tm in tiers.items():
                gi = [p for p in gp if tm[p["i"]]]
                t[name] = dict(km2=float(area[tm].sum()), circle_km2=float(area[tm & circ].sum()),
                               mesh100_km2=float(area[tm & m].sum()),
                               ground_ge1=sum(1 for p in gi if (p["size"] or 0) >= 1.0),
                               ground_lt075=sum(1 for p in gi if p["size"] is not None and p["size"] < 0.75),
                               ground_all=len(gi),
                               by_src={src: dict(ge1=sum(1 for p in gi if p["src"] == src and (p["size"] or 0) >= 1.0),
                                                 lt075=sum(1 for p in gi if p["src"] == src and p["size"] is not None and p["size"] < 0.75),
                                                 all=sum(1 for p in gi if p["src"] == src)) for src in ("cocorahs", "mping")},
                               act_reports=int(sum(1 for r in act if tm[int(np.argmin((xy[:, 0] - r["x"]) ** 2 + (xy[:, 1] - r["y"]) ** 2))])))
            res["margins"][str(mk)] = t
            if mk == MAIN_MARGIN:
                radar = m & ~core & ~marg                       # unanchored swaths ("radar-indicated hail"), decided 2026-10-14
                tiers5 = dict(core=core, margin=marg, radar_unanchored=radar, outer=inpoly & ~core & ~marg & ~radar,
                              rest=~(core | marg | radar | inpoly))
                t5 = {}
                for name, tm in tiers5.items():
                    gi = [p for p in gp if tm[p["i"]]]
                    t5[name] = dict(km2=float(area[tm].sum()), circle_km2=float(area[tm & circ].sum()),
                                    by_src={src: dict(ge1=sum(1 for p in gi if p["src"] == src and (p["size"] or 0) >= 1.0),
                                                      all=sum(1 for p in gi if p["src"] == src)) for src in ("cocorahs", "mping")})
                res["five_tier_3km"] = t5
        json.dump(res, open(jp + ".tmp", "w"), indent=1); os.replace(jp + ".tmp", jp)
        t = res["margins"][str(MAIN_MARGIN)]
        print(f"{k} core {t['core']['km2']:6.0f} margin {t['margin']['km2']:6.0f} outer {t['outer']['km2']:6.0f} km2 | "
              f"ground>=1in core/margin/outer/rest {t['core']['ground_ge1']}/{t['margin']['ground_ge1']}/{t['outer']['ground_ge1']}/{t['rest']['ground_ge1']}", flush=True)

if __name__ == "__main__":
    main()
