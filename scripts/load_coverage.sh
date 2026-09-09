#!/usr/bin/env bash

# Load one customer's service territory into coverage_zips.
#
# SEPARATE FROM load_reference.sh ON PURPOSE.  That script loads national static
# data identical for every installation -- 37 NWS report types, 33,791 ZCTAs.
# This one loads a list that belongs to one customer, changes on business
# grounds, and is edited by people.  Different lifecycle, different rerun
# cadence, different owner; sharing a script would tie a territory edit to a
# reload of 33,791 polygons.
#
# The zip list is an ARGUMENT, not a hardcoded path.  A roofing company in
# Dallas runs this against their own file with no code change:
#     load_coverage.sh /repo/config/their_zips.txt
#
# SAFE to re-run.  Inserts what is missing, touches nothing that already exists.

set -euo pipefail

# ------ Configuration ------

# RBI's list is the DEFAULT, not the definition.  Per-customer input lives in
# config/; see config/README.md for the generic/specific split.
ZIPS="${1:-config/coverage_zips.txt}"

# Generic national reference, tracked in planning/ alongside report_types.csv.
# NOT reference/, which is gitignored wholesale.
CITIES="${CITIES:-planning/zip_city_names.csv}"

DB="${PGDATABASE:-weather-property}"
export PGHOST="${PGHOST:-postgis}"
export PGUSER="${PGUSER:-hail_admin}"

