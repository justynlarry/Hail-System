"""Step 3: a fallback capsule aligned with the HRRR storm direction, tested against MESH on the 60 storms.
Run from ~/hail-analysis: .venv/bin/python radar_areas/capsule.py
Built from reports + HRRR only (what a pre-2020 storm or a radar gap would have). Each actionable report gets a
capsule: the segment report +- L along the direction, buffered by b. Variants are area-matched to today's 5-mile
circle (pi * 8.047^2 = 203.4 km2): b = 4 km -> L = 9.43 km; b = 3 km -> L = 14.59 km.
Directions: HRRR Bunkers right-mover (the real fallback), HRRR 500 mb, the fitted storm direction (best case,
from the same reports), and the right-mover turned 90 degrees (control: does alignment matter at all?).
Scored inside the same 30 km domain against MESH >= 1.00 in, and against the independent ground points."""
import os, sys, json, math
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "weather_sources"))
import common as C

OUT = os.path.join(HERE, "capsule"); os.makedirs(OUT, exist_ok=True)
CIRC = math.pi * C.R_M ** 2
VARIANTS = {f"b{b:g}": (b * 1000, (CIRC - math.pi * (b * 1000) ** 2) / (4 * b * 1000)) for b in (4, 3)}   # name: (b, L) in m
WINDS = os.path.join(C.HERE, "partb", "winds"); GROUND = os.path.join(C.HERE, "partb", "ground")

def capsule_mask(xy, reps, deg, b, L):
    u = np.array([math.sin(math.radians(deg)), math.cos(math.radians(deg))])
    m = np.zeros(len(xy), bool)
    for r in reps:
        p = np.array([r["x"], r["y"]]); d = xy - p
        t = np.clip(d @ u, -L, L)
        m |= np.hypot(d[:, 0] - t * u[0], d[:, 1] - t * u[1]) <= b
    return m

def main():
    ns = C.load_members(); members = ns["members"]; S = C.per_storm()
    SEL = sorted(k for k, d in S.items() if d["in_selection"])
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 26913, always_xy=True)
    for k in SEL:
        jp = os.path.join(OUT, f"{k}.json")
        if os.path.exists(jp): continue
        d = S[k]; c = members[k]; act = [r for r in c if r["act"]]
        w = json.load(open(os.path.join(WINDS, f"{k}.json")))
        dirs = {"hrrr_right_mover": w["hrrr"]["toward"]["Bunkers right (approx)"], "hrrr_500mb": w["hrrr"]["toward"]["500 mb"],
                "fitted": w["storm_toward"], "control_perpendicular": (w["hrrr"]["toward"]["Bunkers right (approx)"] + 90) % 360}
        rr, cc = C.domain(c); xy = np.column_stack([C.GX[rr, cc], C.GY[rr, cc]]); area = C.CELL_KM2[rr, cc]
        m = np.nan_to_num(C.mesh_max(d)[rr, cc], nan=-1.0) >= C.T100
        g = json.load(open(os.path.join(GROUND, f"{k}.json")))["points"]
        gi = []
        for p in g:
            if (p["size_in"] or 0) < 1.0: continue
            x, y = tr.transform(p["lon"], p["lat"]); gi.append(int(np.argmin((xy[:, 0] - x) ** 2 + (xy[:, 1] - y) ** 2)))
        gi = np.array(gi, int)
        def score(mask):
            a, h = float(area[mask].sum()), float(area[mask & m].sum())
            return dict(km2=a, mesh_in=h, ground_ge1_in=int(mask[gi].sum()) if gi.size else 0)
        dact = np.full(len(xy), np.inf)
        for r in act: dact = np.minimum(dact, np.hypot(xy[:, 0] - r["x"], xy[:, 1] - r["y"]))
        res = dict(storm_id=k, start_local=d["start_local"], n_act=len(act), mesh100_km2=float(area[m].sum()), ground_ge1=int(gi.size),
                   directions=dirs, circle=score(dact <= C.R_M), capsules={})
        for vn, (b, L) in VARIANTS.items():
            for dn, deg in dirs.items():
                res["capsules"][f"{vn}|{dn}"] = score(capsule_mask(xy, act, deg, b, L))
        json.dump(res, open(jp + ".tmp", "w"), indent=1); os.replace(jp + ".tmp", jp)
        cr = res["circle"]; hr = res["capsules"]["b4|hrrr_right_mover"]
        f = lambda s: f"{s['mesh_in']/res['mesh100_km2']:.0%}" if res["mesh100_km2"] else "n/a"
        print(f"{k} MESH {res['mesh100_km2']:6.0f} | circle recall {f(cr)} area {cr['km2']:6.0f} | capsule b4 HRRR recall {f(hr)} area {hr['km2']:6.0f}", flush=True)

if __name__ == "__main__":
    main()
