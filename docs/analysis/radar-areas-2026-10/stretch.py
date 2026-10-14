"""Step 3b: does the HRRR direction help when ADDING area? Each actionable report's circle (b = R = 5 mi) is stretched
along a direction by +-L (a capsule of width 2R), against simply enlarging the circle radius to the same total area.
Run from ~/hail-analysis: .venv/bin/python radar_areas/stretch.py
L = 4, 8, 16 km. Directions: HRRR right-mover, fitted (best case), perpendicular (control). Pooled over the 60 storms."""
import os, sys, json, math, glob
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "weather_sources"))
import common as C
from capsule import capsule_mask

LS = (4000.0, 8000.0, 16000.0)
OUT = os.path.join(HERE, "stretch"); os.makedirs(OUT, exist_ok=True)

def main():
    ns = C.load_members(); members = ns["members"]; S = C.per_storm()
    for k in sorted(k for k, d in S.items() if d["in_selection"]):
        jp = os.path.join(OUT, f"{k}.json")
        if os.path.exists(jp): continue
        d = S[k]; c = members[k]; act = [r for r in c if r["act"]]
        w = json.load(open(os.path.join(C.HERE, "partb", "winds", f"{k}.json")))
        rm = w["hrrr"]["toward"]["Bunkers right (approx)"]
        dirs = {"hrrr_right_mover": rm, "fitted": w["storm_toward"], "control_perpendicular": (rm + 90) % 360}
        # wider domain so stretched shapes are not clipped: 30 km + 16 km
        x0, x1 = min(r["x"] for r in c) - 46000, max(r["x"] for r in c) + 46000
        y0, y1 = min(r["y"] for r in c) - 46000, max(r["y"] for r in c) + 46000
        rr, cc = np.nonzero((C.GX >= x0) & (C.GX <= x1) & (C.GY >= y0) & (C.GY <= y1))
        dom = C.min_dist(rr, cc, c) <= C.DOMAIN_M            # MESH still counted only within 30 km of reports
        xy = np.column_stack([C.GX[rr, cc], C.GY[rr, cc]]); area = C.CELL_KM2[rr, cc]
        m = (np.nan_to_num(C.mesh_max(d)[rr, cc], nan=-1.0) >= C.T100) & dom
        dact = np.full(len(xy), np.inf)
        for r in act: dact = np.minimum(dact, np.hypot(xy[:, 0] - r["x"], xy[:, 1] - r["y"]))
        sc = lambda mask: dict(km2=float(area[mask].sum()), mesh_in=float(area[mask & m].sum()))
        res = dict(storm_id=k, mesh100_km2=float(area[m].sum()), circle=sc(dact <= C.R_M), stretched={}, enlarged={})
        for L in LS:
            for dn, deg in dirs.items():
                res["stretched"][f"{L/1000:g}|{dn}"] = sc(capsule_mask(xy, act, deg, C.R_M, L))
            # equal per-report area: pi R'^2 = pi R^2 + 4 R L
            Rp = math.sqrt(C.R_M ** 2 + 4 * C.R_M * L / math.pi)
            res["enlarged"][f"{L/1000:g}"] = dict(sc(dact <= Rp), radius_km=Rp / 1000)
        json.dump(res, open(jp, "w"), indent=1)
    R = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(OUT, "A*.json")))]
    M = sum(r["mesh100_km2"] for r in R); A0 = sum(r["circle"]["km2"] for r in R); H0 = sum(r["circle"]["mesh_in"] for r in R)
    print(f"circle: area {A0:.0f} km2, recall {H0/M:.1%}")
    for L in ("4", "8", "16"):
        Re = sum(r["enlarged"][L]["mesh_in"] for r in R); Ae = sum(r["enlarged"][L]["km2"] for r in R)
        print(f"L {L} km | enlarged circle R {R[0]['enlarged'][L]['radius_km']:.1f} km: area {Ae:.0f} recall {Re/M:.1%} gain per 1000 km2 added {1000*(Re-H0)/max(Ae-A0,1):.0f} km2 MESH")
        for dn in ("hrrr_right_mover", "fitted", "control_perpendicular"):
            Rs = sum(r["stretched"][f"{L}|{dn}"]["mesh_in"] for r in R); As = sum(r["stretched"][f"{L}|{dn}"]["km2"] for r in R)
            print(f"        stretched {dn:22s}: area {As:.0f} recall {Rs/M:.1%} gain per 1000 km2 added {1000*(Rs-H0)/max(As-A0,1):.0f} km2 MESH")
    json.dump(dict(note="pooled in stretch.py output"), open(os.path.join(OUT, "_done.json"), "w"))

if __name__ == "__main__":
    main()
