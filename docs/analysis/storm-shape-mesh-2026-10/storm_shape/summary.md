# Storm shape, Part A (report-based, all years)

Method reused from `scratchpad/storm_tracks/`: HAIL + TSTM WND GST + TSTM WND DMG + DOWNBURST, local (America/Denver) times, UTM 13N, single-linkage at 30 km / 60 min, "ok" = at least 5 reports, at least 30 min, and a linear time-vs-position fit with R² >= 0.5. Analysis only: no DB writes, no tracked files touched. Re-run: `python3 -I analyze_shape.py` (needs `reports_raw.csv` from `q_extract_all.sql`).

**The radius R is 5.0 miles = 8,046.72 m.** I read it from the live `settings.default_match_radius_miles` (a row of 1; last logged change 2026-09-30 did not touch the radii). `hailsys/matching/matcher.py` matches properties to reports with `ST_DWithin(report::geography, property::geography, radius_m)`, one circle per report, only for reports whose type is `roof_relevant` and whose magnitude clears `report_types.min_magnitude` (HAIL >= 1.00 in, TSTM WND GST >= 58 mph, no floor on TSTM WND DMG/DOWNBURST). `tuning.py` is the fallback constant and also reads 5.0. My circle uses all reports in the storm (what the brief asked); the CSV also carries a circle of actionable reports only.

## Data

7,772 reports, 2004-01 to 2026-10, in the coverage zips (5,317, 68%) or within 50 km (2,455). 2,494 clusters; 308 storm-sized (>= 5 reports, >= 30 min); **180 ok-fit storms** (58% of storm-sized). Excluded, by reason: `too_few_reports` 2,119 (fragments), `too_short` 67, `fit_weak` (R² 0.25 to 0.5) 61, `fit_poor` (R² < 0.25) 67. Full list of the 195 storm-sized exclusions in `excluded_storms.csv`; every cluster is in `storms_all.csv`.

The all-years extract is a different query from the 12-month one (50 km buffer test is still UTM grid distance, but "inside" is now ZCTA intersection per zip), so cluster membership could in principle differ slightly. The 2026-06-08 storm came back with exactly the same 34 reports.

## A1. Chaining

**S0025 (2026-06-08) chains.** Re-clustered alone at 15 km / 30 min (half the thresholds), its 34 reports split into:

| piece | reports | time (MT) | centroid | |
|---|---|---|---|---|
| 1 | 22 (all hail) | 15:01 to 15:56 | 39.775 N, 105.142 W | west Jefferson County |
| 2 | 8 (7 hail) | 15:22 to 16:22 | 39.486 N, 104.728 W | roughly 35 km southeast of piece 1 |
| 3 to 5 | 2, 1, 1 | 16:10 to 16:51 | scattered | fragments |

The two big pieces overlap in time by about 35 minutes and sit about 35 km apart. That is what two concurrent cells look like, and it explains why the earlier whole-storm fit was weak (R² 0.47, ~42 km/h east). I can not tell from reports alone whether it was two cells or one storm with sparse reporting; the MESH sample in Part B should settle it. For B2, I would use piece 1 (22 reports) and also show the whole 34.

**How many storms split when thresholds are halved** (308 storm-sized clusters, each re-clustered alone):

- 252 (82%) break into 2 or more pieces. That number overstates chaining: reports are sparse, so halving the thresholds fragments most clusters regardless of whether they merged separate cells.
- 35 (11%) break into 2 or more pieces that are each >= 5 reports. I treat these as chaining suspects (`chain_suspect`). **16 of the 180 ok storms** are suspects, including S0025.
- Dropping the 16 suspects leaves shape statistics essentially unchanged (aspect median 4.33 vs 4.30, cross p90 4.8 vs 5.1 km, along length 40.7 vs 41.3 km). Chaining does not drive the shape results. Per-storm detail: `chaining_check.csv`.

## A3. Shape of the 180 ok storms

Report cloud projected on the fitted track axis (OLS line through the mean position, direction = fitted velocity). Median [IQR]:

