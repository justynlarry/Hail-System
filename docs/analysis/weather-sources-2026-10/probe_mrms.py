"""Part A, source 2: decode one sample of each extra MRMS product with the mesh60 reader, one file at a time.
Checks grid, units, value range over the territory, and peak memory. Run from ~/hail-analysis:
    .venv/bin/python weather_sources/probe_mrms.py"""
import os, sys, json, gzip, shutil, tempfile, urllib.request, resource, gc
import numpy as np, pygrib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mesh60"))
import meshgrid as mg

OUT = os.path.join(HERE, "samples", "mrms"); os.makedirs(OUT, exist_ok=True)
DAY, HMS = "20230510", "230000"      # test hour; A01973 ran 16:23 MDT = 22:23 UTC
PRODUCTS = ["RotationTrack60min_00.50", "RotationTrackML60min_00.50", "POSH_00.50", "SHI_00.50", "RadarQualityIndex_00.00"]
# territory = coverage zips' report extent (storm_shape/reports_raw.csv, zone=inside)
T_LAT, T_LON = (38.52, 40.89), (-105.58, -103.71)

def stamp_near(prod):
    """The listed key at or just after DAY-HMS (instantaneous products are stamped at odd seconds)."""
    x = urllib.request.urlopen(f"{mg.BUCKET}/?list-type=2&prefix=CONUS/{prod}/{DAY}/MRMS_{prod}_{DAY}-{HMS[:2]}", timeout=60).read().decode()
    import re
    keys = sorted(re.findall(rf"MRMS_{prod}_{DAY}-(\d{{6}})\.grib2\.gz", x))
    return keys[0]

def read_crop_any(gz):
    """Like meshgrid.read_crop_pygrib, but takes the grid from the file (regular lat/lon, north-to-south rows).
    Crops to the same Colorado + 50 km box and drops the full grid before returning."""
    tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False); tmp.close()
    try:
        with gzip.open(gz) as a, open(tmp.name, "wb") as b: shutil.copyfileobj(a, b)
        f = pygrib.open(tmp.name); g = f[1]
        assert g["gridType"] == "regular_ll" and g["jScansPositively"] == 0 and g["iScansNegatively"] == 0
        ni, nj, dd = g["Ni"], g["Nj"], g["iDirectionIncrementInDegrees"]
        la1, lo1 = g["latitudeOfFirstGridPointInDegrees"], g["longitudeOfFirstGridPointInDegrees"] - 360
        la = la1 - np.arange(nj) * dd; lo = lo1 + np.arange(ni) * dd
        r = np.nonzero((la <= 41.45) & (la >= 36.55))[0]; c = np.nonzero((lo >= -109.65) & (lo <= -101.44))[0]
        meta = dict(name=g.name, units=g.units, Ni=ni, Nj=nj, dDeg=dd, packing=g["packingType"], validDate=str(g.validDate),
                    discipline=g["discipline"], category=g["parameterCategory"], number=g["parameterNumber"])
        full = g.values
        crop = np.ma.filled(full[r[0]:r[-1] + 1, c[0]:c[-1] + 1], np.nan).astype(np.float32)
        del full, g; f.close(); gc.collect()
        meta["rss_after_decode_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        return crop, meta, (la[r[0]:r[-1] + 1], lo[c[0]:c[-1] + 1])
    finally:
        os.unlink(tmp.name)

res = {}
for prod in PRODUCTS:
    st = stamp_near(prod)
    name = f"MRMS_{prod}_{DAY}-{st}.grib2.gz"; gz = os.path.join(OUT, name)
    if not os.path.exists(gz):
        urllib.request.urlretrieve(f"{mg.BUCKET}/CONUS/{prod}/{DAY}/{name}", gz)
    crop, meta, (la, lo) = read_crop_any(gz)      # grid read from the file: rotation tracks are 0.005 degree, not 0.01
    np.savez_compressed(os.path.join(OUT, name.replace(".grib2.gz", ".npz")), v=crop, lat=la, lon=lo)
    rows = (la >= T_LAT[0]) & (la <= T_LAT[1]); cols = (lo >= T_LON[0]) & (lo <= T_LON[1])
    t = crop[np.ix_(rows, cols)]
    vals = np.unique(np.round(t[np.isfinite(t)], 4))
    res[prod] = dict(file=name, bytes=os.path.getsize(gz), meta=meta,
                     territory_min=float(np.nanmin(t)), territory_max=float(np.nanmax(t)),
                     n_distinct=int(vals.size), lowest_values=vals[:6].tolist(),
                     share_cells_gt0=float((np.nan_to_num(t, nan=-9) > 0).mean()), nan_cells=int(np.isnan(t).sum()))
    print(prod, json.dumps(res[prod], default=str)); del crop, t; gc.collect()
res["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print("peak RSS %.0f MB" % res["peak_rss_mb"])
json.dump(res, open(os.path.join(OUT, "probe_mrms.json"), "w"), indent=1, default=str)
