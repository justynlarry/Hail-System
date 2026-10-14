# Radar-shaped storm areas and the HRRR fallback (2026-10-14)

This is the evidence for the 2026-10-14 addendum to parking-lot item 206, and for the two questions
added to item 208. It is analysis, not project code. Nothing here touches the database or the
matcher.

**Read first:** `summary.md`. The tier validation for both source sets (mPING + CoCoRaHS, and
CoCoRaHS only) at 1, 3 and 5 km margins is in `validation_by_source.txt`.

| Path | What |
|---|---|
| `tiers.py` | Step 2: core / margin / outer tiers, and the "radar-indicated" tier split out; independent ground-point check |
| `capsule.py` | Step 3a: area-matched capsules along the HRRR direction vs the circle |
| `stretch.py` | Step 3b: stretching circles along a direction vs enlarging the radius |
| `maps.py`, `maps/` | Three illustrative storms |
| `per_storm/`, `capsule/`, `stretch/` | Per-storm checkpoints: areas and counts only, no raw points |

**Inputs** (not in this folder):
- `docs/analysis/storm-shape-mesh-2026-10` (mesh60) and `docs/analysis/weather-sources-2026-10`:
  warning polygons, HRRR winds and the ground points.
- The raw mPING points are not in git (item 215). The checkpoints here hold only counts per tier and
  source. The maps plot mPING points, as with the 2026-10-14 weather-sources maps.

**Rerunning:** the scripts expect the `~/hail-analysis` layout (`mesh60/`, `storm_shape/`,
`weather_sources/` next to this folder) and the environment in
`docs/analysis/storm-shape-mesh-2026-10/requirements.txt`. They use cached inputs only; no downloads.
Each checkpoints per storm.

## Scripts were written in-session

These scripts were written by Claude during an exploration session, not keyed in. They are committed
because they feed a recorded finding.
