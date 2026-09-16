#!/usr/bin/env python3
"""Bounding box of RBI's 193 coverage ZCTAs, straight from the TIGER shapefile.

No geopandas/GDAL here on purpose: a shapefile polygon record carries its own
minimum bounding rectangle in the first 32 bytes of its content, so the whole
job is stdlib struct + seeks driven by the .shx index. Reading the 822 MB .shp
sequentially would be pointless when .shx gives us the offset of each record.

Output is NAD83 (4269) degrees, matching the source. At Front Range latitudes
the NAD83->WGS84 shift is on the order of a metre, far under the margin we add
downstream, but the datum is stated so nobody has to guess later.
"""

import struct
import sys

from paths import COVERAGE_ZIPS as COVERAGE, TIGER


def read_coverage_zips(path):
    zips = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            zips.add(line)
    return zips


def dbf_field_values(path, field_name):
    """Yield one field's value per record, in physical record order.

    Record order in .dbf is the same as in .shp; that parallelism is what lets
    us match a ZCTA code to a shape without a real shapefile reader.
    """
    with open(path, "rb") as fh:
        header = fh.read(32)
        num_records, header_len, record_len = struct.unpack("<IHH", header[4:12])

        fields = []
        offset = 1  # byte 0 of each record is the deletion flag
        while True:
            desc = fh.read(32)
            if desc[0:1] == b"\x0d":
                break
            name = desc[0:11].rstrip(b"\x00").decode("ascii")
            length = desc[16]
            fields.append((name, offset, length))
            offset += length

        match = [f for f in fields if f[0] == field_name]
        if not match:
            raise SystemExit(
                f"field {field_name!r} not in .dbf; have {[f[0] for f in fields]}"
            )
        _, fld_off, fld_len = match[0]

        fh.seek(header_len)
        for _ in range(num_records):
            rec = fh.read(record_len)
            if len(rec) < record_len:
                break
            yield rec[fld_off : fld_off + fld_len].decode("ascii").strip()


def shx_offsets(path):
    """Byte offset of each .shp record, from the .shx index."""
    data = path.read_bytes()
    body = data[100:]
    for i in range(0, len(body), 8):
        (offset_words,) = struct.unpack(">i", body[i : i + 4])
        yield offset_words * 2


def shape_bbox(shp, offset):
    """(xmin, ymin, xmax, ymax) from one polygon record's stored MBR."""
    shp.seek(offset + 8)  # skip the 8-byte record header
    (shape_type,) = struct.unpack("<i", shp.read(4))
    if shape_type == 0:  # null shape
        return None
    if shape_type not in (3, 5, 13, 15, 23, 25):
        raise SystemExit(f"unexpected shape type {shape_type} at offset {offset}")
    return struct.unpack("<4d", shp.read(32))


def main():
    wanted = read_coverage_zips(COVERAGE)
    codes = list(dbf_field_values(TIGER.with_suffix(".dbf"), "ZCTA5CE20"))
    offsets = list(shx_offsets(TIGER.with_suffix(".shx")))
    # 10 of RBI's 193 zips are PO-box / university / federal-center zips with no
    # ZCTA polygon at all. They are reported below rather than passed over,
    # because the same 10 will silently match nothing in any coverage join.

    if len(codes) != len(offsets):
        raise SystemExit(f"dbf/shx record count mismatch: {len(codes)} vs {len(offsets)}")

    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    found = set()

    with open(TIGER.with_suffix(".shp"), "rb") as shp:
        for code, offset in zip(codes, offsets):
            if code not in wanted:
                continue
            box = shape_bbox(shp, offset)
            if box is None:
                continue
            found.add(code)
            xmin = min(xmin, box[0])
            ymin = min(ymin, box[1])
            xmax = max(xmax, box[2])
            ymax = max(ymax, box[3])

    missing = wanted - found
    print(f"coverage zips in config : {len(wanted)}")
    print(f"matched in TIGER ZCTAs  : {len(found)}")
    if missing:
        print(f"NOT FOUND in TIGER      : {sorted(missing)}", file=sys.stderr)

    print()
    print("exact bbox of coverage ZCTAs (NAD83 / EPSG:4269):")
    print(f"  lon {xmin:.5f} .. {xmax:.5f}")
    print(f"  lat {ymin:.5f} .. {ymax:.5f}")


if __name__ == "__main__":
    main()
