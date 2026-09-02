# CLAUDE.md

Project context for Claude Code. Read `database-schema.md` before touching the database.

## What this is

A storm outreach system for Roof Brokers, Inc. (RBI), a Front Range roofing
contractor. It pulls free NWS storm reports nightly, maps them to affected zip
codes, lets staff pull real estate listings in those areas from RentCast, and
send templated email to the listing agents.

Single developer. Single server. Currently in planning — nothing is built yet.

## Current phase

**Phase 0 — groundwork.** Machine setup, database design, boundary data.
The schema is designed but no tables exist.

Phases in order: 0 groundwork → 1 IEM ingest + zip mapping → 2 storm browser
with CSV export → 3 RentCast listings → 4 accounts → 5 email → 6 pilot →
7 rollout.

Do not build ahead of the current phase.

## Stack

- Rocky Linux VM on Proxmox, single Dell OptiPlex, on RBI's office network
- PostgreSQL + PostGIS
- Python backend
- Web UI, reachable via Cloudflare tunnel
- Deployed with Ansible where practical

## Non-negotiable rules

**Nothing sends email automatically.** There is no code path from the nightly
ingest to an outbound message. A human clicks send. Do not add scheduled sends,
auto-followups, or "helpful" automation around sending.

**The suppression check runs against `dnc_list` at send time, in the same
transaction as the send.** Not in the UI, not from a cached list, not from a
flag on the realtor row. If a rule must hold, it holds in the database.

**Storm reports are never deleted and never collapsed to zip codes at write
time.** Full lat/lon fidelity in, zips derived on read. The buffer radius is a
tuning parameter.

**`send_log` and `email_templates` are append-only.** Templates are superseded,
never edited. Sends are inserted, and only their provider status is updated
afterward.

**All timestamps are `TIMESTAMPTZ` stored in UTC.** Convert to `America/Denver`
at display only.

**RentCast calls cost money.** Never add a call to a code path that runs
automatically or on page load. Every pull is explicitly user-initiated and
logged to `api_pulls`.

## Known data traps

These have already bitten us. Do not re-discover them.

- IEM sends the **literal string `None`** as its null marker for magnitude.
  Coercing it to 0 produces 549 magnitude-zero tornadoes.
- IEM `TYPECODE` is **not unique** — `R` is both RAIN and HEAVY RAIN. Keys are
  `(report_type, report_text)`.
- Some IEM CSV rows have **unquoted commas inside the CITY field**. Never split
  on commas; use a real CSV parser.
- IEM `QUALIFIER` of `M` (measured) on hail **does not mean instrument-measured**
  — it tracks reporter training. Use `SOURCE` if a confidence signal is needed.
- Census TIGER ships in **NAD83 (4269)**; IEM and RentCast are **WGS84 (4326)**.
  Reproject at load. Mixing them fails silently.
- RentCast `id` is a **property** id, not a listing id. A relisted house reuses it.
- RentCast agent email is **frequently missing**. Handle null.

## Conventions

- Ask before installing anything not already present.
- Prefer stdlib and boring dependencies.
- Scripts that ingest external data must be idempotent and safe to re-run.
- Failures should be loud. Silent partial success is worse than an error.
- Comment the *why*, not the *what*, especially around the traps above.

## Working style

The user is teaching himself as this is built — Bash, Docker, Python, Postgres.
Explain reasoning briefly when making a non-obvious choice. Do not silently
refactor working code. Do not add features that were not asked for.

Plan first, build second.
