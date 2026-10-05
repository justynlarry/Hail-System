# Schema review prompt

Run after the drift reconciliation pass is committed. Re-usable — run it again
after any round of edits.

Paste into Claude Code from the repo root.

---

Read `CLAUDE.md` and `docs/database-schema.md` first. Do not skim them; the
non-obvious reasoning behind several tables is documented there and the DDL is
supposed to match it.

Review every migration in `sql/` (001 through 033) plus `scripts/load_reference.sh`
and `scripts/load_municipal.sh`.
This is a **review, not a rewrite** — report findings and wait for my go-ahead
before changing anything. Do not refactor working code, and do not add anything
I did not ask for.

**001–009 are frozen** (the archive backfill ran against them), so a fix to one
of those is proposed as a new additive migration, not an edit to the file.
011–017 are those additive migrations: 011 a `COMMENT` fix; 012
`county_boundaries`; 013 `api_pulls.storm_date`/`report_text`; 014
`properties.geom` and its two GiST indexes; 015 `storm_listing_matches.emp_id`
and a `matched_at` index; 016 an `iem_data.ingested_at` index; 017
`report_zip_distances`, its `AFTER INSERT` trigger and the ceiling/guard
functions; 018 `users.sessions_invalidated_at` and the single-row `settings`
table; 019 the deferred last-admin constraint trigger; 020 the radius columns
in `settings`, `settings_history` and its trigger; 021 `municipal_boundaries`
(for the parked permits work); 022 `match_runs`; 023 the RentCast billing-day
and quota columns, which also replaces 020's history function and trigger.
024 `address_standardizer` and the `address_key()` function plus a first
`properties.address_key` column; 025 rebuilds that column as generated; 026
`dnc_import_batches` and `dnc_import_rows`; 027 rebuilds `address_key()` and
the column so a NULL part cannot shift the other fields; 028
`settings.listing_freshness_days` and `settings_history.listing_freshness_days`,
which replaces 023's history function and trigger again; 029 and 030 the
append-only guard functions and triggers on `send_log` and `email_templates`;
031 `geocode_cache` and `address_searches`; 032 `ALTER ROLE ... SET
search_path` for `hail_app` and `hail_ingest`; 033 a partial unique index on
`api_pulls`. `sql/guard_test.sql` is **not a migration**: it tests 029 and 030
inside a transaction it rolls back, so build with `sql/[0-9]*.sql` and run it
separately (§1).
`010_roles.sql` holds the roles and the grants for tables that existed when it
was written; every later file that creates a table (012, 017, 018, 020, 021,
022, 026, 031) carries its own grants.

**A reconciliation pass has already run.** Documentation and DDL now agree on
table count, `api_pulls` naming, `send_log` naming, the `system` role, email
normalization, office columns, and the three CHECK constraints. Do not re-open
any of those decisions. This pass is about correctness: does it build, and does
it do what the docs say it does.

## 1. Does it build?

Build from empty against a scratch database and report exactly where it fails.

**Roles are cluster-wide, not per-database.** `010` ends with `ALTER ROLE ...
PASSWORD` and `032` with `ALTER ROLE ... SET search_path`, so building in a
scratch *database* inside the real cluster would overwrite the real roles'
passwords and search paths. Build in a throwaway cluster or container (for
example a fresh `postgis/postgis` container with `address_standardizer` and
its data package available), never against the running `hail-dev` or
production cluster. `024` also needs `CREATE EXTENSION` rights.

```bash
createdb hail_scratch
# 010_roles.sql reads both passwords from the environment and aborts with a
# nonzero status if either is missing or empty, so export them for the loop.
export HAIL_INGEST_PASSWORD=scratch HAIL_APP_PASSWORD=scratch
for f in sql/[0-9]*.sql; do   # not guard_test.sql, which is a test
    echo "--- $f"
    psql -v ON_ERROR_STOP=1 -d hail_scratch -f "$f" || { echo "FAILED: $f"; break; }
done
```

Then confirm with `\dt` and `\d <table>` on each. Expect **30 tables in
`public` plus PostGIS's `spatial_ref_sys`**: the project's 27, and `us_gaz`,
`us_lex` and `us_rules`, which `address_standardizer_data_us` creates (`sql/024`).

