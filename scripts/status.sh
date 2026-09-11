#!/usr/bin/env bash
#
# Operator status checks for the Hail-System ingest pipeline.
# Intended location: scripts/status.sh
#
#   status.sh [runs|data|rejects|nightly|all]      (default: all)
#
# Exit status:
#   0  a nightly run completed within STALE_HOURS
#   1  none has -- this is the alert condition
#   2  usage error, or the database is not reachable
#
# Why the verdict lives in the exit status rather than only in the output:
# ingest health is an *absence* query (decision-log 2026-09-08). A crashed run
# leaves run_status = 'running' forever and reads as healthy-in-progress; a run
# that never fired leaves no row at all. "When did a nightly last succeed" is
# the only phrasing that holds across failed, crashed and never-started. Putting
# that answer in $? means a notifier can consume this later -- systemd
# OnFailure=, an Irin check, a cron wrapper -- without the query being rewritten
# somewhere else and drifting from this one.
#
# Environment overrides: DB_SERVICE, DB_USER, DB_NAME, STALE_HOURS.

set -euo pipefail

# Repo standard (decision-log 2026-09-08). `pipefail` specifically: a pipeline's
# exit status is its last command's, so without it `psql ... | grep -q` reports
# on grep and hides a psql failure entirely.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

DB_SERVICE=${DB_SERVICE:-postgis}
DB_USER=${DB_USER:-hail_admin}
DB_NAME=${DB_NAME:-weather-property}
STALE_HOURS=${STALE_HOURS:-30}

die() { printf 'status.sh: %s\n' "$*" >&2; exit 2; }

# STALE_HOURS is interpolated into SQL below. It comes from the environment, so
# it is checked before it gets there rather than trusted because the caller is
# probably you.
[[ $STALE_HOURS =~ ^[0-9]+$ ]] || die "STALE_HOURS must be a whole number, got '$STALE_HOURS'"

# `docker compose` needs the project directory -- the compose file and .env are
# found relative to the working directory, not to the script. Derived from the
# script's own path so a clone anywhere works, rather than a hardcoded ~/hail-system.
cd "$REPO_ROOT" || die "cannot enter $REPO_ROOT"

require_db() {
    local cid
    # Status captured separately, then the output tested. Never infer a
    # command's success from what it printed (hail-consolidated.md section 7).
    if ! cid=$(docker compose ps --status=running -q "$DB_SERVICE" 2>/dev/null); then
        die "docker compose failed -- is the Docker daemon running?"
    fi
    [[ -n $cid ]] || die "service '$DB_SERVICE' is not running. Try: docker compose up -d"
}

# $1 = SQL, any further arguments are passed to psql before -c.
psql_run() {
    local sql=$1; shift
    docker compose exec -T "$DB_SERVICE" \
        psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@" -c "$sql"
}

cmd_runs() {
    echo "== Recent ingest runs =="
    psql_run "
        SELECT run_id, run_mode, run_status, window_start,
               rows_seen, rows_inserted, rows_skipped, finished_at
          FROM ingest_runs
         ORDER BY run_id DESC
         LIMIT 5;"
}

cmd_data() {
    echo "== Stored data =="
    # AT TIME ZONE is display only. Storage stays UTC (non-negotiable rule 5).
    psql_run "
        SELECT count(*)                                            AS report_rows,
               max(utc_datetime)                                   AS newest_utc,
               max(utc_datetime) AT TIME ZONE 'America/Denver'     AS newest_denver
          FROM iem_data;"
}

cmd_rejects() {
    echo "== Reject reasons, last 10 runs =="
    # Rejects are never deduplicated across runs by design, so counts here are
    # per-run occurrences. A steady trickle of field_count_mismatch is normal
    # (the unquoted-comma CITY rows recur); a sudden or sustained rise is not.
    psql_run "
        SELECT reason, count(*) AS n
          FROM iem_ingest_rejects
         WHERE run_id IN (SELECT run_id FROM ingest_runs ORDER BY run_id DESC LIMIT 10)
         GROUP BY reason
         ORDER BY n DESC;"
}

cmd_nightly() {
    echo "== Nightly health =="
    psql_run "
        SELECT max(finished_at)         AS last_ok,
               now() - max(finished_at) AS age
          FROM ingest_runs
         WHERE run_mode = 'nightly' AND run_status = 'complete';"

    local fresh
    # An aggregate over zero rows still returns one row, holding NULL -- so this
    # never comes back empty, and the shell never has to tell "no rows" apart
    # from "false". coalesce() turns the NULL comparison into that false.
    fresh=$(psql_run "
        SELECT coalesce(max(finished_at) > now() - interval '$STALE_HOURS hours', false)
          FROM ingest_runs
         WHERE run_mode = 'nightly' AND run_status = 'complete';" -tA)
    fresh=${fresh//[[:space:]]/}

    if [[ $fresh == t ]]; then
        echo "OK: a nightly run completed within the last ${STALE_HOURS}h."
        return 0
    fi

    echo "STALE: no nightly run has completed in the last ${STALE_HOURS}h." >&2
    return 1
}

usage() {
    cat <<EOF
usage: status.sh [runs|data|rejects|nightly|all]

  runs     the last five ingest runs and their counts
  data     how many reports are stored, and how recent
  rejects  reject reasons across the last ten runs
  nightly  when a nightly run last succeeded; exits 1 if that is stale
  all      all of the above (default), exiting 1 if nightly is stale

environment: DB_SERVICE=$DB_SERVICE DB_USER=$DB_USER DB_NAME=$DB_NAME STALE_HOURS=$STALE_HOURS
EOF
}

main() {
    local cmd=${1:-all}
    case $cmd in
        -h|--help|help) usage; exit 0 ;;
        runs|data|rejects|nightly|all) ;;
        *) usage >&2; die "unknown command '$cmd'" ;;
    esac

    require_db

    case $cmd in
        runs)    cmd_runs ;;
        data)    cmd_data ;;
        rejects) cmd_rejects ;;
        nightly) cmd_nightly ;;
        all)
            local rc=0
            cmd_runs;    echo
            cmd_data;    echo
            cmd_rejects; echo
            # Nightly runs last so its verdict is the last thing on screen, and
            # its status becomes the script's. `|| rc=$?` is what keeps set -e
            # from exiting here before the message is useful.
            cmd_nightly || rc=$?
            return $rc
            ;;
    esac
}

main "$@"