| Measure | Value |
|---|---|
| Reports per storm | 8 [6, 11] (31 storms have exactly 5) |
| Along-track length (max minus min projection) | **41 km** [29, 56] |
| Cross-track SD | 3.9 km [2.3, 5.6] |
| Cross-track 90th percentile of distance from axis | **5.1 km** [3.0, 7.5] |
| Aspect = length / (2 x p90) | **4.3** [2.9, 6.2] |
| Aspect = SD along / SD cross | 4.0 [2.8, 5.7] |
| PCA elongation (sqrt of eigenvalue ratio, needs no time axis) | 4.0 [2.8, 5.9] |
| Fit R² / speed | 0.81 [0.69, 0.90] / 32 km/h [23, 43] |

Share with aspect above 1.5 / 2 / 3: 94% / 89% / 73%. Direction toward: E 53, SE 55, S 26, NE 26, N 14, NW/SW/W 2 each.

For scale: a circle of R has a diameter of 16.1 km. The typical report cloud is 41 km long and its 90% band is about 10 km wide, so narrower than one circle is wide.

**PCA cross-check.** The time-fit axis and the PCA major axis agree closely: median gap 1.4°, largest 27.8°, **no storm disagrees by more than 30°** (`axis_disagree` is empty). Caveat: that agreement is largely produced by my own fit filter. Among the 128 storm-sized clusters I excluded for weak or poor fits, 40 disagree by more than 30°, and their median gap is 15° (weak) and 34° (poor).

### Is the elongation real? Two controls I added, because the raw numbers flatter the idea

1. **Spatial elongation beats a random cloud.** Isotropic Gaussian point clouds with the same report counts give a PCA elongation of 1.6 (median). The ok storms give 4.0. The 128 excluded storm-sized clusters give 2.2. So report clouds are clearly more elongated than random, and the storms with a coherent motion are the most elongated.
2. **The aspect ratio itself carries little extra information.** If I shuffle report times within each storm-sized cluster (200 shuffles each), only 5.3% of shuffles pass the R² >= 0.5 filter, against 58% of real clusters. So time and position are genuinely coherent. But the shuffled passers have a median aspect of 4.0, about the same as the real storms' 4.3. The filter itself selects for elongation. So an aspect of about 4 should be read as "this cluster is elongated along its motion", not as "hail footprints are 4:1".

Other things that limit these numbers:
- Report positions are quantised to 0.01° (about 1 km). 9 ok storms have p90 < 1 km and 20 have < 2 km; their aspect ratios (top five: 27 to 49) are artefacts of near-collinear reports. 31 storms have only 5 reports.
- Elongation measured on single-linkage clusters is partly the algorithm: linked chains are long by construction. The cutoffs 30 km / 60 min bound length but not width.
- Reports follow roads and population (I-25 corridor), which stretches clouds along corridors independent of hail.
- **Width here reflects where people were, not where hail fell.** It is a lower bound for the swath width.

## A4. Eras (details in `shape_by_era.md`)

| Era | ok storms | reports/yr | along length | cross p90 | aspect |
|---|---|---|---|---|---|
| 2004 to 2012 | 52 | 291 | 41 km | 5.3 km | 3.7 |
| 2013 to 2019 | 64 | 344 | 42 km | 4.5 km | 4.4 |
| 2020 to present | 64 | 405 | 41 km | 5.2 km | 4.4 |

Report volume rose and wind's share of reports doubled (11% to 25%) in 2020 on, but no shape metric differs between eras (Kruskal-Wallis p from 0.19 to 0.95), and aspect does not trend with report count. **Pooling is justified for shape**, with the caveat of low power (52 to 64 storms per era).

## A5. Circle (today's rule) vs capsule

Both footprints were built in UTM 13N metres (circles use a 16-segment buffer; UTM scale error is under 0.1%, so about 8 m at R; the matcher uses geodesic distance). **Capsule extension is a parameter, `EXT_KM`, set to 5.0 km** (`EXT_KM=... python3 -I analyze_shape.py` to change). The capsule axis runs between the first and last report projections, extended by EXT_KM at each end, and is buffered with round caps.

