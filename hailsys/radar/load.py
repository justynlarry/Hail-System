"""Loads one local day of radar into the database.

Fetches the NOAA day file (fetch.py), decodes the cells at or above the floor inside the crop
box (mesh.py) and stores them with a radar_days row.  The crop box comes from the active
coverage zips, so another territory gets another box with no code change.  Runs on the 
connection it is given and leaves the commit to the caller.
"""

import os
from datetime import datetime, timedelta, timezone
from math import ceil, floor

from hailsys.radar import fetch, mesh

# Fixed limit, the same as sql/048, nothing under half an inch is stored and no parameter
# set may ask for a threshold below it.
FLOOR_IN = 0.50
FLOOR_MM = FLOOR_IN * mesh.MM_PER_INCH
# Fixed limit, like the floor:  no hail larger than about 8 in (203 mm) has been measured, so a MESH
# value above 10 inches is NOAA's processing, not hail.  A day with any cell above it is recorded
# 'suspect' with no cells.
CEILING_IN = 10.0
CEILING_MM = CEILING_IN * mesh.MM_PER_INCH


# The crop box is the coverage extent plus this much on every side.  Fixed, a zone margin is at 
# most 50 km (sql/048) and a degree of latitude is 111 km, so half a degree holds any margin
# a parameter set can have.
BOX_MARGIN_DEGREES = 0.5

# NOAA publishes the day file a little after its stamp.  A 404 younger than this is lateness,
# not a gap, so it is an error and not a 'missing' day that a backfill would then skip.
MISSING_AFTER = timedelta(hours=6)


class LoadError(Exception):
    """Nothing was stored for the day, message explains."""


_BOX_SQL = """
SELECT ST_YMax(e) AS lat_max, ST_YMin(e) AS lat_min, ST_XMin(e) AS lon_min, ST_XMax(e) AS lon_max
FROM (SELECT ST_Extent(z.geom) AS e
    FROM coverage_zips c
    JOIN zcta_boundaries z ON z.zcta5 = c.zcta5
    WHERE c.removed_at IS NULL) x
"""

_STATUS_SQL = "SELECT status FROM radar_days WHERE day = %(day)s"

_DAY_SQL = """
INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min,
    box_lon_min, box_lon_max, floor_mm, cells_in_box, cells_stored, max_mm)
VALUES (%(day)s, %(run_id)s, %(status)s, %(stamp)s, %(key)s, %(lat_max)s, %(lat_min)s,
    %(lon_min)s, %(lon_max)s, %(floor_mm)s, %(cells_in_box)s, %(cells_stored)s, %(max_mm)s)
"""

_START_SQL = "INSERT INTO radar_runs (run_mode) VALUES (%(mode)s) RETURNING run_id"

_FINISH_SQL = """
UPDATE radar_runs
SET run_status = %(status)s, finished_at = now(), days_loaded = %(loaded)s,
    days_missing = %(missing)s, days_suspect = %(suspect)s, days_built = %(built)s,
    days_failed = %(failed)s, error_detail = %(error)s
WHERE run_id = %(run_id)s
"""


def crop_box(conn):
    """(lat_max, lat_min, lon_min, lon_max) of the active coverage zips plus the margin, on the grid.
    
    Edges are moved outward to whole cells and kept inside NOAA's grid.
    """
    row = conn.execute(_BOX_SQL).fetchone()
    if row["lat_max"] is None:
        raise LoadError("There are no active coverage zips, so there is no area to load.")
    m = BOX_MARGIN_DEGREES
    row0 = max(0, floor((mesh.LA1 - (row["lat_max"] + m)) / mesh.STEP))
    row1 = min(mesh.NJ - 1, ceil((mesh.LA1 - (row["lat_min"] - m)) / mesh.STEP))
    col0 = max(0, floor(((row["lon_min"] - m) - mesh.LO1) / mesh.STEP))
    col1 = min(mesh.NI - 1, ceil(((row["lon_max"] + m) - mesh.LO1) / mesh.STEP))
    return (round(mesh.LA1 - row0 * mesh.STEP, 6), round(mesh.LA1 - row1 * mesh.STEP, 6),
            round(mesh.LO1 + col0 * mesh.STEP, 6), round(mesh.LO1 + col1 * mesh.STEP, 6))


