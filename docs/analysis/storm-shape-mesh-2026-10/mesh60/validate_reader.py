"""Step 3: validate pygrib against the stdlib decoder (storm_shape/mesh/mesh_lib.py) on one NOAA MESH_Max_60min file.
Run: .venv/bin/python mesh60/validate_reader.py   (from ~/hail-analysis)"""
import sys, os, gzip, shutil, urllib.request, resource
import numpy as np, pygrib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "storm_shape", "mesh"))
import mesh_lib
import meshgrid as mg

name = "MRMS_MESH_Max_60min_00.50_20260608-220000.grib2.gz"
url = f"https://noaa-mrms-pds.s3.amazonaws.com/CONUS/MESH_Max_60min_00.50/20260608/{name}"
os.makedirs(mg.RAW, exist_ok=True)
gz = os.path.join(mg.RAW, name)
if not os.path.exists(gz):
    urllib.request.urlretrieve(url, gz)

# standard reader (the crop function used for the whole run)
mm_pg, meta = mg.read_crop_pygrib(gz)
print("pygrib meta:", meta)

# stdlib reader, same box (mesh_lib rounds the box edges to cell indices; give it the cell-centre bounds)
d = mesh_lib.read_crop(gz, mg.LAT_TOP, mg.LAT_TOP - (mg.NR - 1) * 0.01, mg.LON_LEFT, mg.LON_LEFT + (mg.NC - 1) * 0.01)
mm_sl = np.array(d["mm"], dtype=np.float64)
print("stdlib crop", mm_sl.shape, "lat_top %.3f lon_left %.3f" % (d["lat_top"], d["lon_left"]), "| pygrib crop", mm_pg.shape,
      "lat_top %.3f lon_left %.3f" % (mg.LAT_TOP, mg.LON_LEFT))
assert mm_sl.shape == mm_pg.shape, "shape mismatch"

nan = np.isnan(mm_pg)
print("cells:", mm_pg.size, "| pygrib NaN (masked) cells:", int(nan.sum()), "| stdlib values at those cells:",
      sorted(set(np.round(mm_sl[nan], 3).tolist()))[:10])
both = ~nan
diff = np.abs(mm_pg[both] - mm_sl[both])
print("where pygrib has a value: max |diff| = %.3g mm, cells differing by > 0.001 mm: %d of %d" % (diff.max(), int((diff > 1e-3).sum()), int(both.sum())))
print("value range pygrib %.2f..%.2f, stdlib %.2f..%.2f" % (np.nanmin(mm_pg), np.nanmax(mm_pg), mm_sl.min(), mm_sl.max()))
for th in (19.05, 25.4):
    print(f"cells >= {th} mm: pygrib {int((np.nan_to_num(mm_pg, nan=-1) >= th).sum())}, stdlib {int((mm_sl >= th).sum())}")
print("peak RSS %.0f MB" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
