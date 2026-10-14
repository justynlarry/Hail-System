# Extra weather sources on top of reports + MESH: Part A (feasibility)

Analysis only. Work is in `~/hail-analysis/weather_sources/`. `/tmp/hail-dev` is superseded and was
not used. No git.

**Status:** Parts A, B and C are done (2026-10-14). mPING was completed once the key arrived; the core set was approved for Part B 2026-10-14. Part B covers warning polygons,
CoCoRaHS and steering winds, plus mPING under its research licence. The maps for 3–5 storms belong
to Part B.

**Inputs and limits:**
- **Inputs:** `~/hail-analysis/{storm_shape,storm_tracks,mesh60}`.
- **Territory:** taken as the extent of reports inside the coverage zips, lat 38.52–40.89,
  lon −105.58 to −103.71 (`storm_shape/reports_raw.csv`). This is mine; the real boundary is the
  coverage zip list.
- **Test day:** 2023-05-10, storm A01973, the largest MESH swath of the 60.
- **Memory:** free memory at start was 476 MB free and 8.8 GB available, and stayed about the same.
  Peak RSS per step: rotation-track decode 995 MB (the largest; under the 2 GB line), HRRR 159 MB,
  IGRA2 streaming 16 MB.
- **Fetching:** one fetch at a time, no parallel workers, no system packages. Largest single download:
  77 MB (IGRA2 Denver). Every fetch is in `fetch_log.tsv`.
- **Disk:** samples total 119 MB.

## The six sources

| # | Source | Access | Cost | History | Live? | Territory density | Python effort | Independent of MESH? | Part A |
|---|---|---|---|---|---|---|---|---|---|
| 1 | NWS SVR/TOR warning polygons | IEM API / CSV export; NWS API live | free, no key | polygons ≥ 2004; **hail tag ≥ 2010-04** | **yes**, minutes (NWS API) | every warned storm; 146–875 SVR polygons per summer (BOU+PUB) | low (GeoJSON/CSV, stdlib) | **partial**: same radars, plus forecaster judgment and spotters | **pass** |
| 2a | MRMS SHI, POSH | NOAA S3 | free | 2020-10-14 | yes | grid | low (pygrib) | **no**: MESH = 2.54√SHI exactly; POSH ranks with SHI at 0.94 | **fail** |
| 2b | MRMS rotation tracks | NOAA S3 (60-min, from 2020-10-14); IEM 24-h only (from 2019) | free | 2020-10-14 | yes | grid, 0.005° | medium (about 1 GB per decode) | **partial**: velocity, not reflectivity; same radars | weak pass |
| 2c | MRMS RadarQualityIndex | NOAA S3; IEM earlier | free | 2020-10-14 (S3) | yes | grid | low | n/a: describes radar quality, not hail | carry as a covariate |
| 3 | CoCoRaHS hail | CoCoRaHS CSV export | free, no key; **terms not read** | 1998 | no; median entry delay 0.7 h (2023) | 639 reports, 191 stations, 75 days (2023 season); 73 at ≥ 1.00 in | low (CSV) | **yes** (ground); 76% are not in our IEM reports | **pass** |
| 4 | mPING | API v2 with key (`Authorization: Token`) | free; **"Research" licence only** | 2013 | **yes**: newest report 2 min old | 2,370 hail reports on 88 days (2023 season); 227 at ≥ 1.00 in | low (JSON) | yes (ground); 81% of ≥ 1.00 in reports already in IEM, mostly as PUBLIC | **pass** (licence caveat; updated 2026-10-14) |
| 5 | NCEI Storm Events | bulk CSV | free | 1950 (hail from 1955) | **no**: lags 3–4 months | 470 hail events in 2023 | low (CSV) | **no**: 99% duplicate of IEM hail; no hail damage figures | **fail** |
| 6a | HRRR winds | NOAA S3, byte ranges | free | 2014-07-30 | **yes** | grid, 3 km | medium (Lambert grid, grid-relative winds) | **yes** (model) | **pass** |
| 6b | Denver soundings | IGRA2 / IEM | free | 1919 to **2022-07-09, then stopped** | twice daily, until 2022 | one point | low (fixed-width) | **yes** | **pass, historical only** |

Per-source detail, URLs and traps are in `notes/1_…` to `notes/6_…`.

## What Part A found that changes the plan

1. **SHI and POSH are MESH.**
   - MESH = 2.54 × √SHI to within 0.2 mm on 1,944 cells, with identical footprints.
   - POSH is SHI rescaled by the freezing level.
   - Layering them on MESH would count the same radar twice.
