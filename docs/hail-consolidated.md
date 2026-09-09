# Hail System — Consolidated Brief

**Purpose of this file.** A single self-contained handoff document so that
anyone working on this project — Justyn, Claude Code in the terminal, or an
assistant in a browser/GUI with no repo access — is working from the same facts.
If you are reading this without the repository in front of you, this is the
whole picture.

**Status of this file.** It is a *summary*, not the source of truth. When it
disagrees with the files it summarizes, the source files win:

| Topic | Authoritative file |
|---|---|
| Data model, field meanings | `docs/database-schema.md` |
| Why a choice was made | `docs/decision-log.md` |
| External APIs, endpoints, field traps | `docs/data-sources.md` |
| What gets built when | `docs/phases.md` |
| Building the server from bare metal | `docs/server-setup.md` |
| Rules for AI assistants | `CLAUDE.md` |
| Actual DDL | `sql/0*.sql` |

Last synced against the repo: **2026-09-08**, commit `1fad5f4` plus an
uncommitted working tree. Since the previous sync at `9d7ba2e` the project
gained a running container stack, database roles and grants, a per-service
credential split, a `.dockerignore`, a second machine, and a logging convention.
`sql/001`–`010` were verified end to end against `postgis/postgis:16-3.4` on
this date, the reference load run twice to confirm idempotence, and each
database role connected to test its own grants.

The IEM section of `docs/data-sources.md` was re-verified against the live
service on 2026-09-08 and corrected — it described an endpoint shape that does
not exist. See §7 and that file.

**The `CLAUDE.md` status conflict flagged in the previous revision is closed.**
It now reads Phase 1 and records two machines.

---

## 1. What this is

A storm outreach system for **Roof Brokers, Inc. (RBI)**, a Front Range
(Colorado) roofing contractor.

The loop it automates:

1. Nightly, pull free **NWS Local Storm Reports** from the Iowa Environmental
   Mesonet (IEM) and store them at full lat/lon fidelity.
2. Let staff browse that storm history and map any report to the **zip codes**
   within a configurable radius.
3. On an explicit human click, pull **real estate listings** in those zips from
   **RentCast** (paid, metered).
4. Let a human review the matched listings and **send templated email to the
   listing agents** — never the homeowners.

The pitch is deliberately narrow: *"hail of X size was reported near this
listing."* It reports a public record. It never claims damage.

**Scale.** Single developer (Justyn). Two machines — a `hail-dev` VM and the
production OptiPlex (§9). Four or five user accounts,
probably ever. ~135,856 storm report rows for ten years of Colorado.

---

## 2. Where the project actually stands

**Phase 1 — IEM ingest. Phase 0 is closed. Nothing runs unattended yet.**

### Infrastructure — new since the last sync, and verified on 2026-09-08

- **`docker-compose.yml`** defines three services on one network, `hailnet`:
  - `postgis` — stock `postgis/postgis:16-3.4`, the only long-running service.
    Named volume `pgdata`; `./sql` mounted read-only at `/sql`.
  - `ingest` — `python:3.12-slim` + `psycopg[binary]==3.2.3`. Behind the
    `tools` profile, so `docker compose up` does not start it.
  - `loader` — the postgis image plus the `postgis` client package for
    `shp2pgsql`. Repo bind-mounted read-only at `/repo`. Also `tools`.
- **The schema is not auto-applied.** `./sql` is mounted at `/sql`, *not* at
  `/docker-entrypoint-initdb.d`, so `docker compose up` yields an empty
  database. Applying it is an explicit step, and order matters — `010` last.
- **Reference load verified end to end:** 37 report types, 33,791 ZCTAs (527
  Colorado), all at SRID 4326. Re-run inserts 0 rows.

### Database roles — `sql/010_roles.sql`

Three Postgres login roles, unrelated to `users.role`, which is the
application's own login model enforced in Python:

| Role | Used by | Reach |
|---|---|---|
| `hail_admin` | `postgis` superuser, and the `loader` service | Everything. Runs DDL and provisioning. |
| `hail_ingest` | the `ingest` service | `SELECT` on `report_types`; `SELECT, INSERT` on `iem_data` and `iem_ingest_rejects`; `SELECT, INSERT, UPDATE` on `ingest_runs`. No `DELETE` anywhere. |
| `hail_app` | the future web UI | The three cost stages: read reference and weather, write property/matching/sending/operations. No `DELETE` anywhere. |

**Verified by trying it, not by reading the grants.** From inside the ingest
container as `hail_ingest`: `send_log`, `dnc_list`, `email_templates` and
`realtors` all refuse with *permission denied*; `DELETE FROM iem_data` refuses;
`report_types`, `iem_data` and `ingest_runs` are reachable. The grants in `010`
are load-bearing, so they are worth re-testing whenever a table is added — the
convention recorded in that file is that **every SQL file creating a table ends
with the grants for it**.

### Credentials

`.env` holds **secrets only** — three passwords. Every non-secret (hostname,
database name, which role each service connects as) lives in
`docker-compose.yml`. Credentials reach containers through per-service
`environment:` blocks and **never** through `env_file:`, because `env_file`
injects every key in `.env` into every container that uses it — an override
changes what `PGPASSWORD` *is* but leaves the other secrets sitting beside it.
Enumerating keys per service is the only form where a container holds just its
own identity, which is what makes `010`'s grants meaningful rather than
decorative. Compose resolves `${VAR}` on the host at parse time, so no secret is
written into the YAML or any image, and every reference carries `:?` so a
missing value is a startup error rather than a blank password.

