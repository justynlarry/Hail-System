# Storm tracks, 2025-10-13 to 2026-10-12 (America/Denver)

Source: `iem_data` joined to `report_types`, read-only queries (`q_extract.sql`), clustering and fits in `analyze.py` (stdlib Python). Re-run: `python3 -I analyze.py`. Latest report in the archive is 2026-10-11 19:45 MT.

## 1. Inventory and what was included

Wind reports ARE in `iem_data`, so the analysis went ahead. Full inventory of the past 12 months (all of Colorado, 10,784 rows):

| Type | n | Included? |
|---|---|---|
| SNOW 5906, RAIN 1784, FLASH FLOOD 83, EXTREME COLD 76, DEBRIS FLOW 52, FOG 33, plus 6 other minor types | n/a | no, not convective hail/wind |
| NON-TSTM WND GST | 1897 | **no**: non-convective (downslope/synoptic) wind, which is what the brief said to exclude |
| NON-TSTM WND DMG | 14 | **no**, same reason |
| **HAIL** | 453 | yes |
| **TSTM WND GST** | 366 | yes (magnitude = gust mph, 45 to 113) |
| **TSTM WND DMG** | 37 | yes (no magnitude) |
| DOWNBURST | 0 | would be included; none in the window |
| TORNADO 23, LANDSPOUT 11, FUNNEL CLOUD 8 | 42 | no. Convective, but not hail/wind and not asked for |

- **No outreach floors applied.** `report_types.min_magnitude` (HAIL 1.00 in, gust 58 mph) is an outreach rule, not a meteorological one. Sub-floor hail (min 0.25 in) and gusts (min 45 mph) still mark where a storm was. Note that this makes max hail/wind describe *any* report, not outreach-eligible ones.
- `report_types.roof_relevant` is true for all three included types, so it didn't discriminate here. The inclusion rule is the TSTM/NON-TSTM split plus hail.

## 2. Area

Coverage = union of the 183 active `coverage_zips` ZCTA polygons; "buffer" = within 50 km of that union (UTM 13N / EPSG:26913 distance). 856 included-type reports in the window:

| | HAIL | TSTM WND GST | TSTM WND DMG | total |
|---|---|---|---|---|
| inside coverage | 166 | 54 | 6 | **226** |
| buffer (0 to 50 km) | 103 | 54 | 2 | **159** |
| beyond 50 km (not analysed) | 184 | 258 | 29 | 471 |

More than half of all hail/wind reports (471 of 856) are beyond the 50 km buffer, on the eastern plains. That is a real truncation problem. See caveats.

## 3 to 4. Time and clustering

All times converted to America/Denver before display; clustering used absolute UTC minutes, not calendar dates, so evening storms are not split. 38 of the 200 reports in qualifying storms have a UTC date different from their local date.

Single-linkage: two reports link if within D km (UTM 13N) **and** within T minutes. Hail and wind clustered together. Cells are `clusters / qualifying storms` (qualifying = ≥5 reports spanning ≥30 min), 385 reports:

| D \ T | 30 min | **60 min** | 120 min |
|---|---|---|---|
| 15 km | 157 / 10 | 150 / 11 | 149 / 11 |
| **30 km** | 129 / 13 | **113 / 17** | 107 / 17 |
| 60 km | 111 / 15 | 87 / 18 | 77 / 17 |

Reading: the *total* cluster count is very sensitive (77 to 157) because most are 1 to 4-report fragments (53 singletons at baseline). The *qualifying storm* count is much steadier: 17 at baseline, 10 to 18 across the grid, 13 to 18 for D ≥ 30 km. Time threshold matters little at 60 to 120 min. Distance matters more. At 15 km, storms break up and fall below the 5-report bar.

Buffer sensitivity at baseline thresholds: 25 km, 321 reports, 12 storms; 50 km, 385, 17; 100 km, 558, 21; no limit, 856, 33. The 50 km choice matters; see caveats.

## 5. Per-storm results

`storms.csv` has one row per cluster (113); 17 qualify. `storm_reports.csv` has every analysed report (385) with its `storm_id`. Direction fields are empty for non-qualifying clusters. IDs are chronological.

- **Direction/speed:** OLS of UTM x and y (km) against time (hours); bearing = `atan2(vx, vy)`, the direction the storm moved **toward**, degrees clockwise from north. `fit_r2` = share of position variance explained by linear motion (x and y combined). `direction_confidence`: ok (R² ≥ 0.5), weak (0.25 to 0.5), poor (< 0.25). Of 17: **12 ok, 2 weak, 3 poor**; median R² 0.64.
- **Footprint:** convex hull of the storm's reports, buffered 8.05 km (5 mi, the zip/match radius default from decision log 2026-09-10; the radii now live in `settings`, so confirm the current value before using these for attribution). `footprint_km2` is in the CSV; polygons are in `storms.geojson` (EPSG:4326) with the fitted track line.

## 6. Summary

**Directions** (17 qualifying storms, direction toward):

| N | NE | E | SE | S | SW | W | NW |
|---|---|---|---|---|---|---|---|
| 2 | 3 | 5 | 5 | 2 | 0 | 0 | 0 |

Restricted to the 12 "ok" fits: N 2, NE 2, E 4, SE 3, S 1. Nothing moves toward the western half of the compass, which is the expected west-to-east Front Range pattern. **I have not checked this against radar or NWS storm motion.**

