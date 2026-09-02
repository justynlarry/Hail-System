#!/usr/bin/env python3
"""
Check a list of ZIP codes against the raw TIGER/Line ZCTA shapefile.

Reads the .dbf (the attribute table that ships alongside the .shp) directly.
We only need the ZCTA5 codes, not the geometry, so there is no reason to pull
in GDAL or geopandas for this -- the .dbf is a simple binary format and the
stdlib `struct` module reads it in about forty lines.

Usage:
    ./check_zctas.py tl_2024_us_zcta520.dbf coverage_zips.txt

Exits 1 if any coverage ZIP has no ZCTA polygon. That is intentional: a ZIP
with no ZCTA will silently return zero listings from the spatial join while
still costing a RentCast call, so it should stop a load rather than warn.
"""

import struct
import sys
from pathlib import Path

# TIGER renames this field every vintage. 20 = 2020 census, 10 = 2010.
ZCTA_FIELD_CANDIDATES = ("ZCTA5CE20", "ZCTA5CE10", "ZCTA5CE", "GEOID20", "GEOID10")


def read_dbf(path):
    """Yield each record of a .dbf as a dict of {field_name: str}."""
    with open(path, "rb") as fh:
        # --- Header: fixed 32 bytes. -------------------------------------
        # <  little-endian
        # B  byte 0   : version
        # 3B bytes 1-3: last-update Y/M/D
        # I  bytes 4-7: record count
        # H  bytes 8-9: header length (where the records start)
        # H  bytes 10-11: record length
        header = fh.read(32)
        if len(header) < 32:
            raise ValueError(f"{path}: too short to be a .dbf")
        _ver, _y, _m, _d, n_records, header_len, record_len = struct.unpack(
            "<B3BIHH", header[:12]
        )

        # --- Field descriptors: 32 bytes each, terminated by 0x0D. -------
        fields = []
        while True:
            desc = fh.read(32)
            if not desc or desc[0] == 0x0D:  # 0x0D marks end of descriptors
                break
            name = desc[:11].split(b"\x00")[0].decode("ascii", "replace")
            length = desc[16]
            fields.append((name, length))

        # Records begin at header_len regardless of where we stopped reading.
        fh.seek(header_len)

        for _ in range(n_records):
            raw = fh.read(record_len)
            if len(raw) < record_len:
                break  # truncated file; caller sees a short count
            if raw[0:1] == b"*":  # 0x2A = record marked deleted
                continue
            offset = 1  # skip the deletion flag byte
            row = {}
            for name, length in fields:
                row[name] = raw[offset:offset + length].decode("latin-1").strip()
                offset += length
            yield row


def pick_zcta_field(sample):
    for candidate in ZCTA_FIELD_CANDIDATES:
        if candidate in sample:
            return candidate
    raise SystemExit(
        f"No ZCTA field found. Fields present: {sorted(sample)}\n"
        f"Add the right one to ZCTA_FIELD_CANDIDATES."
    )


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__.strip())

    dbf_path, zips_path = Path(sys.argv[1]), Path(sys.argv[2])
    if dbf_path.suffix.lower() == ".shp":
        dbf_path = dbf_path.with_suffix(".dbf")

    records = read_dbf(dbf_path)
    try:
        first = next(records)
    except StopIteration:
        raise SystemExit(f"{dbf_path}: no records")

    field = pick_zcta_field(first)
    zctas = {first[field]}
    zctas.update(row[field] for row in records)

    wanted = [
        line.strip()
        for line in zips_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    missing = [z for z in wanted if z not in zctas]

    print(f"ZCTA field used      : {field}")
    print(f"ZCTAs in shapefile   : {len(zctas):,}")
    print(f"Coverage ZIPs checked : {len(wanted)}")
    print(f"Matched              : {len(wanted) - len(missing)}")
    print(f"No ZCTA polygon      : {len(missing)}")

    if missing:
        print("\nThese have no ZCTA and will return zero listings:")
        for z in missing:
            print(f"  {z}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