**`guard_test.sql` needs data and fails on an empty build.** It takes its test
rows from the first `users`, `realtors` and `storm_listing_matches` rows, so on
a fresh build it stops at its first insert with a not-null error (which is its
design: "If a subselect finds no row, the NOT NULL fails loudly"). Seed the
scratch database first, then run it:

```sql
INSERT INTO report_types (report_type, report_text, unit_confidence)
    VALUES ('H', 'HAIL', 'certain');
INSERT INTO iem_data (latitude, longitude, ingested_at, utc_datetime,
                      nws_issuer, report_type, report_text)
    VALUES (39.7, -104.9, now(), now(), 'BOU', 'H', 'HAIL');
INSERT INTO properties (rentcast_id, property_address)
    VALUES ('seed-1', '1 Main St, Denver, CO 80202');
INSERT INTO listings (list_date, rentcast_id, list_status)
    VALUES (now(), 'seed-1', 'Active');
INSERT INTO realtors DEFAULT VALUES;
INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used)
    SELECT i.iem_id, l.listing_id, 1.0, 5.0 FROM iem_data i, listings l;
```

`psql -d hail_scratch -f sql/guard_test.sql` should then print a `PASS` notice for
every check and no `FAIL`: 24 on 2026-10-05, run against a throwaway
`postgis/postgis:16-3.4` container (it was 18 when it covered only `029` and
`030`). The test ends in `ROLLBACK`. Drop the scratch database, or the
container, when done.

If PostgreSQL is not reachable on this host, say so rather than guessing — the
database runs in Docker (`postgis/postgis`) and `psql` may not be installed
locally.

## 2. Earlier findings, and what is still open

**Fixed — confirm each is still fixed (regression checks), do not re-fix.**
Verified against the DDL on 2026-09-22:

- `dnc_list.added_by` and `removed_by` are `BIGINT` foreign keys to
  `users (emp_id)` (`added_by` `NOT NULL`, `removed_by` nullable, with the
  removal-pair `CHECK`). This is the audit column recording who suppressed an
  address; it must hold a user id.
- `sql/008` uses `estimated_api_calls`, `actual_api_calls` and `api_status` in
  its CHECK constraints and `COMMENT ON`.
- `sql/004` declares `report_source_norm` once.
- `sql/002` has `emp_lname`, and `'!'` in single quotes.
- Every `COMMENT ON` statement is terminated (the files build under
  `ON_ERROR_STOP`).
- `realtors.email_norm` has only the partial unique index
  `realtors_email_norm_uq`, no inline `UNIQUE`.

**Open — observed 2026-09-22, not yet triaged or decided.** Confirm and report;
do not fix without my go-ahead, and remember 001–009 are frozen:

- `sql/012`, `013`, `014` and `016` are not wrapped in `BEGIN;` / `COMMIT;`
  (§4 requires it). `011`, `015` and `017` are.
- `sql/012_counties.sql` declares `county_fips CHAR(5)` and `state_fips CHAR(2)`,
  which §3 forbids (`CHAR(n)` blank-pads; use `TEXT` plus a `CHECK`). FIPS codes
  are fixed-width, so this may be a deliberate choice — flag it as a decision,
  not an error. `sql/021`'s `municipal_boundaries.place_fips CHAR(5)` is the
  same case (with a five-digit `CHECK`), and should get the same answer.

**Open — observed 2026-09-24, not yet triaged:**

- `sql/022`'s index is named `match_runs_storm_idx`, which breaks the
  `{table}_{column}_idx` convention (`match_runs_storm_date_report_text_idx`).
  Left as applied; a rename would be a new migration.
- `sql/020` grants `USAGE` on `settings_history_history_id_seq`, which is
  redundant for an identity column (decision log 2026-09-22, correction).
  Harmless, and left in place.

018–023 are all wrapped in `BEGIN;` / `COMMIT;`, and so are 024–033 (`024` and
`026` in two blocks each). Written 2026-10-05 from reading the files; the build
itself was not run.

