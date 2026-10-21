"""Reads a NOAA MRMS MESH GRIB2 file with the standard library only.

Runtime images have no numpy or pygrib, so this decodes the GRIB2 sections and the
PNG inside them.  The grid is NOAA's, the same for every CONUS MRMS product, so it
is checked and not configured.  No database or network, fetch.py downloads, then 
the loader writes.

Two things the analysis decoder (docs/analysis/.../mesh_lib.py) didn't encounter:  
    - some files pack 8-bit PNG samples, not 16
    - Loud errors beat assert, which python -O removes.
"""


import gzip
import math
import struct
import zlib
from datetime import datetime, timezone

NI, NJ = 7000,3500
LA1 = 54.995
LO1 = -129.995
STEP = 0.01

MM_PER_INCH = 25.4

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class MeshError(Exception):
    """The file is not the MRMS file this reader understands, nothing returned."""


def _sections(data):
    pos = 16
    while pos < len(data) - 4:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        yield data[pos + 4], data[pos:pos + length]
        pos += length


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _png_rows(raw, stride, bpp, last_row):
    """Yield (row_number, bytes) for rows 0..last_row with the PNG filters undone.
    
    Each filter refers to the row above, so every row up to the last wanted one has to
    be unfiltered even if it is outside the crop.
    """
    prev = bytearray(stride)
    for r in range(last_row + 1):
        offset = r * (stride + 1)
        kind = raw[offset]
        cur = bytearray(raw[offset + 1:offset + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 255
        elif kind == 2:
            cur = bytearray([(a + b) & 255 for a, b in zip(cur, prev)])
        elif kind == 3:
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                corner = prev[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + _paeth(left, prev[i], corner)) & 255
        elif kind != 0:
            raise MeshError(f"unknown PNG filter {kind}")
        prev = cur
        yield r, cur


def _png_samples(png):
    """Returns (raw filtered bytes, bytes per sample) from the PNG in section 7."""
    if png[:8] != _PNG_SIGNATURE:
        raise MeshError("section 7 is not a PNG")
    pos = 8
    idat = []
    bytes_per_sample = None
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if (width, height, colour, interlace) != (NI, NJ, 0, 0) or depth not in (8, 16):
                raise MeshError(f"unexpected PNG header {(width, height, depth, colour, interlace)}")
            bytes_per_sample = depth // 8
        elif kind == b"IDAT":
            idat.append(body)
        pos += 12 + length
    if bytes_per_sample is None or not idat:
        raise MeshError("PNG has no header or no data")
    return zlib.decompress(b"".join(idat)), bytes_per_sample


def read_cells(path, box, floor_mm):
    """Decode the crop box and return the cells at or above floor_mm.
    
    box is (lat_max, lat_min, lon_min, lon_max), the file is gzip if the name ends .gz.
    Returns a dict:  valid_time (UTC datetime from the file itself), cells_in_box,
    max_mm (largest value in the box, None if none reached the floor) and cells, a list
    of (grid_row, grid_col, mm).  Rows and columns are the file's own indexes, the cell 
    centre is LA1 - row * STEP, LO1 + col * STEP.  A negative value means no data or no
    hail and doesn't reach the floor.
    """
    lat_max, lat_min, lon_min, lon_max = box
    with (gzip.open if str(path).endswith(".gz") else open)(path, "rb") as f:
        data = f.read()
    if data[:4] != b"GRIB" or data[7] != 2:
        raise MeshError("not a GRIB2 file")
    png = valid = packing = None
    for number, body in _sections(data):
        if number == 1:
            year, month, day, hour, minute, second = struct.unpack(">HBBBBB", body[12:19])
            valid = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        elif number == 3:
            ni, nj = struct.unpack(">II", body[30:38])
            la1, lo1 = struct.unpack(">ii", body[46:54])
            step = struct.unpack(">I", body[63:67])[0]
            if ((ni, nj) != (NI, NJ) or abs(la1 / 1e6 - LA1) > 1e-6
                    or abs(lo1 / 1e6 - 360 - LO1) > 1e-6 or step != 10000 or body[71] != 0):
                raise MeshError("the grid is not the MRMS CONUS grid")
        elif number == 5:
            if struct.unpack(">H", body[9:11])[0] != 41:
                raise MeshError("data is not PNG packed")
            ref = struct.unpack(">f", body[11:15])[0]
            binary, decimal = struct.unpack(">hh", body[15:19])
            packing = (ref, 2.0 ** binary, 10.0 ** decimal, body[19])
        elif number == 6 and body[5] != 255:
            raise MeshError("the file has a bitmap, missing values are not handled")
        elif number == 7:
            png = body[5:]
    if png is None or packing is None or valid is None:
        raise MeshError("a section is missing")
    ref, scale, divisor, nbits = packing
    raw, bytes_per_sample = _png_samples(png)
    if nbits != 8 * bytes_per_sample:
        raise MeshError(f"packed at {nbits} bits but the PNG holds {8 * bytes_per_sample}")

    row0 = round((LA1 - lat_max) / STEP)
    row1 = round((LA1 - lat_min) / STEP)
    col0 = round((lon_min - LO1) / STEP)
    col1 = round((lon_max - LO1) / STEP)
    if not (0 <= row0 <= row1 < NJ and 0 <= col0 <= col1 < NI):
        raise MeshError("the crop box is outside the grid")

    # Smallest packed value worth converting, with a 1 of slack:  the exact test follows.
    loosest = math.floor((floor_mm * divisor - ref) / scale) -1
    code = ">%d%s" % (col1 - col0 + 1, "B" if bytes_per_sample == 1 else "H")
    stride = NI * bytes_per_sample
    cells = []
    best = None
    for r, row in _png_rows(raw, stride, bytes_per_sample, row1):
        if r < row0:
            continue
        values = struct.unpack(code, bytes(row[col0 * bytes_per_sample:(col1 + 1) * bytes_per_sample]))
        for j, x in enumerate(values):
            if x < loosest:
                continue
            mm  = (ref + x * scale) / divisor
            if mm >= floor_mm:
                cells.append((r, col0 + j, mm))
                best = mm if best is None or mm > best else best
    return {"valid_time": valid, "cells_in_box": (row1 - row0 + 1) * (col1 - col0 + 1),
            "max_mm": best, "cells": cells}

