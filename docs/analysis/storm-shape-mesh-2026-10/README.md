# Storm shape and MRMS MESH: report-based circles against radar hail (2026-10)

This is the evidence for decision log 2026-10-13, *Findings, not a decision: report-based circles
against radar hail (MRMS MESH), 60 storms*, and for parking-lot item 205. It is analysis, not
project code. Nothing here touches the database.

**Read first:** `mesh60/summary.md`, the 60-storm MESH test. Then `storm_shape/summary.md` for
Part A (shape, circle vs capsule) and Part B (S0025, gap-fill), and `storm_tracks/summary.md`
for the 12-month clustering.

| Folder | What | Ran on |
|---|---|---|
| `storm_tracks/` | 12-month storm clustering, S0025 | hail-dev (PostGIS via `docker exec`) |
| `storm_shape/` | all-years shape, circle vs capsule, gap-fill; `mesh/` holds the stdlib GRIB2 decoder and the S0025 test | hail-dev |
| `mesh60/` | the 60-storm MESH test (pygrib, numpy, matplotlib) | workstation (thor) |

## Not in git

- **Every `*.csv`.** The repo-wide `.gitignore` rule exists because this remote is public. They are
  regenerable:
  - `storm_shape/reports_raw.csv` comes from `storm_shape/q_extract_all.sql` on hail-dev.
  - `storms_all.csv`, `shape_by_storm.csv` and the other `storm_shape` CSVs come from
    `analyze_shape.py` and `b4_gaps.py`. They need PostGIS on hail-dev.
  - `storm_tracks/storm_reports.csv` and `reports_raw.csv` come from `storm_tracks/q_extract.sql`
    and `analyze.py`.
  - `mesh60/per_storm.csv` and `per_report.csv` come from `mesh60/summarize.py`, using
    `mesh60/per_storm/*.json`, which are committed.
- **The MESH download cache.** `mesh60/raw/` (about 20 MB of `.grib2.gz`) and `mesh60/crops/`
  (`.npz`) are re-fetched from NOAA's public S3 bucket on a rerun, and ignored by this folder's
  `.gitignore`.
- **The virtualenv.**

A full copy including the CSVs and caches is on the workstation at
`~/hail-analysis-archive-2026-10-13/` (checksums in its `MANIFEST.sha256`).

## Rerunning `mesh60`

From this folder:

1. `python3 -m venv .venv`
2. `.venv/bin/pip install -r requirements.txt`
3. `.venv/bin/python mesh60/run_storms.py`
4. `.venv/bin/python mesh60/summarize.py`

`run_storms.py` needs `storm_shape/reports_raw.csv`, `storm_shape/storms_all.csv`,
`storm_shape/shape_by_storm.csv` and `storm_tracks/storm_reports.csv` to be present. On hail-dev,
regenerate them as above or copy them from the archive.

Memory: one CONUS grid at a time, one worker. Peak was 427 MB on the workstation.

## Scripts were written in-session

These scripts were written by Claude during exploration sessions, not keyed in. They are committed
because they feed a recorded finding.