**Run 2026-10-05 in a throwaway container:** the build succeeded through `033`
and `guard_test.sql` passed 24 of 24 once seeded. Findings are filed as
parking-lot items 164–167; the four foreign keys with default names (`020`, `022`,
`026`) are item 167.

**Things to check in 024–033, from reading them 2026-10-05, not yet triaged:**

- `sql/031`'s GiST index is `geocode_cache_geom_gix`, which matches the existing
  `_gix` GiST naming (`003`, `004`, `014`) and not the `{table}_{column}_idx`
  convention. Probably deliberate; confirm and say so.
- `sql/024`'s `properties_address_key` index is dropped with its column in `025`,
  which creates `properties_address_key_idx`; `027` drops and recreates the same
  name. Confirm exactly one such index exists after a full build.
- `sql/032` is cluster-level (see §1) and has a verification comment with a typo
  (`stanadardize_address`); it is a comment, so it does not affect the build.

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
- Duplicate **index** names across tables (index names are schema-wide in
  Postgres). Duplicate `CHECK` constraint names on different tables are legal
  and are not a finding: `finished_has_timestamp` is on both `match_runs` and
  `ingest_runs`, and `removal_is_complete` is on three tables
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
- GiST on `properties.geom` and on `(properties.geom::geography)` (`sql/014`),
  `county_boundaries.geom` (`sql/012`)
- `storm_listing_matches (matched_at DESC)` (`sql/015`) and
  `iem_data (ingested_at DESC)` (`sql/016`), both for the activity feed
- GiST on `municipal_boundaries.geom` (`sql/021`), and
  `match_runs (storm_date, report_text)` (`sql/022`), for the work-state query
- `properties (address_key)`, partial `WHERE address_key IS NOT NULL`
  (`properties_address_key_idx`, `sql/027`); GiST on `geocode_cache.geom` and
  `address_searches (searched_at DESC)` (`sql/031`); and the partial unique index
  `api_pulls_one_running_per_storm` on `(storm_date, report_text) WHERE
  api_status = 'running' AND storm_date IS NOT NULL` (`sql/033`)
- `report_zip_distances` is keyed `(iem_id, zcta5)`, so lookups by `iem_id`
  are covered. There is deliberately **no** index on `zcta5` — parking-lot item
  41 defers it until an address lookup needs it; do not flag it as missing
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
- **`send_log` and `email_templates` are append-only in the database**
  (`sql/029`, `030`), the project's most load-bearing rule. Confirm:
  `send_log_guard()` and `email_templates_guard()` exist; each table has a
  `BEFORE DELETE` and a `BEFORE UPDATE` row trigger; `send_log` allows an update
  only to `send_status`, `status_updated_at`, `provider_message_id`,
  `error_detail` and `sent_at` (write-once), and status only moves forward
  (`queued` to `sent`/`failed`, `sent` to `bounced`/`complained`, `bounced` to
  `complained`); `email_templates` allows only `is_active` true to false and
  never false to true; `DELETE` and `TRUNCATE` are revoked from `hail_app` on
  both. **The table owner can still `TRUNCATE`; that is accepted** (decision log
  2026-09-30), so do not flag it as new. `sql/guard_test.sql` is the check.
- `email_templates` and `send_log` append-only; nothing deleted anywhere. Two
  derived tables are the exception and are rebuildable: `report_zip_distances`
  (truncate and rerun the backfill) and `storm_listing_matches` rows no send
  points at. `fk_send_log_match` has no `ON DELETE` action, so a match with a
  send against it cannot be deleted — confirm that still holds.
- `report_zip_distances` cannot lack rows for a report: the `AFTER INSERT`
  trigger on `iem_data` fills it, and `hail_assert_radius_within_ceiling()`
  raises for any radius past `hail_pair_ceiling_m()` (10 miles). Confirm
  `hail_ingest` has `SELECT` on `zcta_boundaries` and `SELECT, INSERT` on
  `report_zip_distances` (the trigger runs as the inserting role), and
  `hail_app` has `SELECT` only.
- `api_pulls.storm_date` and `report_text` are `NULL` together or set together
  (`storm_link_paired`).
