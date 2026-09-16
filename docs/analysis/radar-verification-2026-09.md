# Do radar hail signatures corroborate our hail reports?

One-time study, run 2026-09-16. Evidence, not a decision — the decision it
produced is `decision-log.md`, 2026-09-16. Dated in the filename because a
repeat run against a longer archive should sit beside this one, not overwrite
it.

**Question.** `iem_data` hail reports come from human observers, and roughly
half of them come from `PUBLIC` — an untrained member of the public phoning
the NWS. The system's entire claim is that *a report was filed near this
listing*. If public reports were materially less trustworthy than trained
ones, `report_sources.confidence_tier` would need to surface in the UI so a
sender could weigh them differently. That had been a judgment call with no
evidence attached. This is the evidence.

**Method in one line.** For each hail report in the coverage area, ask whether
NEXRAD Level-III hail detections — radar-derived, independent of the LSR
network — put a hail signature near it in space and time.

---

## Answer

**Public hail reports corroborate against radar at the same rate as trained
spotter reports.** At 5 miles / 30 minutes:

| Source | Match rate | 95% CI | n |
|---|---|---|---|
| PUBLIC | 93.5% | [91.8, 94.8] | 1,055 |
| TRAINED SPOTTER | 92.4% | [90.7, 93.8] | 1,127 |
| COCORAHS | 91.6% | [85.2, 95.4] | 119 |

PUBLIC runs **+1.09 points above** TRAINED SPOTTER — z = +0.99, **p = 0.32**,
indistinguishable from noise. PUBLIC vs COCORAHS: p = 0.44. After removing
archive gaps (below) the two largest sources sit at 94.6% and 94.7%, which is
the same number.

The direction is worth noting because it is the opposite of the worry. If
untrained reports were the weak link, PUBLIC should have trailed. It does not.

**COCORAHS is the one source that looks lower, and the sample cannot support
saying so.** n = 119 gives a 95% interval of [85.2, 95.4] — ten points wide,
overlapping both larger sources completely. Its nominal 3-point deficit after
gap correction is not a finding, and should not be quoted as one. If COCORAHS
ever matters enough to rate separately, it needs its own study with more
years, not a footnote from this one.

---

## What was compared

| | Reports | Radar |
|---|---|---|
| Source | `iem_data`, exported to `output/hail_reports_2016_2025.csv` | NCEI SWDI `hail-YYYY.csv.gz`, 2016–2025 |
| Origin | Human observers, phoned or filed to the NWS | NEXRAD Level-III hail detection algorithm |
| Rows | 2,538 hail reports in coverage zips | 116,395,642 national → 1,039,400 in the Front Range box |
| Time | `TIMESTAMPTZ`, UTC | `ZTIME`, `YYYYMMDDHHMMSS`, UTC |

The two are genuinely independent: a radar signature is a volume-scan product
computed from reflectivity aloft and owes nothing to whether anyone was
outside. That independence is the only reason this comparison means anything.

**Both sides are UTC, so no conversion happens anywhere in the matching.**
`America/Denver` is a display concern and appears nowhere in these scripts —
which is also why the per-day grouping in check 5 is by UTC day, not local
day, and should not be read as a storm-day count.

### The SWDI file format

Columns are `ZTIME, LON, LAT, WSR_ID, CELL_ID, RANGE, AZIMUTH, SEVPROB, PROB,
MAXSIZE`. Coordinates are WGS84 decimal degrees; `MAXSIZE` is inches, `PROB`
and `SEVPROB` are percentages.

**These are all signatures, not the probability-100 filtered subset.** The
bulk-CSV endpoint publishes one hail product with no filtered variant —
filtering is a web-service query option, not a separate file — and the data
confirms it: `PROB` spans 10 to 100 in ten-point steps, with only 16.6% of
kept rows at 100.

Three traps found, recorded here so a rerun does not rediscover them:

- **`-999` is the null marker**, the same shape as IEM's literal `None`. It
  appears on `SEVPROB`, `PROB` and `MAXSIZE` *always as a set* — 196,274 kept
  rows (18.9%), never a partial. Coerced to zero it reads as "0% chance, zero
  inch hail"; left alone it reads as a measurement. These rows count toward
  "was there a signature" and are excluded from the size comparison.
- **`MAXSIZE` formatting changed between years.** 2016 writes `.5`, 2025
  writes `0.5`. `float()` handles both; string comparison or exact-match
  dedupe would not.
- `CELL_ID` is a recycled two-character label, **not** a stable storm
  identifier. Nothing here joins on it.

Zero malformed rows across all 116M — no field-count mismatches, no non-finite
coordinates. The `isfinite` guard before the range test is kept anyway, per
the `Decimal('NaN')` trap in `CLAUDE.md`.