2. **Storm Events is a copy of the reports for hail.**
   - 467 of 470 territory hail events (99%) match an IEM report at the same time and place, 98% with
     the same size.
   - There is no property-damage figure on any territory hail event in 2023.
   - It lags 3–4 months.
3. **Denver soundings stopped on 2022-07-09.**
   - IGRA2 was updated yesterday and still ends there, as does IEM.
   - The nearest active sites are across the Divide (Grand Junction) or in Nebraska.
   - So soundings cover 125 of the 180 good-fit storms but only 5 of the 60 MESH storms.
   - HRRR from 2014-07-30 fills the gap; between them all 180 are covered.
   - Why Denver stopped is unverified.
4. **Warning polygons reach back before MESH, and carry a forecaster hail size from 2010.**
   - This is the only source here with a storm-shaped footprint for 2004–2020.
   - Polygons cover all 180 storms; hail tags cover 146.
5. **CoCoRaHS mostly adds points we don't have, and mostly small hail.**
   - That makes it the one source that can *lower* confidence on the ground: hail fell here, and it
     was small.
   - Only 8 of the 2023 reports carry a hail-pad stone count; most are visual size reports.
6. **Traps found:**
   - IEM's warning export silently returns tornado warnings only unless the `phenomena` list is
     given.
   - Storm Events times are standard time all year (`MST-7`).
   - HRRR winds need about a 4° rotation from grid to true north.
   - Rotation tracks are on a finer grid than MESH; the MESH reader's grid assertion caught it.

## Proposed for Part B (approved 2026-10-14: warnings, CoCoRaHS, steering winds, mPING; the optional rows were not run)

| Source | Proposed test | Storms |
|---|---|---|
| 1 Warning polygons | **Agreement:** share of MESH ≥ 1.00 in inside the polygons, and of polygon area with MESH. **New information:** polygon area with no MESH and no report. **Pre-2020 stand-in:** polygon union vs MESH swath (recall, precision) on the 60, and hail tag vs MESH max inside. | 60 |
| 3 CoCoRaHS | **Agreement:** MESH at CoCoRaHS points vs reported size. **New information:** CoCoRaHS ≥ 1.00 in outside MESH and away from reports. **Lowering:** small or no hail inside the MESH swath. | 60 (date-matched) |
| 6 HRRR + Denver soundings | Angle error between wind (700, 500, 400, 300 mb, and the 700–300 mean) and the fitted storm direction. Soundings vs HRRR where both exist (2014-07 to 2022-07). | all 180 (HRRR 110, soundings 125) |
| 2b Rotation tracks (optional) | Share of MESH swath with rotation, and whether rotating storms have higher report–MESH agreement. About 1 GB per decode; one file at a time. | 60 |
| 2c RadarQualityIndex (optional) | Whether low RQI explains the storms and reports where MESH and reports disagree (the S0025 question). | 60 |
| 4 mPING (added 2026-10-14, key obtained) | As for CoCoRaHS: MESH at mPING points vs size; mPING ≥ 1.00 in outside MESH and away from reports; small hail inside the swath. Research licence: the assessment only. | 60 |

**Not proposed:**
- SHI and POSH (not independent).
- Storm Events (duplicate).

## Part B: marginal value beyond reports + MESH

Same 60 storms, MESH windows and 30 km domains as `mesh60`; winds use all 180 good-fit storms.
Scripts: `common.py`, `partb_warnings.py`, `partb_ground.py`, `partb_winds.py`, `summarize_partb.py`.
Per-storm checkpoints are in `partb/`, numbers in `partb_numbers.json`, maps in `maps/`.

### 1. Warning polygons (60 storms)

Polygons are the SVR and TOR warnings whose validity overlaps the storm's MESH window and which
contain a domain cell. All 60 storms had at least one (median 5).

| | Warning polygons | Today's circles |
|---|---|---|
| Share of MESH ≥ 1.00 in area inside (recall), pooled | **98.0%** | 56.0% |
| Same, per storm, median [IQR] (52 storms ≥ 10 km²) | 99% [97, 100] | 67% [43, 81] |
| Area, summed over storms | 171,704 km² (46% of the domains) | 53,013 km² |
| Share of the shape's area with MESH ≥ 1.00 in (precision), pooled | **10.9%** | 20.2% |
| Jaccard with the MESH swath, per-storm median | **0.087** | 0.176 |
| Area with no MESH ≥ 0.75 in and > 8 mi from any actionable report | 44% of polygon area | — |
| Circles or polygons (union): recall | 98.4% | |

- **Agreement:** polygons contain almost all MESH hail.
- **New information:** almost none. Polygons add area, not hail: 44% of their area has neither radar
  hail nor a report.
