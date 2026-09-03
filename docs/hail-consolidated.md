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
| Rules for AI assistants | `CLAUDE.md` |
| Actual DDL | `sql/00*.sql` |

Last synced against the repo: **2026-09-03**, commit `2b51e99` (drift
reconciliation plus the schema-review fixes; schema verified to build clean on
PostgreSQL 16 / PostGIS 3.4).

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

**Scale.** Single developer (Justyn). Single server. Four or five user accounts,
probably ever. ~135,856 storm report rows for ten years of Colorado.

---

## 2. Where the project actually stands

**Phase 0 — Groundwork. Nothing is running yet.**

Built so far:

- Repo structure, `.gitignore` (excludes `data/`, `reference/`, `*.csv`, shapefiles)
- Full written design: schema, decision log, data-source notes, phase plan
- Boundary data downloaded — TIGER 2025 national ZCTA and county shapefiles
  under `data/raw/tiger/`
- Ten years of Colorado LSR CSV pulled to `data/lsr_201601010000_202608312359.csv`
- Reference data derived: `reference/report_types.csv`, `sources.csv`,
  `qualifiers.csv`, `data_quality_notes.md` — statistical evidence, not load
  input. The curated seed the loader actually reads is
  `planning/report_types.csv`.
- `scripts/build_reference_tables.py`, `scripts/zcat-data-check.py`,
  `scripts/load_reference.sh`
- `sql/001`–`sql/008` — DDL for all fifteen tables. **Reviewed, fixed, and
  verified to build clean** on PostgreSQL 16 / PostGIS 3.4.
- `docker/loader.Dockerfile` — the loader image. Stock `postgis/postgis` plus
  the `postgis` client package, which is what carries `shp2pgsql`.
- Reference data loads: 37 report types, 33,791 ZCTAs, both at SRID 4326.
- **Phase 0's "done when" is met** — zip codes within 5 miles of an arbitrary
  lat/lon, 21 zips around the office point in ~22 ms.

Not built: the database itself, the IEM ingest, any web UI, any RentCast client,
any sending path. **Do not build ahead of the current phase.**

**Phase 0 is done when** a spatial query returns the zip codes within 5 miles of
an arbitrary lat/lon.

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
   points at a row that must still exist.

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
incurred only after a person has deliberately narrowed scope.

---

## 5. The data model

Fifteen tables. Full field-level detail is in `docs/database-schema.md`; an
ASCII ER diagram is in `docs/db-schema-diagram.md`.

### Weather side
| Table | What it holds |
|---|---|
| `report_types` | 37 rows. Meaning of a report type: magnitude unit, unit confidence, `roof_relevant`, and `min_magnitude` (the outreach floor; NULL means none). Composite PK `(report_type, report_text)`. `mag_unit` is nullable — NULL means "we do not know the unit", which is not the same as `none`. |
| `iem_data` | One row per NWS Local Storm Report. Exact lat/lon, generated `geom` (GiST indexed), UTC timestamp, magnitude, qualifier, remark. Natural key is `UNIQUE NULLS NOT DISTINCT` so null-magnitude rows deduplicate. The only table fed by an automatic job. |

### Reference
| Table | What it holds |
|---|---|
| `report_sources` | 36 rows. What a reporting source is and how far to trust it — `confidence_tier`, `is_automated`. **No FK from `iem_data`**: source is free text typed at NWS offices and an FK would break the nightly ingest. A lookup, joined on `report_source_norm`, never a constraint. |
| `zcta_boundaries` | ~33,000 Census ZCTA polygons, nationwide, EPSG 4326. `centroid` is generated with `ST_PointOnSurface`, not `ST_Centroid`, so it cannot fall outside a C-shaped zip. Loaded once, never written to. **No foreign keys** — joined spatially. |
| `coverage_zips` | RBI's service territory, 183 ZCTAs. Keyed on `zcta5` with an FK to `zcta_boundaries`. Ours, and it will be edited. |

### Property side
| Table | What it holds |
|---|---|
| `properties` | One row per physical house, PK `rentcast_id`. Only facts still true in five years (year built, yes; price, no). |
| `listings` | One row per *time a house was for sale*. Surrogate `listing_id`, natural key `(rentcast_id, list_date)`. Carries an agent **snapshot** plus `raw_payload` JSONB. Its `list_agent_email_norm` and `list_office_email_norm` are snapshots and neither is unique — many listings sharing one agent is the normal case. |
| `realtors` | Resolved agent identities, keyed on `email_norm` (UNIQUE). Also carries `office_email_norm`, **not** unique — a brokerage address is shared by every agent in the office. Exists for frequency capping and send history. |
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

### Key strategy

- **Surrogate `BIGINT` PKs** on every table we control: `iem_id`, `listing_id`,
  `realtor_id`, `match_id`, `send_id`, `template_id`, `emp_id`, `pull_id`,
  `api_log_id`, `dnc_id`.
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
  "admin" conventionally means "can do everything."
- **Whole-country boundary data, not Colorado-only.** The spatial index makes
  national scope free to query.
- **Legacy DNC lists are imported before any send**, marked
  `source = 'legacy_import'`, with `added_by` set to the system account's
  `emp_id`. Keeping the source distinguishable stops imported rows from drowning
  the bounce and complaint signal from new suppressions.
- **IP:** Justyn owns the code; RBI is licensed a running system on their
  hardware. Generic components live separately from RBI-specific config so the
  legal boundary follows a file boundary.

---

## 7. Known data traps

These have already bitten. Do not re-discover them.

### IEM
- **`MAG` contains the literal string `None`** as its null marker — 3,353 of
  135,856 rows. Coerced to 0 it produces 629 magnitude-zero flash floods and
  549 magnitude-zero tornadoes.
