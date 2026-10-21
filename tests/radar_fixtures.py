"""Builds small synthetic MRMS-style GRIB2 files for the radar tests.

Real files are 7000 x 3500 and hold the hail in a few rows, so the grid is full size
(the reader checks it) and almost all zero.  Packing: value = (REF + x) / 10, so x = 0 is
-1.0 (no data) and x = 137 is 12.7 mm.
"""

import gzip
import struct
import zlib

NI, NJ = 7000, 3500
REF = -10.0

# Rows 10..20, columns 100..130 of the file: a box near the top-left corner
BOX = (54.995 - 0.10, 54.995 - 0.20, -129.995 + 1.00, -129.995 + 1.30)
FLOOR_MM = 12.7


def mm(x):
    return (REF + x) / 10.0


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_row(kind, cur, prev, bpp):
    out = bytearray(len(cur))
    for i, value in enumerate(cur):
        left = cur[i - bpp] if i >= bpp else 0
        up = prev[i]
        corner = prev[i - bpp] if i >= bpp else 0
        predicted = (0, left, up, (left + up) >> 1, _paeth(left, up, corner))[kind]
        out[i] = (value - predicted) & 255
    return bytes([kind]) + bytes(out)


def png(samples, depth, filters):
    """samples: {(row, col): x}.  filters: {row: PNG filter type}, default 0."""
    bpp = depth // 8
    stride = NI * bpp
    zero = bytes(stride)
    blank = b"\x00" + zero
    by_row = {}
    for (sr, sc), x in samples.items():
        by_row.setdefault(sr, {})[sc] = x
    rows = []
    prev = zero
    for r in range(NJ):
        if r not in by_row and not filters.get(r, 0):
            rows.append(blank)       # a zero row stored unfiltered, whatever is above it
            prev = zero
            continue
        cur = bytearray(zero)
        for sc, x in by_row.get(r, {}).items():
            cur[sc * bpp:(sc + 1) * bpp] = x.to_bytes(bpp, "big")
        cur = bytes(cur)
        rows.append(_filter_row(filters.get(r, 0), cur, prev, bpp))
        prev = cur
    raw = b"".join(rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", NI, NJ, depth, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _section(number, body):
    return struct.pack(">IB", len(body) + 5, number) + body


def grib(samples, depth=16, filters=None, *, ni=NI, template=41, nbits=None, bitmap=False,
         when=(2026, 6, 9, 6, 0, 0)):
    s1 = bytearray(16)
    s1[7:14] = struct.pack(">HBBBBB", *when)
    s3 = bytearray(67)
    s3[25:33] = struct.pack(">II", ni, NJ)
    s3[41:49] = struct.pack(">ii", 54995000, 230005000)
    s3[58:62] = struct.pack(">I", 10000)
    s3[66] = 0
    s5 = bytearray(15)
    s5[4:6] = struct.pack(">H", template)
    s5[6:10] = struct.pack(">f", REF)
    s5[10:14] = struct.pack(">hh", 0, 1)
    s5[14] = nbits or depth
    s6 = bytes([0 if bitmap else 255])
    sections = (_section(1, bytes(s1)) + _section(3, bytes(s3)) + _section(5, bytes(s5))
                + _section(6, s6) + _section(7, png(samples, depth, filters or {})))
    return b"GRIB\0\0\0\2" + struct.pack(">Q", 16 + len(sections) + 4) + sections + b"7777"


def write(path, data, gz=False):
    with (gzip.open(path, "wb") if gz else open(path, "wb")) as f:
        f.write(data)


# -- IEM warning shapefile (hailsys/radar/warnings.py) ------------------------------------------

import io
import zipfile

SQUARE = [(-105.0, 40.0), (-105.0, 40.1), (-104.9, 40.1), (-104.9, 40.0), (-105.0, 40.0)]
BOWTIE = [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)]

_DBF_FIELDS = [("WFO", 3), ("ISSUED", 12), ("EXPIRED", 12), ("PHENOM", 2), ("GTYPE", 1), ("SIG", 1),
               ("ETN", 4), ("POLY_BEG", 12), ("POLY_END", 12), ("PROD_ID", 36), ("VTEC_YR", 4)]


def warning(wfo="BOU", phenom="SV", sig="W", gtype="P", etn=1, year=1999, begin="199901011200",
            end="199901011230", issued="199901011200", expired="199901011300", product="p1",
            ring=SQUARE, deleted=False, kind=5, parts=1):
    """One record for shapefile_zip().  ring None with kind 0 writes a null shape."""
    return {"row": {"WFO": wfo, "ISSUED": issued, "EXPIRED": expired, "PHENOM": phenom, "GTYPE": gtype,
                    "SIG": sig, "ETN": str(etn), "POLY_BEG": begin, "POLY_END": end,
                    "PROD_ID": product, "VTEC_YR": str(year)},
            "ring": ring, "deleted": deleted, "kind": kind, "parts": parts}


def _dbf(records):
    header = 32 + 32 * len(_DBF_FIELDS) + 1
    size = 1 + sum(width for _, width in _DBF_FIELDS)
    out = bytearray(struct_pack_dbf(len(records), header, size))
    for name, width in _DBF_FIELDS:
        out += name.encode().ljust(11, b"\0") + b"C" + bytes(4) + bytes([width, 0]) + bytes(14)
    out += b"\x0d"
    for rec in records:
        out += b"*" if rec["deleted"] else b" "
        for name, width in _DBF_FIELDS:
            out += rec["row"][name].encode("latin-1").ljust(width)[:width]
    return bytes(out)


def struct_pack_dbf(count, header, size):
    return b"\x03" + bytes(3) + struct.pack("<IHH", count, header, size) + bytes(20)


def _shp(records):
    body = bytearray()
    for number, rec in enumerate(records, 1):
        ring = rec["ring"] or []
        if rec["kind"] == 5:
            xs = [p[0] for p in ring] or [0]
            ys = [p[1] for p in ring] or [0]
            content = struct.pack("<i4d2i", 5, min(xs), min(ys), max(xs), max(ys), rec["parts"], len(ring))
            content += struct.pack(f"<{rec['parts']}i", *([0] * rec["parts"]))
            content += b"".join(struct.pack("<2d", x, y) for x, y in ring)
        else:
            content = struct.pack("<i", rec["kind"]) + bytes(40)
        body += struct.pack(">ii", number, len(content) // 2) + content
    head = struct.pack(">i", 9994) + bytes(20) + struct.pack(">i", (100 + len(body)) // 2)
    head += struct.pack("<ii", 1000, 5) + bytes(64)
    return head + bytes(body)


def shapefile_zip(records, path=None):
    """An IEM style export zip (.shp and .dbf; .shx and the rest are not read).  Returns bytes,
    or writes to path."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("wwa_test.shp", _shp(records))
        z.writestr("wwa_test.dbf", _dbf(records))
    data = buffer.getvalue()
    if path:
        with open(path, "wb") as f:
            f.write(data)
    return data
