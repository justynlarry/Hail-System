# MESH_Max_60min against the 60 good-fit storms since 2020-10-14

Analysis only. Outputs are in `~/hail-analysis/mesh60/`. Inputs were copied from `/tmp/hail-dev/scratchpad/{storm_shape,storm_tracks}` into `~/hail-analysis/`, because `~/hail-analysis` did not exist. No database, no git.

## Setup and limits

- **Memory.** Free memory at the start was 5,672 MB free and 9,802 MB available (15,644 MB total, no swap). At the run starts it was 2.4 to 3.3 GB free and about 9.2 to 9.8 GB available. Peak RSS was 398 MB for validation and **427 MB** for the full run, under the 2 GB stop line.
- **How the run worked.** One worker. One CONUS grid in memory at a time: `meshgrid.read_crop_pygrib` slices the crop, copies it and drops the full grid before it returns.
- **Caching and checkpoints.** Crops are cached as `crops/*.npz` (134 files, 2.6 MB) and raw `.grib2.gz` files as `raw/` (20 MB). Each storm is checkpointed to `per_storm/<id>.json`. S3 reset the connection twice; the run resumed from the checkpoints, and the download now retries.
- **venv.** `~/hail-analysis/.venv` uses Python 3.14.8 and binary wheels only: numpy 2.5.4, matplotlib 3.11.2, pygrib 2.1.8. pygrib brought in pyproj 3.8.0 as a dependency, with eccodes and PROJ bundled in the wheels. **No system packages were needed.**

## Step 3: reader validation

File: `MRMS_MESH_Max_60min_00.50_20260608-220000`. pygrib and the stdlib decoder (`storm_shape/mesh/mesh_lib.py`) were run on the same crop: 490 × 821 cells (lat 41.445 to 36.555, lon −109.645 to −101.445). This is Colorado plus 50 km.

