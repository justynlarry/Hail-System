#!/usr/bin/env bash

# Load reference data: report_types (CSV) and zcta_boundaries (TIGER shapefile).

# SAFE to re-run.  Both loads go through staging tables and INSERT ... ON
# CONFLICT DO NOTHING, second run won't duplicate records.



set -euo pipefail

DB="${PGDATABASE:-hail}"
# planning/report_types.csv, not reference/report_types.csv.  They are not
# duplicates: this one is the curated 6-column seed matching the table, the
# reference/ one is the 11-column statistical extract that build_reference_
# tables.py generates as evidence.  Loading the wrong one loads statistics.
CSV="${CSV:-planning/report_types.csv}"
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
# The stage table mirrors the CSV's columns exactly.  \copy maps by POSITION,
# and HEADER true only skips the header row rather than reading names from it --
# so a stage table of the wrong shape or column order loads silently wrong.
# Naming the columns in the INSERT is what makes the mapping explicit.
#
# roof_relevant is Y/N in the CSV.  Postgres accepts Y/N as boolean literals, and
# the stage column is NOT NULL so a blank field fails the row loudly rather than
# quietly taking the table's DEFAULT FALSE.  A blank here would mean "nobody
# decided yet", which must not silently become "not roof relevant".
#
# min_magnitude is expected to be present but empty for now -- the column exists
# so the CSV shape is settled before the thresholds are chosen.
#
# TRAP, and someone will lose an hour to this: the INSERT is
# ON CONFLICT DO NOTHING, so after the first successful load, editing
# roof_relevant or min_magnitude in the CSV and re-running changes NOTHING.
# That is deliberate -- it protects edits staff have made in the database from
# being reverted by a stale seed file.  The CSV is a seed, not a source of
# truth.  After first load, changes are an UPDATE:
#       UPDATE report_types SET roof_relevant = TRUE
#        WHERE (report_type, report_text) = ('H', 'HAIL');
# Note the composite key: report_type alone is not unique.

psql -v ON_ERROR_STOP=1 -d "$DB" << SQL
BEGIN;

CREATE TEMP TABLE report_types_stage (
    report_type     TEXT    NOT NULL,
    report_text     TEXT    NOT NULL,
    mag_unit        TEXT,
    unit_confidence TEXT    NOT NULL,
    roof_relevant   BOOLEAN NOT NULL,
    min_magnitude   NUMERIC(6,2)
);

\copy report_types_stage FROM '$CSV' WITH (FORMAT csv, HEADER true)

INSERT INTO report_types
       (report_type, report_text, mag_unit, unit_confidence,
        roof_relevant, min_magnitude)
SELECT report_type,
       report_text,
       nullif(mag_unit, ''),
       unit_confidence,
       roof_relevant,
       min_magnitude
FROM   report_types_stage
ON CONFLICT (report_type, report_text) DO NOTHING;

COMMIT;

SQL


log "loading ZCTAs from $SHP (reprojecting $SRID_IN -> $SRID_OUT)"

# -c (create), NOT -d (drop then create): -d emits a DropGeometryColumn for a
# stage table that does not exist on a first run, which raises and -- under
# ON_ERROR_STOP=1 -- kills the load before it starts.  Dropping it ourselves
# first is idempotent and does not error on a clean database.
psql -v ON_ERROR_STOP=1 -d "$DB" -qc "DROP TABLE IF EXISTS zcta_stage;"

shp2pgsql -s "${SRID_IN}:${SRID_OUT}" -g geom -c -D -W LATIN1 \
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



