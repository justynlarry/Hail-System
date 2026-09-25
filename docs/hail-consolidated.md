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

Last synced against the repo: **2026-09-24**, commit `0f628f4`. The previous
sync was **2026-09-17** (commit `e22c02f`), at the start of Phase 3. In the
week between, **Phase 3 closed (2026-09-21) and Phase 4 closed (2026-09-24)**,
and this file was not touched once. The same drift the 2026-09-17 sync
recorded happened again, a full phase and a half this time: `decision-log.md`,
`parking-lot.md`, `phases.md` and `database-schema.md` were kept current as
the work landed, and this summary fell behind them. Nothing forces the files
to move together. **Treat this file as the last to update, never the first to
read for a recent fact**: for anything since 2026-09-17, check the decision
log.

What this sync adds: §2 gains Phase 3's close, Phase 4 (accounts, roles, the
admin page, CSRF, `match_runs`, re-pull, the RentCast quota and the storm-list
pagination), and the parked permits and jurisdiction research. §5 grows from
eighteen tables to twenty-three. §6 gains the Phase 3 and Phase 4 decisions,
condensed. §7 gains the RentCast response-handling trap and the
bind-mount-versus-baked-image trap. §9 notes that the web app has no logging
configuration. §10 and §11 are brought up to date. Where an older paragraph
below is now wrong, it is marked **Superseded** or **Update 2026-09-24**
inline, not rewritten, so the history of what was believed when stays
readable.

Everything below was verified against the **`hail-dev`** stack rather than read
off the source. Where a number appears — 177,523, 33,791, 3,235, 37,104 — it
came from a query on the sync date unless marked otherwise (2026-09-24 for
this sync; older sections keep the date they were written). A few facts are
per-machine deployment state rather than project state (the territory load,
most notably); those are marked inline.

