"""Part B, source 6: do steering winds predict the fitted storm direction? All 180 good-fit storms.
Run from ~/hail-analysis: .venv/bin/python weather_sources/partb_winds.py
- Storm direction: bearing_toward_deg from storm_shape/shape_by_storm.csv (time fit, R2 >= 0.5).
- HRRR (storms from 2014-07-30): the f00 analysis at the whole UTC hour nearest the storm's mid time, at the grid
  point nearest the report centroid. Fields fetched by byte range from the .idx (one request each, cached).
  HRRR u/v are grid-relative and are rotated to true north with the Lambert cone (wgrib2's convention).
- Denver sounding (IGRA2 USM00072469, to 2022-07-09): the launch nearest the storm's mid time, if within 6 h.
Predictors: wind toward-direction at 700, 500, 300 mb; mean of 700/500/400/300 mb; Bunkers right-mover from an
approximate 0-6 km layer (HRRR: 10 m and 800..400 mb every 50 mb; sounding: height-interpolated). The layer and the
Bunkers estimate are approximations, labelled as such. Angle error = predictor minus storm, wrapped to +-180."""
import os, sys, json, csv, math, zipfile, io, datetime as dt, collections, statistics as st
import numpy as np, pygrib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

OUT = os.path.join(C.HERE, "partb", "winds"); os.makedirs(OUT, exist_ok=True)
RAW = os.path.join(C.HERE, "samples", "winds", "hrrr"); os.makedirs(RAW, exist_ok=True)
HB = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
LEVELS = ["10 m above ground"] + [f"{p} mb" for p in (800, 750, 700, 650, 600, 550, 500, 450, 400, 300)]
SND = os.path.join(C.HERE, "samples", "winds", "igra2_USM00072469.zip")
STN_ELEV = 1611.0

def wrap(a): return (a + 180) % 360 - 180
def toward(u, v): return (math.degrees(math.atan2(u, v)) + 360) % 360

def bunkers(mean, shear):
    """Right-mover: mean wind + 7.5 m/s to the right of the 0-6 km shear vector."""
    s = math.hypot(*shear)
    if s == 0: return mean
    return (mean[0] + 7.5 * shear[1] / s, mean[1] - 7.5 * shear[0] / s)

def predictors(w, layer, low, top):
    """w: {level: (u, v)} earth-relative m/s. layer: list of (u, v) through ~0-6 km. low/top: (u, v)."""
    out = {}
    for lev in ("700 mb", "500 mb", "300 mb"):
        if lev in w: out[lev] = toward(*w[lev])
    m = [w[l] for l in ("700 mb", "500 mb", "400 mb", "300 mb") if l in w]
    if len(m) == 4: out["mean 700-300"] = toward(sum(a for a, _ in m) / 4, sum(b for _, b in m) / 4)
    if layer and low and top:
        mean = (sum(a for a, _ in layer) / len(layer), sum(b for _, b in layer) / len(layer))
        out["mean 0-6 km (approx)"] = toward(*mean)
        out["Bunkers right (approx)"] = toward(*bunkers(mean, (top[0] - low[0], top[1] - low[1])))
    return out

