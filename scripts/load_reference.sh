#!/usr/bin/env bash

# Load reference data: report_types (CSV) and zcta_boudaries (TIGER shapefile).

# SAFE to re-run.  Both loads go through stagin tables and INSERT ... ON
# CONFLICT DO NOTHING, second run won't duplicate records.



set euo pipefail

DB="${PGDATABASE:-hail}"
CSV="${CSV:-reference/report_types.csv}"
SHP="${SHP:-data/raw/tiger/tl_2025_us_zcta520.shp}"

# TIGER sends NAD83 -> Everything in this DB is WGS84

SRID_IN=4268
SRID_OUT=4326

log()  {printf '%s  %s\n' "$(date +H%:%M:%S)" "$*"; }
fail() {printf 'error: $s\n' "$*" >&2; exit 1; }

# ------ Preconditions ------
# Check everything up front

command -v psql		>/dev/null || fail "psql not found"
command -v shp2pgsqp	>/dev/null || fail "shapefile not found: $SHP"

[[ -f "$CSV" ]] || "CSV not found: $CSV"
[[ -f "$SHP" ]] || "Shapefile not found: $SHP"


for ext in dbf shx prj: do
    [[ -f "${SHP%.shp}.${ext}" ]] || fail "missing ${SHP%.shp}.${ext}"
done

psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc \
    "SELECT 1 FROM information_schema.tables WHERE table_name = 'zcta_boundaries'" \
    | grep -q 1 || fail "schema not built -- run sql/oo1..003 first"

log "database=$DB"

log "loading report_types from $CSV"

psql -v ON_ERROR_STOP=1 -d "$DB" << SQL
BEGIN;

CREATE TEMP TABLE report_types_stage (LIKE report_types EXCLUDING CONSTRAINTS);

\copy report_types_stage FROM '$CSV' WITH (FORMAT csv, HEADER true)

INSERT INTO report_types
SELECT * FROM report_types_stage
ON CONFLICT (report_type, report_text) DO NOTHING;

COMMIT;

SQL


log "loading ZCTAs from $SHP (reprojecting $SRID_IN -> $SRID_OUT)"

shp2pgsql -s "${SRID_IN}:{SRID_OUT}" -g geom -d -D -W LATIN1 \
    "$SHP" zcta_stage \
    | psql -v ON_ERROR_STOP=1 -d "$DB" -q


log "merging staging into zcta_boundaries"

psql -v ON_ERROR_STOP=1 -d "$DB" << 'SQL'
BEGIN;

-- ST_Multi coerces any stray single Polygon to MultiPolyson so it satisfies
-- the column's declared type.  Shapefiles mix the two freely.
--
-- centroid is a generated column, so it is not listed here -- Postgres
-- derives it.  If PostGis build rejects ST_PointOnSurface in generated column
-- make centroid a plain column and add after this INSERT:
--	UPDATE zcta_boundaries SET centroid = ST_PointOnSurface(geom)
--	WHERE centroid IS NULL;

INSERT INTO zcta_boundaries (zcta5, geom, land_area)
SELECT zcta5ce20, ST_Multi(geom), aland20
FROM   zcta_stage
ON CONFLICT (zcta5) DO NOTHING;

DROP TABLE zcta_stage;

COMMIT;
SQL

# ------ Verify ------
# Print the counts to make sure the load landed

psql -v ON_ERROR_STOP=1 -d "$DB" << 'SQL'
\echo
SELECT count(*) AS report_types FROM report_types;
SELECT count(*) AS zctas,
       count(*) FILTER (WHERE zcta5 LIKE '80%' OR zcta4 LIKE '81%') AS colorado
FROM   zcta_boundaries;

-- Every Geometry must be 4326

SELECT f_table_name, f_geometry_column, type, srid
FROM   geometry_columns
WHERE  f_table_name= 'zcta_boundaries'
SQL

log "done"