**Speeds:** median 31 km/h (19 mph), quartiles 17 / 31 / 46 km/h. Bins: <20 km/h 4, 20 to 40 km/h 6, 40 to 60 km/h 7, none faster. Report times are approximate, so treat individual speeds as rough.

**Seasonality of the qualifying storms:** June 10, July 3, August 3, May 1. Nothing before May (hail/wind reports outside May to Sept are a handful of isolated ones).

**Five largest storms (by report count):**

1. **S0025, 2026-06-08 15:01 to 16:51.** 34 reports (32 hail, 2 gusts), max hail 1.75 in, max gust 66 mph. Jefferson 20, Douglas 5, Adams 4, Elbert 3. Moved E (96°) at ~42 km/h, 77 km track. R² 0.47 (weak). Footprint ~5,740 km². Entirely inside coverage.
2. **S0015, 2026-06-01 13:05 to 15:34.** 28 hail, max 2.0 in. Denver 14, Adams 7, Arapahoe 5. Moved E (93°) at ~23 km/h, R² 0.80 (ok). All inside coverage.
3. **S0035, 2026-06-23 16:19 to 19:28.** 20 hail, max 2.5 in, almost all El Paso County. Slow SSE (157°), ~14 km/h, R² 0.51. Longest-lived of the five (189 min).
4. **S0044, 2026-06-24 16:09 to 18:16.** 15 hail, max 2.75 in, Morgan 11, Washington 4. SE (129°) at ~31 km/h, R² 0.96, the cleanest fit. Entirely in the buffer (northeast of the territory).
5. **S0040, 2026-06-23 20:36 to 22:25.** 14 reports, max hail 3.0 in, gust 58 mph, all Morgan County. Fit gives 4.6 km/h with R² 0.10 (poor): the reports are nearly stationary, so **its bearing is meaningless** (likely a slow or stalled cell, or one place reported repeatedly). Hail size alone makes it a candidate for review.

Also notable: **S0013 (2026-05-21)** has the largest hail (4.5 in) among 10 reports, all in the buffer.

**Paired hail+wind vs hail alone** (same thresholds):

| Input | reports | clusters | qualifying storms |
|---|---|---|---|
| hail + wind | 385 | 113 | **17** |
| hail only | 269 | 67 | **12** |
| wind only | 116 | 62 | 4 |

Wind added 5 qualifying storms, +42% over hail alone. Of the 17: 11 meet the bar on hail reports alone; 4 contain no hail (wind-only: S0069, S0072, S0075, S0085, all July/August gust events); 2 have some hail but too little (<5 reports or <30 min) without the wind reports. So yes, **wind filled gaps**, mostly in July and August, when hail was sparse (9 hail vs 43 gust reports in July). Hail-only counts across the sensitivity grid are 8 to 12; across the 9-cell grid, paired minus hail-only is 2 to 6 storms (5 at baseline), so wind adds storms under every setting tried. The counts are not independent of each other: wind reports can also bridge two hail clusters into one.

## Caveats

- **Report density follows population.** The Denver metro, Colorado Springs and the I-25 corridor have many reports per storm; the plains have few observers. "Largest storm" mostly means "storm that crossed the most people". A storm over empty land can look small or fail the 5-report bar. Counts say little about storm intensity. Likewise, the apparent absence of reports in the foothills and mountains does not show where storms formed, so the brief's "storms form west of the territory" can't be tested with this data.
- **Report times are approximate.** NWS Local Storm Reports carry the time the spotter or office logged, often rounded or delayed, and not the time the hail fell. Velocity from a linear fit across ~1 to 3 hours of noisy times is rough; for the weak/poor fits (5 of 17) treat the bearing as unreliable.
- **Truncation at the buffer.** 471 reports lie beyond 50 km, and clustering without any limit finds 33 qualifying storms vs 17. Storms moving E/SE leave the buffer, so their tracks and footprints are cut off, and the reported "start"/"end" may not be the true ones. This understates track length and may bias bearings. Consider re-running at 100 km before using the footprints.
- **Colorado only.** `iem_data` contains only CO rows (the ingest does not hold neighbouring states), so the buffer is also cut at the state line (NE/N/E).
- **Single linkage can chain** distinct cells into one cluster if reports bridge them in space and time; the 30 km / 60 min thresholds are guesses. Duplicate reports of one event by different sources are not removed.
- **Fit method:** straight-line motion, 1 hour or more of data; curving or splitting storms fit poorly. Footprints are hulls of reports plus buffer, not radar swaths.
- **Included types:** magnitudes are not filtered (see §1).

## Unverified

- Bearing/speed/location against radar, NWS storm data or news; none was checked.
- **`map.html` was not opened or rendered.** No browser and no node on this machine, so the JS has never run. Syntax and drawing are untested. `storms.geojson` can be loaded in any GIS tool as an independent check.
- Footprint polygons were produced by PostGIS but I didn't inspect them visually; centroid and county counts of the top five looked plausible.
- Whether NON-TSTM WND reports in Colorado are *all* non-convective. The TSTM/NON-TSTM split is the NWS's own label; I did not audit individual reports.
- The production OptiPlex was not touched; everything ran against the `hail-dev` database.

## Files

`storms.csv`, `storm_reports.csv`, `storms.geojson`, `map.html`, `summary.md`; working files `analyze.py`, `map_template.html`, `q_extract.sql`, `reports_raw.csv`, `numbers.json`.
