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
