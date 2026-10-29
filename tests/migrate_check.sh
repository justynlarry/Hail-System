#!/usr/bin/env bash
#
# Checks for scripts/migrate.sh (item 277).  Run it in a THROWAWAY postgis container, never against
# hail-dev or production: it builds databases from sql/ from empty, and sql/010 sets the roles'
# passwords, which are cluster-wide.  It refuses to run on a cluster that has the weather-property
# database.  From the repo root:
#
#   docker run -d --name hail-scratch --network none -e POSTGRES_PASSWORD=scratch \
#       -v "$PWD:/repo:ro" postgis/postgis:16-3.4
#   docker exec -e PGUSER=postgres -e HAIL_INGEST_PASSWORD=scratch -e HAIL_APP_PASSWORD=scratch \
#       -w /repo hail-scratch bash tests/migrate_check.sh
#   docker rm -f hail-scratch
#
# What it pins: a fresh database is built from every file and each is recorded; the report then shows
# nothing pending; a database with tables but nothing recorded is refused (hail-dev before its
# baseline); a baseline records without running and only once; an edited file and a deleted one are
# caught and nothing is applied; a new file is applied and recorded; a failing file stops the run and is
# not recorded; two files with one number and an unknown option are refused.

set -uo pipefail

MIGRATE="${MIGRATE:-$PWD/scripts/migrate.sh}"   # another copy, to check that the checks catch a bug
WORK=$(mktemp -d)
failures=0

if [ "$(psql -X -At -d postgres -c "SELECT count(*) FROM pg_database WHERE datname = 'weather-property'")" != "0" ]; then
    echo "migrate_check: this cluster has weather-property; run it in a throwaway container" >&2
    exit 2
fi

check() {  # check NAME EXPECTED_EXIT GREP_FOR_IN_OUTPUT -- command...
    local name=$1 want=$2 pattern=$3; shift 4
    local out got
    out=$("$@" 2>&1); got=$?
    if [ "$got" = "$want" ] && grep -qE -- "$pattern" <<<"$out"; then
        echo "PASS  $name"
    else
        echo "FAIL  $name (exit $got, wanted $want; output did not match /$pattern/ or the exit differed)"
        sed 's/^/        /' <<<"$out" | tail -5
        failures=$((failures + 1))
    fi
}
rows() { psql -X -At -d "$1" -c "$2"; }
cleanup() { dropdb --if-exists mc_fresh; dropdb --if-exists mc_base; rm -rf "$WORK"; }
trap cleanup EXIT
cleanup; WORK=$(mktemp -d)

createdb mc_fresh
check "a fresh database is built from every file" 0 "applied and recorded" -- \
    env PGDATABASE=mc_fresh SQL_DIR=sql bash "$MIGRATE" --apply
total=$(ls sql | grep -cE '^[0-9]{3}_[a-z0-9_]+\.sql$')
check "every file is recorded as applied" 0 "^$total\|$total$" -- \
    rows mc_fresh "SELECT count(*), count(*) FILTER (WHERE how = 'applied') FROM schema_migrations"
check "the report then shows nothing pending" 0 "recorded $total, pending 0" -- \
    env PGDATABASE=mc_fresh SQL_DIR=sql bash "$MIGRATE"

createdb -T mc_fresh mc_base
psql -X -q -d mc_base -c "TRUNCATE schema_migrations"
check "tables but nothing recorded: --apply is refused" 1 "tables but nothing recorded" -- \
    env PGDATABASE=mc_base SQL_DIR=sql bash "$MIGRATE" --apply
last=$(ls sql | grep -E '^[0-9]{3}_[a-z0-9_]+\.sql$' | LC_ALL=C sort | tail -1 | cut -c1-3)
check "a baseline records without running" 0 "recorded $total files" -- \
    env PGDATABASE=mc_base SQL_DIR=sql bash "$MIGRATE" --baseline "$last"
check "the baseline rows say so" 0 "^$total$" -- \
    rows mc_base "SELECT count(*) FROM schema_migrations WHERE how = 'baseline'"
check "a second baseline is refused" 1 "already has rows" -- \
    env PGDATABASE=mc_base SQL_DIR=sql bash "$MIGRATE" --baseline "$last"

cp sql/[0-9]*.sql "$WORK"/
echo "-- edited" >> "$WORK/011_ingest.sql"
check "an edited file is caught and nothing applied" 1 "CHANGED since it was applied: 011_ingest.sql" -- \
    env PGDATABASE=mc_base SQL_DIR="$WORK" bash "$MIGRATE" --apply
cp sql/011_ingest.sql "$WORK"/
mv "$WORK/011_ingest.sql" "$WORK/011_ingest.sql.away"
check "a deleted file is caught" 1 "no longer in .*: 011_ingest.sql" -- \
    env PGDATABASE=mc_base SQL_DIR="$WORK" bash "$MIGRATE"
mv "$WORK/011_ingest.sql.away" "$WORK/011_ingest.sql"

next=$(printf '%03d' $((10#$last + 1)))
after=$(printf '%03d' $((10#$last + 2)))
printf 'BEGIN;\nCREATE TABLE mc_probe (id int);\nCOMMIT;\n' > "$WORK/${next}_mc_probe.sql"
check "a new file is applied and recorded" 0 "applied and recorded 1" -- \
    env PGDATABASE=mc_base SQL_DIR="$WORK" bash "$MIGRATE" --apply
printf 'BEGIN;\nCREATE TABLE mc_probe (id int);\nCOMMIT;\n' > "$WORK/${after}_mc_again.sql"
check "a failing file stops the run" 1 "${after}_mc_again.sql failed; nothing is recorded" -- \
    env PGDATABASE=mc_base SQL_DIR="$WORK" bash "$MIGRATE" --apply
check "and is not recorded" 0 "^${next}_mc_probe.sql$" -- \
    rows mc_base "SELECT string_agg(filename, ',') FROM schema_migrations WHERE how = 'applied'"
rm "$WORK/${after}_mc_again.sql"

cp "$WORK/${next}_mc_probe.sql" "$WORK/${next}_mc_twin.sql"
check "two files with one number are refused" 1 "two files share a number: $next" -- \
    env PGDATABASE=mc_base SQL_DIR="$WORK" bash "$MIGRATE"
check "an unknown option is refused" 1 "unknown option" -- \
    env PGDATABASE=mc_base SQL_DIR=sql bash "$MIGRATE" --aply

echo
[ "$failures" -eq 0 ] && echo "migrate_check: all passed" || echo "migrate_check: $failures failed"
[ "$failures" -eq 0 ]
