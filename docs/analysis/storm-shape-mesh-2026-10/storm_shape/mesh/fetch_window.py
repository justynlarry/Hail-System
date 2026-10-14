"""Download MRMS MESH (2-min instantaneous, IEM mtarchive) for a UTC window, decode a Colorado crop, save each crop as uint16 (mm*10, <0 -> 0).
Usage: fetch_window.py YYYYMMDD HHMM_start HHMM_end   (end inclusive)  e.g. 20260608 2030 2330"""
import sys, os, urllib.request, struct, datetime as dt
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh_lib
SCR = "/tmp/claude-1000/-home-hail-user-hail-system/161af0a4-2bd0-4c46-bcb9-1b5f0cd7675f/scratchpad"
RAW, CROPS = f"{SCR}/mesh_raw", f"{SCR}/mesh_crops"
LAT_MAX, LAT_MIN, LON_MIN, LON_MAX = 42.0, 37.5, -107.0, -102.0

def one(args):
    day, hhmm = args
    name = f"MESH_00.50_{day}-{hhmm}00.grib2.gz"
    url = f"https://mtarchive.geol.iastate.edu/{day[:4]}/{day[4:6]}/{day[6:]}/mrms/ncep/MESH/{name}"
    dst, out = f"{RAW}/{name}", f"{CROPS}/{day}-{hhmm}.bin"
    if os.path.exists(out): return hhmm, "cached"
    try:
        if not os.path.exists(dst):
            with urllib.request.urlopen(url, timeout=60) as r, open(dst, "wb") as f: f.write(r.read())
        d = mesh_lib.read_crop(dst, LAT_MAX, LAT_MIN, LON_MIN, LON_MAX)
        vals = [max(0, int(round(v * 10))) for row in d["mm"] for v in row]
        open(out, "wb").write(struct.pack(">%dH" % len(vals), *vals))
        return hhmm, "ok"
    except Exception as e:
        return hhmm, "ERR %s" % e

if __name__ == "__main__":
    day, a, b = sys.argv[1:4]
    t = dt.datetime.strptime(day + a, "%Y%m%d%H%M"); end = dt.datetime.strptime(day + b, "%Y%m%d%H%M"); jobs = []
    while t <= end: jobs.append((t.strftime("%Y%m%d"), t.strftime("%H%M"))); t += dt.timedelta(minutes=2)
    with Pool(2) as p:
        res = p.map(one, jobs)
    bad = [r for r in res if not r[1] in ("ok", "cached")]
    print(len(jobs), "files;", len(bad), "problems", bad[:5])
