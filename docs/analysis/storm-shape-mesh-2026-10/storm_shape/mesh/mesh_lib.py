"""Stdlib-only reader for NCEP MRMS GRIB2 (template 3.0 lat/lon grid, template 5.41 = PNG packing).
Throwaway analysis code. No numpy/gdal/wgrib2 on this machine, so this decodes the GRIB2 sections and the embedded 16-bit
grayscale PNG itself. Verified against file MESH_00.50_20260608-220000 only (see B1 notes); other MRMS products
(e.g. MESH_Max_*) have the same layout but were not all decoded."""
import gzip, struct, zlib

# Grid of every CONUS MRMS 0.01-degree product (read from section 3, asserted below)
NI, NJ, LA1, LO1, D = 7000, 3500, 54.995, 230.005 - 360.0, 0.01

def _sections(b):
    p = 16
    while p < len(b) - 4:
        ln = struct.unpack(">I", b[p:p + 4])[0]; yield b[p + 4], b[p:p + ln]; p += ln

def _paeth(a, b, c):
    p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)

def read_crop(path, lat_max, lat_min, lon_min, lon_max):
    """Decode only the rows needed (PNG filters reference the previous row, so rows 0..last are unfiltered, rows outside
    the crop are discarded). Returns dict(rows, cols, lat_top, lon_left, mm=list of lists of float mm, time=(y,m,d,h,mi,s))."""
    b = gzip.open(path).read() if str(path).endswith(".gz") else open(path, "rb").read()
    assert b[:4] == b"GRIB" and b[7] == 2
    png = None; refv = None
    for sec, d in _sections(b):
        if sec == 3:
            ni, nj = struct.unpack(">II", d[30:38]); la1, lo1 = struct.unpack(">ii", d[46:54]); dd = struct.unpack(">I", d[63:67])[0]
            assert (ni, nj) == (NI, NJ) and abs(la1 / 1e6 - LA1) < 1e-6 and abs(lo1 / 1e6 - 360 - LO1) < 1e-6 and dd == 10000 and d[71] == 0, "unexpected grid"
        elif sec == 1:
            tm = struct.unpack(">HBBBBB", d[12:19])
        elif sec == 5:
            assert struct.unpack(">H", d[9:11])[0] == 41, "not PNG packing"
            R = struct.unpack(">f", d[11:15])[0]; E, Dd = struct.unpack(">hh", d[15:19]); nbits = d[19]
            assert nbits == 16
            refv = (R, 2.0 ** E, 10.0 ** Dd)          # Y = (R + X * 2^E) / 10^D
        elif sec == 7:
            png = d[5:]
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and refv
    q = 8; idat = []
    while q < len(png):
        l, t = struct.unpack(">I4s", png[q:q + 8])
        if t == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", png[q + 8:q + 18]); assert (w, h, bd, ct) == (NI, NJ, 16, 0)
        if t == b"IDAT": idat.append(png[q + 8:q + 8 + l])
        q += 12 + l
    raw = zlib.decompress(b"".join(idat))
    r0 = int(round((LA1 - lat_max) / D)); r1 = int(round((LA1 - lat_min) / D))
    c0 = int(round((lon_min - LO1) / D)); c1 = int(round((lon_max - LO1) / D))
    stride = NI * 2; prev = bytearray(stride); out = []
    for r in range(r1 + 1):
        o = r * (stride + 1); ft = raw[o]; cur = bytearray(raw[o + 1:o + 1 + stride])
        if ft == 1:
            for i in range(2, stride): cur[i] = (cur[i] + cur[i - 2]) & 255
        elif ft == 2:
            cur = bytearray([(a + b2) & 255 for a, b2 in zip(cur, prev)])
        elif ft == 3:
            for i in range(stride): cur[i] = (cur[i] + (((cur[i - 2] if i >= 2 else 0) + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = cur[i - 2] if i >= 2 else 0; c = prev[i - 2] if i >= 2 else 0
                cur[i] = (cur[i] + _paeth(a, prev[i], c)) & 255
        elif ft != 0: raise ValueError("bad PNG filter")
        prev = cur
        if r >= r0:
            xs = struct.unpack(">%dH" % (c1 - c0 + 1), bytes(cur[c0 * 2:(c1 + 1) * 2]))
            out.append([(refv[0] + x * refv[1]) / refv[2] for x in xs])
    return dict(rows=len(out), cols=c1 - c0 + 1, lat_top=LA1 - r0 * D, lon_left=LO1 + c0 * D, mm=out, time=tm)