The database is `weather-property`. The hyphen means it must be double-quoted in
any literal SQL that names it, which is why `010` grants `CONNECT` through
`format('... %I ...', current_database())` instead.

### Built before this sync, unchanged

- Full written design: schema, decision log, data-source notes, phase plan
- TIGER 2025 national ZCTA and county shapefiles under `data/raw/tiger/`
- Ten years of Colorado LSR CSV at `data/lsr_201601010000_202608312359.csv`
- Derived reference CSVs in `reference/` — statistical evidence, not load input
- `sql/001`–`009`: DDL for all seventeen tables
- `scripts/build_reference_tables.py`, `zcat-data-check.py`, `load_reference.sh`
- `scripts/iem_parse.py` — the shared row parser both ingest scripts will import
- **Initial `roof_relevant` set:** 12 of 37 types, 5 with magnitude floors
  (HAIL 1.00″, TSTM WND GST / NON-TSTM WND GST 58 mph, HIGH SUST WINDS 40 mph,
  HEAVY SNOW 6″). 17.8% of the archive — 24,124 of 135,856 reports. `SNOW` is
  excluded and that single call set the scale: with it, the set would be 82.9%.
- **Phase 0's "done when" met 2026-09-03** — zips within 5 miles of an arbitrary
  lat/lon, 21 zips around the office point in ~22 ms. That number depends on a
  second index: the buffer query casts to `geography` to work in metres, and a
  cast is evaluated per row, so the plain `geom` GiST index cannot serve it.
  `zcta_boundaries` carries two — `zcta_boundaries_geom_gix` on `geom` for
  geometry predicates, `zcta_boundaries_geog_gix` on `(geom::geography)` for
  distance. Without the second, the same query is a parallel sequential scan at
  17.9 seconds.

### Resolved since last sync

- **The `*.csv` gitignore gap is closed.** `planning/report_types.csv` — which
  carries the `roof_relevant` business judgments — is now tracked. `.gitignore`
  ignores `reference/` wholesale plus DNC/unsubscribe name patterns instead of a
  blanket `*.csv`.

### Not built

The ingest scripts themselves, any web UI, any RentCast client, any sending
path, any `report_sources` seed, and **any test suite at all** — there is no
`tests/` directory, no test runner, and no test dependency. Deliberate: test
infrastructure is not Phase 0/1 groundwork. `iem_parse.py` is pure and takes its
`valid_types` as an argument, so it is already shaped for testing with no
fixtures and no database whenever that becomes phase-appropriate.

**Do not build ahead of the current phase.**

**Phase 1 is done when** a spreadsheet of affected zip codes can be produced for
a real storm from last month, and the nightly job has run unattended for a week.

---

## 3. Non-negotiable rules

These are invariants, not preferences. They override convenience, and they
override a request that did not consider them — if a suggestion violates one,
say so rather than implementing it.

1. **Nothing sends email automatically, ever.** There is no code path from the
   nightly ingest to an outbound message. A human clicks send. No scheduled
   sends, no auto-followups, no "helpful" automation around sending.
2. **The suppression check runs against `dnc_list` at send time, inside the same
   transaction as the send.** Not in the UI, not from a cached list, not from a
   flag on the realtor row. If a rule must hold, it holds in the database.
3. **Storm reports are never deleted and never collapsed to zip codes at write
   time.** Full lat/lon in; zips derived on read. The radius is a tuning knob.
4. **`send_log` and `email_templates` are append-only.** Templates are
   superseded, never edited. Sends are inserted; only provider status is updated
   afterward.
5. **All timestamps are `TIMESTAMPTZ` in UTC.** Convert to `America/Denver` at
   display only.
6. **RentCast calls cost money.** Never on an automatic path or on page load.
   Every pull is user-initiated and logged to `api_pulls` / `api_call_log`.
7. **Nothing is deleted anywhere.** Suppressions are marked removed, users
   deactivated, templates superseded, territory rows retired. Every audit column
   points at a row that must still exist. No role holds `DELETE` on any table.

---

## 4. Architecture in one picture

The system has two halves that never touch each other directly.

- **Weather half** — free, automatic, runs whether or not anyone is watching.
- **Property half** — costs money, runs only when a person asks.

They meet in exactly one table: `storm_listing_matches`. Every outbound email
hangs off that table.

```
  IEM (free, nightly)
         │
         ▼
    iem_data ────spatial────> zcta_boundaries
   (points)                          │
      │                           equality
      │                              ▼
      │                        coverage_zips
      │                     (territory filter)
      │                              │
      │                              ▼
      │                          zip list
      │                              │
      │                     ┌────────┴────────┐
      │                     │  HUMAN CLICKS   │
      │                     │  PULL  (spend)  │
      │                     └────────┬────────┘
      │                              │
      │                RentCast (paid, on demand)
      │                              │
      │                              ▼
      │                     properties ──> listings
      │                                        │
      └────────> storm_listing_matches <───────┘
                          │
                          ▼
                     send_log ──> realtors
                          │            │
                          │            ▼
                          └───────> dnc_list
```

**Three stages, each narrower and more expensive than the last:** free browse →
paid pull → human send. Exploration happens entirely on free data; cost is
incurred only after a person has deliberately narrowed scope. The `hail_app`
grants in `sql/010_roles.sql` are grouped by these same three stages, so the
privilege list can be read against this diagram.

---

## 5. The data model

Seventeen tables. Full field-level detail is in `docs/database-schema.md`; an
ASCII ER diagram is in `docs/db-schema-diagram.md`.

