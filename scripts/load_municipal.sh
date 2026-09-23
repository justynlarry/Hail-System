#!/usr/bin/env bash

# Load DOLA municipal boundaries into municipal_boundaries.
#
#     docker compose run --rm loader scripts/load_municipal.sh \
#         data/raw/dola/municipal_boundaries_2026-09-23.geojson
#
# The GeoJSON is the dissolved DOLA layer, fetched with outSR=4326 and paged
# with a count check.  Its metadata sits beside it as <same name>.layer.json;
# the source edit date is read from there.
#
# SEPARATE FROM load_reference.sh ON PURPOSE.  That script loads national
# TIGER shapefiles through shp2pgsql.  This is one state's layer from a
# different publisher, on its own refresh cadence, and arrives as GeoJSON --
# which shp2pgsql cannot read, and no GDAL is installed, so psql parses it
# with ST_GeomFromGeoJSON instead.
#
# SAFE to re-run.  DELETE + INSERT in one transaction replaces the whole set.
# This departs from the ZCTA/county loads (ON CONFLICT DO NOTHING) on
# purpose: a municipality annexes land, and DO NOTHING would keep the old
# boundary forever.  The table is derived entirely from DOLA, so replacing it
# is not deletion under "nothing is deleted".

set -euo pipefail

GEOJSON="${1:?usage: load_municipal.sh path/to/municipal_boundaries_<date>.geojson}"
META="${GEOJSON%.geojson}.layer.json"
SOURCE_URL="${SOURCE_URL:-https://services3.arcgis.com/DgjqnJA1rgO92Soi/arcgis/rest/services/DOLA_Municipalities_(Boundaries_Dissolved)/FeatureServer/0}"

DB="${PGDATABASE:-weather-property}"
export PGHOST="${PGHOST:-postgis}"
export PGUSER="${PGUSER:-hail_admin}"

log()  { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ------ Preconditions ------

command -v psql >/dev/null || fail "psql not found"
[[ -f "$GEOJSON" ]] || fail "GeoJSON not found: $GEOJSON"
[[ -f "$META"    ]] || fail "layer metadata not found: $META (expected beside the GeoJSON)"

# Connection before tables -- same reason as load_reference.sh: a pipeline
# into grep would report a wrong password as a missing table.
psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc 'SELECT 1' >/dev/null \
    || fail "cannot connect to database '$DB' as '$PGUSER' at '$PGHOST' -- check PGPASSWORD in .env"

found=$(psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc \
    "SELECT 1 FROM information_schema.tables WHERE table_name = 'municipal_boundaries'") \
    || fail "query failed while checking for municipal_boundaries"
[[ "$found" == 1 ]] || fail "table municipal_boundaries missing -- run sql/021 first"

# ------ Stage the files ------
# \copy reads a file line by line, so the whole FeatureCollection has to be one
# line to arrive as one jsonb value.  Stripping raw newlines is safe for JSON:
# a newline inside a string is always escaped as \n, so a literal one can only
# be whitespace between tokens.  The \x01/\x02 quote and delimiter bytes below
# cannot appear in valid JSON unescaped, so CSV mode passes the text through
# untouched -- and CSV mode, unlike text mode, does not eat backslashes.
#
# Fixed paths, not mktemp: \copy does not interpolate psql variables.
stage_geo=/tmp/hail_municipal.geojson
stage_meta=/tmp/hail_municipal.layer.json
trap 'rm -f "$stage_geo" "$stage_meta"' EXIT

tr -d '\r\n' < "$GEOJSON" > "$stage_geo"
tr -d '\r\n' < "$META"    > "$stage_meta"

log "database=$DB  geojson=$GEOJSON"

# ------ Load ------

psql -v ON_ERROR_STOP=1 -d "$DB" -v source_url="$SOURCE_URL" <<'SQL'
BEGIN;

CREATE TEMP TABLE geo_stage  (doc JSONB);
CREATE TEMP TABLE meta_stage (doc JSONB);

\copy geo_stage  FROM '/tmp/hail_municipal.geojson'   WITH (FORMAT csv, QUOTE E'\x01', DELIMITER E'\x02')
\copy meta_stage FROM '/tmp/hail_municipal.layer.json' WITH (FORMAT csv, QUOTE E'\x01', DELIMITER E'\x02')

-- DOLA writes a missing value as the literal STRING 'null', not JSON null.
-- Same shape as IEM's 'None': left alone it reads as a real value.  Converted
-- per attribute here, so source_attrs keeps every key as received.
CREATE TEMP TABLE feature_stage AS
SELECT ordinality AS n,
       (SELECT jsonb_object_agg(k, CASE WHEN v = '"null"'::jsonb
                                        THEN 'null'::jsonb ELSE v END)
          FROM jsonb_each(f->'properties') AS p(k, v))         AS attrs,
       ST_SetSRID(ST_GeomFromGeoJSON(f->'geometry'), 4326)    AS geom
FROM   geo_stage,
       jsonb_array_elements(doc->'features') WITH ORDINALITY AS e(f, ordinality);

\echo ''
SELECT count(*)                                  AS features_in_file,
       count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalid_before_fix
FROM   feature_stage;

-- ST_MakeValid can return a GeometryCollection (a polygon plus a stray line
-- where a ring touched itself).  The column is MultiPolygon, so extract the
-- polygon parts (type 3) before ST_Multi; without the extract, the INSERT
-- fails on the first such row.
DELETE FROM municipal_boundaries;

INSERT INTO municipal_boundaries
       (place_fips, name, geom, source_attrs, source_url, source_edited_at)
SELECT attrs->>'city',
       attrs->>'first_city',
       ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3)),
       attrs,
       :'source_url',
       to_timestamp((m.doc->'editingInfo'->>'dataLastEditDate')::bigint / 1000.0)
FROM   feature_stage, meta_stage m;

-- Fail loudly rather than commit a partial or degraded set.
DO $$
DECLARE
    n_file   bigint := (SELECT count(*) FROM feature_stage);
    n_table  bigint := (SELECT count(*) FROM municipal_boundaries);
    n_bad    bigint := (SELECT count(*) FROM municipal_boundaries
                        WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom));
BEGIN
    IF n_table <> n_file THEN
        RAISE EXCEPTION 'loaded % rows from % features', n_table, n_file;
    END IF;
    IF n_bad > 0 THEN
        RAISE EXCEPTION '% geometries invalid or empty after ST_MakeValid', n_bad;
    END IF;
END $$;

COMMIT;
SQL

# ------ Verify ------

psql -v ON_ERROR_STOP=1 -d "$DB" <<'SQL'
\echo ''
SELECT count(*) AS municipalities,
       count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalid,
       min(source_edited_at) AS source_edited_at,
       max(loaded_at)        AS loaded_at
FROM   municipal_boundaries;

SELECT f_table_name, f_geometry_column, type, srid
FROM   geometry_columns
WHERE  f_table_name = 'municipal_boundaries';
SQL

log "done"
