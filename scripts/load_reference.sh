#!/usr/bin/env bash

# Load reference data: report_types (CSV), report_sources (CSV) and
# zcta_boundaries (TIGER shapefile).

# SAFE to re-run.  All loads go through staging tables and INSERT ... ON
# CONFLICT DO NOTHING, second run won't duplicate records.



set -euo pipefail

# Defaults match docker-compose.yml: the service is named postgis (which is its
# hostname on hailnet), the database is weather-property, the superuser is
# hail_admin.  This script runs DDL -- staging tables, DROP TABLE -- so it needs
# the superuser, not hail_app.  PGPASSWORD is set by the loader service's own
# environment: block in docker-compose.yml, which is the only place it appears.
DB="${PGDATABASE:-weather-property}"
# planning/report_types.csv, not reference/report_types.csv.  They are not
# duplicates: this one is the curated 6-column seed matching the table, the
# reference/ one is the 11-column statistical extract that build_reference_
# tables.py generates as evidence.  Loading the wrong one loads statistics.
CSV="${CSV:-planning/report_types.csv}"
# planning/report_sources.csv, same category as report_types.csv: a curated
# seed carrying business judgment, not a statistical extract.  47 rows, one per
# distinct report_source_norm present in iem_data as of 2026-09-10 -- built
# from what the ingest can actually produce, not from a raw archive scan, so
# every value has a lookup row and none is seeded that nothing joins to.
SOURCES_CSV="${SOURCES_CSV:-planning/report_sources.csv}"
# TIGER unzips into a directory named after the archive; the .shp is one level
# down, not beside it.
SHP="${SHP:-data/raw/tiger/tl_2025_us_zcta520/tl_2025_us_zcta520.shp}"
export PGHOST="${PGHOST:-postgis}"
export PGUSER="${PGUSER:-hail_admin}"


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
[[ -f "$SOURCES_CSV" ]] || fail "CSV not found: $SOURCES_CSV"
[[ -f "$SHP" ]] || fail "Shapefile not found: $SHP"


for ext in dbf shx prj; do
    [[ -f "${SHP%.shp}.${ext}" ]] || fail "missing ${SHP%.shp}.${ext}"
done

# Check the CONNECTION before checking for tables.  Piping psql into grep throws
# psql's exit status away -- the pipeline's status is grep's -- so a wrong
# password or an unreachable host produced no output, grep found nothing, and
# the script blamed a missing table.  That sends you to sql/001..003 to debug a
# problem that is actually in .env.
psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc 'SELECT 1' >/dev/null \
    || fail "cannot connect to database '$DB' as '$PGUSER' at '$PGHOST' -- check PGPASSWORD in .env"

for t in report_types report_sources zcta_boundaries users; do
    found=$(psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc \
        "SELECT 1 FROM information_schema.tables WHERE table_name = '$t'") \
        || fail "query failed while checking for table $t"
    [[ "$found" == 1 ]] || fail "table $t missing -- run sql/001..003 first"
done

# The system account must exist.  Resolved BY QUERY, never hardcoded to 1: the
# whole reason that account exists (decision log, 2026-09-03) is that
# machine-written rows need a real author, and an integer literal here would
# quietly attribute them to whoever happens to be emp_id 1.  Matches how
# load_coverage.sh resolves the same account -- user_name, not role, because
# role holds the cost stages (admin / sender / viewer) plus system.
system_id=$(psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc \
    "SELECT emp_id FROM users WHERE user_name = 'system'") \
    || fail "query failed while resolving the system account"
[[ -n "$system_id" ]] \
    || fail "no user_name='system' in users -- run sql/002_users.sql first"

log "database=$DB"
log "system account emp_id=$system_id"

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


log "loading report_sources from $SOURCES_CSV"