def hrrr_winds(t, lat, lon):
    """Earth-relative winds at the nearest HRRR point, analysis hour t (naive UTC)."""
    day, hh = t.strftime("%Y%m%d"), t.strftime("%H")
    base = f"{HB}/hrrr.{day}/conus/hrrr.t{hh}z.wrfprsf00.grib2"
    idx = C.fetch(base + ".idx", os.path.join(RAW, f"{day}_{hh}.idx"))
    L = [l.split(":") for l in open(idx).read().splitlines()]
    want = {}
    for i, f in enumerate(L):
        if f[3] in ("UGRD", "VGRD") and f[4] in LEVELS and f[5] == "anl":
            end = int(L[i + 1][1]) - 1 if i + 1 < len(L) else ""
            want[(f[3], f[4])] = (int(f[1]), end)
    g2 = os.path.join(RAW, f"{day}_{hh}_winds.grib2")
    if not os.path.exists(g2):
        with open(g2 + ".part", "wb") as fh:
            for (var, lev), (a, b) in sorted(want.items(), key=lambda x: x[1][0]):
                import urllib.request
                req = urllib.request.Request(base, headers={"Range": f"bytes={a}-{b}", "User-Agent": "hail-analysis research"})
                for k in range(3):
                    try:
                        with urllib.request.urlopen(req, timeout=120) as r: blob = r.read(); break
                    except OSError:
                        if k == 2: raise
                fh.write(blob); C.log_fetch(f"hrrr {day}{hh} {var} {lev}", 206, len(blob), base)
        os.replace(g2 + ".part", g2)
    G = pygrib.open(g2); w = {}; idxpt = None; rot = None
    msgs = {}
    for g in G:
        lev = "10 m above ground" if g.typeOfLevel == "heightAboveGround" else f"{g.level} mb"
        sn = {"10u": "u", "10v": "v"}.get(g.shortName, g.shortName)    # eccodes names the 10 m wind 10u/10v
        msgs[(sn, lev)] = g
    for (sn, lev), g in msgs.items():
        if idxpt is None:
            la, lo = g.latlons(); lo = np.where(lo > 180, lo - 360, lo)
            idxpt = np.unravel_index(np.argmin((la - lat) ** 2 + ((lo - lon) * math.cos(math.radians(lat))) ** 2), la.shape)
            assert g["uvRelativeToGrid"] == 1
            cone = math.sin(math.radians(g["Latin1InDegrees"])); lov = g["LoVInDegrees"] - 360
            rot = math.radians(cone * (lo[idxpt] - lov)); del la, lo
    for lev in LEVELS:
        if ("u", lev) in msgs and ("v", lev) in msgs:
            u, v = float(msgs[("u", lev)].values[idxpt]), float(msgs[("v", lev)].values[idxpt])
            w[lev] = (math.cos(rot) * u + math.sin(rot) * v, -math.sin(rot) * u + math.cos(rot) * v)
    G.close()
    layer = [w[l] for l in LEVELS[:-1] if l in w]          # 10 m and 800..400 mb: approximates 0-6 km AGL on the Front Range
    low, top = w.get("10 m above ground"), w.get("400 mb")
    return w, (layer if len(layer) >= 8 else None), low, top, math.degrees(rot), sorted(w)

def load_soundings(times):
    """Stream IGRA2; keep soundings whose launch is within 6 h of any wanted time. Returns {launch: levels}."""
    keep = {}; cur = None; lev = []
    wanted = sorted(times)
    def near(t): return any(abs((t - x).total_seconds()) <= 6 * 3600 for x in wanted)
    with zipfile.ZipFile(SND) as Z, Z.open(Z.namelist()[0]) as fh:
        for line in io.TextIOWrapper(fh, encoding="ascii"):
            if line.startswith("#"):
                if cur is not None and lev: keep[cur] = lev
                y, mo, d, h = int(line[13:17]), int(line[18:20]), int(line[21:23]), int(line[24:26])
                cur = dt.datetime(y, mo, d, 0 if h == 99 else h) if y >= 2004 else None
                cur = cur if cur is not None and near(cur) else None; lev = []
                continue
            if cur is None: continue
            p, z, wd, ws = int(line[9:15]), int(line[16:21]), int(line[40:45]), int(line[46:51])
            lt2 = line[1]
            if wd < 0 or ws < 0: continue
            if lt2 == "1": z = STN_ELEV if z < 0 else z            # surface level
            if z < 0: continue
            sp = ws / 10; rad = math.radians(wd)
            lev.append(dict(p=p / 100, z=float(z), u=-sp * math.sin(rad), v=-sp * math.cos(rad)))
    if cur is not None and lev: keep[cur] = lev
    return keep

