#!/usr/bin/env bash
#
# Copy the suppression list from one database to another:  hail-dev to a new production server (item 285).
#
#   scripts/dnc_copy.sh export FILE    on the source:  every dnc_list row to FILE, a CSV only you can read
#   scripts/dnc_copy.sh load FILE      on the target:  load FILE into dnc_list in one transaction
#
# Run from the repo root on the host;  it reaches the database through the postgis service, as status.sh does.
# FILE holds real people's addresses:  keep it under data/ (gitignored), move it with scp or rsync over Tailscale,
# and delete it on both machines once the load reports its counts.
#
# A row keeps its address, date, source, reason, name, phone and removal.  The source database's users do not exist
# on the target, so added_by and removed_by become the target's system account, and the reason gains
# "[copied from hail-dev <date>; added by <user_name>]".  realtor_id is not copied:  the sql/064 trigger links it
# on the target, now or when the realtor arrives.
#
# The load is safe to re-run (ON CONFLICT DO NOTHING) and refuses, with nothing committed, if any address in the
# file is then missing from dnc_list, or if an address active in the file is removed on the target:  a suppression
# never comes out weaker than it went in.
#
# Environment overrides:  DB_SERVICE, DB_USER, DB_NAME;  DB_CONTAINER runs psql in that container instead of the
# compose service (for a throwaway test).

set -euo pipefail

DB_SERVICE=${DB_SERVICE:-postgis}
DB_USER=${DB_USER:-hail_admin}
DB_NAME=${DB_NAME:-weather-property}

die() { printf 'dnc_copy.sh: %s\n' "$*" >&2; exit 1; }

psql_db() {
    if [[ -n ${DB_CONTAINER:-} ]]; then
        docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -X "$@"
    else
        docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -X "$@"
    fi
}

cmd_export() {
    local file=$1
    [[ ! -e $file ]] || die "$file already exists; move it away first"
    umask 077
    psql_db -q -c "COPY (
        SELECT d.email_raw, d.added_at, d.source, d.reason, d.name_at_add, d.phone_at_add, d.removed_at,
               a.user_name AS added_by_name, r.user_name AS removed_by_name
        FROM dnc_list d
        LEFT JOIN users a ON a.emp_id = d.added_by
        LEFT JOIN users r ON r.emp_id = d.removed_by
        ORDER BY d.dnc_id) TO STDOUT WITH (FORMAT csv, HEADER true)" > "$file"
    psql_db -At -F' ' -c "SELECT 'exported', count(*), 'rows:', count(*) FILTER (WHERE removed_at IS NULL), 'active,',
                                count(*) FILTER (WHERE removed_at IS NOT NULL), 'removed'
                         FROM dnc_list"
    echo "wrote $file (mode 600).  Delete it on both machines after the load."
}

cmd_load() {
    local file=$1
    [[ -f $file ]] || die "no such file: $file"
    head -1 "$file" | grep -qx 'email_raw,added_at,source,reason,name_at_add,phone_at_add,removed_at,added_by_name,removed_by_name' \
        || die "$file does not start with the header export writes"
    # The data goes in-line after COPY ... FROM STDIN, so the SQL and the file reach psql as one stream.
    {
        cat <<'SQL'
BEGIN;
SELECT emp_id AS system_id FROM users WHERE user_name = 'system' \gset
CREATE TEMP TABLE dnc_stage (
    email_raw TEXT, added_at TIMESTAMPTZ, source TEXT, reason TEXT, name_at_add TEXT, phone_at_add TEXT,
    removed_at TIMESTAMPTZ, added_by_name TEXT, removed_by_name TEXT);
COPY dnc_stage FROM STDIN WITH (FORMAT csv, HEADER true);
SQL
        cat "$file"
        printf '\\.\n'
        cat <<'SQL'
WITH inserted AS (
    INSERT INTO dnc_list (email_raw, added_at, added_by, source, reason, name_at_add, phone_at_add,
                          removed_at, removed_by)
    SELECT email_raw, added_at, :system_id, source,
           concat_ws(' ', nullif(reason, ''),
                     format('[copied from hail-dev %s; added by %s%s]', current_date, coalesce(added_by_name, '?'),
                            CASE WHEN removed_at IS NOT NULL
                                 THEN ', removed by ' || coalesce(removed_by_name, '?') ELSE '' END)),
           name_at_add, phone_at_add, removed_at,
           CASE WHEN removed_at IS NOT NULL THEN :system_id END
    FROM dnc_stage
    ON CONFLICT (email_norm) DO NOTHING
    RETURNING 1)
SELECT count(*) AS inserted FROM inserted;

-- Either check failing raises, and nothing above commits.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM dnc_stage s
               LEFT JOIN dnc_list d ON d.email_norm = lower(trim(s.email_raw))
               WHERE d.dnc_id IS NULL) THEN
        RAISE EXCEPTION 'an address in the file is not in dnc_list after the load';
    END IF;
    IF EXISTS (SELECT 1 FROM dnc_stage s
               JOIN dnc_list d ON d.email_norm = lower(trim(s.email_raw))
               WHERE s.removed_at IS NULL AND d.removed_at IS NOT NULL) THEN
        RAISE EXCEPTION 'an address active in the file is removed here:  it would stop being suppressed';
    END IF;
END $$;

SELECT count(*) AS in_file,
       count(*) FILTER (WHERE removed_at IS NULL) AS file_active,
       count(*) FILTER (WHERE removed_at IS NOT NULL) AS file_removed,
       (SELECT count(*) FROM dnc_list WHERE removed_at IS NULL) AS dnc_list_active,
       (SELECT count(*) FROM dnc_list WHERE removed_at IS NOT NULL) AS dnc_list_removed
FROM dnc_stage;
COMMIT;
SQL
    } | psql_db -q
    echo "loaded.  Delete $file on both machines."
}

case "${1:-}" in
    export) [[ $# -eq 2 ]] || die "usage: dnc_copy.sh export FILE"; cmd_export "$2" ;;
    load)   [[ $# -eq 2 ]] || die "usage: dnc_copy.sh load FILE";   cmd_load "$2" ;;
    *)      die "usage: dnc_copy.sh export FILE | load FILE" ;;
esac