def start_run(conn, mode):
    """Record a run and return its run_id."""
    return conn.execute(_START_SQL, {"mode": mode}).fetchone()["run_id"]


def finish_run(conn, run_id, *, loaded, missing, suspect=0, built=0, failed=0, error=None):
    conn.execute(_FINISH_SQL, {"run_id": run_id, "loaded": loaded, "missing": missing, "suspect": suspect,
                               "built": built, "failed": failed,
                               "error": error, "status": "failed" if error else "complete"})


def delete_day(conn, day):
    """Remove a day and everything built from it, so it can be fetched again.
    
    The likelihood builds go first, under every set.  likelihood_builds.day references
    radar_days (sql/054), and they were all built from the cells being thrown away.
    """
    for table in ("likelihood_zones", "likelihood_builds",
                  "radar_zones", "radar_zone_builds", "radar_cells", "radar_days"):
        conn.execute(f"DELETE FROM {table} WHERE day = %(day)s", {"day": day})


def load_day(conn, day, tz, directory, *, box, run_id, now=None, **fetch_kw):
    """Fetch, decode and store local date 'day'.  Returns 'loaded', 'missing', 'suspect' or 'skipped'.
    
    'skipped' means the day is already in radar_days (loaded, missing or suspect), use delete_day first
    to fetch it again.  LoadError if the file is not the one asked for, or NOAA has no file
    but the stamp is too recent to call it missing.  Other errors (FetchError, MeshError)
    propagate.  Downloaded file is deleted either way.
    """
    if conn.execute(_STATUS_SQL, {"day": day}).fetchone() is not None:
        return "skipped"
    now = now or datetime.now(timezone.utc)
    args = {"day": day, "run_id": run_id, "lat_max": box[0], "lat_min": box[1],
            "lon_min": box[2], "lon_max": box[3], "floor_mm": FLOOR_MM}
    try:
        path, stamp, key = fetch.fetch_day(day, tz, directory, **fetch_kw)
    except fetch.NotAvailable:
        stamp = fetch.day_stamp(day, tz)
        if now - stamp < MISSING_AFTER:
            raise LoadError(f"NOAA has no file for {day} yet, its stamp {stamp:%Y-%m-%d %H:%MZ} "
                            f"is under {MISSING_AFTER} old.  Try again later.")
        conn.execute(_DAY_SQL, {**args, "status": "missing", "stamp": stamp,
                                "key": fetch.source_key(stamp), "cells_in_box": None,
                                "cells_stored": 0, "max_mm": None})

        return "missing"
    try:
        decoded = mesh.read_cells(path, box, FLOOR_MM)
    finally:
        os.remove(path)
    if decoded["valid_time"] != stamp:
        raise LoadError(f"The file for {day} says it is valid {decoded['valid_time']:%Y-%m-%d %H:%MZ}, "
                        f"not {stamp:%Y-%m-%d %H:%MZ}.")
    if decoded["max_mm"] is not None and decoded["max_mm"] > CEILING_MM:
        conn.execute(_DAY_SQL, {**args, "status": "suspect", "stamp": stamp, "key": key,
                                "cells_in_box": decoded["cells_in_box"], "cells_stored": 0,
                                "max_mm": decoded["max_mm"]})
        return "suspect"
    cells = decoded["cells"]
    conn.execute(_DAY_SQL, {**args, "status": "loaded", "stamp": stamp, "key": key,
                            "cells_in_box": decoded["cells_in_box"], "cells_stored": len(cells),
                            "max_mm": decoded["max_mm"]})
    if cells:
        with conn.cursor() as cur:
            with cur.copy("COPY radar_cells (day, grid_row, grid_col, mm, geom) FROM STDIN") as copy:
                for row, col, mm in cells:
                    lon = mesh.LO1 + col * mesh.STEP
                    lat = mesh.LA1 - row * mesh.STEP
                    copy.write_row((day, row, col, mm, f"SRID=4326;POINT({lon:.6f} {lat:.6f})"))
    return "loaded"
