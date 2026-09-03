#!/usr/bin/env bash

# Load reference data: report_types (CSV) and zcta_boundaries (TIGER shapefile).

# SAFE to re-run.  Both loads go through staging tables and INSERT ... ON
# CONFLICT DO NOTHING, second run won't duplicate records.



set -euo pipefail

DB="${PGDATABASE:-hail}"
CSV="${CSV:-reference/report_types.csv}"
SHP="${SHP:-data/raw/tiger/tl_2025_us_zcta520.shp}"
export PGHOST="${PGHOST:-db}"
export PGUSER="${PGUSER:-hail}"


# TIGER sends NAD83 -> Everything in this DB is WGS84

SRID_IN=4269
SRID_OUT=4326

log()  { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ------ Preconditions ------
# Check everything up front

command -v psql		>/dev/null || fail "psql not found"
command -v shp2pgsql	>/dev/null || fail "shp2pgsql not found (postgis-client)"

[[ -f "$CSV" ]] || fail "CSV not found: $CSV"
[[ -f "$SHP" ]] || fail "Shapefile not found: $SHP"


for ext in dbf shx prj; do
    [[ -f "${SHP%.shp}.${ext}" ]] || fail "missing ${SHP%.shp}.${ext}"
done

for t in report_types zcta_boundaries; do
    psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc \
        "SELECT 1 FROM information_schema.tables WHERE table_name = '$t'" \
        | grep -q 1 || fail "table $t missing -- run sql/001..003 first"
done

log "database=$DB"

log "loading report_types from $CSV"

# NOTE: this heredoc is << SQL (unquoted) on purpose -- $CSV has to expand.
# The others below are << 'SQL' so that nothing in them expands.  The asymmetry
# is deliberate; do not "fix" it.
#
# The stage table mirrors the CSV's 11 columns, not report_types' 5.  \copy maps
# columns by POSITION, and HEADER true only skips the header row rather than
# reading names from it -- so a stage table of the wrong shape or column order
# loads silently wrong.  Naming the five columns in the INSERT is what makes the
# mapping explicit.
#
# roof_relevant is not in the CSV.  It takes its DEFAULT FALSE here and is set
# afterward by a documented UPDATE -- the same mechanism as adding a new
# roof-relevant type later.

psql -v ON_ERROR_STOP=1 -d "$DB" << SQL
BEGIN;

CREATE TEMP TABLE report_types_stage (
    typecode          TEXT,
    typetext          TEXT,
    report_count      INTEGER,
    first_seen        TIMESTAMPTZ,
    last_seen         TIMESTAMPTZ,
    mag_present_count INTEGER,
    mag_null_count    INTEGER,
    mag_min           NUMERIC,
    mag_max           NUMERIC,
    mag_unit          TEXT,
    unit_confidence   TEXT
);

\copy report_types_stage FROM '$CSV' WITH (FORMAT csv, HEADER true)

INSERT INTO report_types (report_type, report_text, mag_unit, unit_confidence)
SELECT typecode,
       typetext,
       nullif(mag_unit, ''),
       unit_confidence
FROM   report_types_stage
ON CONFLICT (report_type, report_text) DO NOTHING;

COMMIT;

SQL


log "loading ZCTAs from $SHP (reprojecting $SRID_IN -> $SRID_OUT)"

shp2pgsql -s "${SRID_IN}:${SRID_OUT}" -g geom -d -D -W LATIN1 \
    "$SHP" zcta_stage \
    | psql -v ON_ERROR_STOP=1 -d "$DB" -q


log "merging staging into zcta_boundaries"

psql -v ON_ERROR_STOP=1 -d "$DB" << 'SQL'
BEGIN;

-- ST_Multi coerces any stray single Polygon to MultiPolygon so it satisfies
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
       count(*) FILTER (WHERE zcta5 LIKE '80%' OR zcta5 LIKE '81%') AS colorado
FROM   zcta_boundaries;

-- Every Geometry must be 4326

SELECT f_table_name, f_geometry_column, type, srid
FROM   geometry_columns
WHERE  f_table_name= 'zcta_boundaries';
SQL

log "done"



