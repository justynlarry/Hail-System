#!/usr/bin/env python3
"""Prove storms.PAIRS_SQL (report_zip_distances) matches the live spatial join.

storms.py used to find zips with a live ST_DWithin join against
zcta_boundaries; it now reads the precomputed report_zip_distances table
(sql/017).  This runs both over the same windows and demands an EXACT match:
same pairs, same distance_miles, same every other column.  Not approximate --
both sides call the same spheroidal ST_Distance on the same geometry and round
the same way, so any difference is a finding.  The only place a mismatch could
plausibly live is exactly at the radius boundary, so each window also reports
how many stored pairs sit within 1 m of the radius.

Run this again whenever the table is recomputed: raising hail_pair_ceiling_m(),
or a TIGER reload of zcta_boundaries (the table is derived from those polygons).

    docker compose run --rm -v "$PWD/scripts:/app/scripts:ro" app \\
        python scripts/verify_zip_distances.py [--only NAME] [--radius-miles N]

(The mount is because the app image bakes scripts/ in at build time.)

Read-only: runs as hail_app, in a READ ONLY transaction.  The old query is slow
on wide ranges (~140 s for two months of 2019) -- that slowness is the reason
the table exists, not a hang.  Exits 1 on any mismatch.
"""

import argparse
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta

from hailsys.db import get_connection
from hailsys.queries import storms
from hailsys.tuning import (
    DEFAULT_ZIP_RADIUS_MILES,
    DISPLAY_TZ,
    denver_day_bounds,
    miles_to_metres,
)

# The reference implementation: storms.py as of 93c7f85^, frozen here on
# purpose so the check does not depend on git history and cannot drift with
# storms.py.  Do not "keep in sync" with storms.py -- being different from it
# is the point.  It is the whole PAIRS_SQL, not just the join, so the columns
# compared are the ones the export ships.
OLD_PAIRS_SQL = """
SELECT
    c.zcta5,
    c.area_name,
    i.iem_id,
    (i.utc_datetime AT TIME ZONE 'America/Denver') AS local_time,
    i.utc_datetime,
    i.report_type,
    i.report_text,
    i.magnitude,
    t.mag_unit,
    round((ST_Distance(i.geom::geography, z.geom::geography) / 1609.344)::numeric, 2) AS distance_miles,
    i.report_source,
    s.confidence_tier,
    i.report_qualifier,
    i.county,
    i.nws_issuer,
    i.latitude,
    i.longitude,
    i.remark
FROM iem_data i
JOIN report_types t
    ON t.report_type = i.report_type
    AND t.report_text = i.report_text
LEFT JOIN report_sources s
    ON s.source = i.report_source_norm
JOIN zcta_boundaries z
    ON ST_DWithin(i.geom::geography, z.geom::geography, %(radius_m)s)
JOIN coverage_zips c
    ON c.zcta5 = z.zcta5
    AND c.removed_at IS NULL
WHERE i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
ORDER BY c.zcta5, i.utc_datetime, distance_miles
"""

# How many pairs the table holds within 1 m of the radius -- the only place the
# two implementations could plausibly disagree.  Informational: says whether
# the boundary was actually exercised in this window.
BOUNDARY_SQL = """
SELECT count(*) AS n
FROM iem_data i
JOIN report_zip_distances d ON d.iem_id = i.iem_id
JOIN coverage_zips c ON c.zcta5 = d.zcta5 AND c.removed_at IS NULL
WHERE i.utc_datetime >= %(window_start)s
    AND i.utc_datetime < %(window_end)s
    AND (%(report_text)s::text IS NULL OR i.report_text = %(report_text)s)
    AND d.distance_m BETWEEN %(radius_m)s - 1 AND %(radius_m)s + 1
"""

MAX_DIFFS_SHOWN = 20