Two capsules are reported, and the second was **my addition**, not in the brief:
- **`capsule_p90`**, literal reading: half-width = the cross-track 90th percentile. Median half-width is 5.1 km, so this capsule is narrower than a single circle (R = 8.05 km) by construction.
- **`capsule_wide`**: half-width = p90 + R. It is the capsule that reaches as far from the track as a circle around the outermost typical report would.

Storm-level results, 180 storms (areas in km², `circle_vs_capsule.csv`):

| | Circle (today) | Capsule p90 | Capsule wide |
|---|---|---|---|
| Area, median [IQR] | 854 [706, 1070] | 562 [296, 1090] | 1825 [1354, 2735] |
| Sum over storms | 184,578 | 188,351 | 444,500 |
| Overlap with circle | | 105,333 (57% of circle) | 179,030 (97% of circle) |
| Circle only | | 79,245 | 5,547 |
| Capsule only | | 83,018 (44% of capsule) | 265,468 (60% of capsule) |
| Jaccard, median [IQR] | | 0.42 [0.32, 0.48] | 0.47 [0.38, 0.54] |

The circle built from actionable reports only (what the matcher uses) has a median area of 743 km² [519, 932]; 0 for storms with no report above its floor.

**Circles are fragmented.** 132 of 180 circle footprints (73%) are 2 or more disjoint pieces, because reports more than 16 km apart leave gaps. A capsule has no gaps by construction. This is a large part of "capsule only" area for the p90 capsule.

Stored properties (the `properties` table: 25,362 RentCast listing records, not a census of buildings, and as they are today, not at the storm date). Sums are storm-by-property incidences; a property in the footprints of ten storms counts ten times. 25 storms have no stored property in either the circle or the p90 capsule.

| | Circle | Capsule p90 | Capsule wide |
|---|---|---|---|
| Incidences | 317,372 (257,359 actionable-only) | 273,324 | 564,945 |
| In both | | 190,573 | 309,408 |
| Circle only | | 126,799 | 7,964 |
| Capsule only | | 82,751 | 255,537 |

The median capsule_p90 holds 65% as many properties as the circle.

Extension sensitivity (areas summed over storms, km²): EXT 0 / 5 / 10 gives capsule_p90 166k / 188k / 211k (overlap with circle 98k / 105k / 106k) and capsule_wide 393k / 445k / 496k (overlap 179k throughout). The overlap barely moves; the extension mostly adds capsule-only area.

**How to read this.** It is a comparison of shapes, as you said, not a verdict. Two things to take from it. First, the two shapes put very different weight on the track's ends and gaps: the p90 capsule fills the gaps between reports and drops the lateral reach of the outer circles, and the wide capsule keeps the lateral reach and adds a long extension. Second, the capsule's width is derived from the same sparse report positions that cause the circles' gaps, so neither shape carries information about where hail actually fell. Inspection data, or MESH (Part B), is what can say which one is closer.

## Caveats and unverified

- No storm has been checked against radar, NWS storm survey data or news; directions, speeds and the S0025 two-cell reading are unverified.
- Footprint polygons (`footprints.geojson`: 540 features, circle + both capsules per storm) were generated by PostGIS and not viewed. No browser here; matplotlib maps come in B2.
- R² on 5 to 8 points is a weak test: with 5 reports a linear fit is 2-parameter on 5 points, which is why I ran the shuffle control (5% by chance).
- Statistics treat storms as independent. The same clusters feed both aspect and area, and a big day can produce several adjacent storms.
- `properties` does not cover the whole territory evenly (it records where RentCast pulls were run), so property counts understate real exposure in unpulled areas and say nothing about older storms' house stock.
- Hail and wind reports are clustered together here (as before), so "hail footprints" are really convective-storm report footprints. A hail-only rerun was not done.

## Files

`storms_all.csv` (2,494 clusters), `excluded_storms.csv` (195 storm-sized exclusions), `shape_by_storm.csv` (180), `shape_by_era.md`, `circle_vs_capsule.csv` (180), `footprints.geojson`, `chaining_check.csv`; working files `analyze_shape.py`, `q_extract_all.sql`, `reports_raw.csv`, `numbers.json`.

---

# Part B: MESH feasibility, one sample storm, and gap-fill bound