- **Every storm whose MESH was nearly empty still had polygons** (8 of 8, hail tags 0.75–1.5 in).
- **Hail tag:** across 59 storms, the maximum tag correlates 0.77 with the maximum MESH inside the
  polygons and 0.66 with the maximum reported hail. Median tag 1.75 in; median MESH maximum 2.26 in;
  median reported maximum 1.75 in. **This is a storm-level severity signal, not a location signal.**
- **Pre-2020 stand-in: no, not as a shape.** Polygons hold the hail, but they are 3.2× the circles'
  area, with half the circles' precision and half their Jaccard overlap. They are a forecast of where
  a storm *might* go in the next 30–60 minutes, drawn generously. As an *outer bound*, though ("hail,
  if any, fell inside here") they are right 98% of the time, and they exist back to 2004.
- **Caveat:** recall is partly by construction. A polygon covering the whole domain scores 100%.

### 3 and 4. CoCoRaHS and mPING (60 storms)

These are the points inside each storm's domain and MESH window (CoCoRaHS ±60 min, because its
times are approximate). Each point is compared with the maximum MESH within 5 km, the same rule
mesh60 used for reports.

| | CoCoRaHS | mPING |
|---|---|---|
| Points / storms with any | 232 / 39 | 1,020 / 54 |
| Points at ≥ 1.00 in | 67 | 266 |
| **Agreement:** ≥ 1.00 in points with MESH ≥ 1.00 in within 5 km | 53 (79%) | 240 (90%) |
| ≥ 1.00 in points with no MESH ≥ 1.00 in **and** outside today's circles (hail both missed) | **6** | **3** |
| ≥ 1.00 in points outside circles that sit on MESH ≥ 1.00 in (ground confirms swath beyond circle) | 0 | **7** |
| < 0.75 in points with MESH ≥ 1.00 in within 5 km (candidates for lowering) | 59 of 129 | 351 of 605 |
| Size vs MESH-within-5 km correlation | 0.36 | 0.44 |

- **Agreement** is as good as the IEM reports' (mesh60: 90%).
- **New information is tiny.** Across 60 storms, the two sources together find **9** large-hail
  points that reports and MESH both missed, and **7** that confirm MESH hail beyond the circles.
- **For lowering confidence, the 5 km test is too coarse.** Small hail within 5 km of a MESH
  ≥ 1 in cell is normal at a swath's edge. A point-cell or 1 km test would be needed (not run).
- mPING is the denser and more live of the two. CoCoRaHS has no points at all for 21 of the 60
  storms, and is thinner in 2025–2026: 1.1–1.7 points per storm, against 5.7–6.5 in 2023–2024. The
  last storm with a CoCoRaHS point is 2026-06-24. Whether that is fewer observers or later data
  entry is unverified.

### 6. Steering winds (180 storms)

These test whether the wind predicts the storm's **fitted direction** (time-vs-position fit,
`shape_by_storm.csv`). HRRR is the analysis hour nearest the storm's mid time, at its report
centroid, rotated to true north. The sounding is the Denver launch nearest the storm, within 6 h.

| Predictor | HRRR (n = 110) median abs error / bias / within 30° | Sounding (n = 116–123) |
|---|---|---|
| 700 mb | 111° / −69° / 6% | 79° / −23° / 20% |
| 500 mb | 46° / −39° / 26% | 42° / −33° / 34% |
| 300 mb | 32° / −25° / 47% | 43° / −32° / 39% |
| mean 700/500/400/300 mb | 39° / −33° / 37% | 44° / −33° / 34% |
| mean 0–6 km (approx.) | 44° / −35° / 36% | 40° / −32° / 37% |
| **Bunkers right-mover (approx.)** | **19° / +11° / 66%** (76% within 45°) | **23° / +7° / 60%** (73% within 45°) |

- **Storms move about 30–40° to the right of the mean wind**: the bias is negative for every
  level-wind predictor.
- **The right-mover estimate removes most of that.** It predicts direction to a median of 19–23°,
  and holds on the best-fit storms (R² ≥ 0.8: 18° HRRR, 24° sounding). Without chaining suspects:
  unchanged.
- **HRRR and soundings agree** where both exist (54–55 storms): 500 mb by a median of 13°, the
  700–300 mean by 6°, the right-mover by 11°. HRRR is a sound replacement for the discontinued
  Denver launches.
- **The 700 mb wind is useless** on the Front Range; it is near or below ground and terrain-driven.
- Coverage: HRRR 110, soundings 124, either 179 of 180, both 55.
- **Caveats:**
  - The "approx." layers use pressure levels (HRRR) or heights from the station (soundings), not
    a proper per-storm 0–6 km AGL layer.
  - The fitted direction has its own error. Reports are sparse, and the fit filter favours
    coherent storms.

### Maps