def windows():
    """name -> (window_start, window_end, report_text).  Bounds are Denver
    local days converted to UTC, the same way the views build them."""
    today = datetime.now(DISPLAY_TZ).date()
    return {
        "2024-05-30-hail": (
            denver_day_bounds(date(2024, 5, 30))[0],
            denver_day_bounds(date(2024, 5, 30))[1],
            "HAIL",
        ),
        "last-60-days": (
            denver_day_bounds(today - timedelta(days=60))[0],
            denver_day_bounds(today)[1],
            None,
        ),
        # Half-open: 2019-01-01 through 2019-02-28.  The window the original
        # 141 s timing was taken on.
        "2019-jan-feb": (
            denver_day_bounds(date(2019, 1, 1))[0],
            denver_day_bounds(date(2019, 3, 1))[0],
            None,
        ),
    }


def timed_fetch(conn, sql, params):
    t = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows, time.perf_counter() - t


def row_key(row):
    # repr, not the values: repr keeps Decimal scale ('58.60' vs '58.6' differ,
    # which is what "exact" means here) and, unlike Decimal('NaN') == itself,
    # is comparable when a NUMERIC NaN magnitude is in the data.
    return tuple(repr(v) for v in row.values())


def compare(name, conn, window_start, window_end, report_text, radius_m):
    params = {
        "radius_m": radius_m,
        "window_start": window_start,
        "window_end": window_end,
        "report_text": report_text,
    }
    print(f"\n== {name}: {window_start.isoformat()} .. {window_end.isoformat()}"
          f"  type={report_text or 'all'}  radius={radius_m:.2f} m")

    old_rows, old_s = timed_fetch(conn, OLD_PAIRS_SQL, params)
    new_rows, new_s = timed_fetch(conn, storms.PAIRS_SQL, params)
    boundary = timed_fetch(conn, BOUNDARY_SQL, params)[0][0]["n"]

    print(f"   old (live ST_DWithin):      {len(old_rows):>8} rows  {old_s:8.2f} s")
    print(f"   new (report_zip_distances): {len(new_rows):>8} rows  {new_s:8.2f} s")
    print(f"   stored pairs within 1 m of the radius: {boundary}")

    old_cols = list(old_rows[0].keys()) if old_rows else None
    new_cols = list(new_rows[0].keys()) if new_rows else None
    if old_rows and new_rows and old_cols != new_cols:
        print(f"   FAIL  column lists differ:\n     old {old_cols}\n     new {new_cols}")
        return False

    # Multiset, not a positional zip: ORDER BY leaves ties (same zip, same
    # minute, same rounded distance) in arbitrary order on either side.
    old_keys = Counter(row_key(r) for r in old_rows)
    new_keys = Counter(row_key(r) for r in new_rows)
    only_old = old_keys - new_keys
    only_new = new_keys - old_keys

    if not only_old and not only_new:
        print(f"   PASS  {len(old_rows)} rows identical")
        return True

    cols = old_cols or new_cols
    print(f"   FAIL  {sum(only_old.values())} rows only in old, "
          f"{sum(only_new.values())} only in new")
    for label, diff in (("only in OLD", only_old), ("only in NEW", only_new)):
        for key in list(diff)[:MAX_DIFFS_SHOWN]:
            print(f"     {label}: " + ", ".join(f"{c}={v}" for c, v in zip(cols, key)))
        if len(diff) > MAX_DIFFS_SHOWN:
            print(f"     ... {len(diff) - MAX_DIFFS_SHOWN} more {label}")
    return False


def main(argv=None):
    all_windows = windows()
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--only", choices=sorted(all_windows),
                   help="run one window (default: all three)")
    p.add_argument("--radius-miles", type=float, default=DEFAULT_ZIP_RADIUS_MILES)
    args = p.parse_args(argv)

    radius_m = miles_to_metres(args.radius_miles)
    chosen = [args.only] if args.only else list(all_windows)

    ok = True
    with get_connection() as conn:
        conn.read_only = True
        for name in chosen:
            ok = compare(name, conn, *all_windows[name], radius_m) and ok

    print("\nRESULT:", "all windows identical" if ok else "MISMATCH -- see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