Scripts: `mesh/mesh_lib.py` (stdlib GRIB2/PNG decoder), `mesh/fetch_window.py`, `mesh/b2_s0025.py`, `mesh/png_render.py`, `mesh/b2_render.py`, `b4_gaps.py`. Raw downloads and decoded crops are kept outside the repo, in the session scratchpad (5 MB raw).

## B1. Free MESH archives (all fetched and checked unless marked)

| | Iowa State MTArchive | NOAA Open Data (AWS S3 `noaa-mrms-pds`) |
|---|---|---|
| URL pattern | `https://mtarchive.geol.iastate.edu/YYYY/MM/DD/mrms/ncep/MESH/MESH_00.50_YYYYMMDD-HHMM00.grib2.gz` | `https://noaa-mrms-pds.s3.amazonaws.com/CONUS/MESH_00.50/YYYYMMDD/MRMS_MESH_00.50_YYYYMMDD-HHMMSS.grib2.gz` |
| Products | `MESH` (instantaneous, every 2 min, 720 files/day); `MESH_Max_1440min` (rolling 24 h max, every 30 min). **No** `MESH_Max_60min` (404 on 2020-10-20 and 2021-06-08) | `MESH_00.50` (instantaneous, 2 min), `MESH_Max_30min`, `_60min`, `_120min`, `_240min`, `_360min`, `_1440min` (`_00.50` suffix on each) |
| Format and grid | GRIB2, gzip; 7000 x 3500 cells, 0.01 degrees (about 1 km), CONUS 20 to 55 N and 130 to 60 W; units mm | same grid and encoding |
| History start | **2019-08-20** (2019-08-19 returns 404, 08-20 and later 200). `MESH_Max_1440min` has only 2 files/day in the 2019-10 to 2021-01 days I sampled, and 48/day on 2022-06-08 | **2020-10-14** (first key listed), the day MRMS moved to version 12 |
| Size per day (CONUS, no Colorado subset exists) | MESH 720 files: 36.5 MB (2026-06-08), 40.5 MB (2022), 34.7 MB (2020). Max_1440: 43 to 48 MB (48 files, about 1 MB each) | MESH about 73 KB x 720 = 50 MB. Max_60min about 120 to 183 KB x 720 = about 100 to 130 MB. Max_1440: 48 files, about 1 MB each |
| Terms | Site says "There are no usage restrictions"; the data itself is NOAA MRMS | NODD licence: open, attribution requested, do not imply NOAA endorsement. The registry page also warns to take care with MRMS version 11 products before 2020-10-14 |

Both archives deliver whole-CONUS files, so a Colorado-only download does not exist; only the decoded crop is small (my 451 x 501-cell crop is 452 KB as uint16).

**Encoding.** GRIB2 data template 5.41 (PNG packing): a 16-bit grayscale PNG inside section 7, value = (X - 30) / 10 mm. Cells with no hail read -1 (no 0.0 values exist); the smallest positive value I saw is 0.8 mm.

**Reading it in Python.** `cfgrib`/`xarray` (needs eccodes), `pygrib`, or GDAL/`rasterio` should read template 5.41, but **none of them is installed here and I did not test any**. What I verified: the stdlib decoder in `mesh/mesh_lib.py`, which cropped Colorado from `MESH_00.50` (3.0 s per file), `MESH_Max_60min` (3.4 s) and `MESH_Max_1440min` (6.2 s). Cross-check: a 60-minute maximum I built from 30 IEM 2-minute files matched NOAA's own `MESH_Max_60min` file on 249 of 276 cells at or above 0.75 in (largest difference 5 mm), and both had 76 cells at or above 1.00 in.

Not verified: NCEI's MRMS archive, and NSSL's MYRORSS reanalysis (reported to cover about 1998 to 2011); I did not look at either. Installing numpy or pygrib would need your approval.

## B2. S0025 against MESH (2026-06-08)

