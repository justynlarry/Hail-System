"""Jurisdiction for one point:  the city it is in, its county, and how near a line it sits.

Read only.  Jurisdiction is point-in-polygon on municipal_boundaries, not a mailing city.
Douglas County's permits say PARKER for addresses that are not in Parker.  A point in no city
is unincorporated, and its county answers.

Computed on read, from the point, each time.  jurisdiction_checks copies the answer when a check
is recorded, because load_municipal.sh replaces municipal_boundaries wholesale.
"""


# How far the nearest-line search reaches.  A safety limit, not a tuning value:  the
# near-boundary setting is capped at 1000 m (sql/069), so any line close enough to set the
# flag is always inside the reach.  A line farther than this is reported as none.
LINE_REACH_M = 2000

# The index prefilter around the point, in degrees.  0.05 degrees is at least 2.7 km east-west
# up to 60 degrees north (5.5 km north-south), so it always holds LINE_REACH_M:  a polygon whose
# bounding box misses it is wholly farther away than the reach.
SEARCH_BOX_DEGREES = 0.05

# ST_MakePoint takes longitude first.
_POINT = "ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)"

# No rows means the boundaries were never loaded on this machine, which is not the same as every
# point being unincorporated.
_LOADED_SQL = "SELECT max(loaded_at) AS loaded_at FROM municipal_boundaries"

_PLACES_SQL = f"""
SELECT place_fips, name
    FROM municipal_boundaries
  WHERE ST_Intersects(geom, {_POINT})
  ORDER BY name, place_fips
"""

_COUNTY_SQL = f"""
SELECT county_fips, name
    FROM county_boundaries
  WHERE ST_Intersects(geom, {_POINT})
  ORDER BY county_fips
  LIMIT 1
  """


# ST_Boundary of a polygon with holes includes the holes' rings, so an unincorporated enclave
# inside a city counts as a line.  Rounded to 0.1 m here, the precision jurisdiction_checks
# stores, so the flag made from it is the one that table's CHECK re-derives from the stored
# number:  compared unrounded, 150.04 m would be "not near" here and "near" in the table.
# On a tie, the line of a polygon the point is NOT in comes first:  inside Arapahoe, beside the
# Douglas line, the answer is "the Douglas County line".
_NEAREST_LINE_SQL = f"""
WITH pt AS (SELECT {_POINT} AS g),
lines AS (
    SELECT 'municipal' AS kind, m.name, m.geom
        FROM municipal_boundaries m, pt
      WHERE m.geom && ST_Expand(pt.g, %(box)s)
    UNION ALL
    SELECT 'county' AS kind, c.name, c.geom
        FROM county_boundaries c, pt
      WHERE c.geom && ST_Expand(pt.g, %(box)s)
)
SELECT  lines.kind, lines.name,
        round(ST_Distance(ST_Boundary(lines.geom)::geography, pt.g::geography)::numeric, 1)
            AS distance_m
    FROM lines, pt
  ORDER BY distance_m, ST_Intersects(lines.geom, pt.g), lines.kind, lines.name
  LIMIT 1
"""


def lookup(conn, lat, lon, near_boundary_m):
    """The jurisdiction answer for one point, as a dict:

        loaded                  False when municipal_boundaries is empty; nothing else is answered
        boundaries_loaded_at    when the municipal boundaries were loaded
        places                  [{place_fips, name}], by name: [] is unincorporated, two or more
                                is overlapping city polygons
        county                  {county_fips, name}, or None outside every county
        nearest_line            {kind ('municipal' or 'county'), name, distance_m (Decimal, 0.1 m)},
                                or None when no line is within LINE_REACH_M
        near_boundary           in two cities, or a line within near_boundary_m
        near_boundary_m         the setting the flag was made with
    """
    params = {"lat": lat, "lon": lon, "box": SEARCH_BOX_DEGREES}
    with conn.cursor() as cur:
        cur.execute(_LOADED_SQL)
        loaded_at = cur.fetchone()["loaded_at"]
        if loaded_at is None:
            return {"loaded": False, "boundaries_loaded_at": None, "places": [],
                    "county": None, "nearest_line": None, "near_boundary": False,
                    "near_boundary_m": near_boundary_m}
        cur.execute(_PLACES_SQL, params)
        places = [dict(r) for r in cur.fetchall()]
        cur.execute(_COUNTY_SQL, params)
        county = cur.fetchone()
        cur.execute(_NEAREST_LINE_SQL, params)
        line = cur.fetchone()

    if line is not None and line["distance_m"] > LINE_REACH_M:
        line = None

    near = len(places) > 1 or (line is not None and line["distance_m"] <= near_boundary_m)
    return {
        "loaded": True,
        "boundaries_loaded_at": loaded_at,
        "places": places,
        "county": dict(county) if county is not None else None,
        "nearest_line": dict(line) if line is not None else None,
        "near_boundary": near,
        "near_boundary_m": near_boundary_m,
    }