### Weather side
| Table | What it holds |
|---|---|
| `report_types` | 37 rows. Meaning of a report type: magnitude unit, unit confidence, `roof_relevant`, and `min_magnitude` (the outreach floor; NULL means none). Composite PK `(report_type, report_text)`. `mag_unit` is nullable — NULL means "we do not know the unit", which is not the same as `none`. |
| `iem_data` | One row per NWS Local Storm Report. Exact lat/lon, generated `geom` (GiST indexed), UTC timestamp, magnitude, qualifier, remark. Natural key is `UNIQUE NULLS NOT DISTINCT` so null-magnitude rows deduplicate. The only table fed by an automatic job. |

### Reference
| Table | What it holds |
|---|---|
| `report_sources` | 36 rows when seeded; **currently empty** — the table exists, the loader does not fill it. What a reporting source is and how far to trust it — `confidence_tier`, `is_automated`. **No FK from `iem_data`**: source is free text typed at NWS offices and an FK would break the nightly ingest. A lookup, joined on `report_source_norm`, never a constraint. |
| `zcta_boundaries` | 33,791 Census ZCTA polygons, nationwide, EPSG 4326. `centroid` is generated with `ST_PointOnSurface`, not `ST_Centroid`, so it cannot fall outside a C-shaped zip. Two GiST indexes, one on `geom` and one on `(geom::geography)` — see §2. Loaded once, never written to. **No foreign keys** — joined spatially. |
| `coverage_zips` | RBI's service territory, 183 ZCTAs. Keyed on `zcta5` with an FK to `zcta_boundaries`. Ours, and it will be edited. |

### Property side
| Table | What it holds |
|---|---|
| `properties` | One row per physical house, PK `rentcast_id`. Only facts still true in five years (year built, yes; price, no). |
| `listings` | One row per *time a house was for sale*. Surrogate `listing_id`, natural key `(rentcast_id, list_date)`. Carries an agent **snapshot** plus `raw_payload` JSONB. Its `list_agent_email_norm` and `list_office_email_norm` are snapshots and neither is unique — many listings sharing one agent is the normal case. |
| `realtors` | Resolved agent identities, keyed on `email_norm` (UNIQUE, partial: `WHERE email_norm IS NOT NULL`). Also carries `office_email_norm`, **not** unique — a brokerage address is shared by every agent in the office. Exists for frequency capping and send history. |
| `dnc_list` | Suppression list, keyed on `email_norm`. Answers one question: may we send to this address? |

### The hinge
| Table | What it holds |
|---|---|
| `storm_listing_matches` | "This listing was within N miles of that storm report." Unique on `(iem_id, listing_id, radius_used)` so the same pairing can exist at multiple radii. Points at `listing_id`, **not** `rentcast_id`. |

### Sending
| Table | What it holds |
|---|---|
| `send_log` | One row per email to one agent. Snapshots `recipient_email`. `send_status` runs `queued → sent → (bounced\|complained)` or `queued → failed`; `queued_at` is set before the attempt, `sent_at` stays null until the provider accepts. Append-only except provider status. |
| `email_templates` | Versioned message text. Self-FK `supersedes_id`. Never edited in place. |

### Operations
| Table | What it holds |
|---|---|
| `users` | Logins. Roles `admin` / `sender` / `viewer`, plus `system` — a non-login account, bootstrapped in `sql/002`, that owns machine-initiated rows (automatic bounce and complaint suppressions, the legacy DNC import). Constrained in the database so it cannot be activated or given a real password. Unlike the other three it is **not a cost stage**. |
| `api_pulls` | One row per user-initiated RentCast pull. Records `estimated_api_calls` vs. `actual_api_calls` side by side; `api_status` tracks the run. `iem_id` is nullable — a pull need not be tied to one storm. |
| `api_call_log` | One row per zip within a pull. Powers the "this zip was pulled recently" warning. |
| `ingest_runs` | One row per execution of an IEM ingest script (`nightly` / `backfill` / `replay`). Records the UTC window actually requested plus `rows_seen` / `rows_inserted` / `rows_skipped`. No `emp_id` — system-initiated. Written before the work starts, like `api_pulls`. **The alert that matters is the absence of a row**, which is why it is a table and not log output. |
| `iem_ingest_rejects` | One row per input line the parser refused. FK → `ingest_runs`. `raw_row` holds the line verbatim (TEXT, not JSONB — it is here because it did not parse), so rejecting is not lossy. `reason` is a closed **five**-value CHECK; anything outside it must terminate the run rather than be skipped. |

### Key strategy

- **Surrogate `BIGINT` PKs** on every table we control: `iem_id`, `listing_id`,
  `realtor_id`, `match_id`, `send_id`, `template_id`, `emp_id`, `pull_id`,
  `api_log_id`, `dnc_id`, `run_id`, `reject_id`.
- **Natural keys enforced as unique constraints** on every table ingesting
  external data. `iem_data` is unique on
  `(utc_datetime, latitude, longitude, report_text, magnitude)` — this is what
  makes the overlapping nightly window idempotent under `ON CONFLICT DO NOTHING`.
- **Two deliberate exceptions.** `coverage_zips` is keyed on `zcta5` (a Census
  identifier) because the row's whole purpose is to name a Census polygon.
  `send_log.realtor_id` is denormalized because the frequency cap queries it
  constantly.
