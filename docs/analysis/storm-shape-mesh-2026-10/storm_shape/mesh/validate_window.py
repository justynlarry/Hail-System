"""Why did my 60-min max (from IEM 2-min files) differ from NOAA's MESH_Max_60min on 27 of 276 cells? Decoder is bit-identical (validate_pygrib.py),
so test the time window instead. Run with the scratchpad venv."""
import sys, os, gzip, shutil, datetime as dt
import numpy as np, pygrib
R = sys.argv[1]; VAL = f"{R}/val"; RAW = f"{R}/mesh_raw"
def read(gz):
    out = f"{VAL}/{os.path.basename(gz)[:-3]}"
    if not os.path.exists(out):
        with gzip.open(gz) as a, open(out, "wb") as b: shutil.copyfileobj(a, b)
    g = pygrib.open(out)[1]; v = np.ma.filled(g.values, np.nan)[1000:1500, :]    # rows are irrelevant here; take a wide band covering Colorado
    return g.validDate, v
# whole-grid window: rows 1300..1750 (lat 42..37.5) and cols 2300..2800 (lon -107..-102)
def crop(gz):
    out = f"{VAL}/{os.path.basename(gz)[:-3]}"
    if not os.path.exists(out):
        with gzip.open(gz) as a, open(out, "wb") as b: shutil.copyfileobj(a, b)
    g = pygrib.open(out)[1]; return g.validDate, np.ma.filled(g.values, np.nan)[1300:1750, 2300:2800]
s3t, s3 = crop(f"{R}/dl_test/MRMS_MESH_Max_60min_00.50_20260608-220000.grib2.gz")
print("S3 Max_60min validDate:", s3t)
files = sorted(f for f in os.listdir(RAW) if f.startswith("MESH_00.50_20260608-"))
stamps = {}
for f in files:
    t, v = crop(f"{RAW}/{f}"); stamps[f[-16:-8].replace("-", "")] = (t, v)
print("IEM file name vs GRIB valid time (first 3):", [(k, str(stamps[k][0].time())) for k in list(stamps)[:3]])
def window(lo, hi):
    m = None
    for k, (t, v) in stamps.items():
        hm = k[8:12] if False else k[-6:-2]
        if lo <= t.replace(tzinfo=None) <= hi: m = v if m is None else np.fmax(m, v)
    return m
def cmp(label, lo, hi):
    m = window(lo, hi); a = np.nan_to_num(m, nan=-1); b = np.nan_to_num(s3, nan=-1); sel = (a >= 19.05) | (b >= 19.05)
    n = len([1 for k,(t,v) in stamps.items() if lo <= t.replace(tzinfo=None) <= hi])
    print(f"{label:55s} files {n:2d}  cells>=0.75in {int(sel.sum()):3d}  differ {int((np.abs(a - b)[sel] > 1e-6).sum()):3d}  max|diff| {float(np.abs(a - b).max()):.1f} mm")
D = lambda h, m, s=0: dt.datetime(2026, 6, 8, h, m, s)
cmp("my original: 21:02 to 22:00 by file NAME (22:00:38 incl)", D(21, 1, 30), D(22, 0, 59))
cmp("valid time 21:00:00 to 22:00:00 (window of the product)", D(21, 0, 0), D(22, 0, 0))
cmp("valid time 20:59:30 to 22:00:00", D(20, 59, 30), D(22, 0, 0))
cmp("valid time 21:00:30 to 22:00:30", D(21, 0, 30), D(22, 0, 30))
cmp("valid time 20:58 to 22:00:40", D(20, 58, 0), D(22, 0, 40))
for k in list(stamps)[28:34]: print("  file", k, "valid", stamps[k][0].time())
