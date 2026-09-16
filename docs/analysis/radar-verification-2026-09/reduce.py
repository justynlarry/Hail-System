#!/usr/bin/env python3
"""Stream-decompress the SWDI hail files and keep only Front Range rows.

Never extracts a full file: zcat feeds us a pipe and we write the survivors.
One pass per year does counting and filtering together -- decompressing 640 MB
twice to get statistics we could have collected the first time would be silly.

Bounding box is the exact MBR of RBI's 193 coverage ZCTAs (see coverage_bbox.py)
plus a 0.30 degree margin, ~20 miles, so that a radar signature just outside the
territory is still available when Phase 2 applies its spatial tolerance. Over-
inclusion is cheap; under-inclusion means re-running this whole reduction.
"""

import csv
import math
import subprocess
import sys

from paths import REDUCED as OUT, SWDI_DIR as SRC, YEARS

# Coverage MBR, NAD83, from coverage_bbox.py:
#   lon -105.72667 .. -103.70626   lat 38.40715 .. 40.99833
# SWDI is WGS84; the datum difference here is ~1 m, immaterial against a 0.30
# degree margin, so we compare directly rather than reprojecting.
MARGIN = 0.30
LON_MIN, LON_MAX = -105.72667 - MARGIN, -103.70626 + MARGIN
LAT_MIN, LAT_MAX = 38.40715 - MARGIN, 40.99833 + MARGIN

HEADER = ["ZTIME", "LON", "LAT", "WSR_ID", "CELL_ID", "RANGE", "AZIMUTH",
          "SEVPROB", "PROB", "MAXSIZE"]
NCOL = len(HEADER)


def reduce_year(path, out_path):
    total = kept = comments = bad_fieldcount = bad_coord = 0
    # -999 is SWDI's null marker for the three estimate columns, the same shape
    # of trap as IEM's literal "None". Counted, never coerced to zero.
    null_prob = null_maxsize = 0
    prob_vals = {}
    size_min, size_max = math.inf, -math.inf
    tmin, tmax = None, None

    proc = subprocess.Popen(["zcat", str(path)], stdout=subprocess.PIPE, text=True)
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        for line in proc.stdout:
            if line.startswith("#"):
                comments += 1
                continue
            line = line.rstrip("\n")
            if not line:
                continue
            total += 1
            row = line.split(",")
            if len(row) != NCOL:
                # No quoted fields exist in this format, so a split is safe and
                # a wrong count is genuine corruption. Loud, not silent.
                bad_fieldcount += 1
                continue
            try:
                lon = float(row[1])
                lat = float(row[2])
            except ValueError:
                bad_coord += 1
                continue
            # NaN/Infinity parse fine and then poison every comparison below.
            if not (math.isfinite(lon) and math.isfinite(lat)):
                bad_coord += 1
                continue
            if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
                continue

            kept += 1
            writer.writerow(row)

            ztime = row[0]
            tmin = ztime if tmin is None or ztime < tmin else tmin
            tmax = ztime if tmax is None or ztime > tmax else tmax
            prob = row[8]
            prob_vals[prob] = prob_vals.get(prob, 0) + 1
            if prob == "-999":
                null_prob += 1
            size = row[9]
            if size == "-999":
                null_maxsize += 1
            else:
                try:
                    s = float(size)
                    if math.isfinite(s):
                        size_min = min(size_min, s)
                        size_max = max(size_max, s)
                except ValueError:
                    pass

    rc = proc.wait()
    if rc != 0:
        raise SystemExit(f"zcat failed on {path} with exit {rc}")

    return {
        "total": total, "kept": kept, "comments": comments,
        "bad_fieldcount": bad_fieldcount, "bad_coord": bad_coord,
        "null_prob": null_prob, "null_maxsize": null_maxsize,
        "prob_vals": prob_vals,
        "size_min": size_min, "size_max": size_max,
        "tmin": tmin, "tmax": tmax,
    }


def main():
    OUT.mkdir(exist_ok=True)
    grand = {"total": 0, "kept": 0, "bad_fieldcount": 0, "bad_coord": 0,
             "null_prob": 0, "null_maxsize": 0}
    all_prob = {}
    gmin, gmax = math.inf, -math.inf

    print(f"bbox lon {LON_MIN:.5f}..{LON_MAX:.5f}  lat {LAT_MIN:.5f}..{LAT_MAX:.5f}")
    print()
    print(f"{'year':6} {'total rows':>13} {'kept':>9} {'kept %':>8} "
          f"{'ratio':>9} {'out bytes':>11}")

    for year in YEARS:
        src = SRC / f"hail-{year}.csv.gz"
        if not src.exists():
            raise SystemExit(f"missing input: {src}")
        dst = OUT / f"hail-{year}-frontrange.csv"
        st = reduce_year(src, dst)
        size = dst.stat().st_size
        pct = 100.0 * st["kept"] / st["total"] if st["total"] else 0.0
        ratio = st["total"] / st["kept"] if st["kept"] else float("inf")
        print(f"{year:<6} {st['total']:>13,} {st['kept']:>9,} {pct:>7.3f}% "
              f"{ratio:>8.0f}x {size:>11,}", flush=True)

        for k in grand:
            grand[k] += st[k]
        for p, c in st["prob_vals"].items():
            all_prob[p] = all_prob.get(p, 0) + c
        gmin = min(gmin, st["size_min"])
        gmax = max(gmax, st["size_max"])

        if st["bad_fieldcount"] or st["bad_coord"]:
            print(f"       !! {year}: {st['bad_fieldcount']} field-count, "
                  f"{st['bad_coord']} bad-coord rows", flush=True)

    print()
    print(f"TOTAL  {grand['total']:>13,} {grand['kept']:>9,} "
          f"{100.0*grand['kept']/grand['total']:>7.3f}% "
          f"{grand['total']/grand['kept']:>8.0f}x")
    print()
    print(f"rejected rows: field-count {grand['bad_fieldcount']}, "
          f"bad/non-finite coord {grand['bad_coord']}")
    print(f"kept rows with PROB = -999    : {grand['null_prob']:,}")
    print(f"kept rows with MAXSIZE = -999 : {grand['null_maxsize']:,}")
    print(f"MAXSIZE range (excluding -999): {gmin} .. {gmax} in")
    print()
    print("PROB distribution over kept rows:")
    for p in sorted(all_prob, key=lambda v: float(v)):
        print(f"  {p:>6} : {all_prob[p]:>9,}")


if __name__ == "__main__":
    main()
