"""Item 254:  how often do the radar layers call a patch "core" only because of a non-hail report?

Read only.  One connection, never committed, temporary tables only, rolled back at the end.  For every loaded
radar day, the day's patches (cells at or above the threshold of the radar set in use, grouped as the radar build
groups them) are tested against two sets of anchors within the report radius:

    current    any roof-relevant report at or above its type's minimum (hailsys/radar/zones.py, _REPORTS_SQL)
    hail only  actionable IEM HAIL reports not sourced from CoCoRaHS, plus CoCoRaHS hail at or above the set's
               floor (hailsys/likelihood/build.py, _IEM_SQL and _COCORAHS_SQL)

A patch that is core under the current rule and not under hail-only is "wind-only core";  one that is core only
under hail-only is "CoCoRaHS-only core".  Area is the patch's cells (0.01 degree squares), before the margin.

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \\
        -v ./docs/analysis/radar-anchors-2026-10:/out web python /out/measure.py /out/by_day.csv
"""

import csv
import sys
import time
from collections import Counter

import psycopg
from psycopg.rows import dict_row

from hailsys.radar.fetch import day_window
from hailsys.radar.mesh import MM_PER_INCH, STEP
from hailsys.radar.zones import TOUCH_DEGREES
from hailsys.tuning import DISPLAY_TZ

SCRATCH = """
CREATE TEMP TABLE m_all (geom geometry(Point, 4326), text TEXT);
CREATE TEMP TABLE m_hail (geom geometry(Point, 4326));
CREATE TEMP TABLE m_cells (patch INTEGER, geom geometry(Point, 4326));
CREATE TEMP TABLE m_patch (patch INTEGER, cur BOOLEAN, hail BOOLEAN, km2 DOUBLE PRECISION)
"""

ALL_SQL = """
INSERT INTO m_all (geom, text)
SELECT i.geom, i.report_text
FROM iem_data i
JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
WHERE i.utc_datetime >= %(start)s AND i.utc_datetime < %(end)s
    AND t.roof_relevant
    AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
"""

HAIL_SQL = """
INSERT INTO m_hail (geom)
SELECT i.geom
FROM iem_data i
JOIN report_types t ON t.report_type = i.report_type AND t.report_text = i.report_text
WHERE i.utc_datetime >= %(start)s AND i.utc_datetime < %(end)s
    AND i.report_text = 'HAIL'
    AND t.roof_relevant
    AND (t.min_magnitude IS NULL OR i.magnitude >= t.min_magnitude)
    AND i.report_source_norm IS DISTINCT FROM 'COCORAHS'
UNION ALL
SELECT c.geom
FROM cocorahs_hail_reports c
WHERE c.observed_at >= %(start)s AND c.observed_at < %(end)s
    AND coalesce(c.largest_in, c.average_in) >= %(cocorahs_min)s
"""

CELLS_SQL = """
INSERT INTO m_cells (patch, geom)
SELECT ST_ClusterDBSCAN(geom, eps := %(touch)s, minpoints := 1) OVER (), geom
FROM radar_cells WHERE day = %(day)s AND mm >= %(threshold_mm)s::real
"""

PATCH_SQL = """
INSERT INTO m_patch (patch, cur, hail, km2)
SELECT c.patch,
    bool_or(EXISTS (SELECT 1 FROM m_all r WHERE ST_DWithin(c.geom::geography, r.geom::geography, %(radius_m)s))),
    bool_or(EXISTS (SELECT 1 FROM m_hail r WHERE ST_DWithin(c.geom::geography, r.geom::geography, %(radius_m)s))),
    sum(ST_Area(ST_MakeEnvelope(ST_X(c.geom) - %(half)s, ST_Y(c.geom) - %(half)s,
                                ST_X(c.geom) + %(half)s, ST_Y(c.geom) + %(half)s, 4326)::geography)) / 1e6
FROM m_cells c
GROUP BY c.patch
"""

SUMMARY_SQL = """
SELECT count(*) AS patches,
    count(*) FILTER (WHERE cur) AS core_now,
    count(*) FILTER (WHERE hail) AS core_hail,
    count(*) FILTER (WHERE cur AND NOT hail) AS wind_only,
    count(*) FILTER (WHERE hail AND NOT cur) AS cocorahs_only,
    coalesce(sum(km2) FILTER (WHERE cur), 0) AS km2_now,
    coalesce(sum(km2) FILTER (WHERE hail), 0) AS km2_hail,
    coalesce(sum(km2) FILTER (WHERE cur AND NOT hail), 0) AS km2_wind_only,
    coalesce(sum(km2) FILTER (WHERE hail AND NOT cur), 0) AS km2_cocorahs_only
FROM m_patch
"""

# The report texts within the radius of a wind-only core patch.
TEXTS_SQL = """
SELECT r.text, count(*) AS n
FROM m_all r
WHERE r.text <> 'HAIL' AND EXISTS (
    SELECT 1 FROM m_cells c JOIN m_patch p ON p.patch = c.patch
    WHERE p.cur AND NOT p.hail AND ST_DWithin(c.geom::geography, r.geom::geography, %(radius_m)s))
GROUP BY r.text
"""

FIELDS = ["day", "patches", "core_now", "core_hail", "wind_only", "cocorahs_only",
          "km2_now", "km2_hail", "km2_wind_only", "km2_cocorahs_only"]


def main(out_path):
    conn = psycopg.connect(row_factory=dict_row)
    texts = Counter()
    try:
        radar = conn.execute("SELECT * FROM radar_parameter_sets ORDER BY set_id DESC LIMIT 1").fetchone()
        lk = conn.execute("SELECT * FROM likelihood_parameter_sets ORDER BY set_id DESC LIMIT 1").fetchone()
        days = [r["day"] for r in conn.execute(
            "SELECT day FROM radar_days WHERE status = 'loaded' ORDER BY day")]
        print(f"radar set {radar['set_id']}, likelihood set {lk['set_id']}, {len(days)} loaded days", flush=True)
        conn.execute(SCRATCH)
        started = time.time()
        with open(out_path, "w", newline="") as fh:
            out = csv.DictWriter(fh, FIELDS)
            out.writeheader()
            for n, day in enumerate(days, 1):
                start, end = day_window(day, DISPLAY_TZ)
                args = {"day": day, "start": start, "end": end, "touch": TOUCH_DEGREES, "half": STEP / 2,
                        "threshold_mm": float(radar["hail_threshold_in"]) * MM_PER_INCH,
                        "radius_m": float(radar["report_radius_km"]) * 1000.0,
                        "cocorahs_min": lk["cocorahs_min_in"]}
                conn.execute("TRUNCATE m_all, m_hail, m_cells, m_patch")
                conn.execute(ALL_SQL, args)
                conn.execute(HAIL_SQL, args)
                conn.execute(CELLS_SQL, args)
                conn.execute(PATCH_SQL, args)
                row = conn.execute(SUMMARY_SQL).fetchone()
                if row["wind_only"]:
                    for t in conn.execute(TEXTS_SQL, args):
                        texts[t["text"]] += t["n"]
                out.writerow({"day": day, **{k: (round(v, 2) if isinstance(v, float) else v)
                                             for k, v in row.items()}})
                if n % 100 == 0:
                    print(f"{n}/{len(days)} days, {time.time() - started:.0f} s", flush=True)
    finally:
        conn.rollback()             # nothing here is kept;  only temporary tables were written
        conn.close()
    print("report texts near wind-only core patches:", dict(texts.most_common()), flush=True)
    print("done, rolled back", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
