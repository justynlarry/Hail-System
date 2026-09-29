"""Generate the static Colorado-County GeoJSON used by the map

County boundaries don't change often, so this is a fixture not
per-request data.
"""

import json
import sys

import psycopg
from psycopg.rows import dict_row

TOLERANCE = 0.0005      #Degrees

SQL = """
SELECT county_fips, name,
    ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, %(tolerance)s)) AS geom
FROM county_boundaries
WHERE state_fips = '08'
ORDER BY county_fips
"""

def main():
    with psycopg.connect(row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(SQL, {"tolerance": TOLERANCE})
        features = [
            {
                "type": "Feature",
                "properties": {"county_fips": r["county_fips"], "name": r["name"]},
                "geometry": json.loads(r["geom"]),
            }
            for r in cur.fetchall()
        ]
    out = {"type": "FeatureCollection", "features": features}
    path = "hailsys/web/static/colorado_counties.geojson"
    with open(path, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))

    print(f"wrote {path}: {len(features)} counties")
    return 0

if __name__ == "__main__":
    sys.exit(main())
