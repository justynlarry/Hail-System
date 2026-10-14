# Extra weather sources on top of reports and MESH (2026-10-14)

This is the evidence for decision log 2026-10-14, *Findings, not a decision: extra weather sources
on top of reports and MESH*, and for parking-lot items 210 and 215. It is analysis, not project
code. Nothing here touches the database.

**Read first:** `summary.md`. It covers Part A (feasibility per source), Part B (marginal value on
the 60 MESH storms, and the 180 good-fit storms for winds) and Part C (the ranking). Per-source
detail is in `notes/`.

| Path | What |
|---|---|
| `probe_mrms.py`, `probe_mping.py` | Part A test fetches |
| `common.py` | Shared helpers: storm membership, grid, domains, MESH windows (same as `mesh60`) |
| `partb_warnings.py`, `partb_ground.py`, `partb_winds.py` | Part B per source |
| `summarize_partb.py` | Pooled numbers (`partb_numbers.json`) and the 5 maps |
| `partb/warnings/`, `partb/winds/` | Per-storm checkpoints |
| `maps/` | 5 illustrative storms |
| `fetch_log.tsv` | Every fetch: time, file, HTTP status, bytes, URL. The mPING key is never logged. |

## Not in git

- **`samples/`**: raw downloads and extracts, about 1.7 GB. Most of it is HRRR wind fields; all of it
  can be fetched again.
- **`partb/ground/`**: the per-storm CoCoRaHS and **mPING** points. mPING is licensed for research
  only (item 215), so its raw points stay out of this public repo. mPING was used for internal
  validation only; the maps show LSR and CoCoRaHS points only (regenerated without mPING 2026-10-14).
- **Every `*.csv`** (the repo-wide rule).

A full copy, including the above, is on the workstation in `~/hail-analysis/weather_sources/`.

## Rerunning

The scripts expect the `~/hail-analysis` layout: `mesh60/` (MESH crops and per-storm results) and
`storm_shape/` (`reports_raw.csv`, `shape_by_storm.csv`, `storms_all.csv`) next to this folder.
`partb_ground.py` and `probe_mping.py` read `MPING_API_KEY` from `~/Hail-System/.env`.

The environment is that of `docs/analysis/storm-shape-mesh-2026-10/requirements.txt` (numpy,
matplotlib, pygrib, pyproj).

Run from the folder above this one:

1. `partb_warnings.py`
2. `partb_ground.py`
3. `partb_winds.py`
4. `summarize_partb.py`

Each one checkpoints per storm and skips finished storms.

Memory: one grid at a time, one worker. Peak was about 1 GB (an MRMS rotation-track decode in Part A);
Part B stayed under 0.5 GB.

## Scripts were written in-session

These scripts were written by Claude during an exploration session, not keyed in. They are committed
because they feed a recorded finding.
