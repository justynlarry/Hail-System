#!/usr/bin/env python3
"""Does a lone hail report corroborate as well as one from a busy storm day?

Follow-up to the main study, same matching, no new tolerances. The worry this
addresses: 382 of 1,468 Denver-local hail days carry exactly one report
(hail-consolidated.md 7), so if single-report days corroborate worse, a large
share of our targeting data is weaker than the headline 95% suggests -- and a
lone PUBLIC report is the specific case.

Grouping is by LOCAL Denver day, not UTC day. A 02:27 UTC report is the
previous evening in Denver, and putting it on the wrong day would split single
evening storms across two buckets. This is the only place in this study where
the timezone matters; the matching itself is UTC on both sides.

The reports export is hail-only, so grouping by day and report type collapses
to grouping by day. If this is ever rerun over a mixed-type export, key
`day_of` on (local day, report type) and nothing else changes.

Archive-gap days are excluded BEFORE bucketing, not after. A day with no SWDI
coverage contributes reports that cannot match, and concentrated in one bucket
they manufacture a difference that is an artifact of the archive rather than a
property of report density. Both figures are printed so the size of that
effect is visible.

**The gap test has to run on the UTC day as well as the local day, and this is
not optional.** A UTC day with zero rows spans two local days, each of which
usually does have rows from the adjacent UTC day -- so local-day keying alone
retains 46 of the 50 gap reports while still scoring them as non-matches.

In this data the leak is one storm: UTC day 2016-07-08 splits into local
2016-07-07 (22 reports) and 2016-07-08 (19), both busy enough to land in the
11+ bucket, which it then depresses -- flattering the single-report bucket by
comparison. Excluding on local day alone gives a 4.6-point gradient across the
buckets; excluding on both gives 2.4 points with overlapping intervals. The
first is mostly an artifact of the archive.
"""

import collections
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import match as M
from checks import D_MAX, T_MAX, nearest_signature

DENVER = ZoneInfo("America/Denver")

BUCKETS = [("1", 1, 1), ("2-3", 2, 3), ("4-10", 4, 10), ("11+", 11, 10**9)]


def bucket_of(n):
    for label, lo, hi in BUCKETS:
        if lo <= n <= hi:
            return label
    raise AssertionError(n)


def local_day(epoch):
    return datetime.fromtimestamp(epoch, DENVER).strftime("%Y-%m-%d")


def table(rows, key, title, note=None):
    """Match rate by `key`, with n and a Wilson band on every cell."""
    num, den = collections.Counter(), collections.Counter()
    for r in rows:
        k = key(r)
        den[k] += 1
        if r["ok"]:
            num[k] += 1
    print(f"  {title}")
    if note:
        print(f"  {note}")
    print(f"    {'bucket':<18} {'rate':>7} {'95% CI':>16} {'n':>7}")
    return num, den


def emit(num, den, order):
    for k in order:
        if not den[k]:
            continue
        lo, hi = M.wilson(num[k], den[k])
        print(f"    {k:<18} {100*num[k]/den[k]:>6.1f}% "
              f"{'[%5.1f, %5.1f]' % (lo, hi):>16} {den[k]:>7}")
    print()


