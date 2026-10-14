"""Validate mesh_lib (hand-rolled) against pygrib on the same files. Run with the scratchpad venv."""
import sys, gzip, os, shutil, struct
import numpy as np, pygrib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh_lib
R = sys.argv[1]; VAL = f"{R}/val"
LAT_MAX, LAT_MIN, LON_MIN, LON_MAX = 42.0, 37.5, -107.0, -102.0
def pg_crop(gz):
    out = f"{VAL}/{os.path.basename(gz)[:-3]}"
    if not os.path.exists(out):
        with gzip.open(gz) as a, open(out, "wb") as b: shutil.copyfileobj(a, b)
    g = pygrib.open(out)[1]
    lats, lons = g.latlons(); v = g.values        # masked array
    m = (lats <= LAT_MAX) & (lats >= LAT_MIN) & (lons - 360 >= LON_MIN) & (lons - 360 <= LON_MAX)
    return g, lats, lons, np.ma.filled(v, np.nan), m
files = [f"{R}/dl_test/MESH_00.50_20260608-214000.grib2.gz", f"{R}/dl_test/MRMS_MESH_Max_60min_00.50_20260608-220000.grib2.gz", f"{R}/dl_test/MESH_Max_1440min_00.50_20260609-000000.grib2.gz"]
for gz in files:
    g, lats, lons, v, m = pg_crop(gz)
    rows, cols = np.where(m); r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
    pc = v[r0:r1 + 1, c0:c1 + 1]
    d = mesh_lib.read_crop(gz, LAT_MAX, LAT_MIN, LON_MIN, LON_MAX)
    mine = np.array(d["mm"])[1:, :500]    # my crop includes one extra row (lat 42.005) and column (lon -101.995) because of rounding at the box edge
    print(os.path.basename(gz)); print("  pygrib:", g.shortName, g.name, g.units, "| missing value:", g.missingValue, "| crop", pc.shape, "mine (aligned)", mine.shape,
          "| lat0 %.3f lon0 %.3f" % (lats[r0, c0], lons[r0, c0] - 360), "mine lat_top %.3f lon_left %.3f" % (d["lat_top"], d["lon_left"]))
    if pc.shape != mine.shape: print("  SHAPE MISMATCH"); continue
    nanm = np.isnan(pc)
    print("  pygrib NaN cells:", int(nanm.sum()), " distinct values at pygrib-NaN in mine:", sorted(set(np.round(mine[nanm], 1).tolist()))[:5])
    both = ~nanm
    print("  max |diff| where pygrib has a value:", float(np.abs(pc[both] - mine[both]).max()), " cells differing >0.001:", int((np.abs(pc[both] - mine[both]) > 0.001).sum()), "of", int(both.sum()))
    print("  value range pygrib %.1f..%.1f  mine %.1f..%.1f" % (np.nanmin(pc), np.nanmax(pc), mine.min(), mine.max()))
