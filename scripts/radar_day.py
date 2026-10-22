#!/usr/bin/env python3

"""Load and build one local day of radar by hand

    python3 scripts/radar_day.py 2026-06-08                # fetch if needed, then build zones
    python3 scripts/radar_day.py 2026-06-08 --rebuild      # zones again from the stored cells
    python3 scripts/radar_day.py 2026-06-08 --refetch      # delete the day, fetch, build
    python3 scripts/radar_day.py 2026-06-08 --set 3        # build with an older parameter set

Fetching is one public NOAA file (about 1 MB), no key, no cost, and nothing is sent to anyone.
The day is a date in the display time zone.  Each run is recorded in radar_runs as 'manual'.
"""

import argparse
import sys
import tempfile
from datetime import date, datetime, timezone

from hailsys.db import get_connection
from hailsys.radar import fetch, load, warnings, zones
from hailsys.tuning import DISPLAY_TZ


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Load and build one day of radar zones.")
    parser.add_argument("day", type=date.fromisoformat, help="local date, YYYY-MM-DD")
    parser.add_argument("--rebuild", action="store_true",
                        help="build zones again from the cells already stored, fetch nothing")
    parser.add_argument("--refetch", action="store_true",
                        help="delete the day and everything built from it, then fetch it again")
    parser.add_argument("--set", type=int, dest="set_id", default=None,
                        help="parameter set to build with, default the newest")
    parser.add_argument("--no-warnings", action="store_true",
                        help="do not fetch the NWS warning polygons, so no outer layer unless already held")
    args = parser.parse_args(argv)
    if args.rebuild and args.refetch:
        parser.error("--rebuild and --refetch do not go together")
    return args


def main(argv=None):
    args = parse_args(argv)
    if fetch.day_stamp(args.day, DISPLAY_TZ) > datetime.now(timezone.utc):
        print(f"{args.day} has not ended yet.", file=sys.stderr)
        return 2
    with get_connection() as conn:
        run_id = load.start_run(conn, "manual")
        conn.commit()
        loaded = missing = 0
        try:
            if args.refetch:
                load.delete_day(conn, args.day)
            status = "stored"
            with tempfile.TemporaryDirectory() as folder:
                if not args.rebuild:
                    status = load.load_day(conn, args.day, DISPLAY_TZ, folder,
                                           box=load.crop_box(conn), run_id=run_id)
                    loaded, missing = status == "loaded", status == "missing"
                print(f"{args.day}: file {status}")
                if not args.no_warnings:
                    print(f"  warnings: {warnings.ensure_day(conn, args.day, DISPLAY_TZ, folder, run_id=run_id)}")
            if status not in ("missing", "suspect"):
                built = zones.build_day(conn, args.day, DISPLAY_TZ, set_id=args.set_id)
                print(f"  set {built['set_id']}: {built['cells_over_threshold']} cells over the threshold, "
                      f"{built['patches']} patches, {built['patches_anchored']} anchored "
                      f"by {built['anchor_reports']} reports")
                for layer in ("core", "margin", "radar_indicated", "outer"):
                    print(f"  {layer:16s}{built['areas'].get(layer, 0):10.1f} km2")
                print("  warnings overlapping the day: "
                      + ("not loaded, no outer layer" if built["warnings_used"] is None else str(built["warnings_used"])))
            load.finish_run(conn, run_id, loaded=int(loaded), missing=int(missing), suspect=int(status == "suspect"))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            load.finish_run(conn, run_id, loaded=0, missing=0, error=f"{type(exc).__name__}: {exc}")
            conn.commit()
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
