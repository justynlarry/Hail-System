#!/usr/bin/env python3
"""Forward match: for each LSR hail report, is there a radar hail signature near it?

Sweeps a 3x3 tolerance grid (2/5/10 miles against 15/30/60 minutes) and
stratifies by radar probability, rather than committing to one pair of
tolerances. A match rate that holds steady across the grid is a real signal; one
that swings with the tolerance is telling us the tolerance is doing the work.

Forward direction only. Duplication across radars and volume scans inflates the
candidate pool, which can only make a match easier to find -- that bias is
acknowledged and accepted here, and it is exactly why the reverse question
(signatures with no report) is deferred: it would need event clustering first.

Both sides are UTC, so no conversion happens anywhere in the matching.
"""

import bisect
import calendar
import collections
import csv
import math

from paths import REDUCED, REPORTS, YEARS

DISTS = [2.0, 5.0, 10.0]      # miles
TIMES = [15.0, 30.0, 60.0]    # minutes
CELLS = [(d, t) for d in DISTS for t in TIMES]
REF_CELL = CELLS.index((5.0, 30.0))   # reference pairing for the size comparison

# "ANY" counts every signature including the -999 rows, which answer "was there
# a signature?" but carry no probability and no size. The three PROB strata
# exclude them automatically, since -999 is below every threshold.
STRATA = ["ANY", "PROB>=10", "PROB>=50", "PROB=100"]

EARTH_MI = 3958.7613
MAX_D = max(DISTS)
MAX_T_SEC = max(TIMES) * 60.0
# Degree padding for the cheap pre-filter, generous at Front Range latitudes.
PAD_LAT = MAX_D / 69.0 * 1.05
PAD_LON = MAX_D / (69.0 * math.cos(math.radians(39.5))) * 1.05


def parse_ztime(s):
    return calendar.timegm((
        int(s[0:4]), int(s[4:6]), int(s[6:8]),
        int(s[8:10]), int(s[10:12]), int(s[12:14]), 0, 0, 0))


def parse_report_time(s):
    # "2016-04-26 02:27:00+00" -- already UTC, so the offset is decoration.
    return calendar.timegm((
        int(s[0:4]), int(s[5:7]), int(s[8:10]),
        int(s[11:13]), int(s[14:16]), int(s[17:19]), 0, 0, 0))


def load_radar():
    times, lons, lats, probs, sizes = [], [], [], [], []
    rows = 0
    for year in YEARS:
        path = REDUCED / f"hail-{year}-frontrange.csv"
        with open(path, newline="") as fh:
            r = csv.reader(fh)
            next(r)
            for row in r:
                rows += 1
                times.append(parse_ztime(row[0]))
                lons.append(float(row[1]))
                lats.append(float(row[2]))
                probs.append(int(row[8]))
                sizes.append(float(row[9]))
    # Files are per-year and roughly time-ordered, but bisect needs a real sort.
    order = sorted(range(rows), key=times.__getitem__)
    return (
        [times[i] for i in order], [lons[i] for i in order],
        [lats[i] for i in order], [probs[i] for i in order],
        [sizes[i] for i in order],
    )


def load_reports():
    out = []
    with open(REPORTS, newline="") as fh:
        for d in csv.DictReader(fh):
            out.append({
                "id": d["iem_id"],
                "t": parse_report_time(d["utc_datetime"]),
                "lat": float(d["latitude"]),
                "lon": float(d["longitude"]),
                "mag": float(d["magnitude"]),
                "src": d["report_source_norm"],
            })
    return out


