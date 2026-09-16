#!/usr/bin/env python3
"""Robustness checks behind the 2026-09 radar verification write-up.

match.py produces the headline grid. This produces everything that qualifies
it, and every qualifying number in the write-up comes from here:

  1. Is the PUBLIC vs TRAINED SPOTTER gap distinguishable from noise?
  2. Does match rate depend on reported hail size? If the unmatched residue
     were all marginal pea-sized hail that would be a different story than
     reports failing at random.
  3. Is any single year anomalous? A radar archive gap would otherwise
     masquerade as observers disagreeing with radar.
  4. Where do the unmatched reports actually sit -- just outside the ring, or
     nowhere near a signature at all?
  5. Match rate with radar-empty days excluded.

Check 5 is the one that changes the headline. Run after reduce.py.
"""

import bisect
import collections
import math
import time

import match as M

D_MAX, T_MAX = 5.0, 30.0          # the reference cell
COS_LAT = math.cos(math.radians(39.5))
PAD_LAT = D_MAX / 69.0 * 1.05
PAD_LON = D_MAX / (69.0 * COS_LAT) * 1.05

HEADLINE = ["TRAINED SPOTTER", "PUBLIC", "COCORAHS"]


def two_prop_z(k1, n1, k2, n2):
    """Two-sided two-proportion z-test."""
    if not n1 or not n2:
        return 0.0, 1.0
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def size_bucket(m):
    if m < 0.75:
        return "< 0.75 (sub-severe)"
    if m < 1.00:
        return "0.75 - 0.99"
    if m < 1.75:
        return "1.00 - 1.74"
    return ">= 1.75 (significant)"


BUCKETS = ["< 0.75 (sub-severe)", "0.75 - 0.99",
           "1.00 - 1.74", ">= 1.75 (significant)"]


def nearest_signature(rep, rtime, rlon, rlat):
    """Distance in miles to the closest signature within T_MAX, or None."""
    rt = rep["t"]
    lo = bisect.bisect_left(rtime, rt - T_MAX * 60)
    hi = bisect.bisect_right(rtime, rt + T_MAX * 60)
    best = None
    for i in range(lo, hi):
        dlat = rlat[i] - rep["lat"]
        if dlat > PAD_LAT or dlat < -PAD_LAT:
            continue
        dlon = rlon[i] - rep["lon"]
        if dlon > PAD_LON or dlon < -PAD_LON:
            continue
        dist = M.EARTH_MI * math.radians(math.hypot(dlon * COS_LAT, dlat))
        if best is None or dist < best:
            best = dist
    return best


def main():
    rtime, rlon, rlat, rprob, rsize = M.load_radar()
    reports = M.load_reports()

    # Radar rows per UTC day, so a day with no archive coverage is visible.
    day_rows = collections.Counter()
    for t in rtime:
        day_rows[time.strftime("%Y-%m-%d", time.gmtime(t))] += 1

    rows = []
    for rep in reports:
        nearest = nearest_signature(rep, rtime, rlon, rlat)
        day = time.strftime("%Y-%m-%d", time.gmtime(rep["t"]))
        rows.append({
            "rep": rep,
            "day": day,
            "day_rows": day_rows.get(day, 0),
            "nearest": nearest,
            "ok": nearest is not None and nearest <= D_MAX,
        })

    def rate(subset, key=None):
        num = collections.Counter()
        den = collections.Counter()
        for r in subset:
            k = key(r) if key else "ALL"
            den[k] += 1
            if r["ok"]:
                num[k] += 1
        return num, den

    print(f"Reference cell: {D_MAX:.0f} mi / {T_MAX:.0f} min, any signature")
    print(f"{len(reports):,} reports, {len(rtime):,} signatures")
    print()

    print("1. PUBLIC vs TRAINED SPOTTER")
    num, den = rate(rows, lambda r: r["rep"]["src"])
    k1, n1 = num["PUBLIC"], den["PUBLIC"]
    k2, n2 = num["TRAINED SPOTTER"], den["TRAINED SPOTTER"]
    z, p = two_prop_z(k1, n1, k2, n2)
    print(f"   PUBLIC          {100*k1/n1:.2f}%  ({k1}/{n1})")
    print(f"   TRAINED SPOTTER {100*k2/n2:.2f}%  ({k2}/{n2})")
    print(f"   difference {100*(k1/n1-k2/n2):+.2f} pts,  z = {z:+.2f},  p = {p:.3f}")
    k3, n3 = num["COCORAHS"], den["COCORAHS"]
    z2, p2 = two_prop_z(k1, n1, k3, n3)
    print(f"   PUBLIC vs COCORAHS ({100*k3/n3:.2f}%, {k3}/{n3}): "
          f"z = {z2:+.2f}, p = {p2:.3f}")
    print()

    print("2. match rate by reported hail size")
    num, den = rate(rows, lambda r: size_bucket(r["rep"]["mag"]))
    for b in BUCKETS:
        if den[b]:
            print(f"   {b:<24} {100*num[b]/den[b]:>5.1f}%   n={den[b]}")
    print()

    print("3. match rate by year (a radar outage shows up here)")
    num, den = rate(rows, lambda r: r["day"][:4])
    for y in sorted(den):
        print(f"   {y}  {100*num[y]/den[y]:>5.1f}%   n={den[y]}")
    print()

    unmatched = [r for r in rows if not r["ok"]]
    near = [r["nearest"] for r in unmatched if r["nearest"] is not None]
    print(f"4. the {len(unmatched)} unmatched reports")
    print(f"   no signature at all within {T_MAX:.0f} min: "
          f"{len(unmatched)-len(near)}")
    if near:
        near.sort()
        print(f"   nearest signature, miles: min {near[0]:.1f}, "
              f"median {near[len(near)//2]:.1f}, max {near[-1]:.1f}")
    print()
    days = collections.Counter(r["day"] for r in unmatched)
    print("   worst days (unmatched reports vs radar rows in box that day):")
    print(f"     {'day':<12} {'unmatched':>9} {'radar rows':>12}")
    for d, c in days.most_common(8):
        print(f"     {d:<12} {c:>9} {day_rows.get(d,0):>12}")
    print()

    print("5. match rate with radar-empty days excluded")
    print("   A day with zero signatures anywhere in the box is an archive gap,")
    print("   not radar disagreeing with an observer -- there is nothing there")
    print("   to disagree. NCEI: absence of SWDI data does not imply absence of")
    print("   weather.")
    print()
    for label, keep in [
        ("all days", lambda r: True),
        ("excluding days with 0 radar rows", lambda r: r["day_rows"] > 0),
        ("excluding days with <50 radar rows", lambda r: r["day_rows"] >= 50),
    ]:
        sub = [r for r in rows if keep(r)]
        num, den = rate(sub)
        print(f"   {label:<36} {100*num['ALL']/den['ALL']:>5.1f}%  "
              f"({num['ALL']}/{den['ALL']})")
        snum, sden = rate(sub, lambda r: r["rep"]["src"])
        for s in HEADLINE:
            if not sden[s]:
                continue
            lo, hi = M.wilson(snum[s], sden[s])
            print(f"       {s:<18} {100*snum[s]/sden[s]:>5.1f}%  "
                  f"[{lo:>4.1f}, {hi:>4.1f}]  n={sden[s]}")
        print()


if __name__ == "__main__":
    main()
