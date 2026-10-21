# Which MESH 1440-minute file covers a Denver storm day (Phase 5b, slice 0, 2026-10-21)

Analysis, not project code. Written by Claude in-session (not keyed in) and committed because it feeds
the decision-log entry "2026-10-21 — The MESH day-file stamp marks the end of the window".

| Path | What |
|---|---|
| `window_check.py` | Compares `MESH_Max_1440min` stamped T with the cell-by-cell maximum of the 24 hourly `MESH_Max_60min` files ending at T (and at T plus or minus 1 h), over a Colorado crop. Downloads from the public NOAA bucket into the current directory (cached). Uses `mesh_lib.py` from `../storm-shape-mesh-2026-10/storm_shape/mesh/` |
| `window_check.log` | The run. Section A and the 2026-06-08 line of section B completed; the 2023-05-10 line stopped on `assert nbits == 16` (that day's hourly files use 8-bit PNG packing, which `mesh_lib` does not read). The two `retry` lines are S3 connection resets |

**Result:** the stamp is the END of a trailing 24-hour window. On 2026-06-08 the match is exact (416,331 of
416,331 cells, worst difference 0.0 mm) at stamps 20:00, 21:00, 22:00 and 23:00 UTC, and the 06:00 UTC
2026-06-09 file (Denver midnight in summer) matches too. A one-hour offset does not.

**Not done here:** a second date (2023-05-10), and a winter (MST) day. The slice 1 decoder reads both bit
depths; the 2023 check becomes a golden test there.