# Unquoted heredoc again -- $SOURCES_CSV has to expand.  Same asymmetry as
# above; do not "fix" it.
#
# added_by is NOT NULL with an FK to users.  Resolved in the shell above and
# passed in as :system_id, matching load_coverage.sh.  Unquoted :system_id, not
# :'system_id' -- emp_id is BIGINT, and the quoted form would hand Postgres a
# string literal.
#
# notes gets nullif'd as belt-and-braces: \copy in FORMAT csv already reads an
# unquoted empty field as NULL, but a quoted empty ("") would come through as
# '', which reads as "has a note, and it is blank".
#
# is_automated is deliberately NULL for five sources (USGS, UNKNOWN,
# UNOFFICIAL STATI, AVIATION SITE, DEPT OF).  The column is nullable and "we
# do not know" is the honest value; an empty CSV field becomes NULL for a
# boolean column.
#
# Same ON CONFLICT DO NOTHING trap as report_types: after the first successful
# load, editing a confidence_tier in the CSV and re-running changes NOTHING.
# That protects tier judgments made in the database from a stale seed file.
# After first load, changes are an UPDATE:
#       UPDATE report_sources SET confidence_tier = 'moderate'
#        WHERE source = 'STORM CHASER';
#
# NOTE ON THE TIERS THEMSELVES: high covers 84.7% of the archive by volume,
# because COCORAHS and TRAINED SPOTTER alone are 53% of it.  The tier is a
# property of the REPORTER, and its meaning depends on the event type -- an
# automated station measures wind and precipitation well and does not size
# hail at all.  Do not collapse this to a single confidence word in a UI;
# show the source names and let a human read them.
#
# There is NO foreign key from iem_data.report_source_norm to this table, and
# there must not be: SOURCE is free text typed at NWS offices, and a new value
# would break the nightly ingest.  This is a lookup, joined on
# report_source_norm, never a constraint.  The cost is that an unseeded value
# returns NULL through a LEFT JOIN and reads as "unrated" with no error -- so
# after loading, confirm nothing is unmatched:
#       SELECT count(*) FROM iem_data i
#       LEFT JOIN report_sources s ON s.source = i.report_source_norm
#       WHERE s.source IS NULL;

psql -v ON_ERROR_STOP=1 -v system_id="$system_id" -d "$DB" << SQL
BEGIN;

CREATE TEMP TABLE report_sources_stage (
    source          TEXT    NOT NULL,
    display_name    TEXT    NOT NULL,
    confidence_tier TEXT    NOT NULL,
    is_automated    BOOLEAN,
    notes           TEXT
);

\copy report_sources_stage FROM '$SOURCES_CSV' WITH (FORMAT csv, HEADER true)

INSERT INTO report_sources
       (source, display_name, confidence_tier, is_automated, notes, added_by)
SELECT source,
       display_name,
       confidence_tier,
       is_automated,
       nullif(notes, ''),
       :system_id
FROM   report_sources_stage
ON CONFLICT (source) DO NOTHING;

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

-- Expect 47 | 18 | 19 | 8 | 2 | 5
SELECT count(*)                                          AS report_sources,
       count(*) FILTER (WHERE confidence_tier = 'high')     AS high,
       count(*) FILTER (WHERE confidence_tier = 'moderate') AS moderate,
       count(*) FILTER (WHERE confidence_tier = 'low')      AS low,
       count(*) FILTER (WHERE confidence_tier = 'unrated')  AS unrated,
       count(*) FILTER (WHERE is_automated IS NULL)         AS unknown_automation
FROM   report_sources;

-- Must be 0.  A non-zero count is a source in iem_data with no lookup row,
-- which shows in a UI as "unrated" and raises no error anywhere.
SELECT count(*) AS unmatched_sources
FROM   iem_data i
LEFT JOIN report_sources s ON s.source = i.report_source_norm
WHERE  s.source IS NULL;

SELECT count(*) AS zctas,
       count(*) FILTER (WHERE zcta5 LIKE '80%' OR zcta5 LIKE '81%') AS colorado
FROM   zcta_boundaries;

-- Every Geometry must be 4326

SELECT f_table_name, f_geometry_column, type, srid
FROM   geometry_columns
WHERE  f_table_name= 'zcta_boundaries';
SQL

log "done"