def main():
    rtime, rlon, rlat, rprob, rsize = M.load_radar()
    reports = M.load_reports()

    # Radar rows per local day (matching the grouping) and per UTC day (the
    # unit the archive actually goes missing in). Both are needed; see the
    # module docstring.
    radar_local = collections.Counter()
    radar_utc = collections.Counter()
    for t in rtime:
        radar_local[local_day(t)] += 1
        radar_utc[time.strftime("%Y-%m-%d", time.gmtime(t))] += 1

    rows = []
    for rep in reports:
        nearest = nearest_signature(rep, rtime, rlon, rlat)
        day = local_day(rep["t"])
        utc = time.strftime("%Y-%m-%d", time.gmtime(rep["t"]))
        rows.append({
            "rep": rep,
            "day": day,
            "day_rows": radar_local.get(day, 0),
            "utc_rows": radar_utc.get(utc, 0),
            "ok": nearest is not None and nearest <= D_MAX,
        })

    print(f"Reference cell: {D_MAX:.0f} mi / {T_MAX:.0f} min, any signature")
    print(f"Grouped by local Denver day. {len(reports):,} reports.")
    print()

    # --- what the gap exclusion actually removes -------------------------
    local_gap = [r for r in rows if r["day_rows"] == 0]
    utc_gap = [r for r in rows if r["utc_rows"] == 0]
    leaked = [r for r in utc_gap if r["day_rows"] > 0]
    print("Archive gaps")
    print(f"  reports on a local day with zero rows : {len(local_gap)}")
    print(f"  reports on a UTC day with zero rows   : {len(utc_gap)}"
          f"  (matched: {sum(1 for r in utc_gap if r['ok'])})")
    print(f"  of those, retained by local-day keying : {len(leaked)}")
    if leaked:
        per_day = collections.Counter(r["day"] for r in leaked)
        print("  where the leak lands, by local day:")
        for d, c in per_day.most_common(5):
            print(f"    {d:<12} {c:>4} reports")
    print()

    kept = [r for r in rows if r["day_rows"] > 0 and r["utc_rows"] > 0]

    for label, subset in [
            ("ALL DAYS", rows),
            ("LOCAL-DAY GAPS ONLY (insufficient -- shown for contrast)",
             [r for r in rows if r["day_rows"] > 0]),
            ("ARCHIVE-GAP REPORTS EXCLUDED (local and UTC)", kept)]:
        per_day = collections.Counter(r["day"] for r in subset)
        ndays = collections.Counter(bucket_of(c) for c in per_day.values())
        print("=" * 62)
        print(label)
        print("=" * 62)
        print(f"  {len(per_day)} local hail days")
        print(f"    {'bucket':<18} {'days':>7}")
        for lbl, _, _ in BUCKETS:
            print(f"    {lbl:<18} {ndays[lbl]:>7}")
        print()
        num, den = table(
            subset, lambda r: bucket_of(per_day[r["day"]]),
            "match rate by reports-per-day")
        emit(num, den, [b[0] for b in BUCKETS])

    # --- the single-report bucket, by source -----------------------------
    per_day = collections.Counter(r["day"] for r in kept)
    singles = [r for r in kept if per_day[r["day"]] == 1]
    print("=" * 62)
    print("SINGLE-REPORT DAYS BY SOURCE (archive-gap reports excluded)")
    print("=" * 62)
    src_order = [s for s, _ in collections.Counter(
        r["rep"]["src"] for r in singles).most_common()]
    num, den = table(singles, lambda r: r["rep"]["src"],
                     "match rate for a lone report, by who filed it")
    emit(num, den, src_order)

    # Lone PUBLIC against PUBLIC on every other day -- the actual question.
    pub_single = [r for r in singles if r["rep"]["src"] == "PUBLIC"]
    pub_multi = [r for r in kept
                 if r["rep"]["src"] == "PUBLIC" and per_day[r["day"]] > 1]
    k1, n1 = sum(1 for r in pub_single if r["ok"]), len(pub_single)
    k2, n2 = sum(1 for r in pub_multi if r["ok"]), len(pub_multi)
    if n1 and n2:
        from checks import two_prop_z
        z, p = two_prop_z(k1, n1, k2, n2)
        lo1, hi1 = M.wilson(k1, n1)
        lo2, hi2 = M.wilson(k2, n2)
        print("  lone PUBLIC report vs PUBLIC on a multi-report day")
        print(f"    lone           {100*k1/n1:>6.1f}%  [{lo1:5.1f}, {hi1:5.1f}]  n={n1}")
        print(f"    multi-report   {100*k2/n2:>6.1f}%  [{lo2:5.1f}, {hi2:5.1f}]  n={n2}")
        print(f"    difference {100*(k1/n1-k2/n2):+.1f} pts,  z = {z:+.2f},  p = {p:.3f}")
        print()


if __name__ == "__main__":
    main()