`maps/A01973.png`, `A02418.png`, `A02164.png`, `A02156.png`, `A02215.png` each show:
- MESH, today's circles, SVR/TOR polygons, LSR, CoCoRaHS and mPING points;
- arrows for the fitted motion, HRRR 500 mb and the right-mover.

**A02418** shows the warning problem: the polygons blanket the whole domain while the swaths are
narrow, and mPING and CoCoRaHS cluster where the reports already are. **A02156** (eastern plains)
shows MESH finding a large swath with almost no ground reports of any kind.

## Part C: recommendation

**Ranking (marginal value beyond reports + MESH, against effort):**

| Rank | Source | Marginal value | Effort | When it is useful |
|---|---|---|---|---|
| 1 | **HRRR winds (right-mover)** | **High for direction/shape**: 19° median error, independent of radar and reports. Turns "which way is the swath elongated" into a forecast, for capsules or for ordering houses along the track. | Medium (byte-range GRIB, grid rotation, about 15 MB per storm hour) | Live (2014-07 on) **and** historical |
| 2 | **Warning polygons** | **Medium**, as an outer bound (98% of MESH hail inside) and a storm-level severity tag (r = 0.77). **Low as a shape** (Jaccard 0.09). | Low (CSV + shapefile) | Live (NWS API, minutes) and historical from 2004, so the **only pre-2020 bound** |
| 3 | mPING | Low: agreement 90%, but only 3 large-hail points missed by reports + MESH, 7 confirmations beyond circles. Dense small-hail coverage. | Low (API), but **research licence only** | Live, from 2013 |
| 4 | CoCoRaHS | Low: 79% agreement, 6 missed points, thinner in 2025–2026 | Low | Hours after; historical from 1998 |
| 5 | Denver soundings | Same information as HRRR's right-mover where both exist | Low | **Historical only** (ended 2022-07-09); fills 2004–2014 before HRRR |
| — | Storm Events, SHI, POSH | None (duplicate of reports, or of MESH) | — | — |

**Smallest set that captures most of the benefit:** reports + MESH + **HRRR right-mover winds** +
**warning polygons**.
- Winds give direction, which nothing else here does well.
- Polygons give a live trigger and the only footprint bound before MESH (2004–2020). Denver
  soundings extend the direction estimate to 2004–2014.

**Where adding more stops paying off:** after those two.
- mPING and CoCoRaHS mostly re-confirm what reports and MESH already show: 90% agreement, and only
  9 new large-hail points across 60 storms.
- Their remaining use is lowering confidence with small hail, which this test could not establish at
  5 km resolution.
- mPING's research-only licence also blocks product use until mPING says otherwise.

**Live only / historical only:**
- **Live and historical:** HRRR, warning polygons, mPING.
- **Historical only:** Denver soundings (stopped 2022) and Storm Events (3–4 month lag, and a
  duplicate anyway).
- **CoCoRaHS:** usable hours after a storm, not during it.

**Not tested, worth a follow-up if the open questions matter:**
- RadarQualityIndex against the S0025-type disagreements (the mountains question).
- A capsule oriented by the HRRR right-mover and checked against MESH: the shape test this direction
  result makes possible.
- A 1 km point test for small-hail "lowering".

## Unverified

- Part B: the time windows, the 5 km point rule, the ±60 min CoCoRaHS slack and the approximate
  0–6 km layers are my choices. Warning recall is partly by construction (see section 1).
- Terms of use for CoCoRaHS, NWS/IEM, NCEI and mPING (no terms pages were read).
- mPING licence text (registration offers only "Research"); whether the API returns every public hail report.
- Why Denver soundings stopped, and whether they continue under another ID.
- Rotation-track and RadarQualityIndex units (pygrib reports "unknown").
- Exact update latency of IEM warnings, MRMS products and HRRR on AWS.
- RAP on AWS.
- Whether CoCoRaHS ObservationTime is hail time.
- The territory box.

## Files

- `summary.md` (this file)
- `notes/1_warning_polygons.md` … `notes/6_steering_winds.md`
- `probe_mrms.py`, `probe_mping.py` (Part A)
- `common.py`, `partb_warnings.py`, `partb_ground.py`, `partb_winds.py`, `summarize_partb.py` (Part B)
- `partb/`: per-storm checkpoints and run logs
- `partb_numbers.json`: every Part B number quoted above
- `maps/`: 5 illustrative storms
- `fetch_log.tsv`: every fetch, with HTTP status, bytes and URL (the mPING key is never logged)
- `samples/`: raw samples, extracts and caches, about 1.7 GB, most of it HRRR wind fields
  (`samples/winds/hrrr`, 1.6 GB; deletable, re-fetchable)
