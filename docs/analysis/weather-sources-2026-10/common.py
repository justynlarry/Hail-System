"""Shared Part B helpers: storm membership, the mesh60 grid in UTM, per-storm domain, MESH max, and fetch logging.
Everything is computed exactly as in mesh60/run_storms.py, so Part B numbers are comparable with mesh60."""
import os, sys, json, glob, math, contextlib, io, datetime as dt, urllib.request, time
import numpy as np
from pyproj import Transformer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "mesh60"))
import meshgrid as mg

LOG = os.path.join(HERE, "fetch_log.tsv")
R_M, FAR_M, DOMAIN_M = 5.0 * 1609.344, 8.0 * 1609.344, 30_000.0
T100, T075 = 25.4, 19.05

def log_fetch(name, status, nbytes, url):
    with open(LOG, "a") as fh:
        fh.write(f"{dt.datetime.now(dt.timezone.utc):%FT%TZ}\t{name}\t{status}\t{nbytes}\t{url}\n")

def fetch(url, dst, headers=None, tries=3):
    """Download url to dst once (cached), with retries; logs every network fetch."""
    if os.path.exists(dst): return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "hail-analysis research"})
    for a in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r, open(dst + ".part", "wb") as f:
                n = 0
                while True:
                    b = r.read(1 << 20)
                    if not b: break
                    f.write(b); n += len(b)
            os.replace(dst + ".part", dst); log_fetch(os.path.basename(dst), 200, n, url); return dst
        except urllib.error.HTTPError as e:
            log_fetch(os.path.basename(dst), e.code, 0, url); raise
        except OSError as e:
            if a == tries - 1: raise
            print(f"  retry {os.path.basename(dst)}: {e}", flush=True); time.sleep(5 * (a + 1))

def load_members():
    """Storm membership and report rows, from run_storms.py's own (verified) code, without running the MESH loop."""
    src = open(os.path.join(ROOT, "mesh60", "run_storms.py")).read().split("# ---------------- grid geometry")[0]
    ns = {"__file__": os.path.join(ROOT, "mesh60", "run_storms.py")}
    with contextlib.redirect_stdout(io.StringIO()): exec(compile(src, "run_storms_members", "exec"), ns)
    return ns

def per_storm():
    return {d["storm_id"]: d for d in (json.load(open(p)) for p in glob.glob(os.path.join(ROOT, "mesh60", "per_storm", "*.json")))}

LATS, LONS = mg.lat_lon_axes()
LON2, LAT2 = np.meshgrid(LONS, LATS)
GX, GY = Transformer.from_crs(4326, 26913, always_xy=True).transform(LON2, LAT2)
CELL_KM2 = (6371.0072 * math.radians(mg.DD)) ** 2 * np.cos(np.radians(LAT2))

def min_dist(rr, cc, pts):
    d = np.full(rr.size, np.inf)
    for p in pts: d = np.minimum(d, np.hypot(GX[rr, cc] - p["x"], GY[rr, cc] - p["y"]))
    return d

def domain(c):
    """(rows, cols) of cells within 30 km of any of the storm's reports, as in run_storms.analyse."""
    x0, x1 = min(r["x"] for r in c) - DOMAIN_M, max(r["x"] for r in c) + DOMAIN_M
    y0, y1 = min(r["y"] for r in c) - DOMAIN_M, max(r["y"] for r in c) + DOMAIN_M
    rr, cc = np.nonzero((GX >= x0) & (GX <= x1) & (GY >= y0) & (GY <= y1))
    keep = min_dist(rr, cc, c) <= DOMAIN_M
    return rr[keep], cc[keep]

def mesh_max(d):
    """Per-cell max over the storm's cached hourly MESH crops (mesh60/crops)."""
    mx = None
    for f in d["files"]:
        a = np.load(os.path.join(mg.CACHE, f["file"] + ".npz"))["mm"]; mx = a if mx is None else np.fmax(mx, a)
    return mx

def window_utc(d):
    """The MESH window of a storm: first file stamp minus 60 min to the last file stamp (UTC, naive)."""
    hrs = [dt.datetime.fromisoformat(f["hour"]).replace(tzinfo=None) for f in d["files"]]
    return min(hrs) - dt.timedelta(minutes=60), max(hrs)

def nearest_cell(lat, lon):
    return int(round((mg.LAT_TOP - lat) / mg.DD)), int(round((lon - mg.LON_LEFT) / mg.DD))