MESH window: 20 minutes either side of the report times, using the IEM 2-minute files (48 files for the 22-report piece, 75 for the whole 34), maximum per cell. Footprint = cells at or above 25.4 mm (1.00 in), plus 19.05 mm (0.75 in) as a second line because MESH usually reads low against ground reports (see below). **All MESH areas are restricted to within 30 km of the storm's reports**, because the same crop holds other storms. Maps: `mesh/s0025_piece22.png`, `mesh/s0025_whole34.png`. Left panel: MESH, the circle footprint (black), the gap (blue). Right panel: capsule p90 (green) and capsule wide (purple). Legend swatches are in that order: four MESH tiers (0.75 to 1.0, 1.0 to 1.5, 1.5 to 2.0, over 2.0 in), circle, gap, corridor (dashed), capsule p90, capsule wide, actionable hail report, other hail report, wind report, 20 km bar. I drew them with a stdlib PNG renderer, **not matplotlib, which is not installed** (needs your OK to install). Numbers: `mesh/b2_numbers.json`.

**Gap definition** (same as B4): the corridor is every point within R of the track line between the first and last report projections; the gap is the corridor minus the union of the circles. The piece's time-fit axis is meaningless (R² 0.05, 10 km/h), so the main run uses the PCA (spatial) axis; the time-fit axis is the sensitivity row.

| | Reports | Gap area | MESH >= 1.00 in in gap | MESH >= 0.75 in in gap |
|---|---|---|---|---|
| Piece, 22 | PCA axis | 2.5 km² | 0 km² | 0 |
| Piece, 22 | time axis | 47 km² | 0 | 0 |
| Whole, 34 | PCA axis | 651 km² | 0.2 km² (0.03%) | 3.3 km² (0.5%) |
| Whole, 34 | time axis | 869 km² | 8.5 km² (1.0%) | 19.7 km² (2.3%) |

**MESH shows essentially no hail in S0025's gaps.** For comparison, MESH at or above 1.00 in fills 8.1% of the whole-storm circle footprint. But this is a weak test of the gap-fill idea: the piece's 22 circles are packed and leave almost no gap, and the whole storm's gap is the empty space between two separate cells (the blue band in the map), which is a chaining artefact rather than a break inside one hail swath. B4's candidates below would test real within-swath gaps.

**Footprint comparison** (MESH at or above 1.00 in, within 30 km; the "covered" share is MESH area inside the shape; "precision" is MESH area divided by shape area):

| Shape | Piece: covered | Piece: precision | Whole: covered | Whole: precision |
|---|---|---|---|---|
| Circle, all reports | 51% (16 of 31 km²) | 1.5% | 44% (218 of 500 km²) | 8.1% |
| Circle, actionable reports only (today) | **0%** | 0% | 29% | 7.2% |
| Capsule p90 (PCA axis) | 0% | 0% | 72% | 6.4% |
| Capsule wide (PCA axis) | 80% | 1.4% | 88% | 5.3% |

At the 0.75 in line the circle covers 42% / 46% of MESH hail, capsule wide 65% / 80%, with precision 6.5 to 19% for the circle. Across everything: the MESH hail is far narrower than any of the shapes (typical swaths a few km wide against a 16 km circle diameter), so **precision is low for all of them**, and none of the shapes is much closer to the MESH footprint than the others. Capsule results move a lot with the axis choice (whole storm, p90 capsule: 72% covered with the PCA axis, 45% with the time axis), which reflects how poor the whole-storm track is.

**Reports against MESH.** For the 22 piece reports, MESH within 5 km tops out at about 0.95 in (24 mm) and **none of the 13 actionable hail reports (1.0 to 1.75 in reported) has MESH at or above 1.00 in within 5 km**. In the whole storm, 6 of 20 actionable hail reports do (the eastern cell, where reports sit on the MESH swath). The median MESH within 5 km is 0.95 in for both sets against 1.0 in reported. Possible reasons, none checked: MESH reads low for marginal hail, radar coverage over the foothills west of Denver is poor (beam blockage), or report positions are off by more than 5 km. So "MESH is right and the reports are wrong" cannot be concluded.

Also notable: 56% of the 1.00 in MESH area within 30 km of the whole storm's reports is farther than 8 miles from any report (71% for actionable reports): hail that no report-based rule would reach.

## B3. Cost of repeating B2 for the Part A storms