- **0 of 402,290 cells differ.** The largest difference is 1.5e-6 mm, which is the float32 cast in my crop code.
- Both readers find 81 cells at or above 1.00 in and 260 at or above 0.75 in. The value range is −1.0 to 34.9 mm in both.
- pygrib masked no cells in this crop. −1 means "no hail".
- The handoff's earlier "one extra row and column" came from how `mesh_lib` rounds the box edges. Here both readers were given cell-centre bounds, so the crops match exactly.
- pygrib reports `name`, `units` and `shortName` as "unknown", because these are MRMS local parameter tables. Units are taken as mm from the earlier work. Values match that.
- The earlier 249-of-276 mismatch (a 60-minute max built from 2-minute files vs NOAA's product) remains unexplained. It is not a decoder problem. `validate_window.py` was not rerun.

## Step 4: selection

- `storm_shape/` holds no per-report storm assignment for the all-years storms. `storm_tracks/storm_reports.csv` covers only the 12-month S-storms. So I rebuilt membership by re-running the same stdlib single-linkage clustering (30 km / 60 min) on `reports_raw.csv`. **All 2,494 clusters match `storms_all.csv` on report count, start and end.**
- **60 good-fit storms** start on or after 2020-10-14 (local time), from 2021-05-02 to 2026-08-22.
- **S0025 is not one of the 60.** It is A02408: 34 reports, excluded as `fit_weak` (R² 0.47), and a chaining suspect. I ran it through the identical pipeline as two reference rows: `REF_S0025_whole34` and `REF_S0025_piece22`, the 22-report west cell.

## Step 5: method

- **Files.** I used the hourly `MESH_Max_60min_00.50` files stamped HH:00:00 UTC. The bucket publishes this product every 2 minutes, and the brief asked for hourly files. The run takes every hour H whose window (H − 60 min, H] overlaps the storm's first-to-last report time (UTC from `reports_raw.csv`), and the per-cell maximum across those files.
  - That is 1 to 5 files per storm, 143 file uses (134 distinct).
  - **All were the exact top-of-hour stamp: no gaps, no substitutions.**
  - There is no ±20-minute padding (the earlier B2 used it). The hourly windows already extend up to 60 minutes before the first report.
  - *Unverified:* that the file stamped H covers exactly (H − 60, H]. The GRIB valid time equals the stamp, but I did not check the accumulation window.
- **Geometry.** Cell centres were projected to EPSG:26913 with pyproj. pyproj reproduces the PostGIS x/y in `reports_raw.csv` (518,560.9 / 4,515,468.8 for report 349601). Cell area is the spherical area of a 0.01° cell, about 0.94 km² at 39.5° N. A cell counts as inside a shape if its centre is.
- **Domain.** Cells within 30 km of any of the storm's reports, hail or wind.
- **Circles.** R = 5.0 mi (8,046.72 m) around **actionable** reports: hail ≥ 1.00 in, gust ≥ 58 mph, and any TSTM WND DMG or DOWNBURST. The radius and floors come from `storm_shape/summary.md`; there is no DB here to re-read `settings` or `report_types`. "More than 8 mi" means 12,874.75 m from the nearest actionable report.
- **MESH area.** Cells at or above 25.4 mm (1.00 in). The 0.75 in line is also given in the CSVs.
- **Per report.** For each actionable hail report, the maximum MESH over cells within 5 km.
- **Foothills cutoff: longitude −105.0**, applied to the storm's report centroid and to each report. **This is a proxy; elevation was not used**, because no DEM is installed. Around Denver the plains-to-foothills break is near −105.1 to −105.2, and near Colorado Springs it is near −104.9. Sensitivity is given at −104.9 and −105.1.

## Step 6: results

### Overall, 60 storms

| | Value |
|---|---|
| Storms with any MESH ≥ 1.00 in within 30 km | 53 (7 have none, 8 have < 10 km²) |
| MESH ≥ 1.00 in area, all storms | 19,094 km² |
| **Share covered by today's circles**, pooled | **56%** |
| Same, per storm, the 52 with ≥ 10 km² | median **67%** [IQR 42, 81] |
| **Share more than 8 mi from any actionable report**, pooled | **29%** |
| Same, per storm | median **20%** [6, 38] |
| Storms with 0% covered / < 25% covered / > 50% beyond 8 mi | 1 / 5 / 9 of 52 |
| Precision: share of circle area with MESH ≥ 1.00 in, per storm | median 14% [4, 32] |
| **Actionable hail reports with MESH ≥ 1.00 in within 5 km** | **485 of 536 (90%)** |
| Same at ≥ 0.75 in | 519 of 536 (97%) |
| Median MESH within 5 km vs median reported size | 1.76 in vs 1.25 in |
| Storms with ≥ 3 actionable hail reports and a hit rate below 50% | 4 of 46. The only one at 0% is A01808, with 3 reports. |

How to read this:

- **Reports and radar mostly agree on where hail fell.** Nine in ten actionable hail reports have ≥ 1.00 in MESH within 5 km.
- **The circles miss a substantial minority of the radar hail area.** About 44% of MESH ≥ 1.00 in area lies outside every circle, and 29% lies more than 8 miles from any actionable report.
- **Most of each circle has no MESH hail.** The median is 14% filled.
- **MESH reads high against reports here** (median 1.76 vs 1.25 in). That is the opposite of S0025. Part of it is the statistic itself: a maximum over 1 to 5 hours and a 5 km disc is biased upward. I did not calibrate MESH.

### Foothills vs plains

| Cutoff (storm centroid) | Foothills: storms, pooled coverage, median beyond 8 mi | Plains: storms, pooled coverage, median beyond 8 mi |
|---|---|---|
| −104.9 | 13, **77%**, 14% | 47, **52%**, 22% |
| **−105.0** | 3, **73%**, 22% | 57, **56%**, 17% |
| −105.1 | 0 | 60 |

| Cutoff (per report) | West: MESH ≥ 1.00 in within 5 km | East |
|---|---|---|
| −104.9 | 138 of 151 (91%) | 347 of 385 (90%) |
| **−105.0** | 81 of 89 (91%) | 404 of 447 (90%) |
| −105.1 | 26 of 29 (90%) | 459 of 507 (91%) |

**No foothills penalty shows up.** The western storms agree as well as or better than the plains on both measures. **The comparison has almost no real foothills storms in it, though:**

- None of the 60 storms is centred west of −105.06. S0025's piece is centred at −105.142, further west than any of them.
- Only 29 actionable hail reports lie west of −105.1.

So these data cannot confirm or rule out poorer radar performance in the mountains proper.

### Was S0025 typical?

| | MESH ≥ 1 in | Covered | > 8 mi | Actionable hail reports with MESH ≥ 1 in within 5 km |
|---|---|---|---|---|
| S0025 whole (34) | 476 km² | 31% (9 of the 52 ranked storms are this low or lower) | 54% (9 of 52 this high or higher) | 5 of 20 (25%) |
| S0025 west piece (22) | 24 km² | **0%** (matched only by A02451, a wind-only storm, among the 52) | 84% | **0 of 13** |
| 60-storm reference | | median 67% | median 20% | 90% pooled |

**S0025 is an outlier, not the norm.**

- The whole storm sits in the worst fifth on coverage and on the beyond-8-mile share.
- The west piece is at the extreme on all three measures. Its 0 of 13 report hits is lower than every storm in the 60 with more than 3 actionable hail reports.
- Across the 60 storms, today's circles cover a median two-thirds of the MESH hail area, not none.

**The brief merged two numbers.** "Circles covered none" is the 22-report piece. "56 to 71% beyond 8 mi" is the whole 34-report storm, where the circles covered 29% (31% here). This run's hourly files give 54% beyond 8 mi for the whole storm's actionable reports. The earlier 2-minute ±20-minute run gave 71%; the gap is the window difference.

Possible causes of the outlier, none checked: a foothills radar artefact (only 29 reports in the 60 storms are that far west), report position error, or MESH simply low on that cell.

### Confound check: neighbouring storms

`neighbour_check.py` looks for reports from other clusters inside each storm's domain and file window. Only 1 of the 60 storms has any (A02418, which has 1 report from A02420). Without it, pooled coverage is 57% and beyond 8 mi is 28%, so nearly the same.

**MESH from cells that drew no reports at all cannot be detected this way.** The "beyond 8 mi" share is therefore partly hail that no report-based rule could have targeted. Whether people or buildings were under it is unchecked.

## Step 7: maps, the 5 worst

The 5 worst are ranked by lowest share covered among the 52 storms with ≥ 10 km² of MESH ≥ 1.00 in. Maps are in `maps/worst{1..5}_<id>.png`, in UTM 13N km.

| # | Storm | Date | MESH ≥ 1 in | Covered | > 8 mi | Hail hits | Note |
|---|---|---|---|---|---|---|---|
| 1 | A02451 | 2026-07-28 | 20 km² | 0% | 100% | 0 of 0 | **Wind-only storm** (8 actionable wind reports). Coverage of hail is not a fair test here. |
| 2 | A02418 | 2026-06-23 | 382 km² | 9% | 72% | 17 of 20 | Reports clustered west; large MESH swaths to the NE and SE drew no reports |
| 3 | A02263 | 2025-04-24 | 61 km² | 16% | 75% | 1 of 3 | |
| 4 | A02156 | 2024-05-20 | 1,253 km² | 21% | 59% | 3 of 3 | Eastern plains (−103.45), sparse reports |
| 5 | A02419 | 2026-06-23 | 443 km² | 22% | 70% | 4 of 5 | Same day as #2 |

All five are plains storms. The pattern in #2, #4 and #5 is reports landing on the swath (high hit rates) while most of the swath has no report near it.

## Unverified / limits

- Radius and actionable floors were taken from `storm_shape/summary.md`, not re-read from the DB.
- The window of the Max_60min file was not independently checked (see Step 5).
- The foothills split is by longitude, not elevation, and has very few true foothills storms.
- MESH is uncalibrated. "Covered" depends on the 1.00 in threshold. At 0.75 in, pooled coverage is 52%.
- Some 30 km domains may include hail from unreported neighbouring cells (see the confound check).
- No storm was checked against NWS surveys, news or insurance data.
- Storms are not independent: several share a day.
- The handoff's extra gap-check storms (A01742, A02257, A02079) were not run; the brief drops gap-filling. A02257 is in the 60 and has no MESH ≥ 1 in.

## Files

`validate_reader.py`, `meshgrid.py`, `run_storms.py`, `summarize.py`, `neighbour_check.py`; `per_storm.csv` (60 rows + 2 reference rows), `per_report.csv` (actionable hail reports), `per_storm/*.json`, `summary_numbers.json`, `neighbours.json`, `neighbour_summary.json`, `run.log`, `maps/`, `crops/`, `raw/`.
