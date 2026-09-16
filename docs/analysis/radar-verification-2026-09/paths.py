"""Shared paths for the 2026-09 radar verification scripts.

Everything resolves from this file's location, so the scripts run from any
working directory and keep working if the repo moves. The original versions
lived in a session scratchpad with absolute paths baked in, which made the
study unreproducible the moment the session ended -- the failure this module
exists to prevent.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Inputs that are NOT in the repo. data/ is gitignored (~1.9 GB), so a rerun
# has to re-download; see the "Reproducing this" section of the write-up.
SWDI_DIR = REPO / "data"
TIGER = REPO / "data/raw/tiger/tl_2025_us_zcta520/tl_2025_us_zcta520"

# Inputs that ARE in the repo.
COVERAGE_ZIPS = REPO / "config/coverage_zips.txt"

# Exported from iem_data; see the write-up for the query shape.
REPORTS = REPO / "output/hail_reports_2016_2025.csv"

# Intermediate. Also gitignored (64 MB) -- regenerate with reduce.py.
REDUCED = REPO / "output/radar-reduced"

YEARS = range(2016, 2026)