- **Units come from the type name, never the value range.** Range inference was
  actively wrong: tornado EF numbers read as inches, fog visibility as inches,
  heat index as mph.
- **`TYPECODE` is not unique.** Nine codes map to two texts each — `R` is both
  RAIN and HEAVY RAIN, `S` both SNOW and HEAVY SNOW. The key is
  `(report_type, report_text)`.
- **76 rows have unquoted commas inside `CITY`** (`BISON LAKE, GLENWOOD 15`),
  giving 17 fields instead of 16. Never split on commas — use a real CSV parser.
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

### Census TIGER
- **TIGER ships in NAD83 (EPSG 4269); IEM and RentCast are WGS84 (4326).**
  Reproject at load (`shp2pgsql -s 4269:4326`). Mixing them fails silently — the
  join runs, returns too few rows, and never errors.
- A shapefile is a **set**: `.shp`, `.dbf`, `.prj`, `.shx`. Extracting only the
  `.shp` fails.
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

---

## 8. External sources

| Source | Cost | Notes |
|---|---|---|
| **IEM Local Storm Reports** | Free, no key, no documented rate limit | Realtime GeoJSON/CSV endpoint for the nightly job (`hours=N`); archive endpoint back to 2003 for backfill and replay (`sts`/`ets`). Schema page: `https://mesonet.agron.iastate.edu/request/gis/lsrs.phtml` |
| **Census TIGER/Line 2025** | Free | National ZCTA (`tl_2025_us_zcta520.zip`, ~33k rows) and county (`tl_2025_us_county.zip`, ~3.2k rows) files. No state split exists for ZCTA. |
| **RentCast** | **Paid**, monthly lookup allowance | `GET /listings/sale`, paginated to 500, sorted by `lastSeenDate` desc. Docs: `https://developers.rentcast.io/reference/property-listings-schema` (append `.md` for markdown). |
| **Email provider** | TBD | Must *explicitly permit* outreach to non-opt-in recipients — several providers terminate for it. Needs bounce/complaint webhooks returning a matchable message id, plus throttling for warmup, on a separate sending subdomain. |

**Prior history worth knowing:** a contractor-built predecessor used Mailchimp
and led to blacklisting. Whether RBI's main domain took reputation damage is an
open Phase 0 question; if so, remediation is its own line item.

---

## 9. Stack and environment

- Dell OptiPlex on RBI's office network, running **Proxmox**
- **Rocky Linux** VM (plus a PBS VM for backup)
- **PostgreSQL + PostGIS**
- **Python** backend, stdlib and boring dependencies preferred
- Web UI reachable via **Cloudflare tunnel**
- Deployed with **Ansible** where practical
- Monitoring through an existing instance called **Irin**

---

## 10. Repo layout

```
CLAUDE.md                     rules for AI assistants — read first
README.md                     currently empty
docs/
  hail-consolidated.md        this file
  database-schema.md          field-level data model, 15 tables, open questions
  db-schema-diagram.md        ASCII ER diagram
  decision-log.md             dated, append-only; supersede, never rewrite
  data-sources.md             IEM / TIGER / RentCast endpoints and traps
  phases.md                   phases 0–7 with a "done when" for each
  command-ref.md              Justyn's own Docker/Postgres/type notes
sql/
  001_extensions.sql          postgis
  002_users.sql               users
  003_reference.sql           report_types, zcta_boundaries
  004_weather.sql             iem_data, coverage_zips
  005_property.sql            properties, listings, realtors, dnc_list
  006_matching.sql            storm_listing_matches
  007_sending.sql             send_log, email_templates
  008_operations.sql          api_pulls, api_call_log
scripts/
  build_reference_tables.py   derives reference CSVs from the raw LSR archive
  zcat-data-check.py          checks coverage zips against the TIGER .dbf
  load_reference.sh           idempotent loader: report_types CSV + ZCTA shapefile
reference/                    gitignored — derived statistical CSVs, DNC lists
docker/
  loader.Dockerfile           postgis image + the postgis client package
data/                         gitignored — raw LSR archive, TIGER shapefiles
planning/                     spreadsheets, coverage zip list, working notes
  report_types.csv            THE curated seed for report_types (gitignored)
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
   `email_templates` are append-only by convention and in code — no trigger, no
   rule, no `REVOKE`. Every other load-bearing rule in this project lives in the
   database; this one does not. **Deferred to Phase 5**, when the real update
   pattern is known.

Also open and blocked on RBI rather than on us: **DNS access and existing
subscription status**, needed for the Phase 5 sending identity. The ask starts
in Phase 0 because DNS changes at a small company can sit in an inbox for weeks.

---

## 12. Deliberate non-goals

Recorded so they are not re-litigated as oversights.

- No realtor deduplication beyond exact normalized email.
- No confidence score or percentage — a tiered label showing its inputs instead.
- No automatic sending, ever.
- No trimming of `iem_data`.
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
- **Do not build ahead of the current phase.** Phase 0 now.
- **Ask before installing anything not already present.** Prefer stdlib and
  boring dependencies.
- **Ingest scripts must be idempotent and safe to re-run.**
- **Failures should be loud.** Silent partial success is worse than an error.
- **Comment the *why*, not the *what*** — especially around the traps in §7.
- **Do not silently refactor working code, and do not add unrequested features.**
- **Justyn is teaching himself** Bash, Docker, Python, and Postgres as this is
  built. Explain the reasoning behind non-obvious choices briefly rather than
  producing finished code with no account of it.
- **New design decisions get appended to `docs/decision-log.md`** with a date.
  Old entries are never rewritten; a reversal is a new entry that supersedes.