def sounding_winds(L):
    w = {}
    for x in L:
        for m in (700, 500, 400, 300):
            if abs(x["p"] - m) < 0.01: w[f"{m} mb"] = (x["u"], x["v"])
    L = sorted(L, key=lambda x: x["z"]); zs = np.array([x["z"] for x in L])
    sfc = zs.min(); top_z = sfc + 6000
    if zs.max() < top_z or len(L) < 5: return w, None, None, None
    us = np.array([x["u"] for x in L]); vs = np.array([x["v"] for x in L])
    grid = np.linspace(sfc, top_z, 61)
    ui, vi = np.interp(grid, zs, us), np.interp(grid, zs, vs)
    layer = list(zip(ui.tolist(), vi.tolist()))
    return w, layer, (float(ui[0]), float(vi[0])), (float(ui[-1]), float(vi[-1]))

def main():
    ns = C.load_members(); members = ns["members"]
    shape = list(csv.DictReader(open(os.path.join(C.ROOT, "storm_shape", "shape_by_storm.csv"))))
    assert len(shape) == 180
    info = {}
    for s in shape:
        c = members[s["storm_id"]]
        t0, t1 = c[0]["utc"].replace(tzinfo=None), c[-1]["utc"].replace(tzinfo=None)
        mid = t0 + (t1 - t0) / 2
        info[s["storm_id"]] = dict(mid=mid, lat=float(np.mean([r["lat"] for r in c])), lon=float(np.mean([r["lon"] for r in c])),
                                   storm_toward=float(s["bearing_toward_deg"]), speed_kmh=float(s["speed_kmh"]), r2=float(s["fit_r2"]),
                                   start_local=s["start_local"], chain_suspect=s["chain_suspect"])
    todo_snd = [k for k, v in info.items() if v["mid"] <= dt.datetime(2022, 7, 10)]
    snd = load_soundings([info[k]["mid"] for k in todo_snd]) if not all(os.path.exists(os.path.join(OUT, f"{k}.json")) for k in info) else {}
    for n, (k, v) in enumerate(sorted(info.items()), 1):
        jp = os.path.join(OUT, f"{k}.json")
        if os.path.exists(jp): continue
        res = dict(storm_id=k, start_local=v["start_local"], mid_utc=str(v["mid"]), storm_toward=v["storm_toward"], speed_kmh=v["speed_kmh"],
                   r2=v["r2"], chain_suspect=v["chain_suspect"], centroid=(v["lat"], v["lon"]), hrrr=None, sounding=None)
        if v["mid"] >= dt.datetime(2014, 7, 30):
            h = (v["mid"] + dt.timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
            w, layer, low, top, rotdeg, levs = hrrr_winds(h, v["lat"], v["lon"])
            p = predictors(w, layer, low, top)
            res["hrrr"] = dict(hour=str(h), rotation_deg=round(rotdeg, 2), levels=levs,
                               toward={a: round(b, 1) for a, b in p.items()}, err={a: round(wrap(b - v["storm_toward"]), 1) for a, b in p.items()},
                               speed500_ms=round(math.hypot(*w["500 mb"]), 1) if "500 mb" in w else None)
        if k in todo_snd and snd:
            best = min(snd, key=lambda t: abs((t - v["mid"]).total_seconds()))
            if abs((best - v["mid"]).total_seconds()) <= 6 * 3600:
                w, layer, low, top = sounding_winds(snd[best])
                p = predictors(w, layer, low, top)
                res["sounding"] = dict(launch=str(best), dt_h=round((best - v["mid"]).total_seconds() / 3600, 1),
                                       toward={a: round(b, 1) for a, b in p.items()}, err={a: round(wrap(b - v["storm_toward"]), 1) for a, b in p.items()})
        json.dump(res, open(jp + ".tmp", "w"), indent=1); os.replace(jp + ".tmp", jp)
        e = lambda src: (res[src] or {}).get("err", {}).get("500 mb")
        print(f"[{n}/180] {k} {v['start_local'][:10]} storm->{v['storm_toward']:.0f} | 500mb err HRRR {e('hrrr')} snd {e('sounding')}", flush=True)

if __name__ == "__main__":
    main()
