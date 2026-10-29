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

**Phase 5, email sending, is current.** A Constant Contact send path (admin-only `/send`
screens, an unsubscribe sync, a send queue and a bounce check) is built and rehearsed on the
`hail-dev` VM, but it has never sent to a real recipient, and the production box has none of it.
`CLAUDE.md` lists what is built and what is still needed before a production send, and
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
- **Apply the migrations** with `docker compose run --rm -T loader bash scripts/migrate.sh --apply`. On an
  empty database it applies every `sql/NNN_*.sql` file in order and records each in `schema_migrations`
  (item 277). The schema is not applied automatically; `docs/command-ref.md` (Migrations) has the rest.
- **Load the reference data** with `scripts/load_reference.sh`.
- **Load the service territory** with `scripts/load_coverage.sh`.

Only `ingest` bakes its code into the image, on purpose, so the nightly timers run a known
artifact: rebuild it after a change to anything it imports. `web`, `app` and `loader` bind-mount
the repo, so an edit is a deploy (`web` at its next restart; `docs/command-ref.md`).

## Tests

```bash
python3 -m unittest discover -s tests
```

The suite is stdlib `unittest`, about 2,350 tests (2026-10-28). Most need packages only the `web` and `app`
images have (`psycopg`, `jinja2`, `cryptography`) or the database, so the host run is a partial check: on
2026-10-28 it ran 2,350, skipped 1,842, and **ended with 24 errors in 10 modules** whose imports are not
guarded (see `docs/parking-lot.md` item 171). Run a file in the `web` container, where it is the real check
(the container sets `PGHOST` and connects as `hail_app`; each file's docstring gives its command):

```bash
docker compose run --rm --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_cc_tokens
```

**The full suite, with the `tests/js/` checks, runs in the `test` service** (item 222). It is built from the
`test` stage of `docker/app.Dockerfile`, the app image plus Node; `web` and `app` build `target: app`, so Node
never reaches the server. It bind-mounts `hailsys`, `scripts`, `tests`, `systemd` and `planning`, so rebuild it
only after a change to `requirements.txt` or the Dockerfile:

```bash
docker compose --profile test build test
docker compose --profile test run --rm -T test
```

On 2026-10-29: 2,623 tests, 380 skipped, OK. The tests that need `hail_admin` skip under `hail_app` and say so;
run them with the `hail_admin` command in `tests/test_log_guards.py`'s docstring. **Any error is a real failure.**

The web
routes that no test reaches are listed in item 53.