- **Every normalized email column is `lower(trim(...))`** — `realtors.email_norm`,
  `realtors.office_email_norm`, `listings.list_agent_email_norm`,
  `listings.list_office_email_norm`, `dnc_list.email_norm`. This is correctness,
  not style: the suppression check compares against `dnc_list.email_norm`, and a
  case-function mismatch between two tables means the check silently never
  matches.
- **Three CHECK constraints carry rules that are easy to lose.**
  `distance_within_radius` on `storm_listing_matches`
  (`distance_miles <= radius_used`) catches a matcher bug writing rows the radius
  could not have produced. `sent_has_timestamp` on `send_log` forbids a row
  claiming `sent`, `bounced`, or `complained` with no `sent_at`.
  `report_type_pair_complete` on `email_templates` requires both halves of the
  type pair or neither — Postgres FKs default to `MATCH SIMPLE`, which skips the
  check entirely when any column in the key is null, so a half-set pair would
  otherwise slip past the composite FK unverified.
- **A CHECK fails only on definite false, never on unknown.** `ingest_runs`
  relies on this: `counts_consistent` passes while a run is in flight because
  the counts are still null, so one constraint covers both states.

---

## 6. Design decisions worth knowing before proposing anything

Condensed from `docs/decision-log.md`. Each of these has already been argued
through; re-proposing the opposite needs a new reason, not a fresh opinion.

- **Generalized beyond hail from day one.** Event type is a column value, not a
  table name. Adding wind or wildfire later is an `UPDATE` to
  `report_types.roof_relevant`, not a migration.
- **RentCast pulls are user-initiated, not scheduled.** A nightly pull buys
  listings nobody reads, and they go stale anyway. Cost: no "new since last
  night" watermark — recovered via `listings.first_seen_at`.
- **`properties` and `listings` are separate tables.** RentCast's `id` is a
  *property* id; a relist reuses it. One combined table would silently overwrite
  the listing an agent was contacted about, including which agent.
- **Listing agent fields are duplicated on `listings` alongside `realtor_id`.**
  Two different facts: the snapshot at listing time versus the current resolved
  person. **Do not normalize this away.**
- **No realtor deduplication beyond exact normalized email.** Jen Watson may
  exist five times under five addresses; that is acceptable. Over-emailing is a
  recoverable annoyance; wrongly silencing a working agent is invisible and
  permanent. Asymmetric risk, so err toward contact.
- **Suppression is a table keyed on email, not a flag on `realtors`** — for
  exactly the reason above, and so addresses never seen as a realtor can be
  suppressed.
- **Confidence is a tiered label computed at query time**, showing its inputs
  ("Moderate — 3 reports, up to 1.25″, 2 spotters"). No stored percentage: a
  number implies a probability the data does not support, and a stored score
  goes stale silently when the weighting changes.
- **Email wording claims a report, not damage.** Defensible, survives scrutiny,
  needs no certainty score to hold up.
- **Sending goes through a queue and `queued` is a real status.** The frequency
  cap counts `queued` rows, not just `sent` — otherwise a large batch
  double-sends before the first clears.
- **`admin` manages users and nothing else** — cannot touch templates,
  suppression, or sending. This needs enforcing explicitly in code, because
  "admin" conventionally means "can do everything." Note that all three
  application roles share one *database* role, `hail_app`; the split between
  them is Python's job, not Postgres's.
- **Whole-country boundary data, not Colorado-only.** The spatial index makes
  national scope free to query.
- **No `qualifiers` table — deferred, not rejected.** Three codes, and the
  glossary text available for them (`reference/qualifiers.csv`) says `M` means
  instrument-measured, which is the trap in §7 stated backwards. Loading it
  would promote a known-false claim to a UI label.
- **An out-of-domain `QUALIFIER` ends the run; it is not a reject** (2026-09-06,
  reaffirmed 2026-09-08). `iem_parse.py` validates against `{M, E, U}` and
  raises `QualifierDomainError` before building the record, so the run stops on
  a sentence naming the field, value, `VALID`, `TYPECODE` and `WFO` rather than
  on an `IntegrityError` from mid-batch. A fourth code is a **changed upstream
  contract**, not a bad row: rejecting would discard whole storm reports over a
  field that only tracks reporter training, and nulling would convert a changed
  contract into no signal at all. The run ends either way — a reject does not
  rescue it — so the real trade is "stop and look" versus "quietly discard until
  someone reads a count." **The reject enumeration stays closed at five.**
- **Malformed rows are rejected, logged, and skipped — not repaired**, and only
  for the five enumerated reasons. The friction of a migration to add a sixth is
  the mechanism that stops skip-and-continue from drifting into swallowing
  whatever goes wrong.
- **`set -euo pipefail` is the standard for shell in this repo** (2026-09-08).
  See §7 for the incident that produced it.
- **Legacy DNC lists are imported before any send**, marked
  `source = 'legacy_import'`, with `added_by` set to the system account's
  `emp_id`. Keeping the source distinguishable stops imported rows from drowning
  the bounce and complaint signal from new suppressions.
- **IP:** Justyn owns the code; RBI is licensed a running system on their
  hardware. Generic components live separately from RBI-specific config so the
  legal boundary follows a file boundary.

---

## 7. Known traps

These have already bitten. Do not re-discover them.

### IEM
- **`MAG` contains the literal string `None`** as its null marker — 3,353 of
  135,856 rows. Coerced to 0 it produces 629 magnitude-zero flash floods and
  549 magnitude-zero tornadoes.