def wilson(k, n):
    """95% Wilson interval -- COCORAHS has n=119, so plain percentages would
    imply a precision the sample size does not support."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963985
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (100 * max(0.0, centre - half), 100 * min(1.0, centre + half))


def main():
    print("loading radar rows ...", flush=True)
    rtime, rlon, rlat, rprob, rsize = load_radar()
    print(f"  {len(rtime):,} signatures, "
          f"{rtime[0]} .. {rtime[-1]} epoch", flush=True)

    reports = load_reports()
    print(f"  {len(reports):,} hail reports", flush=True)
    print()

    # matched[cell][stratum] -> Counter by source
    matched = [{s: collections.Counter() for s in STRATA} for _ in CELLS]
    totals = collections.Counter()
    # Size pairs at each cell: source -> list of (report_mag, radar_maxsize)
    pairs = [collections.defaultdict(list) for _ in CELLS]

    cos_lat = math.cos(math.radians(39.5))

    for rep in reports:
        src = rep["src"]
        totals[src] += 1
        rt, rla, rlo = rep["t"], rep["lat"], rep["lon"]

        lo = bisect.bisect_left(rtime, rt - MAX_T_SEC)
        hi = bisect.bisect_right(rtime, rt + MAX_T_SEC)

        hit = [{s: False for s in STRATA} for _ in CELLS]
        best = [None] * len(CELLS)   # (distance, maxsize) nearest sized signature

        for i in range(lo, hi):
            dlat = rlat[i] - rla
            if dlat > PAD_LAT or dlat < -PAD_LAT:
                continue
            dlon = rlon[i] - rlo
            if dlon > PAD_LON or dlon < -PAD_LON:
                continue
            # Equirectangular is good to well under 1% over 10 miles; the
            # reports are quantised to ~0.4 mi anyway, so great-circle precision
            # would be false rigour.
            x = dlon * cos_lat
            dist = EARTH_MI * math.radians(math.hypot(x, dlat))
            if dist > MAX_D:
                continue
            dt_min = abs(rtime[i] - rt) / 60.0

            p = rprob[i]
            sz = rsize[i]
            for c, (dmax, tmax) in enumerate(CELLS):
                if dist > dmax or dt_min > tmax:
                    continue
                h = hit[c]
                h["ANY"] = True
                if p >= 10:
                    h["PROB>=10"] = True
                    if p >= 50:
                        h["PROB>=50"] = True
                        if p >= 100:
                            h["PROB=100"] = True
                if sz != -999 and (best[c] is None or dist < best[c][0]):
                    best[c] = (dist, sz)

        for c in range(len(CELLS)):
            for s in STRATA:
                if hit[c][s]:
                    matched[c][s][src] += 1
            if best[c] is not None and rep["mag"] > 0:
                pairs[c][src].append((rep["mag"], best[c][1]))

    render(matched, totals, pairs)


def render(matched, totals, pairs):
    order = [s for s, _ in totals.most_common()]
    headline = [s for s in order if totals[s] >= 100]
    rest = [s for s in order if totals[s] < 100]
    rest_n = sum(totals[s] for s in rest)

    print("=" * 78)
    print("MATCH RATE: share of hail reports with >=1 radar signature in tolerance")
    print("=" * 78)
    print()
    print(f"{'miles':>5} {'min':>4} | " + " | ".join(f"{s:>16}" for s in STRATA))
    print("-" * 78)
    for c, (d, t) in enumerate(CELLS):
        cells = []
        for s in STRATA:
            k = sum(matched[c][s][x] for x in order)
            cells.append(f"{100*k/sum(totals.values()):>6.1f}% ({k:>4})")
        print(f"{d:>5.0f} {t:>4.0f} | " + " | ".join(f"{v:>16}" for v in cells))
    print()
    print(f"denominator: all {sum(totals.values()):,} reports")
    print()

    for s in STRATA:
        print("=" * 78)
        print(f"BY REPORT SOURCE -- stratum {s}")
        print("=" * 78)
        print()
        hdr = f"{'miles':>5} {'min':>4} | " + " | ".join(
            f"{x[:14]:>14} (n={totals[x]})" for x in headline)
        print(hdr)
        print("-" * len(hdr))
        for c, (d, t) in enumerate(CELLS):
            cells = []
            for x in headline:
                k = matched[c][s][x]
                n = totals[x]
                cells.append(f"{100*k/n:>5.1f}% ({k:>4})")
            print(f"{d:>5.0f} {t:>4.0f} | " + " | ".join(
                f"{v:>{max(14,len(x[:14])+7)}}" for v, x in zip(cells, headline)))
        print()
        # Wilson bands at the reference cell so small n cannot masquerade as precision
        d, t = CELLS[REF_CELL]
        print(f"  95% CI at the {d:.0f} mi / {t:.0f} min cell:")
        for x in headline:
            k, n = matched[REF_CELL][s][x], totals[x]
            lo, hi = wilson(k, n)
            print(f"    {x:<18} {100*k/n:>5.1f}%  [{lo:>5.1f}, {hi:>5.1f}]  n={n}")
        if rest:
            k = sum(matched[REF_CELL][s][x] for x in rest)
            lo, hi = wilson(k, rest_n)
            print(f"    {'(all other sources)':<18} {100*k/rest_n:>5.1f}%  "
                  f"[{lo:>5.1f}, {hi:>5.1f}]  n={rest_n}")
        print()

    d, t = CELLS[REF_CELL]
    print("=" * 78)
    print(f"SIZE: radar MAXSIZE vs reported magnitude, nearest signature")
    print(f"at the {d:.0f} mi / {t:.0f} min cell; -999 rows excluded")
    print("=" * 78)
    print()
    print(f"{'source':<18} {'n':>5} {'rep med':>8} {'rad med':>8} "
          f"{'med diff':>9} {'rad>rep':>8} {'corr':>6}")
    print("-" * 70)
    for x in headline + ["(all other sources)"]:
        if x == "(all other sources)":
            data = [p for s in rest for p in pairs[REF_CELL][s]]
        else:
            data = pairs[REF_CELL][x]
        if not data:
            continue
        rep = sorted(p[0] for p in data)
        rad = sorted(p[1] for p in data)
        diff = sorted(p[1] - p[0] for p in data)
        med = lambda v: v[len(v) // 2] if len(v) % 2 else (v[len(v)//2-1]+v[len(v)//2])/2
        over = sum(1 for p in data if p[1] > p[0])
        n = len(data)
        mx, my = sum(p[0] for p in data)/n, sum(p[1] for p in data)/n
        sx = math.sqrt(sum((p[0]-mx)**2 for p in data))
        sy = math.sqrt(sum((p[1]-my)**2 for p in data))
        cov = sum((p[0]-mx)*(p[1]-my) for p in data)
        corr = cov/(sx*sy) if sx and sy else float('nan')
        print(f"{x:<18} {n:>5} {med(rep):>8.2f} {med(rad):>8.2f} "
              f"{med(diff):>9.2f} {100*over/n:>7.1f}% {corr:>6.2f}")
    print()
    print("  rep med / rad med  : median reported and radar-estimated size, inches")
    print("  med diff           : median of (radar - report), per matched pair")
    print("  rad>rep            : share of pairs where radar estimate exceeds report")
    print("  corr               : Pearson r between the two sizes")


if __name__ == "__main__":
    main()
