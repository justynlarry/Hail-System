# Handoff: storm shape / MESH work (hail-dev VM -> thor)

Claims here are from the previous session; verify against the files before relying on them.

## What exists
- `../storm_tracks/`: 12-month storm clustering (hail + convective wind), `summary.md`, `analyze.py`.
- `./` (storm_shape): Part A (all years, shape, circle vs capsule), `summary.md` (Part A + Part B1-B4), scripts `analyze_shape.py`, `b4_gaps.py`, `q_extract_all.sql`, `reports_raw.csv` (7,772 reports, extracted from the hail-dev Postgres; **no DB on thor**), `gap_fill.csv`, `circle_vs_capsule.csv`, `shape_by_storm.csv`, `storms_all.csv`.
- `./mesh/`: stdlib GRIB2 decoder `mesh_lib.py`, `fetch_window.py`, `b2_s0025.py`, `b2_render.py`, `png_render.py`, `validate_pygrib.py`, `validate_window.py`, maps `s0025_*.png`.
- NOT copied: `.venv` (not portable; recreate), raw MESH downloads and decoded crops (re-fetchable).

## Latest user instructions (approved, not yet done)
1. Make `scratchpad/.venv` (`python3 -m venv`), install numpy, matplotlib, pygrib. (On hail-dev this installed cleanly: numpy 2.5.4, matplotlib 3.11.2, pygrib 2.1.8; pygrib wheel bundles eccodes, no system libs.)
2. Re-validate against the hand-rolled decoder on the same file, explain mismatches, then use the standard reader instead of `mesh_lib.py`.
   - DONE: `validate_pygrib.py` showed mesh_lib is bit-identical to pygrib on 3 products (225,000 cells each, max diff 7e-15). Only difference: my crop has one extra row/column from rounding at the box edge.
   - So the earlier "249 of 276 cells match" (60-min max built from IEM 2-min files vs NOAA `MESH_Max_60min`) is NOT a decoder error. Hypothesis, untested: the two products use different time windows or file stamps. `validate_window.py` tests this; its result was never seen (the run was interrupted when the VM struggled).
3. Run ALL storms covered by NOAA's 60-minute max product (about 60 storms, start >= 2020-10-14, from the 180 good-fit storms in `shape_by_storm.csv`; windows = storm start/end +-20 min, UTC). For each storm report:
   - area of MESH >= 1.00 in (25.4 mm) within 30 km of its reports;
   - share of that area covered by today's actionable-report circles (R = 5 mi = 8046.72 m);
   - share more than 8 mi from any actionable report;
   - for each actionable hail report, max MESH within 5 km;
   - foothills vs plains (state the cutoff).
   Then summarize report-MESH agreement overall and by foothills vs plains. Include A01742, A02257, A02079 with their gap check, if in the 60. Maps (matplotlib PNG) for the 5 storms with the worst agreement. Report and stop.

## Data sources
- NOAA S3: `https://noaa-mrms-pds.s3.amazonaws.com/CONUS/MESH_Max_60min_00.50/YYYYMMDD/MRMS_MESH_Max_60min_00.50_YYYYMMDD-HHMMSS.grib2.gz` (every 2 min, ~120-180 KB, from 2020-10-14). 7000x3500 grid, 0.01 deg, lat 54.995 down, lon -129.995 east, mm, -1 = no hail.
- Iowa State: `https://mtarchive.geol.iastate.edu/YYYY/MM/DD/mrms/ncep/MESH/MESH_00.50_YYYYMMDD-HHMM00.grib2.gz` (2-min, from 2019-08-20).
- Per-decode cost with the hand decoder: 3-6 s and ~110 MB RAM; hail-dev has 3.6 GB RAM and 2 cores, which is why this was too much.

## Things to know
- Report times: `reports_raw.csv` has UTC and America/Denver columns; clustering/fit code is in `analyze_shape.py` (copied into `b4_gaps.py`). Storm membership for the 180 is reproducible only by re-running those scripts, whose geometry steps call PostGIS through `docker exec` on hail-dev. On thor you'd need shapely/pyproj (or a PostGIS) in place of that.
- Actionable report = HAIL >= 1.00 in, TSTM WND GST >= 58 mph, TSTM WND DMG/DOWNBURST any (from `report_types`).
- Properties table (25,362 RentCast listings) is only in the hail-dev DB.
- Repo rule (CLAUDE.md): `.md` docs may be edited after approval; code is normally keyed in by the user, scratchpad exploration excepted. Scripts that feed a decision must be committed.