Only **65 of the 180** good-fit storms start on or after 2019-08-20 (60 on or after 2020-10-14, the MRMS v12 date). **The other 115 (2004 to mid-2019) have no MESH in either archive.** Windows: 20 minutes either side of each storm (median 116 min); windows on the same day are merged.

| Method | Files for the 65 storms | Download | Decode (measured, one core) | Notes |
|---|---|---|---|---|
| 2-min `MESH` from IEM | 3,859 | about 212 MB (about 0.5 s/file, roughly 30 min) | 3.0 s/file = **about 3.2 h**; my 2-worker run ran about 8 s/file while the machine was short of memory, so budget 4 to 5 h | exact timing; best fidelity |
| `MESH_Max_60min` from S3 (60 storms only) | about 165 | about 25 MB | 3.4 s/file = **about 10 min** | 60-minute granularity: windows widen by up to an hour |
| `MESH_Max_1440min`, one per UTC day | 60 to 68 | about 65 MB | 6.2 s/file = **about 7 min** | whole-day max mixes other storms; needs a spatial mask like my 30 km one |

Storage: raw files are deletable after decoding; keeping every decoded 2-minute crop would be about 1.7 GB, but only the per-storm maximum grids are needed (65 x 0.45 MB, about 30 MB) or just the footprint polygons. Memory: each decode peaks at about 110 MB. Not benchmarked: scaling to storm-sized clusters (308) or running nightly (one `MESH_Max_1440min` file a day is about 1 MB).

## B4. Gap-fill bound, report data only (180 good-fit storms)

`gap_fill.csv` (per storm), `b4_numbers.json`. Gap = corridor (within R = 8.05 km of the fitted track, between first and last report projections, flat ends) minus the union of the circles. Properties are the stored RentCast properties (25,362; today's, not the storm date's); sums are storm-by-property incidences. Two versions: circles from all reports (the brief) and from actionable reports only (what the matcher uses).

| | All-report circles | Actionable-report circles |
|---|---|---|
| Gap area, total | 36,492 km² (27% of the 134,145 km² of corridors; per storm median 24%, IQR 11 to 34%) | 47,644 km² |
| Properties in the circles (incidences) | 317,372 | 257,359 |
| Properties in the gaps (incidences) | **39,585** | **65,816** |
| Gap-filling adds | **12.5%** over the circles | **25.6%** over the circles |
| Storms with at least one property in a gap | 136 of 180 (median 38 per storm, IQR 1 to 216) | 142 of 180 (median 81) |
| Distinct properties in some storm's gap | 18,867 | 22,418 |
| ...of those, never in any circle of any of the 180 storms | **21** | **39** |

By era the gap properties are 12.7%, 13.2% and 11.5% of circle properties (2004 to 2012, 2013 to 2019, 2020 on). 27 storms have no stored property in either the corridor or the circles.

Reading: for a single storm, closing the gaps would add roughly an eighth more properties if you count the circles as built from every report, or a quarter more if you start from today's actionable-only circles. Across 20 years almost every gap property has been inside some other storm's circle (only 21 to 39 are not), so the gain is in per-storm attribution, not in reaching new addresses. The distinct count is nearly saturated: 25,213 of the 25,362 stored properties sit inside some storm's circle. Per-storm gap areas depend on the fitted axis; several gaps are the space between two merged cells (as in S0025), not holes in one swath.

**Candidates for a real within-swath MESH gap test** (MESH era, not chaining suspects, largest gaps): A01742 (2020-06-26, 22 reports, gap 991 km²), A02257 (2024-09-17, 10 reports, 322 km², 484 gap properties), A02079 (2023-07-20, 13 reports, 329 km²). I did not run them.

## Caveats for Part B

- One storm; the MESH comparison is a feasibility demonstration, not evidence for or against the capsule.
- MESH is a radar estimate; its bias against ground reports is visible here (see above) and I did not calibrate it.
- All MESH area statistics use a 30 km domain, chosen by me; the thresholds (25.4 mm and 19.05 mm) are also mine.
- Cell polygons are unions of 0.01 degree cells, so areas are about 1 km² per cell and boundaries are blocky.
- The 20-minute window padding is mine.
- The decoder is verified on three products from one day.
