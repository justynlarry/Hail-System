# Schema review prompt

Run after the drift reconciliation pass is committed. Re-usable — run it again
after any round of edits.

Paste into Claude Code from the repo root.

---

Read `CLAUDE.md` and `docs/database-schema.md` first. Do not skim them; the
non-obvious reasoning behind several tables is documented there and the DDL is
supposed to match it.

Review every file in `sql/` (001 through 008) plus `scripts/load_reference.sh`.
This is a **review, not a rewrite** — report findings and wait for my go-ahead
before changing anything. Do not refactor working code, and do not add anything
I did not ask for.

**A reconciliation pass has already run.** Documentation and DDL now agree on
table count, `api_pulls` naming, `send_log` naming, the `system` role, email
normalization, office columns, and the three CHECK constraints. Do not re-open
any of those decisions. This pass is about correctness: does it build, and does
it do what the docs say it does.

## 1. Does it build?

Build from empty against a scratch database and report exactly where it fails:

```bash
createdb hail_scratch
for f in sql/*.sql; do
    echo "--- $f"
    psql -v ON_ERROR_STOP=1 -d hail_scratch -f "$f" || { echo "FAILED: $f"; break; }
done
```

Then confirm with `\dt` (expect 14 tables) and `\d <table>` on each. Drop the
scratch database when done.

If PostgreSQL is not reachable on this host, say so rather than guessing — the
database runs in Docker (`postgis/postgis`) and `psql` may not be installed
locally.

## 2. Known outstanding errors

These are already identified. Confirm each, fix them when I approve, and report
anything similar you find.

**High severity — an audit column that cannot do its job:**

- `dnc_list.added_by` is declared `TIMESTAMPTZ` and references a table named
  `user`. It should be `BIGINT NOT NULL REFERENCES users (emp_id)`. This is the
  column recording who suppressed an address; as written it cannot hold a user
  id and points at a table that does not exist. Do not sort this with the
  cosmetic fixes.
- Check `dnc_list.removed_by` for the same problem.

**Mechanical — names already decided, do not re-open:**

- `sql/008` references `estimated_calls`, `actual_calls`, and `status` in CHECK
  constraints and a `COMMENT ON`. The columns are `estimated_api_calls`,
  `actual_api_calls`, and `api_status`. Rename the references to match the
  columns.

**Already found, not yet fixed:**

- `sql/004` declares `report_source_norm` twice
- `sql/002` has a column typo: `emp_lanme` should be `emp_lname`
- `sql/002` has `"!"` in double quotes — Postgres reads that as an identifier,
  not a string literal. Single quotes.
- Several `COMMENT ON` statements are missing terminating semicolons

**One redundancy to resolve:**

- `realtors.email_norm` carries an inline `UNIQUE` *and* has a separate partial
  unique index `realtors_email_norm_uq` on the same column. Drop the inline
  `UNIQUE` and keep the partial index — the `WHERE email_norm IS NOT NULL`
  states the intent and keeps the index smaller, which matters given how many
  agents arrive with no email.

## 3. Error classes I have made in these files

Check for each specifically. Every one appeared at least once:

- Foreign key column type not matching the referenced column
- `REFERENCES table (constraint_name)` instead of a column name
- Referencing a column or table that does not exist
- Composite foreign keys declared as if single-column
- Missing foreign keys entirely
- Missing unique constraints on documented natural keys
- **Unique constraints that should not be there** — a snapshot column is not a
  natural key
- Duplicate constraint names across tables (database-wide in Postgres)
- Duplicate column declarations within one table
- Index names written as `table.column` instead of a plain identifier
- `NOT NULL` on columns the doc says are nullable, and on columns meaning "this
  has not happened yet" (`finished_at`, `sent_at`, `status_updated_at`)
- Generated columns referencing themselves
- `CHAR(n)` anywhere (blank-padded; should be `TEXT` plus a `CHECK`)
- `GENERATED ALWAYS AS IDENTITY` on a third-party key (`zcta5`, `rentcast_id`)
- Trailing commas before a closing paren
- `NUM(...)` instead of `NUMERIC(...)`
- `CHECK ('a','b')` missing the column name and `IN`
- Double quotes where single quotes are meant
- Typos in `COMMENT ON` text

