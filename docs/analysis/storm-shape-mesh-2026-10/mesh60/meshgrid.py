"""Shared MESH grid helpers: crop box, pygrib reader, S3 listing/fetch, on-disk crop cache.
Memory rule: one full CONUS grid at a time. read_crop_pygrib() slices the crop out, copies it, and drops the full grid
before returning, so the caller never holds a CONUS array."""
import os, gzip, re, shutil, tempfile, urllib.request, gc, time
import numpy as np, pygrib

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "raw")            # downloaded .grib2.gz (kept; small)
CACHE = os.path.join(BASE, "crops")        # cropped grids, .npz
BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PROD = "MESH_Max_60min_00.50"

# MRMS CONUS grid (asserted against every file's section 3 keys)
NI, NJ, LA1, LO1, DD = 7000, 3500, 54.995, -129.995, 0.01
# Colorado (37.0-41.0 N, 109.05-102.04 W) plus a 50 km margin: 0.45 deg lat; 50 km at 41 N is 0.596 deg lon.
# Cell centres inside lat 36.55..41.45, lon -109.65..-101.44:
R0, R1 = 1355, 1844        # rows (north to south), inclusive -> lat 41.445 .. 36.555
C0, C1 = 2035, 2855        # cols (west to east), inclusive   -> lon -109.645 .. -101.445
NR, NC = R1 - R0 + 1, C1 - C0 + 1
LAT_TOP, LON_LEFT = round(LA1 - R0 * DD, 3), round(LO1 + C0 * DD, 3)

def lat_lon_axes():
    return LAT_TOP - np.arange(NR) * DD, LON_LEFT + np.arange(NC) * DD

def read_crop_pygrib(gz):
    """Return (crop mm as float32 with NaN where pygrib masks, meta dict). The full grid lives only inside this call."""
    tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False); tmp.close()
    try:
        with gzip.open(gz) as a, open(tmp.name, "wb") as b: shutil.copyfileobj(a, b)
        f = pygrib.open(tmp.name); g = f[1]
        assert (g["Ni"], g["Nj"]) == (NI, NJ), (g["Ni"], g["Nj"])
        assert abs(g["latitudeOfFirstGridPointInDegrees"] - LA1) < 1e-6 and abs(g["longitudeOfFirstGridPointInDegrees"] - 360 - LO1) < 1e-6
        assert abs(g["iDirectionIncrementInDegrees"] - DD) < 1e-9 and abs(g["jDirectionIncrementInDegrees"] - DD) < 1e-9
        assert g["jScansPositively"] == 0 and g["iScansNegatively"] == 0     # rows run north->south, cols west->east
        meta = dict(name=g.name, shortName=g.shortName, units=g.units, packing=g["packingType"],
                    validDate=str(g.validDate), missingValue=g.missingValue)
        full = g.values                                  # masked float64, 7000x3500 = 196 MB
        crop = np.ma.filled(full[R0:R1 + 1, C0:C1 + 1], np.nan).astype(np.float32)   # astype copies; no view into full
        del full, g; f.close(); gc.collect()
        return crop, meta
    finally:
        os.unlink(tmp.name)

def _get(url, dst=None, tries=4):
    """HTTPS GET with retries (S3 occasionally resets connections); raises after the last try. Writes to dst if given."""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                if dst is None: return r.read()
                with open(dst, "wb") as f: shutil.copyfileobj(r, f)
                return None
        except OSError as e:
            if attempt == tries - 1: raise
            print(f"  retry {attempt + 1} {url.rsplit('/', 1)[-1]}: {e}", flush=True); time.sleep(5 * (attempt + 1))

def list_day(day):
    """All keys of the product for one UTC day (YYYYMMDD) -> sorted list of HHMMSS stamps. Anonymous HTTPS."""
    url = f"{BUCKET}/?list-type=2&prefix=CONUS/{PROD}/{day}/"
    stamps, token = [], None
    while True:
        u = url + (f"&continuation-token={urllib.parse.quote(token)}" if token else "")
        x = _get(u).decode()
        stamps += re.findall(rf"MRMS_{PROD}_{day}-(\d{{6}})\.grib2\.gz", x)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", x)
        if not m: break
        token = m.group(1)
    return sorted(set(stamps))

def cached_crop(day, hhmmss):
    """Load the cropped grid from disk, or download + decode + cache it. Returns float32 mm (NaN = masked)."""
    os.makedirs(RAW, exist_ok=True); os.makedirs(CACHE, exist_ok=True)
    npz = os.path.join(CACHE, f"{day}-{hhmmss}.npz")
    if os.path.exists(npz):
        return np.load(npz)["mm"], "cached"
    name = f"MRMS_{PROD}_{day}-{hhmmss}.grib2.gz"
    gz = os.path.join(RAW, name)
    if not os.path.exists(gz):
        part = gz + ".part"
        _get(f"{BUCKET}/CONUS/{PROD}/{day}/{name}", part)
        os.replace(part, gz)
    crop, meta = read_crop_pygrib(gz)
    np.savez_compressed(npz, mm=crop, valid=meta["validDate"])
    return crop, "decoded"

import urllib.parse  # noqa: E402  (used by list_day)