### Spatial reduction

The Front Range box is the exact bounding rectangle of RBI's 193 coverage
ZCTAs — lon −105.72667..−103.70626, lat 38.40715..40.99833 — plus a 0.30°
(~20 mile) margin. **The margin is deliberately larger than the widest
tolerance tested (10 miles), so no report is truncated at the box edge and no
match rate is an artifact of the crop.**

That reduces 116,395,642 rows to 1,039,400 — **0.893%, a 112× reduction**,
6.5 GB of gzip to 64 MB. Nothing is ever extracted to disk; `zcat` feeds a
pipe and only survivors are written.

The box came from the TIGER ZCTA shapefile rather than from PostGIS, because
this analysis never opens a database connection. **10 of the 193 coverage zips
have no ZCTA polygon** — 80213, 80225, 80502, 80522, 80523, 80539, 80632,
80638, 80639, 80901 — being PO-box, university and federal-center zips, which
the Census does not issue ZCTAs for. They are inside the box regardless, so
this study is unaffected, but the same 10 will silently match nothing in any
`coverage_zips → zcta_boundaries` join anywhere in the system.

TIGER is NAD83 and SWDI is WGS84, per the trap in `CLAUDE.md`. At this
latitude the shift is about a metre, immaterial against a 20-mile margin, so
they are compared directly. **It is the margin that makes that safe, not the
datums being equivalent** — a tighter crop would need a real reprojection.

---

## The tolerance sweep

Tolerances were swept rather than chosen, so that the result could be checked
for stability instead of asserted at one convenient pair.

Share of the 2,538 reports with at least one signature in tolerance:

| miles | min | ANY | PROB≥10 | PROB≥50 | PROB=100 |
|---|---|---|---|---|---|
| 2 | 15 | 76.0% | 71.3% | 70.3% | 60.7% |
| 2 | 30 | 81.3% | 76.8% | 76.0% | 66.0% |
| 2 | 60 | 84.4% | 80.2% | 79.4% | 68.3% |
| 5 | 15 | 91.4% | 90.7% | 90.3% | 83.0% |
| **5** | **30** | **92.9%** | **92.4%** | **92.3%** | **86.7%** |
| 5 | 60 | 94.1% | 93.6% | 93.5% | 88.5% |
| 10 | 15 | 94.1% | 93.7% | 93.6% | 88.0% |
| 10 | 30 | 94.7% | 94.5% | 94.4% | 91.0% |
| 10 | 60 | 95.7% | 95.5% | 95.5% | 92.3% |

**Distance does all the work; time does almost none.** Widening 2→5 miles at
fixed 30 minutes adds 15.6 points. Widening 15→60 minutes at fixed 5 miles
adds 2.9. So the grid is stable in time and unstable in exactly one place: the
2-mile row.

That row is measuring the wrong thing rather than revealing disagreement. LSR
positions are geocoded to town centroids and offsets like "2 NW DURANGO"; the
exported coordinates are quantized to two decimals (~0.4 mi on their own); and
a radar hail signature is a storm-cell centroid *aloft*, displaced from where
the stone actually lands by advection and storm tilt. Two miles is below the
joint resolution of the two datasets. **At 5 miles and beyond the result is
stable, and 5 mi / 30 min is the honest headline.** The 2-mile row is a
resolution floor, not a finding.

Stratifying by probability costs less than expected: demanding `PROB = 100`
still matches 86.7%, and `PROB≥10` and `PROB≥50` land within 0.1 point of each
other — the probability field barely discriminates at all.

---

## Two caveats that bound what this number means

**This is a forward-direction rate, and forward direction is biased toward
matching.** Five radars cover the box — KFTG 237k detections, KPUX 226k, KCYS
216k, KDEN 170k, KGLD 148k — they overlap heavily, and each re-detects the
same cell every volume scan, roughly every five minutes. So a single hailstorm
drifting across Aurora for an hour contributes hundreds of candidate rows.
Duplication can only make a match *easier* to find. **93% is therefore an
upper bound on agreement, not a symmetric skill score**, and it must not be
quoted as "radar and observers agree 93% of the time" — the symmetric claim
was not measured and this design cannot measure it.

**The reverse direction — radar signatures with no report — was deliberately
not computed.** It is the more interesting question and the harder one: the
same duplication that is harmless here is fatal there, because counting
un-reported signatures off raw rows would count one storm hundreds of times.
Answering it needs a space/time event-clustering rule, which is a real design
decision and not a tolerance. It is also the weaker question on its own terms:
NCEI states that absence of SWDI data does not imply absence of weather, so a
signature with no report is ambiguous between "nobody was outside" and "the
archive has a hole" — and the archive demonstrably does have holes, as below.
Deferred, not dropped.