- **`Decimal()` accepts `'NaN'` and `'Infinity'`.** Neither raises
  `InvalidOperation`, so a naive parse passes them straight through. Two
  distinct consequences, and `iem_parse.py` now guards both with `is_finite()`
  *before* any range test:
  - For coordinates, an **ordered comparison against a `Decimal` NaN signals
    `InvalidOperation`** — so `-90 <= value <= 90` raises, and that exception
    escapes `parse_row` and ends the whole run on a row that should have been a
    clean reject.
  - For magnitude there is no range check to fall through to, and **Postgres
    `NUMERIC` accepts `NaN`** (confirmed against `NUMERIC(6,2)`), so the value
    lands in `iem_data.magnitude` and reads as a real measurement forever after.
- **Units come from the type name, never the value range.** Range inference was
  actively wrong: tornado EF numbers read as inches, fog visibility as inches,
  heat index as mph.
- **`TYPECODE` is not unique.** Nine codes map to two texts each — `R` is both
  RAIN and HEAVY RAIN, `S` both SNOW and HEAVY SNOW. The key is
  `(report_type, report_text)`.
- **76 rows have unquoted commas inside `CITY`** (`BISON LAKE, GLENWOOD 15`),
  giving 17 fields instead of 16. Never split on commas — but a real CSV parser
  **detects** these and cannot **repair** them. The quotes were never written,
  so the field boundary is unrecoverable. They are rejected as
  `field_count_mismatch`, and `raw_row` keeping the line verbatim is what makes
  that non-lossy.
- **A misspelled IEM filter parameter is silently ignored, not rejected.**
  Verified against 2018-06-19 (177 reports, 142 hail): `type=HAIL` → 142 rows
  and `magge=1.75` → 75 rows, but `typetext=HAIL` → **177** and
  `magnitude=1.75` → **177** — the full unfiltered set, HTTP 200, no warning.
  A wrong parameter name returns everything, so a script that trusted it would
  look like it was filtering and would not be. (We must not filter at ingest
  regardless; this is a trap for anyone reading the pre-2026-09-08 docs.)
- **`recent` is in SECONDS and `hours=` does not exist** — `hours=30` returns
  HTTP 422 "GET start time parameters missing". `fmt=geojson` returns 422 as
  well; this endpoint serves csv, shp, kml and xlsx only.
- **A quiet day returns a header line and no data rows.** `rows_seen = 0` is a
  normal `complete` run, not a failure.
- **`QUALIFIER` of `M` on hail does not mean instrument-measured.** It tracks
  reporter training; 97.8% of M and 94.9% of E hail values land on the same
  coin/ball catalog. Use `SOURCE` for a confidence signal.
- **`CITY` is not a city** — it is a position relative to a landmark
  (`2 SW Great Divide`).
- **Timestamps are UTC.** A Front Range evening storm crosses midnight UTC and
  splits across two calendar days if grouped naively.
- **`UGC` is null before mid-2022.** Added July 2022, ~99% coverage since.
- **`SOURCE` is free text with case variants.** Normalize; match on
  `report_source_norm`.
- **Single-quote IEM URLs in bash.** Unquoted, `&` backgrounds the job and
  truncates the query string — curl succeeds and returns the wrong data.
- **Colorado WFOs are `BOU`, `PUB`, `GJT`, plus `GLD` and `CYS` on the borders.**
  `wfos=BOU,PUB` silently drops the northeast corner.
- **The `SNOW` magnitude tail is not one storm.** Maximum 175 inches, with a
  handful above 60 — seasonal or storm-total accumulations entered against a
  single LSR. Five reports out of 85,049. Inert today because `SNOW` is
  `roof_relevant = FALSE`, but a magnitude floor on SNOW would admit exactly
  these rows first. Look at this before that flag is ever flipped.

### Census TIGER
- **TIGER ships in NAD83 (EPSG 4269); IEM and RentCast are WGS84 (4326).**
  Reproject at load (`shp2pgsql -s 4269:4326`). Mixing them fails silently — the
  join runs, returns too few rows, and never errors.
- A shapefile is a **set**: `.shp`, `.dbf`, `.prj`, `.shx`. Extracting only the
  `.shp` fails. TIGER also unzips into a **directory named after the archive**,
  so the `.shp` is one level below where the archive sits.
- **An index only helps the expression it is built on.** A GiST index on `geom`
  does nothing for a predicate written against `geom::geography`. The schema
  looked right and the query was 855× slower than it should have been, with no
  error anywhere. Check the plan, not the index list.
- **ZCTAs are not USPS zips.** PO-box-only and institutional zips have no
  polygon. A hand-built 193-entry coverage list had 10 such entries; the FK to
  `zcta_boundaries` is what makes them uninsertable rather than periodically
  re-detected.

### RentCast
- **`id` is a property id, not a listing id.** A relisted house reuses it.
- **`id` is derived from the address string**, so an upstream formatting change
  (`Hargis St` → `Hargis Street`) mints a new id for the same building.
- **Ids are case-sensitive** and must be passed back exactly as returned.
- **`listingAgent.email` is frequently missing.** Handle null — it is the only
  identifier available for a person.
- **No agent MLS id or license number is exposed.** Dedupe on email only.
- **`history` carries no agent and no MLS number**, so reconstructed past
  listings have null agent fields.
- **New Construction is not worth outreach** — a brand-new roof is not a hail
  claim.

### Shell, Docker, and psql

These are newer and cost real time on 2026-09-08.

- **A pipeline's exit status is its last command's.** `set -e` alone does not
  see an upstream failure. The precondition check `psql ... | grep -q 1 || fail
  "table missing"` reported a **missing table** when the real cause was a wrong
  password — sending the reader to `sql/001..003` to debug a problem in `.env`.
  `set -o pipefail` is what catches it, and `shp2pgsql | psql` depends on it
  directly: `psql` exits 0 on empty input when `shp2pgsql` could not read the
  file. **Never infer a command's success from its output** — capture the status
  separately, then test the output.