log()  { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ------ Preconditions ------
# Everything up front, so a failure names the thing that is wrong rather than
# whatever the script happened to reach first.

command -v psql >/dev/null || fail "psql not found"

[[ -f "$ZIPS"   ]] || fail "zip list not found: $ZIPS"
[[ -f "$CITIES" ]] || fail "zip/city reference not found: $CITIES"

# Check the CONNECTION before checking for tables.  Piping psql into grep throws
# psql's exit status away -- a pipeline's status is its LAST command's -- so a
# wrong password reads as a missing table and sends you to debug the wrong file.
# load_reference.sh carries the same comment for the same reason.
psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc 'SELECT 1' >/dev/null \
    || fail "cannot connect to database '$DB' as '$PGUSER' at '$PGHOST' -- check PGPASSWORD in .env"

for t in coverage_zips zcta_boundaries users; do
    found=$(psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc \
        "SELECT 1 FROM information_schema.tables WHERE table_name = '$t'") \
        || fail "query failed while checking for table $t"
    [[ "$found" == 1 ]] || fail "table $t missing -- run sql/001..010 first"
done

# zcta_boundaries must be LOADED, not merely present.  Every insert here depends
# on the FK, so an empty boundary table reports every zip as unmatched, inserts
# nothing, and exits 0 -- technically correct and completely misleading.  This is
# the same silent-empty-join shape as the SRID trap.
zcta_count=$(psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc 'SELECT count(*) FROM zcta_boundaries') \
    || fail "could not count zcta_boundaries"
[[ "$zcta_count" -gt 0 ]] \
    || fail "zcta_boundaries is empty -- run load_reference.sh first, or every zip reports as unmatched"

# The system account must exist.  Resolved BY QUERY, never hardcoded to 1: the
# whole reason that account exists (decision log, 2026-09-03) is that
# machine-written rows need a real author, and an integer literal here would
# break silently on any database whose bootstrap insert landed differently.
system_id=$(psql -v ON_ERROR_STOP=1 -d "$DB" -qtAc \
    "SELECT emp_id FROM users WHERE user_name = 'system'") \
    || fail "query failed while resolving the system account"
[[ -n "$system_id" ]] \
    || fail "no user_name='system' in users -- run sql/002_users.sql first"

log "database=$DB  zips=$ZIPS  cities=$CITIES"
log "zcta_boundaries=$zcta_count rows, system account emp_id=$system_id"

# ------ Normalize the inputs in the shell ------
# Done here rather than in \copy FROM PROGRAM so the SQL heredoc can stay fully
# quoted -- no shell expansion inside it, nothing to escape, and a path
# containing a space or a quote cannot reach a subshell.
#
# The zip list is human-maintained, so tolerate blank lines, # comments, and
# stray whitespace.  Anything that is not five digits after that is a typo the
# customer should see, so it is passed through to be reported, not dropped here.

# Fixed paths, not mktemp: \copy does NOT interpolate psql variables, so the
# filename has to be a literal inside the heredoc.  Naming them here keeps the
# heredoc fully quoted, which is what stops a path from reaching a subshell.
stage_zip=/tmp/hail_coverage_zips.txt
stage_city=/tmp/hail_coverage_cities.csv
trap 'rm -f "$stage_zip" "$stage_city"' EXIT

sed -e 's/#.*//' -e 's/[[:space:]]//g' -e '/^$/d' "$ZIPS" | sort -u > "$stage_zip"
grep -v '^#' "$CITIES" > "$stage_city"

listed=$(wc -l < "$stage_zip")
log "$listed distinct zips in the list"

# ------ Load ------

psql -v ON_ERROR_STOP=1 -d "$DB" \
     -v system_id="$system_id" <<'SQL'
BEGIN;

CREATE TEMP TABLE zip_stage  (zcta5 TEXT);
CREATE TEMP TABLE city_stage (zcta5 TEXT, city TEXT);

\copy zip_stage  FROM '/tmp/hail_coverage_zips.txt'
\copy city_stage FROM '/tmp/hail_coverage_cities.csv' WITH (FORMAT csv, HEADER true)

-- A zip that is not five digits is a typo in the customer's file.  Reported,
-- not silently dropped and not fatal -- the rest of the territory still loads.
\echo ''
SELECT zcta5 AS "MALFORMED (not five digits, skipped)"
FROM   zip_stage WHERE zcta5 !~ '^[0-9]{5}$';

-- ON CONFLICT (zcta5) DO NOTHING, never TRUNCATE.  coverage_zips carries
-- removed_at / removed_by, so retiring a zip is a MARKED ROW, not a deletion.
-- Truncate-and-reload would resurrect a hand-retired zip on the next run and
-- silently put the company back into a territory it had chosen to leave.
--
-- The join to zcta_boundaries is an INNER join: it is what enforces the FK
-- before the insert rather than after, so unmatched zips are reportable
-- instead of aborting the transaction.
--
-- area_name is NOT NULL.  Fallback is 'ZIP <nnnnn>' where USPS has no city --
-- it is a human label nothing queries, and refusing to load real territory over
-- a missing decoration is the wrong trade.  Counted below so it stays visible.
--
-- reason is left NULL deliberately.  It is documented as "the field that will
-- be empty in six months if it is not filled in now"; a loader writing
-- 'bulk import' into every row would fill it with something worse than empty --
-- text that looks like an answer and tells nobody why the territory is in
-- scope.  NULL is honestly unanswered.
WITH inserted AS (
    INSERT INTO coverage_zips (zcta5, area_name, reason, added_by)
    SELECT z.zcta5,
           coalesce(nullif(trim(c.city), ''), 'ZIP ' || z.zcta5),
           NULL,
           :system_id
    FROM   zip_stage z
    JOIN   zcta_boundaries b ON b.zcta5 = z.zcta5
    LEFT   JOIN city_stage c ON c.zcta5 = z.zcta5
    WHERE  z.zcta5 ~ '^[0-9]{5}$'
    ON CONFLICT (zcta5) DO NOTHING
    RETURNING 1
)
SELECT count(*) AS inserted FROM inserted;

COMMIT;
SQL

# ------ Report ------
# The unmatched zips are a STABLE FACT, not an error: PO-box-only, institutional,
# and unassigned zips have no Census polygon and never will.  Failing on them
# would be wrong.  But dropping them silently means nobody learns which zips the
# customer believes they cover that the system cannot represent -- so each one is
# printed, every run, and the exit status stays 0.

psql -v ON_ERROR_STOP=1 -d "$DB" <<'SQL'
CREATE TEMP TABLE zip_stage (zcta5 TEXT);
\copy zip_stage FROM '/tmp/hail_coverage_zips.txt'

\echo ''
\echo 'NO ZCTA POLYGON -- not loaded, and cannot be (see decision log 2026-09-03):'
SELECT z.zcta5 AS zip
FROM   zip_stage z
LEFT   JOIN zcta_boundaries b ON b.zcta5 = z.zcta5
WHERE  b.zcta5 IS NULL AND z.zcta5 ~ '^[0-9]{5}$'
ORDER  BY z.zcta5;

\echo 'Loaded rows whose area_name fell back to ZIP <nnnnn> (no USPS city):'
SELECT zcta5, area_name FROM coverage_zips
WHERE  area_name LIKE 'ZIP %' ORDER BY zcta5;

\echo ''
SELECT count(*) FILTER (WHERE removed_at IS NULL) AS active,
       count(*) FILTER (WHERE removed_at IS NOT NULL) AS retired,
       count(*) AS total
FROM   coverage_zips;
SQL

log "done"