- `users` cannot be deleted — no `ON DELETE CASCADE` on anything pointing at it.
- The `system` account cannot be activated or given a real password.
- Removal column pairs (`removed_at` / `removed_by`) move together.
- `dnc_list.source` permits `'legacy_import'`, or the legacy DNC import fails on
  its first row.
- **Never zero active admins:** `trg_last_admin` is an `AFTER UPDATE OR DELETE`
  constraint trigger, `DEFERRABLE INITIALLY DEFERRED`, so a demote-and-promote
  swap in one transaction passes and demoting the only admin is refused at
  `COMMIT`.
- **`settings` is one row:** `CHECK settings_is_singleton (id = 1)`, and
  `hail_app` has `SELECT, UPDATE` only, no `INSERT`.
- **Every settings change is attributed:** `trg_log_settings_change` fires
  `AFTER UPDATE OF` the two radius and two RentCast columns, and **not**
  `global_sessions_invalidated_at` (the sign-out-everyone update must not fire
  it). Its `WHEN` skips a save that changes nothing, and the function reads
  `current_setting('app.current_emp_id')` with no `missing_ok`, so an
  unattributed change raises.
- **Settings history now watches five columns** (`sql/028`): the trigger fires
  `AFTER UPDATE OF` both radii, billing day, quota and `listing_freshness_days`,
  its `WHEN` compares all five, and `settings_history.listing_freshness_days` is
  NULL on rows written before `028`. `listing_freshness_days` is `SMALLINT NOT
  NULL DEFAULT 7 CHECK (BETWEEN 1 AND 90)`.
- **`address_key` is generated and always six fields:** `properties.address_key`
  is `GENERATED ALWAYS AS (address_key(property_address)) STORED`, NULL only when
  the address has no house number, and the function coalesces every part
  (`sql/027`). `geocode_cache.address_key` is `UNIQUE`. `hail_app` has `SELECT`
  on the `us_lex`, `us_gaz` and `us_rules` tables (`sql/024`), or `address_key()`
  fails for it.
- **`address_searches.outcome`** is limited to `matched`, `no_match`,
  `unparseable` and `service_error`; `hail_app` holds `SELECT, INSERT` only on
  `geocode_cache` and `address_searches`.
- **DNC import staging** (`sql/026`): `dnc_import_rows` cascades from
  `dnc_import_batches` (`ON DELETE CASCADE`), which is deliberate and does not
  touch `dnc_list`; `dnc_import_batches.token` is `UNIQUE`; and nothing
  references `dnc_list` from either staging table.
- **One running pull per storm** (`sql/033`): the index is unique, partial, and
  does not cover a manual-zip pull (`storm_date IS NULL`).
- **Role search path** (`sql/032`): `hail_app` and `hail_ingest` have
  `search_path = "$user", public`, with no `tiger` or `topology`. Check with
  `SHOW search_path` as each role.
- **Settings ranges:** both radii `> 0 AND <= 10.0`, match radius `<=` zip
  radius, billing day `BETWEEN 1 AND 28`, quota `> 0`.
- **`match_runs`:** `finished_has_timestamp` (a finished run has
  `finished_at`), and `run_status` limited to `running`/`complete`/`failed`.

## 7. DDL versus documentation

Report discrepancies **in both directions** — columns in the DDL the docs do not
describe, and documented columns the DDL omits.

`database-schema.md` describes `dnc_list.added_by` as the `emp_id` of who
suppressed it (the system account's `emp_id` for machine-initiated rows). That
matches the DDL; confirm it still does.

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

## 9. `load_municipal.sh`

- The same shell checks as §8: `bash -n`, `set -euo pipefail`, `PGHOST` and
  `PGUSER` exported, `\copy` rather than `COPY`
- Confirm it is idempotent the **other** way from `load_reference.sh`:
  `DELETE` then `INSERT` in one transaction, so a reload replaces the set (an
  annexation must replace the old boundary). `ON CONFLICT DO NOTHING` here
  would be a bug
- Confirm DOLA's literal string `'null'` is converted to JSON null, and that
  geometries go through `ST_MakeValid` → `ST_CollectionExtract(…, 3)` →
  `ST_Multi` before the `MultiPolygon` insert
- Confirm the in-transaction check fails the load if the row count doesn't
  match the file or any geometry is still invalid

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