## 4. Cross-cutting consistency

- All timestamps `TIMESTAMPTZ`. No bare `TIMESTAMP`.
- Surrogate keys `BIGINT GENERATED ALWAYS AS IDENTITY`; third-party keys stored
  as received.
- Every `email_norm`-style column uses `lower(trim(...))`. A case mismatch
  between `realtors` and `dnc_list` means the suppression check silently never
  matches — flag as critical if found.
- Every file wrapped in `BEGIN;` / `COMMIT;`.
- Naming conventions consistent (`_at` on timestamps, `{table}_{column}_idx` on
  indexes, `fk_{table}_{target}` on foreign keys, prefixed status columns).

## 5. Indexes

Confirm these exist and flag any that are redundant:

- GiST on `iem_data.geom` and `zcta_boundaries.geom`
- `send_log (realtor_id, sent_at)` — the frequency-cap lookup
- `api_call_log (zip_code, called_at DESC)` — the recent-pull warning
- Foreign key columns that will be filtered on (Postgres does not index the
  referencing side automatically)

An index whose columns are a prefix of an existing unique constraint is
redundant — call those out.

## 6. Documented rules that must hold in the database

Verify each is actually enforced by DDL, or state clearly that it is
application-layer only:

- Suppression is checked against `dnc_list` at send time — there is deliberately
  **no** FK from `send_log` to `dnc_list`. Confirm none was added.
- `iem_data` unique on `(utc_datetime, latitude, longitude, report_text,
  magnitude)`. Note that `magnitude` is nullable and `NULL <> NULL` — assess
  whether `UNIQUE NULLS NOT DISTINCT` is warranted, and flag it as a decision
  rather than deciding it.
- `storm_listing_matches` unique on `(iem_id, listing_id, radius_used)`.
- `email_templates` and `send_log` append-only; nothing deleted anywhere.
- `users` cannot be deleted — no `ON DELETE CASCADE` on anything pointing at it.
- The `system` account cannot be activated or given a real password.
- Removal column pairs (`removed_at` / `removed_by`) move together.
- `dnc_list.source` permits `'legacy_import'`, or the legacy DNC import fails on
  its first row.

## 7. DDL versus documentation

Report discrepancies **in both directions** — columns in the DDL the docs do not
describe, and documented columns the DDL omits.

One known item: `database-schema.md` still describes `dnc_list.added_by` as
"Who suppressed it, or `system`," phrasing from when the column was free text.
Once the type is corrected per §2, reword it to reference the system account's
`emp_id`.

## 8. `load_reference.sh`

- Run `bash -n` and `shellcheck` if available
- Confirm `set -euo pipefail` is present and correctly spelled — `set euo
  pipefail` is valid Bash that silently disables all of it
- Confirm the reprojection is `4269:4326` (NAD83 → WGS84). **4268 is a real SRID
  and will reproject silently wrong** — check this digit specifically
- Confirm `\copy` (client-side) is used, not `COPY`
- Confirm it is idempotent: staging tables plus `ON CONFLICT DO NOTHING`
- Confirm the final verification SQL statement is semicolon-terminated; psql
  discards an unterminated statement at EOF
- Confirm `PGHOST` and `PGUSER` are exported — this runs inside the
  `postgis/postgis` container, not on the Rocky host

## Output

Group findings by severity:

1. **Breaks the build** — file and line
2. **Builds but is wrong** — silent-failure risks, especially anything making a
   documented rule not actually hold
3. **Inconsistency or drift** — naming, doc mismatches
4. **Suggestions** — clearly marked optional, with reasoning

For each: the file, the line, what is wrong, and why it matters. Where you
propose a change, explain the reasoning briefly — I am learning Postgres as this
is built and the reasoning is worth more to me than the patch.

Do not make any edits until I say so.
