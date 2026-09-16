#!/usr/bin/env python3

"""Generate the static coverage-zip GeoJSON used by the map.

Service Area is (fairly) stable, so this is a fixture more
than per-request data.  Allows browser to cache it.

Simplified Geometry is DISPLAY ONLY, always run against
zcta_boundaries.geom.
"""

import json
import sys

import psycopg
from psycopg.rows import dict_row

TOLERANCE = 0.0005 # In Degrees, ~55 m at this latitude

SQL = """
SELECT  c.zcta5,
        c.area_name,
        ST_AsGeoJSON(ST_SimplifyPreserveTopology(z.geom, %(tolerance)s)) as geom
    FROM coverage_zips c
    JOIN zcta_boundaries z ON z.zcta5 = c.zcta5
WHERE c.removed_at IS NULL
ORDER BY c.zcta5
"""

def main():
    with psycopg.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(SQL, {"tolerance": TOLERANCE})
        features = [
            {
                "type": "Feature",
                "properties": {"zcta5": r["zcta5"], "area_name": r["area_name"]},
                "geometry": json.loads(r["geom"]),
            }
            for r in cur.fetchall()
        ]
    out = {"type": "FeatureCollection", "features": features}
    path = "hailsys/web/static/coverage.geojson"
    with open(path, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))

    print(f"wrote {path}: {len(features)} zips")
    return 0

if __name__ == "__main__":
    sys.exit(main())