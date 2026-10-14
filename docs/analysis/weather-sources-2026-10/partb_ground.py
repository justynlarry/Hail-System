"""Part B, sources 3 and 4: CoCoRaHS and mPING hail points against MESH and today's circles, on the 60 MESH storms.
Run from ~/hail-analysis: .venv/bin/python weather_sources/partb_ground.py
Points are kept if they fall in the storm's 30 km domain and inside its MESH window (UTC). CoCoRaHS times are
local and approximate, so its window is widened by 60 min each side; mPING obtimes are UTC to the second.
Per point: max MESH within 5 km (as mesh60 does for reports), distance to the nearest actionable report."""
import os, sys, json, re, csv, time, math, datetime as dt, urllib.parse, collections
from zoneinfo import ZoneInfo
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

OUT = os.path.join(C.HERE, "partb", "ground"); os.makedirs(OUT, exist_ok=True)
RAWC = os.path.join(C.HERE, "samples", "cocorahs", "partb")
RAWM = os.path.join(C.HERE, "samples", "mping", "partb")
DEN = ZoneInfo("America/Denver")
NEAR_M = 5000.0
SLACK = dt.timedelta(minutes=60)

def mping_key():
    for line in open(os.path.expanduser("~/Hail-System/.env")):
        m = re.match(r"\s*(?:export\s+)?MPING_API_KEY\s*=\s*(.*)\s*$", line)
        if m: return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("MPING_API_KEY not in .env")

def cocorahs_day(day):
    """All Colorado CoCoRaHS hail reports for one observation date (cached CSV)."""
    p = os.path.join(RAWC, f"hail_CO_{day}.csv")
    y, m, d = day.split("-")
    C.fetch(f"https://data.cocorahs.org/export/exportreports.aspx?ReportType=Hail&dtf=1&Format=CSV&State=CO&ReportDateType=reportdate&StartDate={int(m)}/{int(d)}/{y}&EndDate={int(m)}/{int(d)}/{y}", p)
    time.sleep(0.5)
    out = []
    for r in csv.DictReader(open(p), skipinitialspace=True):
        try:
            la, lo = float(r["Latitude"]), float(r["Longitude"])
            t = dt.datetime.strptime(r["ObservationDate"] + " " + r["ObservationTime"], "%Y-%m-%d %I:%M %p").replace(tzinfo=DEN)
        except (ValueError, KeyError):
            continue
        def f(k):
            try: return float(r[k])
            except (ValueError, KeyError): return None
        size = f("LargestSize") or f("AverageSize")
        out.append(dict(src="cocorahs", id=r["StationNumber"], lat=la, lon=lo, utc=t.astimezone(dt.timezone.utc).replace(tzinfo=None), size_in=size))
    return out

def mping_window(sid, w0, w1, bbox, key):
    p = os.path.join(RAWM, f"{sid}.json")
    if not os.path.exists(p):
        os.makedirs(RAWM, exist_ok=True)
        q = {"category": "Hail", "obtime_gte": w0.strftime("%Y-%m-%d %H:%M:%S"), "obtime_lte": w1.strftime("%Y-%m-%d %H:%M:%S"),
             "in_bbox": ",".join(f"{v:.3f}" for v in bbox)}
        url = "https://mping.ou.edu/mping/api/v2/reports?" + urllib.parse.urlencode(q); rows = []
        while url:
            req = urllib.request.Request(url, headers={"Authorization": f"Token {key}", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r: d = json.load(r)
            C.log_fetch(f"mping {sid}", 200, len(d.get("results", [])), url)
            rows += d.get("results", []); url = d.get("next"); time.sleep(1.0)
        json.dump(rows, open(p, "w"))
    out = []
    for x in json.load(open(p)):
        m = re.search(r"\(([\d.]+) in", x.get("description") or "")
        lo, la = x["geom"]["coordinates"]
        out.append(dict(src="mping", id=x["id"], lat=la, lon=lo, utc=dt.datetime.strptime(x["obtime"][:19], "%Y-%m-%dT%H:%M:%S"),
                        size_in=float(m.group(1)) if m else None))
    return out

import urllib.request  # noqa: E402

def main():
    ns = C.load_members(); members = ns["members"]; S = C.per_storm(); key = mping_key()
    SEL = sorted(k for k, d in S.items() if d["in_selection"])
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 26913, always_xy=True)
    for k in SEL:
        jp = os.path.join(OUT, f"{k}.json")
        if os.path.exists(jp): continue
        d = S[k]; c = members[k]; w0, w1 = C.window_utc(d)
        rr, cc = C.domain(c)
        dom = set(zip(rr.tolist(), cc.tolist()))
        lon, lat = C.LON2[rr, cc], C.LAT2[rr, cc]
        bbox = (lon.min() - 0.01, lat.min() - 0.01, lon.max() + 0.01, lat.max() + 0.01)
        mx = C.mesh_max(d)
        act = [r for r in c if r["act"]]
        # CoCoRaHS: observation dates (local) touched by the widened window
        days = sorted({(w0 - SLACK).replace(tzinfo=dt.timezone.utc).astimezone(DEN).date().isoformat(),
                       (w1 + SLACK).replace(tzinfo=dt.timezone.utc).astimezone(DEN).date().isoformat()})
        pts = [p for day in days for p in cocorahs_day(day) if w0 - SLACK <= p["utc"] <= w1 + SLACK]
        pts += [p for p in mping_window(k, w0, w1, bbox, key)]
        keep = []
        for p in pts:
            if C.nearest_cell(p["lat"], p["lon"]) not in dom: continue
            x, y = tr.transform(p["lon"], p["lat"])
            near = np.hypot(C.GX[rr, cc] - x, C.GY[rr, cc] - y) <= NEAR_M
            vm = float(np.nan_to_num(mx[rr, cc][near], nan=-1).max()) if near.any() else -1.0
            da = min((math.hypot(x - a["x"], y - a["y"]) for a in act), default=math.inf)
            keep.append(dict(p, utc=str(p["utc"]), mesh5km_mm=vm, dist_actionable_m=(None if da == math.inf else round(da)),
                             in_circle=da <= C.R_M, beyond8mi=da > C.FAR_M))
        res = dict(storm_id=k, start_local=d["start_local"], window_utc=[str(w0), str(w1)], cocorahs_days=days,
                   n_cocorahs=sum(p["src"] == "cocorahs" for p in keep), n_mping=sum(p["src"] == "mping" for p in keep), points=keep)
        json.dump(res, open(jp + ".tmp", "w"), indent=1); os.replace(jp + ".tmp", jp)
        print(f"{k} {d['start_local'][:10]} CoCoRaHS {res['n_cocorahs']:3d} mPING {res['n_mping']:3d}", flush=True)

if __name__ == "__main__":
    main()