---

## The unmatched residue is mostly an archive gap

Match rate by year swings from **79.2% (2020) to 99.5% (2024)** — a 20-point
spread that no source breakdown explains. It is worth chasing rather than
averaging over, because a radar outage masquerades as observers being wrong.

Of the 180 unmatched reports, **166 have no signature within 30 minutes at any
distance** — real absences, not near-misses. The 14 genuine near-misses sit at
5.3 to 7.3 miles. And the absences cluster hard onto a few days:

| Day | Unmatched reports | Radar rows in box that day |
|---|---|---|
| 2016-07-08 | 41 | **0** |
| 2021-06-13 | 22 | **4** |
| 2020-08-05 | 20 | 439 |
| 2016-06-23 | 4 | **0** |
| 2025-10-27 | 4 | **0** |

**Four days carry zero SWDI rows anywhere in the Front Range box and account
for 50 of the 180 unmatched (28%).** On those days radar is not disagreeing
with the observers; there is nothing there to disagree with.

| | Overall | TRAINED SPOTTER | PUBLIC | COCORAHS |
|---|---|---|---|---|
| All days | 92.9% | 92.4% | 93.5% | 91.6% |
| Excluding 0-row days | **94.8%** | 94.7% | 94.6% | 91.6% |
| Excluding <50-row days | 95.6% | 95.2% | 96.0% | 91.6% |

The corrected rate is about **95%**, and the gap between the two large sources
closes to 0.1 point.

Two further checks came back clean. Match rate is flat across reported hail
size — 91.9% sub-severe, 93.2% at 0.75–0.99", 93.2% at 1.00–1.74", 91.8% at
≥1.75" — so the residue is not marginal pea hail being imagined by observers,
which is the failure mode one would expect if untrained reporting were the
problem. And the box margin exceeds the widest tolerance, so no edge effect.

---

## Radar size does not predict reported size

At 5 mi / 30 min, taking the nearest signature that carries a size (`-999`
rows excluded, and one report with `magnitude = 0.00` dropped since zero is
not a size):

| Source | n | Reported median | Radar median | Median diff | Radar > report | r |
|---|---|---|---|---|---|---|
| TRAINED SPOTTER | 1,030 | 1.00" | 1.00" | 0.00 | 43.7% | 0.31 |
| PUBLIC | 983 | 1.00" | 1.00" | 0.00 | 45.1% | 0.24 |
| COCORAHS | 109 | 1.00" | 1.25" | +0.25 | 53.2% | 0.32 |
| All others | 222 | 1.00" | 1.25" | 0.00 | 43.7% | 0.38 |

**Well-centred in aggregate, near-useless per report.** The medians agree
exactly and the median difference is zero, but Pearson r is 0.24–0.38 and
radar exceeds the report on about 44% of pairs — a coin flip. Radar `MAXSIZE`
carries almost no information about how large the hail at a specific address
actually was.

Note the denominators differ from the match table by design: 2,358 reports
match at this cell, but only 2,344 of those pairs carry a usable size — the
nearest signature is sometimes a `-999` row, and the one zero-magnitude report
is dropped. Both denominators are reported so neither has to be reconstructed
later from the difference.

---

## Reproducing this

Scripts are in `docs/analysis/radar-verification-2026-09/`, stdlib only, no
database connection, nothing installed. They live beside the write-up rather
than in a scratch directory because the question "where did that number come
from" gets asked long after the session that produced it.

```
# 1. Re-download the inputs (data/ is gitignored, ~1.9 GB)
#    https://www.ncei.noaa.gov/pub/data/swdi/database-csv/v2/hail-YYYY.csv.gz
#    for YYYY in 2016..2025, into data/

# 2. Export the reports from iem_data as hail_app (read-only; never write)
#    -> output/hail_reports_2016_2025.csv
#    columns: iem_id, utc_datetime, latitude, longitude, magnitude,
#             report_source, report_source_norm
#    coverage restriction and local-day logic come from the existing storm
#    query core -- do not reimplement them here

cd docs/analysis/radar-verification-2026-09
python3 coverage_bbox.py   # derives the box from TIGER + config/coverage_zips.txt
python3 reduce.py          # ~4 min, streams 6.5 GB -> output/radar-reduced/
python3 match.py           # the sweep grid, per-source tables, size comparison
python3 checks.py          # significance, size/year breakdowns, the gap analysis
```

`reduce.py` is the only slow step. `match.py` runs in about five seconds.

**This analysis reads and never writes.** It opened no database connection at
all in the end — the reports arrived as a CSV — and touched nothing in the
running system.