- **`.dockerignore` patterns do not cross `/`.** A bare `__pycache__/` matches
  only a directory at the **context root**, so `scripts/__pycache__/` sailed
  through into the build context. Nested matches need `**/`. This fails
  silently. Likewise the `!` re-inclusion is **order-dependent**:
  `!.env.example` must follow the `.env.*` rule that would otherwise swallow it.
- **The whole build context is tarred and shipped to the daemon before the first
  instruction runs.** Without a `.dockerignore` this repo sent **1.6 GB** every
  build, because `data/` is in the context even though no `COPY` touches it.
  With one: 36 kB. This is about what never leaves the host, not only image size.
- **psql does not interpolate `:'var'` inside dollar-quoted text.** A
  `DO $$ ... :'password' ... $$` block reaches the server verbatim and is a
  syntax error *even when the variable is set correctly*. Carry the answer
  across that boundary with `set_config`, never the value.
- **An undefined psql variable is not an empty string.** `:'nosuchvar'` is
  passed through literally and is a syntax error. But an env var that is **set
  and empty** leaves the variable defined and interpolates cleanly — which is
  what a half-filled `.env` copied from `.env.example` produces. Guard for empty,
  not just undefined.
- **`\quit` exits psql with status 0.** A `for f in sql/*.sql` loop reads a
  skipped file as a passing one. Fail with a `RAISE` under `ON_ERROR_STOP`.
- **`shp2pgsql -d` is not `-c`.** `-d` emits a `DropGeometryColumn` for a stage
  table that does not exist on a first run, which raises and, under
  `ON_ERROR_STOP=1`, kills the load before it starts.
- **The postgis image ships only the server-side extension.** `shp2pgsql` lives
  in the separate `postgis` client package. The base image clears
  `/var/lib/apt/lists`, so a bare `apt-get install` reports "unable to locate
  package" and looks exactly like the package does not exist. The `apt-get
  update` is the whole fix.

---

## 8. External sources

| Source | Cost | Notes |
|---|---|---|
| **IEM Local Storm Reports** | Free, no key, no documented rate limit | **One** endpoint, `cgi-bin/request/gis/lsr.py`, serves both jobs: nightly passes `recent=108000` (SECONDS), backfill passes `sts`/`ets`, back to 2003. Formats csv/shp/kml/xlsx — **`fmt=geojson` returns 422**. The `lsrs.phtml` schema page documents the shapefile DBF, not the CSV. |
| **Census TIGER/Line 2025** | Free | National ZCTA (`tl_2025_us_zcta520.zip`, 33,791 rows) and county (`tl_2025_us_county.zip`, ~3.2k rows) files. No state split exists for ZCTA. |
| **RentCast** | **Paid**, monthly lookup allowance | `GET /listings/sale`, paginated to 500, sorted by `lastSeenDate` desc. Docs: `https://developers.rentcast.io/reference/property-listings-schema` (append `.md` for markdown). |
| **Email provider** | TBD | Must *explicitly permit* outreach to non-opt-in recipients — several providers terminate for it. Needs bounce/complaint webhooks returning a matchable message id, plus throttling for warmup, on a separate sending subdomain. |

**Prior history worth knowing:** a contractor-built predecessor used Mailchimp
and led to blacklisting. Whether RBI's main domain took reputation damage is an
open question; if so, remediation is its own line item.

---

## 9. Stack, environment, and logging

### Two machines, deliberately alike

| | `hail-dev` | production |
|---|---|---|
| Hardware | VM | Dell OptiPlex on RBI's office network, running **Proxmox** |
| OS | **Rocky 10** | **Rocky Linux** VM (plus a PBS VM for backup) |
| Docker | CE from the CentOS repo | CE from the CentOS repo |
| Timezone | UTC | UTC |
| journald | persistent | persistent, capped at 500M |
| SELinux | **enforcing**, `:Z` on bind mounts | enforcing, `:Z` on bind mounts |

**Dev matching prod is the point, not a coincidence.** SELinux in particular:
a bind mount without `:Z` fails with a permission error that looks nothing like
a permission error, and the place to discover that is a VM that can be rebuilt
in twenty minutes — not a box on RBI's network with the company's data on it.
The same argument covers the Docker install source and the timezone: a bug that
only appears in one of the two environments costs more to find than the
duplication costs to maintain.

Build steps for a machine from bare metal are in `docs/server-setup.md`: static
IP via `nmcli`, Podman removed before Docker CE goes on, timezone set to UTC,
`/var/log/journal` created for persistence with `SystemMaxUse` capped,
`firewalld` left closed because the UI arrives through the tunnel.

### The rest of the stack

- **PostgreSQL 16 + PostGIS 3.4**, in Docker, database `weather-property`
- **Python 3.12** backend, stdlib and boring dependencies preferred;
  `psycopg[binary]==3.2.3` is currently the only dependency