**That claim needs a caveat, added 2026-09-14, still true.** Two paragraphs in
this file once said `coverage_zips` was empty on `hail-dev` in the same
regeneration that carried "Resolved since last sync: `coverage_zips` is no
longer empty" a few lines below them — a full regeneration that carried
stale paragraphs forward instead of catching them, because whatever produced
that version was working from the old text, not from the database. The table
in the "Authoritative file" list above tells you when the log wins over this
summary; it does not cover that case, because no file was wrong — only this
summary was, in a way only a live query against `hail-dev` could adjudicate.
**This sync repeats the same discipline for a new instance of the same risk:**
`docs/parking-lot.md` item 28 ("Vendor Leaflet into `static/` instead of the
CDN") was built earlier in this same session, before this file was
regenerated — `hailsys/web/static/leaflet.css`/`leaflet.js`/`images/` are
committed (`e22c02f`) and `base.html` points at them instead of `unpkg.com`.
Rather than let that sit as an unlogged fact the way the last sync's stale
paragraphs did, `decision-log.md` gained a dated entry for it and
`parking-lot.md` item 28 is now marked resolved, both later in this same
session — appending to those files, not rewriting their history, per their
own conventions. Numbers in this file that describe current database state
are a claim about that moment's regeneration, not a standing guarantee —
re-check against `hail-dev` before relying on one for anything that matters.

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
production OptiPlex (§9). Four or five user accounts, probably ever — **two
people accounts exist today**, `justyn` (`admin`) and `testview` (`viewer`,
kept for role testing), plus the non-login `system` account. **177,523 storm
report rows** on `hail-dev` as of 2026-09-24, 2004-01-26 to present — about 23
years of Colorado, not ten. **1,964 properties** from seven RentCast pulls
across 15 zips, 11 of them in El Paso County.

---

## 2. Where the project actually stands

**Phases 0 through 4 are closed. Phase 2 — the storm-browser web app — closed
2026-09-17; Phase 3 — RentCast listings — closed 2026-09-21; Phase 4 —
Accounts — closed 2026-09-24. Phase 5 — Email — is the current phase**, begun
2026-09-23 with nothing built yet: `send_log`, `email_templates` and
`dnc_list` exist from `sql/007` and are empty, and the legacy DNC import comes
before any send. See "Phase 4 — Accounts" below. Phase 3 delivered the sale-listings client,
the pre-pull estimate, a background-thread pull path, upserts into
`properties`/`listings`/`realtors`, storm-to-listing matching (automatic when a
pull finishes), the match page and the activity feed, all through the web UI,
plus `report_zip_distances` so storm queries no longer run a live spatial join.
Its done-when bar was exercised with a real pull on 2026-09-21: 4 requests
estimated, 4 used (`api_pulls` row 12), 231 listings stored and matched. No
sending path exists yet. See "The web app" below for what Phase 2 shipped,
"Phase 3 begins" for how Phase 3 started, and `docs/phases.md` and the decision
log for the rest.

**On "closed":** this is Justyn's call, recorded here, not a re-verification
against the phase's own "done when" bar. The gaps this file had flagged as of
the last check — no second real account has ever logged in, no CSRF
protection, county grouping never wired into the territory browse UI — were
still true at that check and are not claimed fixed by this entry. They carry
forward as open items against a closed phase, not as blockers reopening it.

**Update 2026-09-24:** of those three, CSRF protection now exists (Phase 4,
Flask-WTF, 2026-09-22), and county grouping is decided as deferred rather than
missed (decision log 2026-09-23, "Three Phase 2 outline items…"). Nobody other
than the developer has logged in yet. `testview` was signed in by Justyn, and
parking-lot item 63 (a path to `hail-dev` for anyone else) is still open.

### Infrastructure — verified on a running stack, 2026-09-08 through 2026-09-10

- **`docker-compose.yml`** defines four services on one network, `hailnet`:
  - `postgis` — stock `postgis/postgis:16-3.4`, the only long-running service.
    Named volume `pgdata`; `./sql` mounted read-only at `/sql`.
  - `ingest` — `python:3.12-slim` + `psycopg[binary]==3.2.3`. Behind the
    `tools` profile, so `docker compose up` does not start it.
  - `loader` — the postgis image plus the `postgis` client package for
    `shp2pgsql`. Repo bind-mounted read-only at `/repo`. Also `tools`.
  - `app` — added 2026-09-11. Same image shape as `ingest`
    (`python:3.12-slim` + `psycopg`, non-root), but connects as `hail_app`
    instead of `hail_ingest`, for read-only reporting/export scripts. `./output`
    bind-mounted at `/app/output` so exported files survive `--rm`. Also
    `tools`.
- **The schema is not auto-applied.** `./sql` is mounted at `/sql`, *not* at
  `/docker-entrypoint-initdb.d`, so `docker compose up` yields an empty
  database. Applying it is an explicit step, and order matters — `010` after
  `001`–`009`, whose tables it grants on, and before `012` and `017`, which
  grant to its roles; the numeric order of `sql/*.sql` satisfies both.
- **Reference load verified end to end:** 37 report types, 33,791 ZCTAs (527
  Colorado), all at SRID 4326. Re-run inserts 0 rows.

### Database roles — `sql/010_roles.sql`

Three Postgres login roles, unrelated to `users.role`, which is the
application's own login model enforced in Python:

| Role | Used by | Reach |
|---|---|---|
| `hail_admin` | `postgis` superuser, and the `loader` service | Everything. Runs DDL and provisioning. |
| `hail_ingest` | the `ingest` service | `SELECT` on `report_types`; `SELECT, INSERT` on `iem_data` and `iem_ingest_rejects`; `SELECT, INSERT, UPDATE` on `ingest_runs`. No `DELETE` anywhere. |
| `hail_app` | the future web UI, and (2026-09-11) the `app` Compose service | The three cost stages: read reference and weather, write property/matching/sending/operations. No `DELETE` anywhere. **It also reads `dnc_list`** (2026-09-24): the CSV exports `LEFT JOIN` it to flag or exclude suppressed agents. The grant was already in `010`, and it is a convenience, not the send-time check. |

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

### Ingest and territory — 2026-09-09 / 2026-09-10

- **`scripts/iem_backfill.py`** — the first ingest script. Imports
  `iem_parse.py`, writes the `ingest_runs` row before the fetch, rejects to
  `iem_ingest_rejects`, and pulls from the IEM archive **over HTTP** (there is no
  file-input mode). **It has run eight times on `hail-dev`** — runs 1–3 were
  window tests, runs 4–8 are the backfill proper, walking the window start back
  from 2021 to 2003:

  | run | window requested | seen | inserted | skipped |
  |---|---|--:|--:|--:|
  | 4 | 2021-01-01 → 2026-09-09 | 84,268 | 79,358 | 1 |
  | 5 | 2018-02-01 → 2018-05-01 | 4,526 | 4,364 | 27 |
  | 6 | 2018-01-01 → 2026-09-09 | 122,954 | 32,462 | 76 |
  | 7 | 2016-01-01 → 2026-09-09 | 135,909 | 12,569 | 76 |
  | 8 | 2003-01-01 → 2016-01-01 | 47,189 | 46,800 | 2 |

  Total in `iem_data`: **176,957 rows, 2004-01-26 → 2026-09-07** (2003 was the
  requested floor; no report exists before 2004-01-26). The overlapping windows
  on runs 6 and 7 insert nothing for the range already loaded — the `iem_data`
  natural key makes re-ingest idempotent, which is why "seen" so far exceeds
  "inserted" there. Run 4's single skip is the unquoted-comma `CITY` row from
  2026-08-31 that disproved the 2018 hypothesis. Run 8's two skips are the first
  `unknown_report_type` rejects against real data (§7). Rejects are **not**
  deduplicated across runs, so the ~76 malformed 2018 rows are re-rejected under
  each wide run's `run_id`; that is the design.

- **The archive floor is `2004-01-01`, moved there on 2026-09-10** (decision-log
  entry, superseding the 2026-09-04 fixed-`2021-01-01` decision). The *fixed
  date, not a rolling window* principle is unchanged — only the date moved, to
  the practical bottom of the IEM LSR archive for Colorado. The floor is a
  backfill/replay concept only: `ARCHIVE_FLOOR` in `iem_backfill.py` drives just
  the `below_archive_floor` warning, and the nightly job never reads it (rolling
  `recent=` window). Two rows below the old floor came into scope with the move:
  the 2019 `DEPT OF` truncated `SOURCE` value (open question 13), and the
  pre-2016 `unknown_report_type` rejects in §7.

- **`scripts/load_coverage.sh`** — loads one customer's territory into
  `coverage_zips`. Separate from `load_reference.sh` on purpose, and takes the
  zip list as an **argument** so a second installation needs no code change.
- **`coverage_zips` — loaded on `hail-dev`.** **183 rows**, confirmed by a
  live count against the database (`SELECT count(*) FROM coverage_zips WHERE
  removed_at IS NULL`), from the 193-entry `config/coverage_zips.txt` — the
  other 10 have no ZCTA polygon and are uninsertable by design (§7).
  `load_coverage.sh` reproduces this load. Before it has been run, a
  coverage-filtered `ST_DWithin` would return **0 rows with no error** — the
  inner join eliminates everything, the same silent-empty-join shape as the
  SRID trap — which is why `load_coverage.sh` itself refuses to run against
  an empty `zcta_boundaries`.
- **`planning/zip_city_names.csv`** — 37,104 USPS zip → city names, all states,
  supplying `area_name`. Static reference, deliberately not a pipeline.
- **`config/`** — directory holding per-customer configuration. The one file in
  it is `coverage_zips.txt`, moved from
  `planning/rbi-zip-code-coverage-area-list.txt`.

**The generic/specific split is now visible in the tree**, not just in comments:

| | `planning/` | `config/` |
|---|---|---|
| Holds | national seeds — `report_types.csv`, `zip_city_names.csv` | this customer's `coverage_zips.txt` |
| Loaded by | `load_reference.sh` | `load_coverage.sh` |
| Replaced for a second customer | never | entirely |

### Storm-zip export and the `app` service — 2026-09-11

- **`scripts/export_storm_zips.py`** — for one storm day, in `America/Denver`
  local time, filterable by `--type` (`report_text`) and `--radius` (default
  `DEFAULT_ZIP_RADIUS_MILES` from `tuning.py`), plus **`--format pairs|zips`
  (added 2026-09-14, default `pairs`)**. `pairs` is one row per
  **report-zip pair**; `zips` is the same query grouped by coverage zip —
  `report_count`, `nearest_miles`/`farthest_miles`, `first_report`/
  `last_report`, `max_magnitude`/`min_magnitude`, `sources`. Both projections
  share one join/filter core in `hailsys/queries/storms.py`: `iem_data` to
  `report_types` (composite key, `report_type` alone is not unique — §7),
  `report_sources` (`LEFT JOIN`, a source with no lookup row yields a `NULL`
  tier rather than dropping the report), `zcta_boundaries` via `ST_DWithin`,
  and `coverage_zips` (excluding retired rows). No magnitude floor — every
  report in range is exported, `NULL` magnitude included, so the triggering
  threshold can still be decided later. The script itself now holds no SQL —
  argument parsing, logging, and CSV writing only, connection acquired through
  `hailsys/db.py`. Writes CSV with `lineterminator='\n'` (§7) to `./output/`,
  filename now suffixed by format (`storm_zips_<date>_<type>_pairs.csv` /
  `..._zips.csv`) since an unsuffixed name stopped being unique the moment a
  second format existed. **This is half of the Phase 1 "done when" bar** in
  `phases.md` — "a spreadsheet of affected zip codes ... for a real storm from
  last month" — the other half being the nightly job running unattended for a
  week, which at the time of this note (2026-09-11) was not yet built. **It is
  now** — see "Nightly timer: the unattended week, checked" below.
- **It cannot run under the `ingest` service.** `hail_ingest` has `SELECT` only
  on `report_types` (plus its write path); `report_sources`, `zcta_boundaries`,
  and `coverage_zips` all belong to `hail_app`'s grants (`010_roles.sql`).
  Running the script as `hail_ingest` fails with
  `psycopg.errors.InsufficientPrivilege: permission denied for table
  report_sources` — correct behavior from the grants, not a bug, but a real gap:
  no Compose service was credentialed as `hail_app` yet.
- **New `app` service + `docker/app.Dockerfile`** close that gap: same shape as
  `ingest` (`python:3.12-slim`, `psycopg`, non-root `uid 1000`), `PGUSER:
  hail_app`, kept as its own service rather than folded into `ingest` so
  `hail_ingest` stays scoped tight to the nightly write path on purpose (see
  the `ingest` service comment in `docker-compose.yml`: "a bug here cannot
  reach `send_log` even by trying"). `./output:/app/output` is a bind mount, not
  a build-time `COPY`, because the CSV has to survive the container being
  removed (`docker compose run --rm`) — a build-time copy would still leave the
  file trapped in an ephemeral container filesystem. It works with no `chown`
  because the container's `app` user and the host's `hail-user` are both
  `uid 1000` — see §7.

### systemd units — 2026-09-11

Two timers installed and armed on `hail-dev`, `systemd/*.service` and
`systemd/*.timer` in the repo:

- **`iem_ingest.timer`** — `OnCalendar=*-*-* 10:00:00` UTC (`Persistent=true`),
  runs `iem_ingest.service`, which invokes `docker compose run --rm ingest
  python3 scripts/iem_ingest.py` as `hail-user`. `TimeoutStartSec=900`.
- **`iem_weekly_replay.timer`** — `OnCalendar=Sun *-*-* 11:00:00`, an hour
  after the nightly, so a replay never races an ingest run. Runs
  `iem_backfill.py --mode replay` over a rolling 30 days, dates computed at run
  time through `/bin/sh -c` (systemd does not expand `$(...)` itself).
  `TimeoutStartSec=1800`.

**Verified by running, not by reading:** a manual `systemctl start
iem_ingest.service` produced a real `run_id` in journald end to end
(`fetch_ok` → `complete`), proving `WorkingDirectory` finds `.env`, `hail-user`
reaches the Docker socket from a systemd context (not just a login shell), and
the compose healthcheck gate holds. A manual run of the replay
(`run_id=18`) produced `window_start=2026-08-12T00:00:00+00:00,
window_end=2026-09-12T00:00:00+00:00` — confirming both the `%%`-escaped
`date` arithmetic resolves correctly under systemd's specifier expansion, and
that `--end $(date -u -d tomorrow ...)` (exclusive) correctly includes today,
where `--end today` would have silently stopped at last midnight.

**Three install-time failures, each informative:**

- **SELinux (`init_t`) refused to read units symlinked from
  `/home/hail-user/hail-system/systemd/`** — `user_home_t` is not a label
  `systemd` (`init_t`) may read. Fixed by copying with `install -m 644` into
  `/etc/systemd/system/` instead of symlinking. A file *created* at that path
  picks up the directory's default context (`systemd_unit_file_t`)
  automatically; a symlink's target keeps the label of wherever it actually
  lives. **This means the repo and the installed copies can drift silently**
  — there is no enforced link between them, only a habit of re-copying after
  an edit.
- **A hyphen instead of an underscore in the timer's `Unit=` name**
  (`iem-ingest.service` vs. the installed `iem_ingest.service`) made systemd
  refuse to start the timer outright — *"Refusing to start, unit
  iem-ingest.service to trigger not loaded"* — rather than arming a timer that
  fires into nothing. Fixed in the repo copy.
- **The default `TimeoutStartSec` (90s) undercuts the ingest script's own retry
  budget.** `HTTP_TIMEOUT=120` and `HTTP_ATTEMPTS=3` in `iem_common.py`, with
  `HTTP_BACKOFF ** attempt` sleeps of 2s then 4s between attempts (not before
  the last one) — worst case is 3 × 120s + 2s + 4s ≈ 366s, roughly 6 minutes,
  not the ~14s of backoff alone. Set to 900 on the nightly and 1800 on the
  weekly replay (which can make several such fetches, chunked by month).
  Undersized, systemd would SIGTERM a still-retrying run and record a timeout
  instead of the real cause.

**Also observed, not previously written up:** the journal shows one
`iem_ingest.service` start (21:00:27–28, before `run_id=16`) logging
`Unknown key 'Wantedby' in section [Install], ignoring` — an earlier installed
copy had the `[Install]` directive miscapitalized (`Wantedby` vs. the required
`WantedBy`). systemd does not treat this as fatal, only ignores the key
silently, so the service still ran — but with no `[Install]` in effect, only
harmless for a unit that is timer-triggered rather than boot-enabled directly.
Self-corrected by the next re-copy; the repo and installed files now agree.
`iem_weekly_replay.service` is also missing the `Documentation=` line the
other three units carry — a completeness gap, not a functional one.

### Built before this sync, unchanged

- Full written design: schema, decision log, data-source notes, phase plan
- TIGER 2025 national ZCTA and county shapefiles under `data/raw/tiger/`
- Colorado LSR archive CSVs at `data/lsr_201601010000_202608312359.csv`
  (2016–2026) and `data/lsr_2003_2015.csv` — reference copies for inspection;
  `iem_backfill.py` pulls from the IEM archive over HTTP, not from these files
- Derived reference CSVs in `reference/` — statistical evidence, not load input
- `sql/001`–`009`: DDL for all seventeen tables
- `scripts/build_reference_tables.py`, `zcat-data-check.py`, `load_reference.sh`
- `scripts/iem_parse.py` — the shared row parser both ingest scripts will import
- **Initial `roof_relevant` set:** 12 of 37 types, 5 with magnitude floors
  (HAIL 1.00″, TSTM WND GST / NON-TSTM WND GST 58 mph, HIGH SUST WINDS 40 mph,
  HEAVY SNOW 6″). Against the 176,957-row backfill corpus that is **38,935 rows,
  22.0%** (was 24,124 / 17.8% against the old 135,856-row ten-year extract).
  `SNOW` is excluded and that single call still sets the scale: include it and
  the set is 143,497 rows, 81.1%.
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
- **`coverage_zips` is no longer empty**, and the silent 0-row join it caused is
  closed. See above.
- **The 10 unmatched coverage zips now have a second, independent explanation**
  from the USPS delivery file — and it corrected one label in the 2026-09-03
  decision entry. See §7.

### Not built, as of the 2026-09-14 sync

Any web UI, any RentCast client, and any sending path. **Superseded 2026-09-17
for the first of the three** — the web UI is now built; see "The web app"
below. RentCast and sending remain untouched, Phase 3 and Phase 5 work.

A **parser test suite now exists** — `tests/test_iem_parse.py`, 44 cases, run
with `python3 -m unittest discover`, all passing on 2026-09-10. It is pure
stdlib `unittest` against `iem_parse.py`: no runner and no test dependency added
(`requirements.txt` is still just `psycopg`). Each case is a §7 trap or a
contract the ingest depends on. This is the "whenever that becomes
phase-appropriate" the earlier sync anticipated — `iem_parse.py` takes its
`valid_types` as an argument, so the suite needs no fixtures and no database.

### Operational tooling and Phase 2 planning — 2026-09-11 through 2026-09-14

- **`scripts/status.sh`** — four read-only checks against the running stack
  (recent runs, stored row counts, reject reasons, nightly staleness) without
  opening `psql` by hand. Exit status doubles as the nightly-health verdict —
  0 if a nightly completed within `STALE_HOURS`, 1 if not — so it can be wired
  into a notifier later without being rewritten.
- **`docs/parking-lot.md`** committed 2026-09-14, closing a real time cost:
  before it existed, at least one finding was re-derived from scratch because
  it was read out of a derived summary rather than the decision log, where it
  had already been recorded four days earlier. Now 21 numbered items plus
  "Also worth carrying" (standing facts about the data — confidence-tier
  skew, thin-report days, dedup rate — not decisions) and "Not on the
  roadmap" (MRMS/MESH, triggered rather than scheduled).
- **Reconciled against `database-schema.md`'s 12 open questions**, same day:
  county (question 4) resolved by the TIGER-polygon decision below; role
  visibility (question 11) checked and confirmed still open; the other ten
  filed as parking-lot items 12–21. One of those ten — out-of-state reports —
  had a declarative source title that made a genuinely open question ("should
  ingest widen past `state=CO`?") read as an already-settled fact; it was
  refiled as a question and cross-linked to the decision-log entry that
  actually settled the adjacent, narrower point (§6).
- **Eight Phase 2 decisions recorded**: Tailscale access, USPS-city grouping,
  TIGER county, Flask, scrypt hashing, and the `hailsys/` package layout,
  plus a seventh the same day — the `hailsys/db.py` connection design — and
  an eighth, also the same day: pulling the storm query into
  `hailsys/queries/storms.py` behind `pairs`/`zips` projections, resolving
  **PL-06**. All eight are condensed in §6. Three of the eight did not stay
  decisions-on-paper: the `hailsys/` package layout (verified 2026-09-11),
  TIGER county (`sql/012_counties.sql` plus the `load_reference.sh` load,
  verified against `hail-dev` — 3,235 counties, resolving open question 4 /
  parking-lot item 4), and `db.py` together with `queries/storms.py`, which
  `export_storm_zips.py` now actually runs on. At the time of this note
  (2026-09-14), Tailscale access, USPS-city grouping, Flask, and scrypt
  hashing remained undecided-into-code. **All four are built now** — see
  "The web app" below, built 2026-09-14 through 2026-09-16.

### Nightly timer: the unattended week, checked — 2026-09-17

`systemctl status iem_ingest.timer`: `active (waiting)` continuously since
**2026-09-11 21:04:16 UTC**, no gaps, no restarts. `ingest_runs` shows an
automatic `10:00:00 UTC` `nightly` run, `run_status = complete`, on every one
of **2026-09-12, 13, 14, 15, 16, and 17** — six unattended days in a row. The
three extra rows on 2026-09-14 (`run_id` 22–24, all timestamped 00:04, minutes
apart) are not the timer misfiring — `journalctl` shows three manual
`systemctl start iem_ingest.service` invocations that day, matching the
already-recorded package-move verification (§6, "The repo becomes a
package"), whose last one is the `run_id=24` that entry names. `iem_weekly_
replay.timer` fired once on schedule (2026-09-13 11:00 UTC) and is next due
2026-09-20. **One more automatic firing — 2026-09-18 10:00 UTC — completes
the seven unattended days Phase 1's "done when" calls for.** This is checked
by running the actual commands above, not inferred from the timer file having
been installed a week ago.

### The web app — built, Phase 2 substantially underway — 2026-09-14 through 2026-09-16

Not decided-into-code anymore. `hailsys/web/` is a working Flask app, `web` is
a real `docker-compose.yml` service, and it has been reachable on `hail-dev`
since 2026-09-11.

- **Auth.** `hailsys/web/auth.py`: `hashlib.scrypt` (n=2¹⁵, r=8, p=1, 32-byte
  digest, per-hash-stored parameters, per the 2026-09-14 decision), a
  `login_required` decorator that redirects to `main.login`, and
  `verify_password` refusing outright on the literal `"!"` `password_hash`
  the `users` CHECK constraint uses to mark the `system` account
  unauthenticatable — an explicit refusal, not a parse error left to fall
  through. `scripts/create_user.py` is the only way to create one: a CLI,
  run as `hail_admin`, `getpass`-prompted twice with a 12-character floor,
  explicitly a **placeholder until Phase 4** builds a real admin UI. **Live
  on `hail-dev` today: two rows in `users`** — `system` (`role=system`,
  inactive, unauthenticatable by design) and `justyn` (`role=admin`, active,
  `last_login_at` populated). No second real account has been created.
  **Superseded 2026-09-22:** `/admin` now creates and manages accounts, and
  `create_user.py`'s future is parking-lot item 65. `users` holds three rows
  as of 2026-09-24, adding `testview` (`viewer`).
- **Routes**, all under `hailsys/web/views.py`, one blueprint (`main`):
  `/` (recent-storm-days browser — day range 30/90/365, report-type filter,
  an "actionable only" checkbox defaulting **on** unless the form was
  actually submitted), `/storms/zips` (lazy-loaded HTML fragment, one storm
  day's zip breakdown), `/territory` (`group_by=city|zip`, same filters,
  the map lives only in `city` mode), `/territory/days` (a city's day-by-day
  fragment, same lazy-fetch pattern), `/export.csv` (zip-level CSV,
  streamed from `fetch_zips`), `/map/points.geojson` (a `FeatureCollection`
  of report points, capped at 2,000 rows), plus `/login` and `/logout`.
  Every route but `/login` is `@login_required`. **Superseded 2026-09-22:**
  `/pull/estimate`, `/pull` and `/match` carry `@role_required("sender",
  "admin")` instead, which also redirects a signed-out visitor, and the
  `/admin` blueprint checks for an admin in one `before_request` hook. See
  "Phase 4 — Accounts" below.
- **The query core absorbed two cross-projection bugs after the 2026-09-14
  extraction, both instructive.** `_ACTIONABLE` was first written directly
  into `RECENT_DAYS_SQL` rather than the shared `_FROM_WHERE` — so expanding
  an "actionable only" day's zip detail silently ran unfiltered, answering a
  wider question than the list that produced it. And `ZIPS_SQL`'s original
  `GROUP BY c.zcta5, c.area_name` let a zip with both hail and wind in one
  window collapse into one row with `max(magnitude)` against whichever
  type's unit happened to sort first — the exact mixed-unit trap `report_
  types.mag_unit` exists to prevent, reintroduced one join downstream of it.
  Both are fixed: `_ACTIONABLE` is now its own interpolated fragment beside
  `_FROM_WHERE` that every projection needing it includes explicitly, and
  `ZIPS_SQL`/`CITIES_SQL` group by `(report_text, mag_unit)` too. **The
  generalization, not just the fix:** a shared query core only closes the
  disagreement it was built to close; a rule added later that only some
  projections apply has to be added to the shared seam deliberately; it does
  not inherit protection just for being adjacent to one. `hailsys/queries/
  storms.py` now holds six SQL strings — `PAIRS_SQL`, `ZIPS_SQL`,
  `RECENT_DAYS_SQL`, `CITIES_SQL`, `CITY_DAYS_SQL`, `REPORT_POINTS_SQL` — all
  built on the same `_FROM_WHERE` / `DISTANCE_EXPR` / `LOCAL_TIME_EXPR`.
  `PAIRS_SQL` still omits `_ACTIONABLE` on purpose — one consumer, nothing yet
  to disagree with it.
- **The map** (`static/map.js`, on `/territory?group_by=city`): Leaflet, no
  tile layer — the 183 coverage polygons serve as the basemap, loaded from a
  **pre-generated static fixture**, `static/coverage.geojson`
  (`scripts/build_coverage_geojson.py`, `ST_SimplifyPreserveTopology` at
  0.0005°, run by eye, cutting 4.18 MB to 418 kB; simplification is
  display-only and never touches `zcta_boundaries.geom` in any matching
  query). Report points and their 5-mile rings come from `/map/points.
  geojson`; points are colored by `report_source_norm`, never raw
  `report_source` (free text, inconsistent case — the same normalization
  discipline the `report_sources` join has always required), though the
  tooltip shows the human-readable raw value. Rings are `L.circle` (true
  metres, correct at every zoom) marked `interactive: false` so they never
  steal a hover from a point drawn on top of them.
- **CSV export** (`/export.csv`) reuses `fetch_zips` — the same query behind
  territory's zip-grouped view, not the day list — because a zip list is
  what a planner actually takes to RentCast next, and reads its filters from
  `request.query_string` rather than re-deriving them from form fields, so
  the download can never show different filters than the page it was
  clicked from.
- **A NEXRAD radar-verification study** (`docs/analysis/radar-verification-
  2026-09.md` plus five scripts under `docs/analysis/radar-verification-2026-
  09/`) checked 2,538 coverage-area hail reports against ten years of NCEI
  SWDI Level-III hail detections — independent of the LSR network — and
  changed what the UI shows as a direct result: **`confidence_tier` is not
  displayed anywhere.** `PUBLIC` reports corroborate at 93.5%, `TRAINED
  SPOTTER` at 92.4% — the premise that `PUBLIC` (roughly half the archive)
  is the weak input runs the wrong way, and a tier label next to a report
  would make a sender discount evidence the data says is just as good.
  Lone-report days (382 of 1,468 Denver-local hail days carry exactly one
  report — §7) do not corroborate worse either: 97.1% at n=69 for one-report
  days versus 94.3–94.7% for busier ones, no real gradient. Radar-estimated
  `MAXSIZE` was not adopted as a severity signal (r = 0.24–0.38 against
  reported magnitude). And the tolerance sweep found 2 miles sits below the
  two datasets' joint positional resolution, which is why the existing
  5-mile buffer (`tuning.py`) is the right scale to have verified against.
  Full reasoning and the corrected UTC-vs-local-day exclusion bug are in §6.
- **`web` in `docker-compose.yml`**: `docker/app.Dockerfile` image, `gunicorn
  --bind 0.0.0.0:8000 --workers 2 hailsys.wsgi:app`, bound to
  `127.0.0.1:8000` only — nothing reaches a real interface, per the Tailscale
  decision — with `./hailsys:/app/hailsys:ro` bind-mounted for dev so an edit
  is live without a rebuild (parking-lot item 30). `requirements.txt` gained
  `flask==3.1.3` and `gunicorn`. The `app` (export) service separately picked
  up a bind mount of `./hailsys/web/static`, for the same reason `./output`
  was mounted in the 2026-09-11 sync: `build_coverage_geojson.py` writes into
  that directory, and without the mount the file would land inside a
  container filesystem that `--rm` deletes.
- **Access for a demo, 2026-09-16: Tailscale Funnel, and it has since been
  turned back off.** `tailscale serve` (the Phase 2 mechanism) is tailnet-only;
  Funnel made the same hostname briefly reachable over public TLS so a
  non-technical viewer could see it without installing Tailscale. **Checked
  today, not assumed:** `tailscale funnel status` and `tailscale serve
  status` both currently report `(tailnet only)` — Funnel is off, as the
  decision-log entry said it would be reverted. While it was on, the
  application's own login was briefly the *only* gate between the internet
  and the system, which the same entry flags as raising the priority of
  CSRF protection.

**What this sync found disagreeing with itself.** Two of the five below were
fixed in the same session, in the file where each actually belongs, rather
than papered over here; three remain open and are left for a person to
decide, not silently patched:

- **No CSRF protection exists anywhere in the app** — `grep -ri csrf hailsys/`
  returns nothing but the decision-log sentence that raised its priority.
  `/logout` is a bare POST with no token, and the Funnel window made this a
  live, if brief, exposure rather than a theoretical one. **Left open** —
  a real fix, not a doc fix. **Update 2026-09-24: fixed** — `CSRFProtect` on
  every POST since 2026-09-22, failures returning a 400 page since 2026-09-23.
- **Phase 2's own "done when" — "someone *other than the developer* can log
  in ... and download it as a spreadsheet" — was not independently
  demonstrated before the phase was called closed (2026-09-17, Justyn's
  call, see §2 top).** The mechanism exists; only one real account
  (`justyn`, the developer) had ever been created as of the last check. The
  Funnel demo let someone *view* the app, not necessarily log into it as
  themselves. **Left open** — carried forward as a real gap against a closed
  phase, not reopened by closing it.
- **County grouping was never wired into the UI**, though `county_boundaries`
  has existed and been loaded since 2026-09-14. `territory()`'s `GROUP_BYS`
  is `("zip", "city")` only — `phases.md`'s Phase 2 checklist ("group by
  city, county, **or zip**") is still one option short of what it describes.
  **Left open** — a real feature, not a doc fix. **Update 2026-09-24:**
  recorded as deliberately deferred, not missed. The browse was scoped to
  questions that don't include county (decision log 2026-09-23, "Three Phase 2
  outline items, settled in scoping and recorded late").
- **`phases.md` named a Cloudflare tunnel under the Phase 2 checklist**, three
  days after the 2026-09-14 decision (Tailscale over Cloudflare) updated
  `CLAUDE.md` and `server-setup.md` but not this one. **Fixed this session**
  — the bullet now names Tailscale and points at the decision-log entry.
- **Zero test coverage for anything under `hailsys/web/`.** Of the 100 tests,
  88 exercise `hailsys/iem/*` — the ingest/parse side — and 12
  (`tests/test_formatting.py`, 2026-09-22) exercise `hailsys/formatting.py`,
  the magnitude formatter, which sits outside `hailsys/web/`. `auth.py`,
  `views.py`, `queries/storms.py`, and the filter's registration in
  `create_app()` — including the two cross-projection bugs fixed by hand in
  an earlier phase — have no automated coverage. **Left open** — writing tests
  is real work, not a doc fix. Tracked as parking-lot item 53.
- **Parking-lot item 28 (vendor Leaflet locally) was built earlier in this
  same session** (`e22c02f`), before `decision-log.md` or `parking-lot.md`
  had a word to say about it. **Fixed this session** — a dated decision-log
  entry now exists and parking-lot item 28 is marked resolved, both by
  appending, per each file's own convention, not by rewriting history.
- **`database-schema.md` said "Seventeen tables" and had no field-level entry
  for `county_boundaries`** (§5). **Fixed this session** — the header now
  says eighteen and `county_boundaries` has its own table section.

### Phase 3 begins — 2026-09-17

`hailsys/rentcast/` exists: `client.py` (sale-listings search — pagination,
throttling to RentCast's 20 req/sec, and error classification into
`RentCastAuthError` / `RentCastValidationError` / `RentCastServerError` /
`RentCastConnectionError`), `estimate.py` (pre-pull cost estimate — zip count
and a projected call count, built on `hailsys.queries.storms.fetch_zips` and
`api_call_log` history), and `sql/013_pull_storm_link.sql` (additive: gives
`api_pulls` a nullable `storm_date`/`report_text` pair, `CHECK`-enforced
both-or-neither, so a pull can be traced back to the storm day it was pulled
for). Nothing calls any of this yet — no pull orchestration, no UI, no
`api_pulls`/`api_call_log` writes.

**Both `client.py` and `estimate.py` shipped broken and were fixed over
several review passes, not written correct the first time — worth recording
as a pattern, not just a result.** `client.py`'s first draft had an
unterminated string that kept the whole module from parsing, half a dozen
misspelled names that would have raised `NameError` on first real use, and
401/403/400/405 all routing to the wrong exception class despite the correct
classes already existing with correct messages — see decision-log-style
detail in the commit (`420717a`), though **no dated decision-log entry
covers this file**, only the commit message; the Phase 3 build is ahead of
its own paper trail the same way Phase 2's build got two days ahead of
`decision-log.md`/`parking-lot.md` in September. `estimate.py`'s first draft
didn't parse at all — a duplicate-parameter signature with no closing colon,
and a body that was never actually indented into the function, so the one
line that mattered (the call to `fetch_zips`) never ran. Both are fixed and
verified (parse, import, and for `client.py`, each exception's `user_message`
checked directly) as of this sync.

**`sql/013_pull_storm_link.sql` applied to `hail-dev` ahead of being
committed**, during review — verified correct (matches the file exactly,
checked against `\d api_pulls`) but worth knowing if `hail-dev`'s schema is
ever diffed against a fresh apply of `sql/*.sql` in commit order before this
file lands in git. **One real finding from that test:** the file's last
statement has no trailing `;` — harmless when applied the normal way
(`psql -f` treats EOF as an implicit terminator, confirmed), but every other
file in `sql/` terminates its last statement explicitly. Also unresolved:
no `BEGIN;`/`COMMIT;` wrapping the four statements (a convention `001`–`009`
and `011` observed and `012` dropped without a recorded reason, and `013`
follows `012`), and no FK-level tie from `report_text` to `report_types` —
consistent with how the web app already treats `report_text` as a
free-standing filter value (§2, "The web app"), not a new gap this file
introduces.

### Phase 3 closes — 2026-09-21

Pull orchestration (`hailsys/rentcast/pull.py`), upserts into
`properties`/`listings`/`realtors` (`upsert.py`), a background-thread pull path
(`hailsys/web/jobs.py`), storm-to-listing matching (`hailsys/matching/matcher.py`,
run automatically when a pull finishes and by the Match button), the match page
(`/storms/matches`), derived work state per storm day
(`hailsys/queries/workstate.py`), the activity feed, and `report_zip_distances`
(`sql/017`), which precomputes report-to-zip distances so storm queries stop
running a live spatial join. On a 2019-wide range, the storm list went from
about 141 s to under 1 s. The done-when was a real pull on 2026-09-21:
4 requests estimated, 4 used (`api_pulls` row 12), 231 listings stored and
matched.

### Phase 4 — Accounts — 2026-09-22 through 2026-09-24

- **Roles enforced on the server.** `viewer` browses and exports; `sender` adds
  the pull estimate, the pull and Match; `admin` is **everything a sender can
  do plus users and settings** (the 2026-09-01 "admin manages users and nothing
  else" rule was reversed 2026-09-22). `role_required` guards the three paid
  routes, and the UI greys what a role can't do rather than hiding it.
- **`/admin`** (its own blueprint, one admin check for every route on it):
  - **Users:** add, change role, sign out, deactivate, reactivate, reset
    password. Never on your own row, refused on the server since 2026-09-23.
  - **Settings:** the two radii, the RentCast billing day and monthly quota,
    this billing period's usage, and the change history.
- **Forced logout by timestamp** (`sql/018`): a per-user and a system-wide
  `*_sessions_invalidated_at`, compared against the session's `issued_at` on
  every request. **Last-admin protection** (`sql/019`) is a deferred
  constraint trigger. **Radii** moved from `tuning.py` into `settings`
  (`sql/020`), read per request. Self-service **change-password**.
- **CSRF** on every POST (Flask-WTF, 2026-09-22); a failure renders a 400 page
  (2026-09-23).
- **`match_runs`** (`sql/022`) records every match attempt, so an empty match
  on a pulled storm reads "Matched, none in range" instead of looking like it
  never ran (item 40). **Re-pull** ("Pull again", greyed past the 365-day claim
  window) goes through `/pull/estimate` (item 46).
- **RentCast quota** (`sql/023`, `hailsys/queries/quota.py`): billing day and
  quota in `settings`, usage summed from `api_call_log` per billing period
  (rolled over at Denver midnight), shown on the admin page, the storm list for
  senders and admins, and the pull estimate. **Warn and allow**, not a hard
  block (item 50).
- **Closing follow-ups, 2026-09-24:**
  - Unreadable RentCast responses now fail loudly with an attempt count.
  - Every aborted zip's calls reach `api_call_log` (item 88).
  - The storm list pages at 50 storm days (item 86).
  - UI fixes: the login page, header wrapping, the admin table.
- **Done-when verified:** signed in as `testview`, Pull is greyed; and with a
  **valid CSRF token** a viewer session gets 403 on `/pull`, `/pull/estimate`,
  `/match` and `/admin/`, and 200 on `/` and `/export.csv`. Without the token
  the POST would fail CSRF first and the 403 would prove nothing.

### Exports, match-page layout and responsive CSS — 2026-09-24 / 2026-09-25

Phase 5 groundwork: nothing here sends. Decisions are in the decision log
under the same dates.

- **CSV exports** (`hailsys/queries/exports.py`, `28a0195`), all from
  `storm_listing_matches` and `realtors`, all excluding suppressed agents by
  default and flagging them on request (`?dnc=include`):
  - `/storms/matches.csv`: one storm day, linked from the match page.
  - `/exports`: a page with a date range, a report type and the Suppressed
    choice; **`/exports/matches.csv`** is the bulk export, one row per listing
    per local storm day and type; **`/exports/realtors.csv`** is the realtor
    list, not deduplicated, and **sender/admin only**. The match exports are
    open to any signed-in role.
  - Every filename carries the export date, because a CSV is a snapshot:
    someone suppressed later is still in the file. **The send-time check is
    still the only real protection.** An explicit date range is capped at 400
    days (2026-09-25). Timed then at under half a second and about 12 MB on
    today's small dataset, and accepted; reopen above roughly 2 s or 10,000
    rows (item 49).
  - `hail_app` reads `dnc_list` for this; the grant already existed.
- **The match page** (`d1b08c5`) has fixed, positional column widths so every
  agent group lines up (item 108), and is condensed. It scrolls sideways on a
  phone (item 109).
- **The activity panel** (`110bd8a`) shows pulls and match runs side by side
  in collapsible `<details>` sections, each line stamped in Denver time.
- **Responsive CSS** (`6ae56af`, `2060328`, branch `responsive-css`): a real
  bug, tables 1.5rem wider than the viewport at every width, fixed; the
  territory split, admin settings and filter bars reflow; phone tap targets.
  **Written with no browser available**, and no specific check is recorded as
  passed (item 114).

### Permits and jurisdiction research — parked, 2026-09-23 / 2026-09-24

Groundwork for building-permit data as a future source, allowed as a
deliberate exception to "do not build ahead". `municipal_boundaries`
(`sql/021`, DOLA's dissolved layer, 274 municipalities) is loaded. Permits
themselves are **parked until the system is running**. The findings are in
`docs/data-sources.md` §5 and the decision log (2026-09-23):
- **82 permit issuers** cover the territory.
- **The Pikes Peak Regional Building Department** is the only regional one.
- **The claim rule:** permit data never becomes "your roof is X years old".

A viability grading of all 82 is in `data/research/permit_viability_2026-09-24/`,
which is gitignored: 3 A, 7 B, 59 C, 13 D.

### Still open against closed phases, as of this sync

- **Phase 2:** nobody other than the developer has logged in (item 63).
- **Nothing under `hailsys/web/` has tests** (item 53).
- **Logging is unconfigured in the web app**, so every `logger.info` is
  dropped. Warnings and errors still reach the container log (item 99, §9).
- **No stale-pull sweep** after a restart mid-pull (item 47). None is stuck
  today.
- **RentCast's billing boundary hour** can't be checked until after
  2026-10-09 (item 87).

**Do not build ahead of the current phase.**

**Phase 1 is done when** a spreadsheet of affected zip codes can be produced for
a real storm from last month, and the nightly job has run unattended for a week.
**Both halves are met.** `export_storm_zips.py` and the web app's `/export.csv`
produce the spreadsheet. The nightly timer has run automatically every day
since 2026-09-12; the most recent `complete` nightly run was 2026-09-24
10:00 UTC.

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

**Twenty-three tables** as of 2026-09-24, counted against `hail-dev`. That's
seventeen from `sql/001`–`009`, plus `county_boundaries` (`sql/012`,
2026-09-14), `report_zip_distances` (`sql/017`, 2026-09-21), `settings`
(`sql/018`) and `settings_history` (`sql/020`, both 2026-09-22),
`municipal_boundaries` (`sql/021`) and `match_runs` (`sql/022`, both
2026-09-23). **The rest of this paragraph is from the 2026-09-14 sync:**
eighteen tables at the time. **Drift found this sync, fixed as part
of it:** `database-schema.md` said "Seventeen tables" and had no field-level
entry for `county_boundaries` at all — only prose under its open-question 4.
Both fixed today: the header now says eighteen, and `county_boundaries` has
its own `### county_boundaries` section, same shape as `zcta_boundaries`.
Full field-level detail is in `docs/database-schema.md`; an ASCII ER diagram
is in `docs/db-schema-diagram.md`.

### Weather side
| Table | What it holds |
|---|---|
| `report_types` | 37 rows. Meaning of a report type: magnitude unit, unit confidence, `roof_relevant`, and `min_magnitude` (the outreach floor; NULL means none). Composite PK `(report_type, report_text)`. `mag_unit` is nullable — NULL means "we do not know the unit", which is not the same as `none`. |
| `report_zip_distances` | Every ZCTA within 10 miles of every report, nationwide, not just coverage zips, so adding a coverage zip later needs no recompute. Nearest-edge distance in metres. PK `(iem_id, zcta5)`. **Derived**, kept current by an `AFTER INSERT` trigger on `iem_data`, so storm queries join it instead of running a live spatial join. Added `sql/017`, 2026-09-21; about 2.26 M rows. |
| `iem_data` | One row per NWS Local Storm Report. Exact lat/lon, generated `geom` (GiST indexed), UTC timestamp, magnitude, qualifier, remark. Natural key is `UNIQUE NULLS NOT DISTINCT` so null-magnitude rows deduplicate. The only table fed by an automatic job. |

### Reference
| Table | What it holds |
|---|---|
| `report_sources` | **Loaded on `hail-dev`: 49 rows** as of 2026-09-24 (18 high / 21 moderate / 8 low / 2 unrated / 5 unknown_automation; 0 unmatched against `iem_data.report_source_norm`). The two added 2026-09-23 are GJT's new 17-character spellings of long-standing sources, from the curated seed `planning/report_sources.csv`. What a reporting source is and how far to trust it — `confidence_tier`, `is_automated`. **No FK from `iem_data`**: source is free text typed at NWS offices and an FK would break the nightly ingest. A lookup, joined on `report_source_norm`, never a constraint. |
| `zcta_boundaries` | 33,791 Census ZCTA polygons, nationwide, EPSG 4326. `centroid` is generated with `ST_PointOnSurface`, not `ST_Centroid`, so it cannot fall outside a C-shaped zip. Two GiST indexes, one on `geom` and one on `(geom::geography)` — see §2. Loaded once, never written to. **No foreign keys** — joined spatially. |
| `coverage_zips` | RBI's service territory. **Loaded on `hail-dev`: 183 rows** from the 193-entry `config/coverage_zips.txt` — the other 10 have no ZCTA polygon and are uninsertable by design (§7). `load_coverage.sh` reproduces the load. Keyed on `zcta5` with an FK to `zcta_boundaries`. `area_name` comes from USPS; `reason` is deliberately left NULL by the loader. Ours, and it will be edited — retirement is a marked row, never a delete. |
| `county_boundaries` | 3,235 Census TIGER county polygons, nationwide, EPSG 4326, GiST-indexed the same way as `zcta_boundaries`. **Loaded on `hail-dev`: 3,235 rows.** Added `sql/012_counties.sql`, 2026-09-14 — an authoritative zip→county crosswalk across all 22 years of the archive, resolving database-schema.md open question 4 / parking-lot item 4. **Not yet surfaced anywhere in the web UI** — the territory browse's `group_by` only offers `zip` and `city` (§2), deliberately deferred (decision log 2026-09-23). No foreign keys — joined spatially, like `zcta_boundaries`. |
| `municipal_boundaries` | 274 Colorado municipalities from DOLA's dissolved layer, EPSG 4326, keyed on the Census place code. Replaced wholesale on reload, since an annexation must replace the old boundary. Added `sql/021`, 2026-09-23, for the parked permits work; not read by any query in the app. Hudson appears under two codes, a known source error. |

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
| `match_runs` | One row per match attempt, written `running` before the work and finished `complete` or `failed`, so a run that matched nothing is a record instead of an absence. `matches_created` counts **new** rows only. Added `sql/022`, 2026-09-23. |

### Sending
| Table | What it holds |
|---|---|
| `send_log` | One row per email to one agent. Snapshots `recipient_email`. `send_status` runs `queued → sent → (bounced\|complained)` or `queued → failed`; `queued_at` is set before the attempt, `sent_at` stays null until the provider accepts. Append-only except provider status. |
| `email_templates` | Versioned message text. Self-FK `supersedes_id`. Never edited in place. |

### Operations
| Table | What it holds |
|---|---|
| `users` | Logins. Roles `admin` / `sender` / `viewer`, plus `system` — a non-login account, bootstrapped in `sql/002`, that owns machine-initiated rows (automatic bounce and complaint suppressions, the legacy DNC import). Constrained in the database so it cannot be activated or given a real password. Unlike the other three it is **not a cost stage**. **Live on `hail-dev`: 3 rows** as of 2026-09-24 — `system`, `justyn` (`admin`) and `testview` (`viewer`, kept for role testing). `sessions_invalidated_at` (`sql/018`) forces one user out; last-admin protection (`sql/019`) refuses leaving zero active admins. Accounts are managed on `/admin`. |
| `settings` | Single row (`sql/018`): the system-wide sign-out timestamp, both radii (`sql/020`), and the RentCast billing day and monthly quota (`sql/023`). Read per request, never cached. |
| `settings_history` | One row per change to the radii or quota columns, written only by trigger, attributed through a transaction-local `app.current_emp_id`. Added `sql/020`; the quota columns `sql/023`. |
| `api_pulls` | One row per user-initiated RentCast pull. Records `estimated_api_calls` vs. `actual_api_calls` side by side; `api_status` tracks the run. `iem_id` is nullable — a pull need not be tied to one storm. |
| `api_call_log` | One row per zip within a pull. Powers the "this zip was pulled recently" warning, and since 2026-09-23 **the RentCast usage figure**: every zip that makes a request gets a row, including one that ends the pull. A NULL `http_status` means no usable status was ever received. |
| `ingest_runs` | One row per execution of an IEM ingest script (`nightly` / `backfill` / `replay`). Records the UTC window actually requested plus `rows_seen` / `rows_inserted` / `rows_skipped`. No `emp_id` — system-initiated. Written before the work starts, like `api_pulls`. **The alert that matters is the absence of a row**, which is why it is a table and not log output. |
| `iem_ingest_rejects` | One row per input line the parser refused. FK → `ingest_runs`. `raw_row` holds the line verbatim (TEXT, not JSONB — it is here because it did not parse), so rejecting is not lossy. `reason` is a closed **five**-value CHECK; anything outside it must terminate the run rather than be skipped. |

### Key strategy

- **Surrogate `BIGINT` PKs** on every table we control: `iem_id`, `listing_id`,
  `realtor_id`, `match_id`, `send_id`, `template_id`, `emp_id`, `pull_id`,
  `api_log_id`, `dnc_id`, `run_id`, `reject_id`, and since Phase 4
  `history_id` and `match_run_id`.
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
  **Superseded 2026-09-22:** admin is now a full superset of sender. The
  person who administers the system is the one who pulls and will send, and
  two accounts for one human is ceremony, not a control.
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

**Phase 2 decisions, recorded 2026-09-14 — all eight are now built** (§2, "The
web app"), not just the three (`hailsys/` layout, TIGER county, `db.py`/
`queries/storms.py`) already built at the time these were first written up:

- **Phase 2 UI is reached over Tailscale, not a Cloudflare tunnel.** The web
  container publishes to `127.0.0.1:8000` only; `tailscale serve --bg 8000` on
  `hail-dev` fronts it. Costs nothing to set up because the entire Phase 2 user
  base is one person already on the tailnet. **`server-setup.md` and
  `CLAUDE.md` updated to match, 2026-09-14** — the former's Firewall section
  now explains the loopback bind and `tailscale serve` rather than the old
  Cloudflare-tunnel reasoning, and the latter's Stack bullet says Tailscale
  instead of Cloudflare tunnel. **Revisit at Phase 6**: Cloudflare Access is
  *less* client-side work for staff (a browser and an email code) and does
  not need RBI's DNS, reversing the assumption that the tunnel is the heavier
  option.
- **"City" in the UI means the USPS city of an affected zip** —
  `coverage_zips.area_name`, a property of the zip in range, not of the report.
- **County comes from TIGER county polygons**, not free text or UGC. A new
  `county_boundaries` table, loaded the same way as the ZCTA load, gives an
  authoritative zip→county crosswalk across all 22 years, which
  `iem_data.county` (case variants) and `nws_geo_code` (null before mid-2022)
  cannot. **Resolves** parking-lot item 4 / open question 4. **Built and
  verified, 2026-09-14** — `sql/012_counties.sql` (additive; `004_weather.sql`
  is frozen post-backfill) adds the table, `load_reference.sh` loads it by
  the same reproject/stage/merge pattern as `zcta_boundaries`, and a run
  against `hail-dev` landed 3,235 counties, all geometry at SRID 4326.
- **Flask with server-rendered Jinja, not FastAPI.** None of FastAPI's three
  advantages — async concurrency, pydantic validation, generated API docs —
  apply: there is no async workload, form handling is a template concern here,
  and there is no third-party API consumer.
- **Password hashing via `hashlib.scrypt`; `SECRET_KEY` joins `.env`.**
  Memory-hard and stdlib, so Phase 2 adds no dependency. The hash column stores
  parameters alongside the digest so raising them later does not require a
  password reset for every user. **`database-schema.md` updated to match,
  2026-09-14** — the `password_hash` row now names scrypt instead of bcrypt
  or argon2.
- **The repo becomes a package.** `hailsys/` holds importable code;
  `scripts/` keeps every existing filename as a thin entrypoint, because the
  systemd units invoke `scripts/iem_ingest.py` by path and a move that does
  not touch them cannot break the nightly. **Done and verified 2026-09-14**
  for the ingest side — `hailsys/tuning.py` and `hailsys/iem/{common,parse}.py`
  exist, `scripts/{iem_backfill,iem_ingest,export_storm_zips}.py` import from
  them, and the move was verified per the order below: baseline export CSV
  captured first, `ingest.Dockerfile` and `app.Dockerfile` updated (`COPY
  hailsys ./hailsys/` plus `ENV PYTHONPATH=/app` — the decision only named the
  `PYTHONPATH` half; without the `COPY` there was nothing at that path to
  find), all three images rebuilt, 88 tests pass, the re-run export was
  byte-identical to the baseline, and a manual `iem_ingest.service` start
  produced a new `run_id` (24) that completed clean. At the time of this note
  (2026-09-14), `web/` was still ahead — a Phase 2 web-app piece, not part of
  this move — and `db.py`/`queries/` were undecided-into-code but built the
  same day; see the next two entries. **`web/` was built two days later**
  (2026-09-14 through 2026-09-16) — see "The web app" under §2 and §10 for
  the current tree.
- **One connection seam in `hailsys/db.py`; `dict_row` rows, no pool in
  Phase 2.** Every query acquires its connection through a single context
  manager; rows come back as dicts so a column added mid-`SELECT` cannot
  silently shift what a positional index returns. No pool: at three to five
  users the saving is milliseconds, and a pool held open across a `postgis`
  container restart hands out dead sockets without a `check=` callback.
  Triggers recorded instead of a phase number: a route holding a connection
  across slow non-database work, sustained concurrency above the gunicorn
  worker count, or measured connection-setup cost. The ingest scripts keep a
  plain `connect()` regardless — one-shot processes gain nothing from pooling.
  **Built the same day**, not just decided: `export_storm_zips.py` is its
  first real call site.
- **The storm query lives in `hailsys/queries/storms.py`.** The joins,
  coverage rule, distance expression, and local-day boundary are written
  once; `pairs` (one row per report-zip pair) and `zips` (the same query
  grouped by coverage zip) are two projections over that one core, so
  `export_storm_zips.py` keeps argument parsing, logging, and CSV writing and
  contains no SQL. Done now — a second consumer (the browse UI) rather than
  waiting for RentCast to need it — because a CSV that disagrees with the
  screen it was downloaded from is a failure with no good diagnosis, and
  extracting after two consumers have diverged costs the reconciliation on
  top of the extraction. Same argument that produced `iem_common.py`.
  **Resolves parking-lot item 6 (PL-06).** The `zips` aggregate answers
  PL-06's open sub-question — what "nearest distance" means for a zip touched
  by two cells 30 miles apart — with a time span (`first_report`/
  `last_report`) rather than a distance span: two separate storm cells almost
  always differ by hours, while one cell's reports land minutes apart, so the
  time span is the stronger discriminator, and `max(distance_miles)` is weak
  inside a bounded 5-mile radius regardless. `report_count` inherits the
  ~0.7% natural-key dedup rate (§7) — it counts stored rows, not necessarily
  every row IEM sent — which is the conservative direction for a number a
  homeowner may eventually see. Verified: `pairs` byte-identical to the
  pre-extraction baseline for 2026-06-24 HAIL at radius 5.0; `zips` returns 45
  rows whose `report_count` sums to 64; 88 tests pass. Output filenames are
  now suffixed by format (`storm_zips_<date>_<type>_pairs.csv` /
  `..._zips.csv`) since a name that doesn't say which format it is stops
  being unambiguous the moment a second format exists.

**Ten more decisions, recorded 2026-09-15 and 2026-09-16, all built the same
day as decided — condensed from `decision-log.md`:**

- **Zip detail loads lazily, as a fetched HTML fragment, not eagerly or as a
  separate page** (2026-09-15). Running `fetch_zips` for every row on
  screen regardless of whether anyone opens it is the wrong default for a
  list that can carry a dozen-plus rows; a full page-and-back-button per row
  is worse friction than not expanding at all. Convention: a template named
  with a leading underscore (`_zips.html`) is includable, not a page —
  no `{% extends %}`, no `<html>`.
- **`_ACTIONABLE` pulled into the shared query core** (2026-09-15), after
  `RECENT_DAYS_SQL` and `ZIPS_SQL` silently disagreed about which reports
  counted for the same "actionable only" day (§2). The lesson generalized:
  a shared seam only protects the rules it was built around, not every rule
  a later projection might apply independently.
- **County-by-polygon lookup verified: index scan, sub-millisecond warm**
  (2026-09-15). `EXPLAIN (ANALYZE, BUFFERS)` against the live 176,973-row
  archive: 0.55 ms warm for one report, 1.97 ms warm for a 9-report storm
  day, always via `Index Scan using county_boundaries_geom_gix`, never a
  sequential scan. Closes a measurement the original county decision
  asserted but never recorded.
- **The storm browser and the RentCast match view are separate pages**
  (2026-09-15). Different units — a storm-browser row is a query over
  `iem_data`/`coverage_zips`; a match-view row (Phase 3) is a query over
  `storm_listing_matches`/`listings`/`realtors`. One page behind a mode
  toggle would make neither filtered view bookmarkable. Answers parking-lot
  item 10's page-placement question in the direction later confirmed by
  territory (below): its own page under the storm browser, not the match
  view.
- **Contact state is shown as history, not a boolean** (2026-09-15, Phase 3
  design). "Already contacted" is ambiguous about whether it's scoped to the
  listing, the agent, or the storm; showing the actual `send_log` sequence
  lets a person judge, rather than baking in a frequency-cap policy
  (parking-lot item 15, still undecided) inside a display decision.
- **`confidence_tier` stays out of the UI, and radar size is not adopted as a
  severity signal** (2026-09-16), from the NEXRAD radar-verification study
  (§2). The column, seed, and `LEFT JOIN` all stay — nothing about storage
  changes — but nothing displays the tier, because the evidence says the
  distinction it would imply (`PUBLIC` reports are weaker) runs the wrong
  direction. Stated reversal condition: a per-source rate that separates by
  more than its confidence interval, on a sample large enough to support the
  claim — not yet met for any source, including the nominally-lower
  COCORAHS (n=119, too small).
- **`ZIPS_SQL` groups by report type** (2026-09-16). Fixed the same
  mixed-unit `max(magnitude)` bug the type-carrying `mag_unit` column exists
  to prevent, reintroduced one join downstream by a `GROUP BY` that didn't
  carry `report_text` — see §2 for the full mechanism.
- **Territory browse is its own page, grouped by city-and-type** (2026-09-16).
  Answers parking-lot item 10. City grouping carries `report_text` for the
  same mixed-unit reason as `ZIPS_SQL`. The city→zip fan-out (a report near
  two cities' zips counts under both) is disclosed on the page as a
  footnote rather than silently making the totals not add up.
- **CSV export from the UI returns zip-level detail, filtered by the page's
  own query string** (2026-09-16) — the thing a planner acts on next, not
  the day-list summary, and never a second source of truth for which
  filters are active. See §2.
- **Map: Leaflet, no tile layer, points colored by `report_source_norm`**
  (2026-09-16). Coverage polygons are a pre-generated static fixture
  (`build_coverage_geojson.py`, `ST_SimplifyPreserveTopology`, 4.18 MB → 418
  kB), display-only and never touching the matching geometry. Colors key on
  the normalized source column for the same reason every other join in this
  system does; the raw value still shows in the tooltip. 5-mile rings use
  `L.circle` (true metres) marked non-interactive so they never block a
  point's hover. See §2 for the full writeup.
- **Access for the demo: Tailscale Funnel, temporarily** (2026-09-16).
  `tailscale serve` is tailnet-only; Funnel briefly made the same hostname
  reachable over public TLS for one non-technical viewer, which also
  briefly made the application's own login the sole gate — raising CSRF's
  priority (implemented 2026-09-22, §2) — and was turned off after. Verified
  off as of the 2026-09-17 sync, and still tailnet-only 2026-09-24.

**Phase 3 decisions, 2026-09-17 through 2026-09-21** (see the decision log for
each):

- **RentCast client in stdlib `urllib`**, `status=Active`, `daysOld` filtered on
  the server, throttled to 20 req/sec, and every physical request counted,
  retries included.
- **A pull links to a storm by `(storm_date, report_text)`**, not one
  `iem_data` row, because a storm has never been one row.
- **Continue past a bad zip, abort past a bad key.** A bad key fails
  identically on every remaining zip.
- **Pulls run in a background thread and match automatically.** Status lives
  in `api_pulls`, never in process memory, because a poll can land on a
  different Gunicorn worker.
- **The pull POST recomputes zips** and checks a count, rather than trusting
  hidden form fields.
- **Work state is derived, never stored.** Looking at a storm never changes
  its state. The one-year claim window turns an old "Not pulled" into history.
- **Matching:** Active listings only, New Construction and Land excluded, and
  `COUNT(DISTINCT listing_id)` for anything counting leads.
- **`report_zip_distances`: compute once, store.** Plus the lesson that with
  `./hailsys` bind-mounted into `web`, **editing is deploying**.

**Phase 4 decisions, 2026-09-22 through 2026-09-24:**

- **Roles are enforced on the route first, and the UI follows.** Admin is a
  superset of sender. The admin routes are one blueprint with one check, so a
  new admin route can't forget it.
- **Role is cached in the session; account state is re-checked every
  request.** Forced logout compares timestamps, because signed cookies leave
  no session list to delete. A role change or an admin password reset signs
  the user out, and your own password change keeps your current session.
- **Last-admin protection is a deferred constraint trigger**, checked at
  `COMMIT` so a demote-and-promote swap in one transaction passes.
- **Settings changes are attributed** through a transaction-local setting, and
  the history trigger is scoped to the columns it logs.
- **CSRF through Flask-WTF**, failing closed, with no token time limit; a
  failure returns a 400 page (supersedes the 2026-09-22 redirect).
- **Radii are read from `settings` per request.** `tuning.py`'s constants stay
  for `scripts/` only.
- **`match_runs` makes an empty match visible**, and "Matched, none in range"
  needs both a completed run and a pull.
- **Re-pull goes through the estimate page.** Storms past the claim window are
  greyed, not blocked, and the estimate page deliberately doesn't block them.
- **RentCast quota:**
  - Billing day capped at 28.
  - Warn and allow.
  - Usage summed from `api_call_log`, with the period rolling over at Denver
    midnight. RentCast's own boundary timezone is unconfirmed.
- **Admins can't act on their own row.** A forced password change after a
  reset was declined for now.
- **The activity feed shows who did what, to everyone**, deliberately, for an
  office of five with admin-created accounts.
- **Scheduled ingest runs a built image, not the working tree.** `app` and
  `ingest` bake their code in; `web` and `loader` see edits live.
- **Unreadable RentCast responses fail loudly with an attempt count**, and
  every aborted zip's calls reach `api_call_log`.
- **The storm list pages at 50 storm days.**
- **Parked, with the claim rule:** permits and jurisdiction work.
  Jurisdiction is always point-in-polygon, never a mailing city.

---

## 7. Known traps

These have already bitten. Do not re-discover them.

### IEM
- **`MAG` contains the literal string `None`** as its null marker — 3,353 of
  135,856 rows in the original ten-year extract. Coerced to 0 it produces 629
  magnitude-zero flash floods and 549 magnitude-zero tornadoes. After the
  backfill, 5,320 of 176,957 `iem_data` rows carry a null magnitude — the
  `None`s plus the types that legitimately have no magnitude unit.
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
- **`unknown_report_type` has fired against real data for the first time** —
  run 8, the 2003–2016 window: `('5', 'ICE STORM')` from 2006-12-20 and
  `('X', 'WALL CLOUD')` from 2010-08-04. Both are legitimate historical IEM
  type/text pairs absent from the curated 37 in `report_types`, so the composite
  FK rejects them; they are the only two rows the backfill has lost this way. A
  *recurring* version — a pair that shows up in nightly data — is a
  `report_types` seed gap to fix, not a parser bug; a one-off from a 2006 ice
  storm is a rejected row and nothing more. The reject enumeration stays closed
  at five; three of the five have now fired.
- **Unquoted commas inside `CITY`** (`BISON LAKE, GLENWOOD 15`) give 17 fields
  instead of 16 — 75 from 2018 and **one from 2026-08-31, so this is ongoing,
  not a historical artifact** (see the 2026-09-09 reversal entry). Never split on
  commas — but a real CSV parser **detects** these and cannot **repair** them.
  The quotes were never written, so the field boundary is unrecoverable. They are
  rejected as `field_count_mismatch`, and `raw_row` keeping the line verbatim is
  what makes that non-lossy. **Across the five backfill runs on `hail-dev` this
  reason has fired 180 times** — the same ~76 rows re-rejected under each wide
  run's `run_id`, which is by design (rejects are never deduplicated across
  runs). It is the only reject reason the backfill hit until run 8.
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
- **`county` is free text with case variants too — and has no normalized
  column.** `iem_data` carries a generated `report_source_norm` but nothing
  equivalent for `county`: `EL PASO` (10,299 rows) and `El Paso` (2,549) are
  distinct values, and 64 county groups differ only by case. Browse-by-county
  (open question 4) has to `upper()` both sides or it splits a county in two.
- **Single-quote IEM URLs in bash.** Unquoted, `&` backgrounds the job and
  truncates the query string — curl succeeds and returns the wrong data.
- **Colorado WFOs are `BOU`, `PUB`, `GJT`, plus `GLD` and `CYS` on the borders.**
  `wfos=BOU,PUB` silently drops the northeast corner.
- **The `SNOW` magnitude tail is not one storm.** Maximum 175 inches, with a
  handful above 60 — seasonal or storm-total accumulations entered against a
  single LSR. Five reports out of 85,049. Inert today because `SNOW` is
  `roof_relevant = FALSE`, but a magnitude floor on SNOW would admit exactly
  these rows first. Look at this before that flag is ever flipped.
- **Hail reports skew to the eastern plains, and most hail days are thin.** By
  county the top of the HAIL distribution is EL PASO, WELD, YUMA, KIT CARSON,
  PUEBLO, LARIMER, LOGAN, WASHINGTON — plains and the Palmer Divide, not the
  Front Range metro. And 382 of 1,468 Denver-local hail days (26%) carry exactly
  one report, so a confidence label that leans on report count will read "thin"
  more often than not. Neither is a defect; both shape how the browse UI and the
  confidence tier should be framed.

### USPS ZIP_Locale_Detail

- **It is a FACILITY file, not a zip→city file.** One row per post office, so a
  zip with several facilities appears several times: 42,288 rows for 37,105
  distinct zips, and **10.0% of zips carry more than one distinct
  `PHYSICAL CITY`**.
- **A modal rule does not deduplicate it.** 3,686 of the 3,719 multi-city zips
  are exact ties at one row each, so "most frequent" decides almost nothing.
  Use **first occurrence in file order** — that is the preferred city name.
  Alphabetical was tested against RBI's six multi-city zips and got two wrong:
  `GOLDEN` over `MORRISON`, `FORT COLLINS` over `TIMNATH`.
- **Use `PHYSICAL CITY`, never `LOCALE NAME`.** `LOCALE NAME` is the facility.
  Zip `00604` is `LOCALE NAME` RAMEY but `PHYSICAL CITY` AGUADILLA.
- **Presence in this file is a useful signal about ZCTA gaps** — see the ZCTA
  entry below.

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
  re-detected. **Cross-tabulating against the USPS delivery file splits those 10
  cleanly and says *why* each is missing:**

  | | in USPS delivery file | has ZCTA | what it is |
  |---|---|---|---|
  | `80502` `80522` `80539` `80632` `80901` | yes | no | PO-box-only — USPS delivers, Census draws no polygon |
  | `80213` `80225` `80523` `80638` `80639` | no | no | not a delivery zip at all — unassigned or institutional |

  This corrected the 2026-09-03 decision entry, which had filed `80638` as
  PO-box-only when it is institutional (UNC), like `80639`.
- **The inverse case exists too.** `80913` (Fort Carson) is **absent from USPS
  but has a ZCTA polygon** — it loads, but has no USPS city, so it is the one
  row in 183 whose `area_name` falls back to the literal `ZIP 80913`.

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
- **A body that can't be read must still carry an attempt count** (2026-09-24).
  `http.client.IncompleteRead` is an `HTTPException`, **not** an `OSError` or
  `ValueError`, so a handler catching `(JSONDecodeError, ValueError, OSError)`
  misses the commonest short-body case. `RentCastResponseError` covers all
  of them, and a JSON object where a list was expected raises too, instead of
  reading as "no listings".
- **RentCast geocodes some new-construction houses to one subdivision point**
  (parking-lot item 100), and address variants mint duplicate properties at
  nearly the same point (item 55: 186 candidate pairs). Distance alone tells
  neither case apart.

### Shell, Docker, systemd, and psql

These are newer and cost real time on 2026-09-08.

- **Which containers see your edits** (2026-09-24). `web` bind-mounts
  `./hailsys` and `loader` bind-mounts the whole repo, so both see edits
  immediately; for `web`, editing is deploying at the next restart. `app` and
  `ingest` bake their code in at build time, so running them without
  `docker compose build` tests the code from the last build. This returned a
  pre-migration answer through `app` on 2026-09-24. The nightly timers
  deliberately don't build first. See `docs/command-ref.md`.
- **A Jinja template can go live before the Python that feeds it.** Workers
  read templates from disk on first use but load Python only at restart, so a
  template edit that needs a new view variable can break the page on a worker
  that hasn't cached the old template yet. Restart `web` once both halves are
  in.

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
- **`\copy` does not interpolate psql variables.** `\copy t FROM :'somevar'`
  is read literally and fails with `:: No such file or directory`, even though
  the same `:'somevar'` works in ordinary SQL on the line below. Staging paths
  have to be literals inside the heredoc — which is also why the heredoc can
  stay fully quoted and no shell expansion reaches the SQL.
- **Python's `csv.writer` emits CRLF by default**, per RFC 4180, and Postgres
  `\copy ... FORMAT csv` rejects it with *"unquoted carriage return found in
  data"*. Pass `lineterminator='\n'`, or normalize afterwards. A generated CSV
  can look perfect in an editor and still fail to load.
- **`shp2pgsql -d` is not `-c`.** `-d` emits a `DropGeometryColumn` for a stage
  table that does not exist on a first run, which raises and, under
  `ON_ERROR_STOP=1`, kills the load before it starts.
- **The postgis image ships only the server-side extension.** `shp2pgsql` lives
  in the separate `postgis` client package. The base image clears
  `/var/lib/apt/lists`, so a bare `apt-get install` reports "unable to locate
  package" and looks exactly like the package does not exist. The `apt-get
  update` is the whole fix.
- **The base image is Debian bullseye, and bullseye went EOL 2026-09-07.** The
  next day `loader.Dockerfile`'s `apt-get update` began failing on expired
  `Release` metadata; `apt-get -o Acquire::Check-Valid-Until=false update` is the
  workaround now in the file. This is a standing problem, not a one-off: every
  `postgis/postgis` tag checked (including `:16-3.4`, the one in use) is still
  bullseye-based, so there is no clean fix until upstream rebases onto bookworm.
  The pinned client package (`postgis=3.5.2+dfsg-1.pgdg110+1`, `pgdg110` =
  Debian 11) has to be bumped in the same move. Tracked in `decision-log.md`.
- **`COPY` into an image is a build-time snapshot, not a live view.** `ingest`
  and `app` both `COPY scripts ./scripts/` with no bind mount, so a script
  edited (or newly added) on the host is invisible in the container — including
  `python3: can't open file ... No such file or directory` for a script that
  demonstrably exists on disk — until `docker compose build <service>` runs
  again. `loader` does not have this trap because it bind-mounts `.:/repo:ro`
  instead.
- **A container's own filesystem does not survive `--rm`, and its `WORKDIR` is
  root-owned even when the process drops to a non-root user.** `export_storm_zips.py`
  writing to `./output` (relative, so `/app/output` inside the container) hit
  `PermissionError` because `/app` is created by `root` during the build,
  before `USER app` switches away from it — and even chowning it would only
  have produced a CSV that vanishes the moment `--rm` deletes the container.
  The fix is a bind mount (`./output:/app/output`) to a host directory that
  already exists, not a permissions fix inside the image. It requires no
  `chown` on either side here specifically because both `useradd
  --uid 1000 app` (in the Dockerfile) and the host account (`hail-user`) land on
  `uid 1000` — a different host UID would need one side adjusted to match.
- **Subtracting two aware `datetime`s does not mean what it looks like it
  means when a fixed-offset assumption meets a DST-observing zone.** Given
  `s = datetime.combine(day, time.min, tzinfo=DISPLAY_TZ)` and `e =
  datetime.combine(day + timedelta(days=1), time.min, tzinfo=DISPLAY_TZ)`,
  `e - s` is **always exactly 24h**, on every day of the year, including the
  two DST-transition days where the real elapsed time in Denver is 23h
  (2026-03-08, spring forward) or 25h (2026-11-01, fall back). Verified by
  running it. **This does not make `denver_day_bounds` wrong for its actual
  job** — `s` and `e` are each independently resolved to the correct absolute
  UTC instant for local midnight on their own date, and that is what reaches
  Postgres as the `TIMESTAMPTZ` bound, so the query window is correct. The trap
  is for anything written *later* that computes a *duration* from
  `window_start`/`window_end` — an elapsed-time log field, a health check, a
  "did this take too long" comparison — since that computation silently
  ignores the DST offset change one to two days a year. Convert to UTC before
  subtracting for elapsed time; never subtract two local-zone-aware values
  directly for that purpose.
- **`systemd-analyze calendar '<expr>'` validates an `OnCalendar` expression
  and prints its next elapse** — the check to run before trusting a timer,
  because a malformed expression installs cleanly and produces a timer that
  simply never fires, with nothing in the journal to say so.

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
`firewalld` left closed. **Updated 2026-09-14** to explain the current reason —
the web container binds to loopback and `tailscale serve` fronts it, so
nothing ever reaches a real interface for firewalld to gate — rather than the
earlier Cloudflare-tunnel reasoning, which no longer applies in Phase 2 (§6).

### The rest of the stack

- **PostgreSQL 16 + PostGIS 3.4**, in Docker, database `weather-property`
- **Python 3.12** backend, stdlib and boring dependencies preferred;
  `requirements.txt` is `psycopg[binary]==3.2.3`, `flask==3.1.3`, `gunicorn`
  and `Flask-WTF==1.3.0` (added for CSRF, 2026-09-22)
- **Web UI (Phase 2, decided 2026-09-14, built and running since):
  Flask**, server-rendered Jinja templates, `gunicorn` in its own
  `docker-compose.yml` service (`web`), reached via `tailscale serve` against
  a loopback-bound container — see §2 and §6. A Cloudflare tunnel was the
  earlier plan and is revisited at Phase 6, not before. Briefly widened to
  the public internet via Tailscale Funnel for a demo (2026-09-16), then
  reverted — see §2.
- **Tailscale** for host-to-host file movement, and (Phase 2) for reaching the
  web UI itself, including Funnel for the one-off demo above
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

**Web logging** (2026-09-24, parking-lot item 99, decision log). Until then
nothing configured logging in the web app, so every `info` line was dropped.
`hailsys/logconfig.py` now sets INFO to stdout, called first in `create_app()`
in every gunicorn worker. Each line carries its level and logger
(`level=INFO logger=hailsys.web.jobs event=…`), because Docker's `json-file`
driver records the time but nothing else. `web`'s log is rotated at 20 MB × 5.
**Read web logs with `docker compose logs -f web`, not `journalctl`:** they go
to Docker, not journald. The ingest scripts still write a bare message to
stdout under systemd, read with `journalctl -u iem_ingest.service`.

**systemd schedules; the container is only the runtime.** A timer unit invokes
`docker compose run`, and the timer carries `Persistent=true` so a missed run
fires after downtime rather than being skipped silently. **`OnFailure=` is
deliberately not set** (decision log 2026-09-11): there is no notification path
yet for it to trigger, and Irin alerting (parking-lot item 9) is unbuilt. Cron
inside the container was declined — a second scheduler on a box that already has
systemd, forfeiting journald capture, `systemctl --failed`, and `OnFailure=`.

---

## 10. Repo layout

```
CLAUDE.md                     rules for AI assistants — read first
README.md                     short project overview, pointing at the docs (2026-09-24)
docker-compose.yml            postgis + ingest + loader + app + web on hailnet
.dockerignore                 keeps data/ and secrets out of the build context
.env                          gitignored — three passwords plus FLASK_SECRET_KEY
.env.example                  same keys, no values
requirements.txt              psycopg[binary]==3.2.3, flask==3.1.3, gunicorn, Flask-WTF==1.3.0 (CSRF, 2026-09-22)
docs/
  hail-consolidated.md        this file
  database-schema.md          field-level data model, 23 tables, open questions
  db-schema-diagram.md        ASCII ER diagram
  decision-log.md             dated, append-only; supersede, never rewrite
  data-sources.md             IEM / TIGER / RentCast endpoints and traps
  phases.md                   phases 0–7 with a "done when" for each
  server-setup.md             bare-metal Rocky build, step by step
  command-ref.md              Justyn's own Docker/Postgres notes, including which services
                               see your edits (2026-09-24)
  schema-review.md            re-runnable review prompt for sql/ + the loader, 001-023
  parking-lot.md              103 numbered items, many resolved and resolution-tracked
  analysis/
    radar-verification-2026-09.md   NEXRAD corroboration study behind the
                               confidence_tier / map-color decisions (§2, §6)
    radar-verification-2026-09/     its five scripts (paths/coverage_bbox/
                               match/reduce/by_day_density/checks.py)
sql/                          apply in order; 010 after 001-009 (it grants on their tables)
                               and before 012/017 (they grant to its roles)
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
  011_ingest.sql              additive COMMENT fix; 001-009 are frozen post-backfill
  012_counties.sql            county_boundaries; additive, same reason as 011 (2026-09-14)
  013_pull_storm_link.sql     api_pulls gains storm_date/report_text, CHECK'd as a pair;
                               additive, same reason as 011/012 (2026-09-17, Phase 3)
  014_properties_geom.sql     properties gains generated geom + two GiST indexes,
                               matching iem_data's pattern (2026-09-18)
  015_slm_emp_attribution.sql storm_listing_matches gains emp_id (nullable) and a
                               matched_at index, for the activity feed (2026-09-19)
  016_iem_ingested_at_idx.sql index on iem_data.ingested_at, for the activity feed's
                               new-storms query (2026-09-19)
  017_report_zip_distances.sql report_zip_distances table + AFTER INSERT trigger +
                               ceiling/guard functions; replaced storms.py's live
                               ST_DWithin join (2026-09-21, Phase 3 close); backfill
                               complete, old-vs-new output verified identical
  018_admin_failsafe.sql      users.sessions_invalidated_at; the settings singleton with
                               global_sessions_invalidated_at (2026-09-22)
  019_last_admin_protection.sql  deferred constraint trigger: never zero active admins
  020_settings_radii.sql      both radii into settings; settings_history + its trigger
  021_municipal_boundaries.sql  DOLA municipal boundaries, for the parked permits work
                               (2026-09-23)
  022_matched_runs.sql        match_runs: every match attempt recorded (2026-09-23)
  023_rentcast_quota.sql      RentCast billing day and monthly quota in settings; history
                               trigger widened to four columns (2026-09-23)
hailsys/                      importable package, moved out of scripts/ (2026-09-14)
  __init__.py                 empty
  tuning.py                   tuning constants and denver_day_bounds; the radius constants
                               are for scripts/ only since 2026-09-22 (the app reads settings)
  settings.py                 fetch_settings(): the settings row per request, radii as float
  formatting.py               display formatting with no Flask import: magnitude(value, unit),
                               the one implementation behind the `magnitude` Jinja filter and
                               map_points()'s magnitude_display (2026-09-21)
  db.py                       connection seam: one context manager, dict_row rows, no pool (2026-09-14)
  iem/
    __init__.py                empty
    common.py                  shared network/DB machinery both ingest scripts import
    parse.py                   shared row parser; both ingest scripts import it
  queries/
    __init__.py                empty
    storms.py                  join/filter core + six projections (pairs, zips, recent_days,
                               cities, city_days, report_points) behind both export_storm_zips.py
                               and the web app (2026-09-14 through 2026-09-16); _FROM_WHERE now
                               joins report_zip_distances instead of a live ST_DWithin (2026-09-21;
                               live and verified — see decision log, same date)
    matches.py                 match-detail query: one row per listing, grouped by agent;
                               pull-coverage lookup for the match page's gap warning (2026-09-21)
    workstate.py                per-storm-day work state (Not pulled / Pulled, not matched /
                               Matched, not sent / Sent), derived at read time, never stored
                               (2026-09-21)
    activity.py                 "since your last login" feed: new storm days (by ingested_at),
                               pulls, match runs (2026-09-21)
    quota.py                    RentCast usage for the billing period, summed from
                               api_call_log (2026-09-23)
    exports.py                  CSV projections: matched listings (one row per listing per
                               local storm day and type) and the realtor list, each with a
                               count and a suppression LEFT JOIN on dnc_list; imports
                               LOCAL_TIME_EXPR from storms.py (2026-09-24)
  matching/                   no __init__.py — implicit namespace package
    matcher.py                  storm-to-listing matching, writes storm_listing_matches;
                               excludes New Construction and Land; run by the /match POST and
                               automatically at the end of a pull by web/jobs.py (2026-09-18;
                               automatic run and Land exclusion since)
  web/
    __init__.py                Flask app factory (create_app); imports views inside the
                               factory, not at module scope, so the package stays importable
                               without a configured app; registers the `magnitude` Jinja filter
    auth.py                    scrypt hash/verify, login_required and role_required, and the
                               per-request load_current_user check (2026-09-14; roles 2026-09-22)
    admin.py                   the /admin blueprint: users, settings, usage, sign out everyone;
                               one before_request admin check (2026-09-22)
    jobs.py                     background thread for a RentCast pull + its automatic match
                               run; daemon=True, so a thread dies with its process (known gap,
                               parking-lot item 47) (2026-09-18)
    views.py                   the `main` blueprint: /, /storms/zips, /territory,
                               /territory/days, /export.csv, /map/points.geojson, /login,
                               /logout, /pull/estimate, /pull, /match, /storms/matches,
                               /storms/matches.csv, /exports, /exports/matches.csv,
                               /exports/realtors.csv (sender/admin), /activity,
                               /account/password (2026-09-14 through 2026-09-24; the
                               storm list pages at 50 days)
    templates/
      base.html                 single header bar (brand + nav + signed-in-as + Sign Out) and
                               the global flash-message panel; pulls in vendored Leaflet
                               (2026-09-17 through 2026-09-21)
      login.html                no header — gated on session.emp_id, same as everywhere else
      storms.html                the recent-storm-days browser; includes _activity.html
      territory.html             city/zip grouped browse + the map (city mode only)
      matches.html                match-detail page: agent groups, coverage-gap warning,
                               CSV download link (2026-09-21; link 2026-09-24)
      exports.html                /exports: date range, type and suppression choice, then the
                               match and realtor downloads (2026-09-24)
      activity.html               full "since your last login" page, no item cap
      _activity.html              fragment shared by storms.html's panel and activity.html;
                               pulls and match runs in side-by-side <details> (2026-09-24)
      pull_estimate.html          confirm-a-pull page: cost estimate, recently-pulled zips,
                               billing-period usage and an overage warning (2026-09-23)
      admin.html                  users table and settings form (2026-09-22)
      change_password.html        self-service password change (2026-09-22)
      csrf_error.html             the 400 page for a failed CSRF check (2026-09-23)
      _zips.html                 fragment: one storm day's zip breakdown
      _city_days.html            fragment: one city's day-by-day breakdown
    static/
      style.css                  restyled 2026-09-17; header merged to one bar, flash-message
                               and activity-panel styles added 2026-09-21; fixed match-page
                               column widths and .feed-columns 2026-09-24; responsive pass
                               (40rem and 48rem breakpoints) 2026-09-24/25
      storms.js                  generic expand/collapse + lazy-fetch-once handler
      map.js                     Leaflet map: coverage polygons, report points, 5-mi rings;
                               tooltip shows the server-formatted magnitude_display
      coverage.geojson           generated fixture (scripts/build_coverage_geojson.py)
      leaflet.css / leaflet.js   vendored from unpkg 2026-09-17, resolving parking-lot item 28
      images/
        marker-icon.png           Leaflet default-icon assets; unused by this app's own
        marker-shadow.png         markers (map.js draws circleMarker/circle, not L.marker) —
                               shipped because leaflet.css references them, harmless if 404
  wsgi.py                    two lines: `from hailsys.web import create_app; app = create_app()`
  rentcast/                  Phase 3, begun 2026-09-17
    __init__.py                empty
    client.py                  sale-listings search: pagination, throttle to 20 req/sec,
                               RentCastAuthError/ValidationError/ServerError/ConnectionError,
                               and RentCastResponseError for unreadable bodies (2026-09-24)
    estimate.py                pre-pull estimate: zip count + projected call count, from
                               fetch_zips and api_call_log history
    pull.py                     orchestrates a pull: api_pulls/api_call_log bookkeeping,
                               hands each zip's listings to upsert.py; a RentCast auth failure
                               aborts the whole pull (2026-09-21)
    upsert.py                   raw RentCast listing dicts -> properties/listings/realtors
                               (2026-09-21)
scripts/
  build_reference_tables.py   derives reference CSVs from the raw LSR archive
  zcat-data-check.py          checks coverage zips against the TIGER .dbf
  iem_backfill.py             historical ingest; has run 8x (runs 4-8 = backfill, 176,957 rows); imports hailsys.iem.common
  iem_ingest.py                the nightly, rolling-window ingest; imports hailsys.iem.common
  export_storm_zips.py        CSV export, one storm day, --format pairs|zips (2026-09-14); no SQL of its own — imports hailsys.db, hailsys.queries.storms, hailsys.iem.common, hailsys.tuning
  load_reference.sh           idempotent loader: report_types CSV, report_sources CSV, ZCTA shapefile, county shapefile (2026-09-14)
  load_coverage.sh            idempotent loader: one customer's territory
  status.sh                   four read-only operator checks; exit code = nightly-health verdict (2026-09-11)
  create_user.py              CLI to create a web-app login; run as hail_admin. Predates
                               /admin; keep for bootstrapping or retire (parking-lot item 65)
  fetch_municipal.py          count-checked, dated GeoJSON snapshot of an ArcGIS layer
                               (DOLA by default) (2026-09-23)
  load_municipal.sh           DELETE + INSERT load of municipal_boundaries (2026-09-23)
  build_coverage_geojson.py   writes static/coverage.geojson from a one-time simplified
                               query; a fixture, not a per-request render (2026-09-16)
  backfill_zip_distances.py   fills report_zip_distances for reports that predate the
                               AFTER INSERT trigger; batched by iem_id range, resumable,
                               run as hail_ingest (2026-09-21)
  verify_zip_distances.py     old-vs-new PAIRS_SQL exact comparison, the pre-93c7f85 spatial
                               join embedded as the reference; read-only, as hail_app, exits 1
                               on any mismatch. Re-run after any recompute of
                               report_zip_distances (ceiling change, TIGER reload)
                               (2026-09-21)
  test_estimate.py            one-off manual check of estimate_pull's recently-pulled split
  test_latest_call.py         direct check of estimate.py's recency lookup
  test_match.py                one-off manual check of the storm matcher
  test_rentcast_pull.py       one-off manual test of the RentCast pull orchestrator
                               (all four: manual probes, not under tests/; --emp-id is
                               required, and only test_rentcast_pull.py's example still
                               shows the system account's 1 — parking-lot item 43)
tests/
  __init__.py                 empty; makes unittest discovery work
  test_iem_parse.py           stdlib unittest cases against hailsys/iem/parse.py
  test_iem_common.py          against hailsys/iem/common.py
  test_iem_backfill.py        against scripts/iem_backfill.py (sys.path.insert, not a package)
  test_iem_ingest.py          against scripts/iem_ingest.py (same)
  test_formatting.py          against hailsys/formatting.py (2026-09-21)
                               (100 cases total: 88 unchanged since 2026-09-11 plus 12 in
                               test_formatting.py — nothing under hailsys/web/ has any test
                               coverage, §2)
docker/
  ingest.Dockerfile           python:3.12-slim + psycopg; COPY hailsys + scripts, PYTHONPATH=/app (2026-09-14); runs as non-root
  loader.Dockerfile           postgis image + pinned client pkg; bullseye-EOL apt workaround; bind-mounts the repo, no PYTHONPATH needed
  app.Dockerfile              same shape as ingest.Dockerfile; connects as hail_app (2026-09-11); also builds `web` (below), which runs it under gunicorn instead of a one-shot script
output/                       gitignored — CSVs from export_storm_zips.py (pairs/zips), bind-mounted into `app`
reference/                    gitignored — derived statistical CSVs, DNC lists
data/                         gitignored — raw LSR archive, TIGER shapefiles, DOLA and
                               permit snapshots (raw/dola, raw/permits), research/
planning/                     GENERIC national seed data + working notes
  report_types.csv            THE curated seed for report_types (tracked)
  zip_city_names.csv          37,104 USPS zip -> city, for coverage area_name
  Postgres-Tables.ods         working notes
config/                       PER-CUSTOMER configuration; see its README
  coverage_zips.txt           RBI's 193 zips (was planning/rbi-zip-code-...)
  README.md                   the generic/specific split, stated
systemd/                      unit files; installed by copy, not symlink (2026-09-11)
  iem_ingest.service           oneshot, docker compose run ingest, TimeoutStartSec=900
  iem_ingest.timer             OnCalendar=*-*-* 10:00:00 UTC, Persistent=true
  iem_weekly_replay.service    30-day replay via iem_backfill.py --mode replay
  iem_weekly_replay.timer      OnCalendar=Sun *-*-* 11:00:00, an hour after the nightly
```

**`tests/` holds five files** — stdlib `unittest`, no runner dependency, 100
cases total across `test_iem_parse.py`, `test_iem_common.py`,
`test_iem_backfill.py`, `test_iem_ingest.py`, and `test_formatting.py`
(pure functions: no `psycopg` stub, only the repo root on `sys.path`). Of the
first four, the last three import
`iem_backfill`/`iem_ingest` from `scripts/` directly (not a package, so via a
`sys.path.insert`) while those modules import `hailsys` at the repo root —
each of those three test files inserts *both* paths.

**The `hailsys/` package move (§6) is done and verified, 2026-09-14.** The
tree above reflects the actual layout: `hailsys/tuning.py` and
`hailsys/iem/{common,parse}.py` hold the code, `scripts/*.py` keep their
filenames and import from `hailsys`, and `ingest.Dockerfile` / `app.Dockerfile`
both gained a `COPY hailsys ./hailsys/` alongside `ENV PYTHONPATH=/app` — the
decision only named the `PYTHONPATH` half; without the `COPY`, `PYTHONPATH`
would have pointed at a directory that didn't exist in the image, which the
build-context snapshot trap the decision warned about would have made look
like a caching problem rather than a missing instruction. `loader.Dockerfile`
was deliberately left alone: it bind-mounts the whole repo at `/repo` and
never runs the Python ingest scripts, so neither the `COPY` nor
`PYTHONPATH=/app` (which wouldn't even resolve against its `/repo`
`WORKDIR`) does anything there.

**`hailsys/db.py` and `hailsys/queries/storms.py` (§6) are also done and
verified, 2026-09-14**, same-day follow-on to the package move above.
`export_storm_zips.py` now imports both and contains no SQL of its own;
`app.Dockerfile` needed no change since it already copies the whole
`hailsys/` tree. Resolves parking-lot item 6 (PL-06).

### Running it

```bash
cp .env.example .env          # then fill in three passwords, plus FLASK_SECRET_KEY
docker compose up -d          # postgis AND web (2026-09-11) — web has no
                               # profile restriction, unlike ingest/loader/app,
                               # which are all profile "tools" and stay down

# apply the schema — NOT automatic; run in numeric order (010 sits after the
# tables it grants on and before 012/017, which grant to its roles)
docker compose run --rm loader \
  bash -c 'for f in /repo/sql/*.sql; do psql -v ON_ERROR_STOP=1 -f "$f" || exit 1; done'

# load GENERIC reference data — 37 report types, 33,791 ZCTAs. Idempotent.
docker compose run --rm loader bash /repo/scripts/load_reference.sh

# load THIS CUSTOMER's territory — 183 of 193; the other 10 are reported.
# The path is an argument: a different customer passes a different file.
docker compose run --rm loader bash /repo/scripts/load_coverage.sh
docker compose run --rm loader bash /repo/scripts/load_coverage.sh /repo/config/other.txt

# optional, for the parked permits work only: municipal boundaries from DOLA.
# The fetch runs on the host (stdlib Python); the load runs in the loader.
python3 scripts/fetch_municipal.py
docker compose run --rm loader bash /repo/scripts/load_municipal.sh \
  data/raw/dola/municipal_boundaries_<date>.geojson
```

Rebuild `app` and `ingest` after any code change before running them
(`docker compose build app` / `ingest`); `web` sees edits at its next restart.

Order matters: `load_coverage.sh` refuses to run against an empty
`zcta_boundaries`, because every insert depends on that FK and an empty boundary
table would report all 193 zips as unmatched, insert nothing, and exit 0.

---

## 11. Open questions

Unresolved. Each is cheaper to settle now than after there is data.
**`docs/parking-lot.md` is now the actively-tracked version of this list** —
resolution status, dependencies between items, and cross-references to the
schema-review pass live there, not here. This section is left as a condensed
snapshot; where the two disagree, the parking lot wins, same as anywhere else
this file summarizes a source.

1. **Is a "storm" a first-class entity?** The UI concept is *"Hail — August 24 —
   14 neighborhoods,"* which today is a `GROUP BY`, not a table. A real
   `storm_events` table would allow naming an event and reporting on it as a
   unit; the cost is defining a clustering rule. Deferring is safe *if* the
   query-based grouping stays consistent.
2. **Resolved in part, 2026-09-22.** ~~What is the default buffer radius, and
   where does it live?~~ 5.0 miles for both the zip and match radius, in the
   `settings` table, editable on `/admin`. Still open: **does the radius vary
   by event type?** Hail swaths and straight-line wind do not have the same
   footprint (parking-lot item 38).
3. **Resolved, 2026-09-22.** ~~Is there a settings table at all?~~ Yes:
   `settings` holds the radii and the RentCast billing day and quota, with
   changes logged to `settings_history`. The frequency-cap window and warmup
   limit will need a home there in Phase 5.
4. **Resolved and built, 2026-09-14.** ~~How are counties handled for
   browse-by-county?~~ County now comes from TIGER county polygons (§6) — a
   `county_boundaries` table gives an authoritative crosswalk across all 22
   years, which none of the three original sources (`nws_geo_code` UGC,
   `iem_data.county` free text, `properties.county_fips`) could do alone.
   `sql/012_counties.sql` and the `load_reference.sh` load are done, not just
   decided — verified against `hail-dev`, 3,235 counties.
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
    **Checked against the 2026-09-14 decisions and still open** — the
    Tailscale-access entry (§6) touches the same territory but explicitly
    declines to answer it, noting only that the application still needs its
    own login regardless of network access.
12. **Resolved, 2026-09-17.** ~~Should ingest widen past `state=CO`?~~
    (Retitled 2026-09-14 — the original phrasing, "out-of-state reports are
    excluded permanently," stated the current default as if it were the
    decision, which it is not; only the default is settled.) Ingest queries
    `state=CO` — a decision made 2026-09-04, over a WFO list, and that entry
    already flags this exact consequence and defers it here. A report over
    the Wyoming or Nebraska line is never fetched. **No buffer radius
    recovers it** — the radius widens the search around a stored report, and
    these are never stored. **New as of 2026-09-08:** IEM exposes
    bounding-box parameters (`north`, `south`, `east`, `west`, added
    2024-10-24), giving this question a mechanism, not just a description —
    a box crossing the state line would store the Wyoming report in the
    first place.

    **Resolution:** neither widening option is being built. RBI is licensed
    only in Colorado, so an out-of-state report has no business use
    regardless of geographic proximity — a licensing constraint, not a data
    gap. **Resolves parking-lot item 21 / database-schema.md open question
    12 — closed, not deferred.** The `state=CO` filter (2026-09-04) stands
    as originally chosen. See decision-log, "Ingest stays state=CO-only; no
    widening."
13. **Resolved and built.** ~~`report_sources` has DDL but no seed.~~
    `planning/report_sources.csv` is the curated seed, 47 rows at first and 49
    since 2026-09-23 (two GJT spellings added) (commit `5e3eb53`,
    "Seed report_sources; fold the loader into load_reference.sh") and
    `load_reference.sh` loads it. Confirmed loaded on `hail-dev`, 2026-09-14:
    47 rows, 0 unmatched against `iem_data.report_source_norm`.

    Original question, kept for context: nothing reads a confidence tier
    until there is ingested data to rate, so this was Phase 1 work — but it
    was the one table whose absence would have been invisible, because a
    `LEFT JOIN` against an empty lookup returns NULL tiers and the UI shows
    "unrated" rather than erroring.
    **Complication found 2026-09-09**, resolved by building the seed from
    what the ingest actually produces rather than from this file:
    `reference/sources.csv` (a *different* file — the statistical extract,
    not the curated seed) holds 36 rows,
    two of them truncated singletons — `DEPARTMENT OF HIG` (from the malformed
    2026-08-31 row the ingest **rejects**, so it can never arrive through
    `iem_data`) and `DEPT OF` (from 2019-03-09).
    **Update 2026-09-10:** `DEPT OF` is no longer unreachable. The archive floor
    moved to `2004-01-01` (§2, decision-log 2026-09-10), so the 2019-03-09 row is
    now in `iem_data` (1 row, alongside 2,655 well-formed `DEPT OF HIGHWAYS`). The
    producible-source set is now 35 of the 36 — everything except
    `DEPARTMENT OF HIG`, which only ever appeared in a row the ingest rejects.
    Both truncated values are harmless either way (a lookup, not a constraint),
    but the seed should still be built from what the ingest can actually produce,
    not from a raw scan of the archive. See the `SOURCE` truncation trap in
    `docs/data-sources.md`.

Also open and blocked on RBI rather than on us: **DNS access and existing
subscription status**, needed for the Phase 5 sending identity. The ask starts
early because DNS changes at a small company can sit in an inbox for weeks.
**Still unrecorded as of 2026-09-24**: nothing in the repo says whether RBI has
answered (parking-lot item 96). Phase 5 is now current, so this is the item
most likely to be waiting on someone else.

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
