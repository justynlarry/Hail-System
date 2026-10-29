#!/usr/bin/env bash

# Apply the sql/ migrations in order and record each in schema_migrations (sql/062).
#
#   scripts/migrate.sh                  report: recorded, pending, and any recorded file that changed.  Changes nothing.
#   scripts/migrate.sh --apply          apply the pending files in order, recording each right after it commits
#   scripts/migrate.sh --baseline 062   record 001 to 062 as already applied without running them (empty table only)
#
# Run it in the loader service, which connects as hail_admin and sees the repo at /repo:
#
#   docker compose run --rm -T loader bash scripts/migrate.sh [--apply | --baseline NNN]
# 
# A person runs this, nothing calls it.  Only files named NNN_name.sql count, so guard_test.sql and the
# dev seeds don't get applied.  Each file has its own BEGIN/COMMIT, so its record is written just after it.  If a 
# run dies between the two, the next run tries the file again and, since most migrations cannot run
# twice, fails loudly.  Add that row by hand in that instance.  SQL_DIR overrides the folder, for the tests.

set -euo pipefail

SQL_DIR="${SQL_DIR:-sql}"
PSQL=(psql -X -q -v ON_ERROR_STOP=1)

fail() { echo "migrate: $*" >&2; exit 1; }
value() { "${PSQL[@]}" -At -c "$1"; }

mode="report"
through=""
case "${1:-}" in
    "") ;;
    --apply) mode="apply" ;;
    --baseline)
        mode="baseline"
        through="${2:-}"
        [[ "$through" =~ ^[0-9]{3}$ ]] || fail "--baseline needs a three-digit file number, such as 062"
        ;;
    *) fail "unknown option '$1' (use --apply or --baseline NNN)" ;;
esac

# The migrations in number order.  The pattern is the same as schema_migrations_filename_shape.
mapfile -t files < <(ls "$SQL_DIR" | grep -E '^[0-9]{3}_[a-z0-9_]+\.sql$' | LC_ALL=C sort)
[ "${#files[@]}" -gt 0 ] || fail "no migration files in $SQL_DIR"
dupes=$(printf '%s\n' "${files[@]}" | cut -c1-3 | uniq -d)
[ -z "$dupes" ] || fail "two files share a number: $dupes"

declare -A hash=()
for f in "${files[@]}"; do
    hash[$f]=$(sha256sum "$SQL_DIR/$f" | cut -d ' ' -f1)
done

has_table=$(value "SELECT to_regclass('public.schema_migrations') IS NOT NULL")
declare -A recorded=()
if [ "$has_table" = "t" ]; then
    while IFS='|' read -r f h; do
        recorded[$f]=$h
    done < <(value "SELECT filename || '|' || sha256 FROM schema_migrations ORDER BY filename")
fi

pending=()
changed=()
for f in "${files[@]}"; do
    if [ -z "${recorded[$f]+set}" ]; then
        pending+=("$f")
    elif [ "${recorded[$f]}" != "${hash[$f]}" ]; then
        changed+=("$f")
    fi
done
gone=()
for f in "${!recorded[@]}"; do
    [ -n "${hash[$f]+set}" ] || gone+=("$f")
done

if [ "$mode" = "baseline" ]; then
    [ "$has_table" = "t" ] || fail "no schema_migrations table: apply sql/062 first"
    [ "${#recorded[@]}" -eq 0 ] || fail "schema_migrations already has rows: a baseline is only for an empty table"
    rows=""
    for f in "${files[@]}"; do
        [ "${f:0:3}" \> "$through" ] && break
        rows+="('$f', '${hash[$f]}', 'baseline'),"
    done
    [ -n "$rows" ] || fail "no files numbered $through or below"
    value "INSERT INTO schema_migrations (filename, sha256, how) VALUES ${rows%,}" >/dev/null
    echo "baseline: recorded $(grep -o "'baseline'" <<<"$rows" | wc -l) files, 001 to $through, without running them"
    exit 0
fi

echo "recorded ${#recorded[@]}, pending ${#pending[@]}"
for f in "${changed[@]}"; do echo "  CHANGED since it was applied: $f"; done
for f in "${gone[@]}"; do echo "  RECORDED but no longer in $SQL_DIR: $f"; done
for f in "${pending[@]}"; do echo "  pending: $f"; done
if [ "${#changed[@]}" -gt 0 ] || [ "${#gone[@]}" -gt 0 ]; then
    fail "a recorded file changed or is gone; a migration is never edited after it is applied (add a new one)"
fi
[ "$mode" = "apply" ] || exit 0
[ "${#pending[@]}" -gt 0 ] || { echo "nothing to apply"; exit 0; }

# A database with tables but nothing recorded would have 001 run again:  that is hail-dev before its
# baseline, with or without sql/062 applied.
if [ "${#recorded[@]}" -eq 0 ] && [ "$(value "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'schema_migrations'")" != "0" ]; then
    fail "this database has tables but nothing recorded: apply sql/062 by hand if it is missing, then --baseline NNN"
fi

unrecorded=()
for f in "${pending[@]}"; do
    echo "--- applying $f"
    "${PSQL[@]}" -f "$SQL_DIR/$f" || fail "$f failed; nothing is recorded for it, and nothing after it ran"
    unrecorded+=("$f")
    # Files before 062 run before the table exists; they are recorded as soon as it does.
    if [ "$(value "SELECT to_regclass('public.schema_migrations') IS NOT NULL")" = "t" ]; then
        for u in "${unrecorded[@]}"; do
            value "INSERT INTO schema_migrations (filename, sha256, how) VALUES ('$u', '${hash[$u]}', 'applied')" >/dev/null
        done
        unrecorded=()
    fi
done
[ "${#unrecorded[@]}" -eq 0 ] || fail "applied but not recorded, no schema_migrations table: ${unrecorded[*]}"
echo "applied and recorded ${#pending[@]}"
