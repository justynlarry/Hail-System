# Hail System

A storm-outreach system for a Front Range (Colorado) roofing contractor.

1. Every night it pulls free NWS Local Storm Reports from the Iowa
   Environmental Mesonet and stores them at full lat/lon.
2. Staff browse that storm history and see which zip codes each report touched,
   within a configurable radius.
3. On an explicit click, it pulls real estate listings in those zips from
   RentCast, a paid API, and matches them to the reports.
4. A person reviews the matches before any templated email goes to the listing
   agents.

It reports a public record ("hail of this size was reported near this
listing"). It never claims damage, and nothing sends email automatically.

## Status

Phases 0–4 are closed:
- 0: groundwork
- 1: nightly ingest
- 2: the storm browser
- 3: RentCast listings and matching
- 4: accounts and roles

**Phase 5, email sending, is current**, and no sending path exists yet.
`docs/phases.md` has each phase's "done when" condition.

## Stack

PostgreSQL 16 + PostGIS 3.4 and a Python 3.12 backend: Flask with
server-rendered Jinja under gunicorn, and `psycopg`. It all runs in Docker
Compose on Rocky Linux, with nightly jobs scheduled by systemd timers. The web
UI is reached over Tailscale.

## Where to read

| For | Read |
|---|---|
| Rules for anyone (or any AI assistant) changing this code | `CLAUDE.md` |
| The whole picture in one document | `docs/hail-consolidated.md` |
| Why each choice was made | `docs/decision-log.md` (the authority when documents disagree) |
| The data model, field by field | `docs/database-schema.md` |
| External APIs and their traps | `docs/data-sources.md` |
| What's open, deferred or parked | `docs/parking-lot.md` |
| Building the server | `docs/server-setup.md` |
| Day-to-day Docker and Postgres commands | `docs/command-ref.md` |

## Running it

The short version is below. `docs/hail-consolidated.md` §10, "Running it", has
the full sequence and what each step is for.

```bash
cp .env.example .env     # fill in the passwords, FLASK_SECRET_KEY and RENTCAST_KEY
docker compose up -d     # starts postgis and web
```

After that:
- **Apply `sql/*.sql` in numeric order** through the `loader` service. The
  schema is not applied automatically.
- **Load the reference data** with `scripts/load_reference.sh`.
- **Load the service territory** with `scripts/load_coverage.sh`.

The `app` and `ingest` services bake their code into the image, so rebuild
them after a code change. `web` sees edits at its next restart
(`docs/command-ref.md`).

## Tests

```bash
python3 -m unittest discover -s tests
```

This runs 100 stdlib `unittest` cases, covering the ingest parser and the
magnitude formatter. Nothing under `hailsys/web/` has tests yet
(`docs/parking-lot.md` item 53).