- Web UI reachable via **Cloudflare tunnel** (outbound-only, so no inbound
  ports — but note Docker writes iptables rules directly and a published `-p`
  bypasses firewalld's zones)
- **Tailscale** for host-to-host file movement
- Deployed with **Ansible** where practical
- Monitoring through an existing instance called **Irin**

### Logging and operational visibility

Full reasoning in the 2026-09-08 decision-log entries. The working rules:

**Two layers, and the split is forced rather than chosen.**

| Layer | Holds | Why it cannot be the other one |
|---|---|---|
| Database — `ingest_runs`, `iem_ingest_rejects` | What a run did, and which lines it refused | Queryable, constrained, and the detail is worth keeping |
| stdout → journald | That the process existed, started, and how it ended | **A database failure cannot be written to the database**, and a process killed before its `except` block writes nothing anywhere |

**The format is logfmt, to stdout, never to a file.**

```
event=ingest_start run_id=41 run_mode=nightly window_start=... window_end=...
event=ingest_done  run_id=41 rows_seen=118 rows_inserted=12 rows_skipped=0
```

- `key=value` pairs, `run_id` on **every** line so one run can be pulled out of
  an interleaved journal.
- The **start line is emitted before anything can fail** — before the HTTP
  request, before the database write. It is the only evidence that survives a
  `SIGKILL`.
- Readable in `journalctl` by eye, parseable by a shipper later without regex.
- **Detail stays in the database.** The log says how many; the table says which.
- **`PYTHONUNBUFFERED=1` is load-bearing**, not tidiness. Python buffers stdout
  when it is not a TTY — exactly the case under systemd — and a process killed
  before the buffer flushes produces *no logs at all*. Already set in
  `docker/ingest.Dockerfile`.

**Health is an absence query, not a status query.**

```sql
SELECT max(finished_at) FROM ingest_runs
 WHERE run_mode = 'nightly' AND run_status = 'complete';
```

Older than ~30 hours is the alert. A status column cannot express this: a
crashed run leaves `running` forever and reads as healthy-in-progress, and a run
that never fired leaves no row to inspect at all. "When did a nightly run last
*succeed*" is the only phrasing that holds across failed, crashed, and never
started.

**systemd schedules; the container is only the runtime.** A timer unit invokes
`docker compose run`, and the unit carries `OnFailure=` and `Persistent=true` so
a missed run fires after downtime rather than being skipped silently. Cron
inside the container was declined — a second scheduler on a box that already has
systemd, forfeiting journald capture, `systemctl --failed`, and `OnFailure=`.

---

## 10. Repo layout

```
CLAUDE.md                     rules for AI assistants — read first
README.md                     currently empty
docker-compose.yml            postgis + ingest + loader on hailnet
.dockerignore                 keeps data/ and secrets out of the build context
.env                          gitignored — three passwords, nothing else
.env.example                  same keys, no values
requirements.txt              psycopg[binary]==3.2.3
docs/
  hail-consolidated.md        this file
  database-schema.md          field-level data model, 17 tables, open questions
  db-schema-diagram.md        ASCII ER diagram
  decision-log.md             dated, append-only; supersede, never rewrite
  data-sources.md             IEM / TIGER / RentCast endpoints and traps
  phases.md                   phases 0–7 with a "done when" for each
  server-setup.md             bare-metal Rocky build, step by step
  command-ref.md              Justyn's own Docker/Postgres/type notes
  schema-review.md            re-runnable review prompt for sql/ + the loader
sql/                          apply in order; 010 must be last
  001_extensions.sql          postgis
  002_users.sql               users (+ the bootstrap system account)
  003_reference.sql           report_types, report_sources, zcta_boundaries
  004_weather.sql             iem_data, coverage_zips
  005_property.sql            properties, listings, realtors, dnc_list
  006_matching.sql            storm_listing_matches
  007_sending.sql             send_log, email_templates
  008_operations.sql          api_pulls, api_call_log
  009_ingest.sql              ingest_runs, iem_ingest_rejects
  010_roles.sql               hail_ingest / hail_app roles, grants, passwords
scripts/
  build_reference_tables.py   derives reference CSVs from the raw LSR archive
  zcat-data-check.py          checks coverage zips against the TIGER .dbf
  iem_parse.py                shared row parser; both ingest scripts import it
  load_reference.sh           idempotent loader: report_types CSV + ZCTA shapefile
docker/
  ingest.Dockerfile           python:3.12-slim + psycopg, runs as non-root
  loader.Dockerfile           postgis image + the pinned postgis client package
reference/                    gitignored — derived statistical CSVs, DNC lists
data/                         gitignored — raw LSR archive, TIGER shapefiles
planning/                     spreadsheets, coverage zip list, working notes
  report_types.csv            THE curated seed for report_types (tracked)
```

**No `tests/` directory exists.** See §2.

### Running it

```bash
cp .env.example .env          # then fill in three passwords
docker compose up -d          # postgis only; ingest/loader are profile "tools"

# apply the schema — NOT automatic, and 010 must come last
docker compose run --rm loader \
  bash -c 'for f in /repo/sql/*.sql; do psql -v ON_ERROR_STOP=1 -f "$f" || exit 1; done'

# load reference data — idempotent, safe to re-run
docker compose run --rm loader bash /repo/scripts/load_reference.sh
```

---

## 11. Open questions

Unresolved. Each is cheaper to settle now than after there is data.

1. **Is a "storm" a first-class entity?** The UI concept is *"Hail — August 24 —
   14 neighborhoods,"* which today is a `GROUP BY`, not a table. A real
   `storm_events` table would allow naming an event and reporting on it as a
   unit; the cost is defining a clustering rule. Deferring is safe *if* the
   query-based grouping stays consistent.
2. **What is the default buffer radius, and where does it live?** Constant,
   settings table, or per-user preference. Related and unanswered: **does the
   radius vary by event type?** Hail swaths and straight-line wind do not have
   the same footprint.
3. **Is there a settings table at all?** Radius default, frequency-cap window,
   monthly API ceiling, warmup limit — none of these currently has a home.
4. **How are counties handled for browse-by-county?** Three county sources exist
   (`nws_geo_code` UGC, `iem_data.county` free text, `properties.county_fips`).
   A crosswalk would reconcile them.
5. **Does the frequency cap have a hard floor?** Decided in principle — a short
   window nobody can click past, plus a soft warning above it. The numbers are
   unset and the floor must live in the database.
6. **Where is the merge-field vocabulary stored?** Agreed it is reference data,
   not a hardcoded list. Not yet designed.
7. **What happens to a listing that goes inactive after a match?** Probably
   surface `list_status` at send time and let the sender decide, but the rule is
   unstated.
8. **Retention of `listings.raw_payload`.** Cheap now, grows without bound. No
   policy set.
9. **Does outreach ever fall back to the office email when an agent has none?**
   Suppression already handles this correctly — the check runs against the
   address actually used, not against a person. **The frequency cap does not.**
   Fifteen agents at one brokerage with no email all resolve to one `info@`
   inbox; each is a distinct `realtor_id`, so a per-realtor cap counts fifteen
   separate sends and one shared inbox receives fifteen emails in a batch.
   Shared inboxes are also the least tolerant recipients, and a complaint is the
   signal that means bad targeting. If this is ever built, the cap needs a
   per-address window alongside the per-realtor one, and `send_log` likely needs
   to record whether the recipient was a person or an office — otherwise
   `realtor_id` stops meaning "who we emailed" and starts meaning "who this was
   about."
10. **Should append-only be enforced by the database?** `send_log` and
    `email_templates` are append-only by convention and in code. `sql/010` now
    withholds `DELETE` from every role, which closes part of this — but `UPDATE`
    is still granted on both tables, so nothing stops a body being rewritten in
    place. **Deferred to Phase 5**, when the real update pattern is known.
11. **Which role sees the operational views?** The three application roles are
    defined as cost stages, and ingest health is not one — it costs nothing to
    look at, but "did last night's ingest run" is an operator question, not a
    browsing one. `hail_app` currently holds `SELECT` on `ingest_runs` and
    `iem_ingest_rejects`, which is a provisional answer, not a decided one.
12. **Out-of-state reports are excluded permanently.** Ingest queries
    `state=CO`, so a report over the Wyoming or Nebraska line is never fetched.
    **No buffer radius recovers it** — the radius widens the search around a
    stored report, and these are never stored. Cheap to widen later (re-ingest
    is idempotent); the reason to decide it deliberately is that nothing will
    ever surface the gap — no row, no reject, no count.
    **New as of 2026-09-08:** IEM exposes bounding-box parameters (`north`,
    `south`, `east`, `west`, added 2024-10-24), so this question now has a
    mechanism attached rather than only a description — a box crossing the state
    line would store the Wyoming report in the first place. This does not
    reopen the 2026-09-04 `state=CO` decision; it means choosing to leave the
    gap is now a choice between two available options.
13. **`report_sources` has DDL but no seed.** Nothing reads a confidence tier
    until there is ingested data to rate, so this is Phase 1 work — but it is
    the one table whose absence is invisible, because a `LEFT JOIN` against an
    empty lookup returns NULL tiers and the UI shows "unrated" rather than
    erroring.

Also open and blocked on RBI rather than on us: **DNS access and existing
subscription status**, needed for the Phase 5 sending identity. The ask starts
early because DNS changes at a small company can sit in an inbox for weeks.

---

## 12. Deliberate non-goals

Recorded so they are not re-litigated as oversights.

- No realtor deduplication beyond exact normalized email.
- No confidence score or percentage — a tiered label showing its inputs instead.
- No automatic sending, ever.
- No trimming of `iem_data`.
- No sixth reject reason. The enumeration is closed at five, and the friction of
  a migration is the point.
- No "currently being viewed" locking. Four people in one office talk to each
  other; `send_log` and the frequency cap prevent double-*sending*, and
  "last contacted" per row covers the case that matters.
- No builder fields. **Agent-website fields were also a non-goal and that half
  is superseded** — `listings.list_office_website` is retained as a brokerage
  snapshot (see the 2026-09-03 decision-log entry). Agent websites remain out
  of scope; they are trivially searchable.
- No listing-agent identity from RentCast beyond email — the API exposes none.
- No homeowner contact. Outreach goes to listing agents.

---

## 13. Working agreements

For any assistant contributing to this project:

- **Plan first, build second.** Say what you intend to do before doing it.
- **Do not build ahead of the current phase.**
- **Ask before installing anything not already present.** Prefer stdlib and
  boring dependencies.
- **Ingest scripts must be idempotent and safe to re-run.**
- **Failures should be loud.** Silent partial success is worse than an error —
  and so is a loud error naming the wrong cause. See the pipefail incident in §7.
- **Verify, do not assert.** The grants in `sql/010` were checked by connecting
  as each role and trying a forbidden statement, not by reading the file. Do the
  same for anything load-bearing.
- **Comment the *why*, not the *what*** — especially around the traps in §7.
- **Do not silently refactor working code, and do not add unrequested features.**
- **Justyn is teaching himself** Bash, Docker, Python, and Postgres as this is
  built. Explain the reasoning behind non-obvious choices briefly rather than
  producing finished code with no account of it.
- **New design decisions get appended to `docs/decision-log.md`** with a date.
  Old entries are never rewritten; a reversal is a new entry that supersedes.
