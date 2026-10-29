# CLAUDE.md

Project context for Claude Code. Read `docs/database-schema.md` before touching the database.

## What this is

A storm outreach system for Roof Brokers, Inc. (RBI), a Front Range roofing
contractor. It pulls free NWS storm reports nightly, maps them to affected zip
codes, lets staff pull real estate listings in those areas from RentCast, and
send templated email to the listing agents.

Single developer. Two machines — a `hail-dev` VM and the production
VM on an HP ProDesk host (`pve-prod01`), deliberately built alike. See `docs/hail-consolidated.md` §9.

## Current phase

**Phase 5 — Email.** Phases 0–4 are closed: 0 on 2026-09-03 when
a spatial query returned the zip codes within 5 miles of an arbitrary
lat/lon, 1 on the IEM ingest and archive backfill going live, 2 on
2026-09-17 with the storm-browser web app (Flask, Tailscale-reached) and its
CSV export built and running, 3 on 2026-09-21 with RentCast pulls,
storm-to-listing matching and the match page working through the web UI, and
4 on 2026-09-24 when a viewer account could browse and export but not pull.

**Caveat on "Phase 0 closed":** that call was made against Phase 0's own
done-when bar — the spatial query — not against every item Phase 0's task
list carried, and the physical production build (racking it at RBI's office,
static IP, UPS, Irin enrollment) is one of those items. **Update 2026-10-20:**
the hardware has arrived and passed acceptance (`pve-prod01`, an HP ProDesk 400 G6
running Proxmox VE; `pbs-prod01`, a separate Dell OptiPlex 3020 running Proxmox
Backup Server; `docs/hail-consolidated.md` §9), and the production VM
`rbi-hail01` (VM 101, Rocky Linux 10.2) is **created but not started**. Nothing is
deployed to it: no Docker, no database, no code. Racking, UPS and Irin enrollment
are not confirmed here. Everything verified so far has run on the `hail-dev` VM.
Do not assume the production box is reachable or has anything deployed to it
without checking.

What Phase 3 delivered: `hailsys/rentcast/` (`client.py`, `estimate.py`,
`pull.py`, `upsert.py`); `sql/013`–`017` (linking a pull back to the storm it
was pulled for, `properties.geom`, match attribution, an `ingested_at` index,
and `report_zip_distances`); `hailsys/matching/matcher.py` writing
`storm_listing_matches`; and the UI for it — `/pull/estimate`, `/pull`,
`/match`, `/storms/matches` and the activity feed. A pull runs in a background
thread and matches automatically when it finishes. (No sending path existed then.)

What Phase 4 delivered: roles enforced server-side —
`role_required("sender", "admin")` on `/pull/estimate`, `/pull` and `/match`,
`login_required` on every other signed-in route, and the `/admin` blueprint's
own `before_request` admin check. The UI greys actions a role can't take
rather than hiding them. `/admin` covers users (add, role, sign out,
deactivate, reactivate, reset password; not on your own row) and the
`settings` table: radii, RentCast billing day and monthly quota, and this
period's usage, with every change logged to `settings_history` by trigger.
Also: forced logout by timestamp (`sql/018`), last-admin protection
(`sql/019`), radii in `settings` (`sql/020`), `match_runs` so an empty match
reads "Matched, none in range" (`sql/022`), quota settings (`sql/023`), a
re-pull link, quota warn-and-allow on the pull estimate, and CSRF on every
POST (a failure returns a 400 page). `sql/021` (`municipal_boundaries`) is
from the parked permits research, not Phase 4. `scripts/create_user.py`
stays as a bootstrap-only path (`docs/parking-lot.md` item 65, closed 2026-10-01).

What exists for Phase 5 so far, as of 2026-10-15, on `hail-dev` only (the production
box has none of it): a Constant Contact send path, **built and rehearsed but never used
to send to a real recipient**. The seven `sent_emails` rows are rehearsal sends to test
addresses.
- **Provider access and suppression.** OAuth grants in `oauth_tokens`, and the
  unsubscribe sync into `dnc_list` (`cc_sync_runs`, `cc_sync_conflicts`, admin `/cc`).
  `dnc_list` holds 768 active suppressions (the 759 imported 2026-09-29 through the admin upload,
  `/admin`, `sql/026`, `hailsys/queries/dncimport.py`, from the Constant Contact export plus
  the Airtable-only entries, one from the sync, and 8 uploaded 2026-10-28). RBI's newsletter
  account is separate, so its unsubscribes are uploaded by hand from Constant Contact's
  notification emails (item 129, accepted 2026-10-28), as a CSV or typed into the admin page's
  form, recorded with `source = 'newsletter'` (`sql/061`, items 268 and 269).
- **What gets sent.** `sent_emails` is the email as sent, one per realtor per send, and
  `send_log` keeps one row per match, pointing at it (design: item 180; one email per house:
  item 55). Eligibility is `settings.match_max_age_days`, `email_cap_days` and
  `listing_freshness_days`. Templates are chosen and described in the UI (item 202). The email
  images, contact email, schedule link and sender identity are edited on the admin page
  (`email_images`, `settings`; item 203 piece 1), not in `docker-compose.yml`.
- **Template builder (item 203, all six pieces).** `hailsys/email/blocks.py` compiles a block list
  into the Jinja pair the engine renders (typed text is escaped and never becomes template syntax);
  `templatestore.save_blocks` saves it as a new version with `email_templates.blocks` (`sql/043`). The
  admin-only `/templates` editor (`hailsys/web/emailtpl.py`) builds, previews, saves and retires block
  templates; its JavaScript is checked in a Node container (item 222). A golden test pins the block-built
  `hail_alert` to the shipped file. The built-in template still renders from its files (item 220). Not on
  the production box: `sql/039` to `051`. `sql/045` to `047` are the drip-campaign settings and tables (item 204, in progress: the campaign screens at `/campaigns` and the follow-up part of the `/send` confirm page exist; no campaign has been run yet, the dev rehearsal is piece 7). The editor's "Send a test to <your address>" button
  (`testsend.py`, `email_test_sends`) emails the draft to the clicking admin only, three Constant Contact
  calls, at most 5 an hour; it is not a send. The editor has a formatting guide, a toolbar on the rich text boxes (`templates.js`)
  and a two-column layout with the preview beside the form.
- **Sending and monitoring.** The admin-only `/send` screens (preview, confirm, start,
  batches, resume, discard, bounce check; `cc_status_runs`). A call counter,
  `cc_api_calls`, makes the engine stop at Constant Contact's daily limit (item 191).
- **Append-only in the database.** `send_log`, `email_templates`, `sent_emails` and the
  Constant Contact log tables and `email_test_sends`, by triggers (`sql/029`, `030`, `034` to `040`, `044`; applied to
  `hail-dev`, not checked on the production box; `sql/guard_test.sql` tests them). The
  table owner can still `TRUNCATE`, accepted (decision log 2026-09-30).
- **Before a production send.** Items 183 (the paid account), 189 (the eligibility
  gate) and 96 (the sending domain), and a production box that has the code. Provider
  chosen 2026-10-06: Constant Contact (decision log, item 119).

What exists for Phase 5b so far, as of 2026-10-22, on `hail-dev` only (decision log 2026-10-20 waived "do not
build ahead" for this phase). Radar layers for the territory page, internal and staff only: nothing radar-derived
reaches an email, an export, a pull or the matcher.
- **Data and zones.** `sql/048` to `051`; `hailsys/radar/` decodes NOAA's public MRMS `MESH_Max_1440min` file (the one
  stamped at the local midnight that ends the day, 2020-10-14 on), stores the cells, and builds four layers per
  day in PostGIS: core (radar hail with a HAIL report within the radius; item 254), margin, radar-indicated, and outer
  (NWS warning polygons). Thresholds live in the
  append-only `radar_parameter_sets` (newest row in use); `settings.storm_states` names the warning states.
- **Jobs.** `scripts/radar_ingest.py` (`nightly`, `rebuild`, `backfill`, `warnings`), `scripts/radar_day.py` for one
  day; `systemd/radar_*` timers at 10:30 UTC nightly and Sundays 11:30 (installed and enabled on `hail-dev`, 2026-10-22); `scripts/status.sh
  radar` and a line on the Storm Days page. The `ingest` image now imports `hailsys/db.py`, `tuning.py` and
  `hailsys/radar/`, so rebuild it after changes to them.
  The backfill ran 2026-10-22: 2,197 days held, 2020-10-14 to 2026-10-21 (2 absent at NOAA).
- **The map (slice 5).** The Territory page has a Radar day picker, a layer toggle box and a legend; the layers come
  from `/map/radar.geojson` (staff only, read only, `hailsys/queries/radar_map.py`). The circle radius is the match
  radius setting (item 216, resolved). Storm Days, Territory and the map open on HAIL (`DEFAULT_REPORT_TEXT`, item 238).
  A MESH value above 10 in (254 mm) is NOAA's processing, not hail: such a day is recorded `suspect` and not drawn (item 247).
- **The findings panel (slice 6, built 2026-10-23, items 251 and 252).** Under the map, for the radar day picked:
  `/territory/findings`, `hailsys/queries/findings.py`, `_findings.html`. The day at a glance, reports against the layers, the report
  circles against core and margin, and a matrix of held houses by radar layer against distance to the nearest actionable hail
  report, with the draft tier letters as a labelled annotation. Staff only, read only, counts and no addresses. About 8 s a day.
- **Slice 7 (built 2026-10-26, items 243 and 256):** the admin page's Hail zones section sets the radar threshold, margin and report radius, the
  CoCoRaHS size floor, the three distance edges, the 20-level grid and `storm_states`, adds each change as a new set (`sql/056`,
  `hailsys/queries/likelihoodadmin.py`, `_hail_zones.html`), lists the sets with "use these values again", and has a "try it on a day" button
  that builds one day both ways and keeps nothing. A rebuild of earlier days is still a command the page names.
- **Not built:** the rebuild-all-days button and calibration, inspection data
  (slice 8). The radar database tests need `hail_admin` (item 241).

**Phase 5c — the hail-likelihood map and unified pull.** Design accepted 2026-10-23 (decision log "Design: one hail-likelihood map per
storm day, one pull from it", with the developer's answers); **step 1 only** (the CoCoRaHS ingest, likelihood zones, a staff-only map
layer, a versioned parameter set, the backtest) was waived in the same day ("Phase 5c starts"), internal. **Built so far (2026-10-23, `hail-dev` only): the CoCoRaHS ingest (step 1a)** — `sql/053`,
`hailsys/cocorahs/`, `scripts/cocorahs_ingest.py` (`nightly` by report timestamp, `weekly` by report date, `backfill`), timers at 11:00 UTC
nightly and Sunday 12:00 UTC, `scripts/status.sh cocorahs`; Colorado backfilled from 2020-10-15 (2,729 reports).
**Step 1b (2026-10-24): the likelihood zones** — `sql/054` (`likelihood_parameter_sets`, `likelihood_level_rules`, `likelihood_builds`, `likelihood_zones`),
`sql/055` (`likelihood_runs`), `hailsys/likelihood/` (`build.py`, `ingest.py`), `scripts/likelihood_ingest.py` (`nightly`, `rebuild`, `backfill`),
timers at 11:30 UTC and Sunday 12:30 UTC, `scripts/status.sh likelihood`; all 2,195 loaded radar days built. A day's levels (Agree, One source,
Context) come from the radar layer and the distance to the nearest hail report (IEM and CoCoRaHS), by the set's rules; hail reports only (item 254). **The staff-only map layer (2026-10-25)** on the Territory page (`/map/likelihood.geojson`, `hailsys/queries/likelihood_map.py`; Agree and One source on, Context off; the radar NWS warnings layer is now off until ticked). The backtest ran 2026-10-25: passed on the zip-overlap measure, IEM hidden 88.7% and CoCoRaHS hidden 93.5% (item 255 resolved; exact-point 63.5% and 85.1%). **The admin fields for the parameter set (slice 7, 2026-10-26)** are on the admin page (see Phase 5b). Step 1 is built.
**Step 2a (waived in and built 2026-10-26): the estimate compared with the zones, read only, no pulls** (`/pull/zones`, `hailsys/queries/zonepull.py`, `pull_zones.html`; **built 2026-10-26**, with a link from `/pull/estimate` for hail days).
Pulls and matching are unchanged: **steps 2b (the pull from the zones) and 3 (matches with `basis` and `level`) are waived in together (decision log 2026-10-26). Step 3 is built (2026-10-27):** `sql/057` and `058` (`storm_listing_matches` gains `storm_date`, `basis`, `level`, `layer`, `set_id`; only a `report` row has an `iem_id`; `send_log` refuses a zone-only match), `hailsys/matching/zonematch.py` and a staff button on `/pull/zones` that writes zone rows for a built day within `match_max_age_days` (30). Zone rows are invisible to every reader (tests pin it). **Step 2b is built (2026-10-27): the pull from the zones** (`sql/059`: `api_pulls.pull_source` and `zone_set_id`, the append-only `pull_zips`; `start_pull(zone=...)` in `hailsys/web/jobs.py`; `POST /pull/zones/start` and a pull-status table on `/pull/zones`). Every pull is a person's click after the zip count and estimate are shown; the zip list is rebuilt from the database, never taken from the form; a pull never writes a zone match. The developer's first real pull (pull 1228, made 2026-10-27 for storm day 2026-09-29, one zip, 1 request, 158 listings) is the only RentCast spend from the zones so far.
**Step 4 (sends by level) is waived in (decision log 2026-10-27), built and rehearsed on test addresses only, all six pieces built and rehearsed 2026-10-28** (`hailsys/queries/zonesend.py`, the zone send list: the report path's eligibility rules plus a cross-kind house-and-day rule and an explicit observer-wording reason; `hailsys/email/zonewording.py` the draft `hail_alert_zone` template, and (3b) `hailsys/email/zonecontext.py` with kind-aware `render.validate_template`/`render` and the zone template registered by `templatestore` as `match_kind` 'zone'; (4a) `queue_batch` and `run_send` take a pinned `zone=` and write each match's own basis, level and set to `send_log`; (4b-1) `templatelist` filters by kind and the report send page and the block editor list report templates only; (4b-2) the admin Hail zones section has the observer-wording checkbox; (4b-3) `/send/?mode=zones` (`hailsys/web/sendzone.py`, `send_zone.html`, `send_zone_confirm.html`, the mode switch) previews, confirms and starts a zone send with the set pinned; (5) `/send/levels` (`hailsys/queries/levelreport.py`, `send_levels.html`) reports bounces, complaints and unsubscribes by level, read only, with report emails split by the level at the house (item 266: `zonesend.report_levels`, looked up when a report send is queued); (6) rehearsed 2026-10-28 with `sql/dev_rehearsal_seed_zone.sql`: two radar One source emails to the developer's own `+t8`/`+t9` addresses, sent through Constant Contact; `sql/060`: `email_templates.match_kind`, a basis, level and set snapshot on `send_log`, the pairing trigger `trg_send_log_kind_pairing` that replaced the blanket guard, and `settings.zone_observer_wording_approved`, false until the CoCoRaHS policy is read); nothing zone-derived reaches a real recipient until items 183, 189 and 96 are settled (item 129 accepted 2026-10-28). Nothing CoCoRaHS- or likelihood-derived may reach an export or the report matcher.

Phases in order: 0 groundwork → 1 IEM ingest + zip mapping → 2 storm browser
with CSV export → 3 RentCast listings → 4 accounts → 5 email → 5b radar layers
on the territory page (internal) → 5c likelihood map (built through sends by level, on `hail-dev`; real
recipients wait on items 183, 189, 96) → 6 pilot → 7 rollout.

Do not build ahead of the current phase.

## Stack

- Rocky Linux VM on Proxmox (HP ProDesk 400 G6, backed up to a separate PBS host), on RBI's office network
- PostgreSQL + PostGIS
- Python backend
- Web UI, reached over Tailscale for Phase 2 (Cloudflare tunnel revisited at
  Phase 6 — see `docs/decision-log.md`, 2026-09-14)
- Deployed with Ansible where practical

## Non-negotiable rules

**Nothing sends email automatically.** There is no code path from the nightly
ingest to an outbound message. A human clicks send. Do not add scheduled sends,
auto-followups, or "helpful" automation around sending.

**The suppression check runs against `dnc_list` at send time.** Not in the UI,
not from a cached list, not from a flag on the realtor row. If a rule must hold,
it holds in the database. With an external provider (Constant Contact) the HTTP
call cannot share a transaction with the check, so: the send list excludes
`dnc_list`, the check runs again immediately before each call, and the `send_log`
row is committed first. See decision log 2026-10-06.

**Storm reports are never deleted and never collapsed to zip codes at write
time.** Full lat/lon fidelity in, zips derived on read. The buffer radius is a
tuning parameter.

**`send_log`, `email_templates` and `sent_emails` are append-only.** Templates are superseded,
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
  on commas. But a real CSV parser only **detects** these — it cannot repair
  them, because the quotes were never written and the field boundary is
  unrecoverable. They are rejected as `field_count_mismatch`; `raw_row` keeps
  the line verbatim, which is what makes rejecting non-lossy.
- **`Decimal()` accepts `'NaN'` and `'Infinity'`** without raising. A NaN
  coordinate then makes an ordered comparison *signal* `InvalidOperation`, so a
  range check raises and the exception escapes the parser and ends the run. And
  Postgres `NUMERIC` accepts `NaN`, so an unguarded magnitude lands in the
  column and reads as a real measurement. Guard with `is_finite()` before any
  range test.
- **A misspelled IEM filter parameter is silently ignored, not rejected.**
  `typetext=` and `magnitude=` return the full unfiltered set with HTTP 200;
  the real names are `type=` and `magge=`. (We must not filter at ingest
  anyway — this is a trap for anyone reading the old docs.)
- IEM `QUALIFIER` of `M` (measured) on hail **does not mean instrument-measured**
  — it tracks reporter training. Use `SOURCE` if a confidence signal is needed.
- Census TIGER ships in **NAD83 (4269)**; IEM and RentCast are **WGS84 (4326)**.
  Reproject at load. Mixing them fails silently.
- RentCast `id` is a **property** id, not a listing id. A relisted house reuses it.
- RentCast agent email is **frequently missing**. Handle null.
- IEM's warning export (`cgi-bin/request/gis/watchwarn.py`) with `limitps=1` and **no explicit
  `phenomena=` list returns tornado warnings only**, silently. Pass `phenomena=SV,TO&significance=W,W`.
  Its shapefile truncates field names to 10 characters (`PRODUCT_ID` arrives as `PROD_ID`).
- NCEI Storm Events times are local **standard** time all year (`CZ_TIMEZONE` `MST-7`): an hour off
  Denver summer time if read as local.
- IEM's JSON API `sbw_interval.geojson` **silently ignores** `states=`, `state=` and `phenomena=` and returns every
  warning in the country (620 for two days); only repeated `wfo=` filters it. The shapefile export `watchwarn.py`
  honours `states=`. Radar uses the shapefile.
- HRRR u/v winds are **grid-relative**: rotate to true north (about -4 degrees near 104 W). eccodes names
  the 10 m wind `10u`/`10v`, not `u`/`v`, so a lookup on `u` silently drops it.
- The CoCoRaHS hail export's header has 24 columns and **`MoreRainThanHail` appears twice**. A dict reader keeps one and
  silently drops the other, so `hailsys/cocorahs/parse.py` reads by position and refuses any header that is not exactly
  the expected one. `NA` means missing, never 0. A station's daily report covers the 24 hours ending about 7 AM on its
  date, so storm day D is read from the D+1 report.
- In SQL, a name in **`GROUP BY` that is both an input column and a `SELECT` alias binds to the input column** (`ORDER BY` does the
  opposite). A query with `GROUP BY storm_date` over an alias broke when `storm_listing_matches` gained a `storm_date` column
  (items 258): group by the expression, and run every reader of a table after a migration adds a column to it.
- **A `CHECK` passes on NULL.** `level IN ('agree', 'one_source')` with a NULL level is NULL, not false, so the row is accepted. Say
  `IS NOT NULL` where a value is required (item 259).

## Conventions

- Ask before installing anything not already present.
- Prefer stdlib and boring dependencies.
- Scripts that ingest external data must be idempotent and safe to re-run.
- Failures should be loud. Silent partial success is worse than an error.
- Comment the *why*, not the *what*, especially around the traps above.
- Anything an operator may need to add or change has a UI access point (the admin page), with
  changes logged to `settings_history`: not a config file, an environment variable or a code
  constant. Fixed ceilings and safety limits stay in code and are documented as such (the
  Constant Contact daily limit and headroom, the suppression rule, `MAX_EMAILS_LIMIT`). Decision
  log 2026-10-13. Done for the email images and sender (decision log 2026-10-14).
- `web`, `app` and `loader` bind-mount the repo (edit = deploy). **Templates reload from disk but Python does not:** restart `web`
  (`docker compose restart web`) after changing `.py` files, before relying on a template that uses them (item 260). Only `ingest`
  runs image-baked code, on purpose, because the nightly timers should run a
  known artifact -- rebuild it before testing ingest changes, and run
  `scripts/status.sh images` to see whether it is behind. See
  `docs/command-ref.md`.

## Findings get filed

**A problem found in review, audit or debugging is not recorded until it is in
`docs/parking-lot.md`.** A line in chat, a handoff note or a commit message is
not a record: it is gone when the session ends. A review that reports problems
and files none of them has not finished.

- **Propose the item in the same message that reports the finding**: a number,
  a `**Status:**` line, the evidence, and a `**When:**` trigger. Filing is still a
  docs edit, so wait for the yes as usual.
- **Check first that it is not already filed.** Grep the parking lot and the
  decision log for the file, function or symptom before proposing a duplicate.
- **If it is fixed in the same session, file it anyway**, as `resolved` with the
  date and commit. The parking lot is the list of problems that were found, not
  only the ones still open.
- **If a finding is accepted rather than fixed, file it with its revisit
  trigger.** A decision to live with something is a decision, and needs a
  *When*.
- **If the session may end before anyone answers, end the final message with a
  list of the findings not yet filed.** An unanswered proposal is not a filed
  item.

**Why:** the pass-1 audit checked whether *filed* items were still open. It could
not check whether *identified* problems had ever been filed, because nothing
records that. Three review findings (export filename sanitising, CSV formula
injection, agent contacts visible to viewers) were reported in chat and
survived only as a line in a handoff.

## Briefs and handoffs are claims, not evidence

A brief, handoff or summary pasted into a session describes what someone
remembers. Verify each factual claim in it (a file, a line number, a row count,
what a function does) against the repo and the database before writing it into
a doc or acting on it, and report where the evidence differs. Where the two
disagree, the evidence wins, and the entry says so.

**Why:** the brief for the filename and CSV findings said validation was against
all of `report_types` (it is the 12 roof-relevant types), that the
reference-table CSVs are loaded into Postgres (the curated `planning/` seeds are),
that there were six CSV writers (seven), and pointed at a line that was already
gone.

## Ask before editing

Verification questions are not work requests. "What's the status of X",
"is Y still open", "does the log have an entry for Z" ask you to read and
report, not to fix what you find. Report what's there, name what's missing,
and stop.

Propose the change and wait for a yes before editing any file — code, SQL,
or docs. This holds even when the fix is obvious and already agreed in
principle, because priorities shift between the decision and the moment,
and an item that was next last week may have been deprioritized since.

This also holds when something is actively broken. "Can you take a look"
at an error is a request to diagnose, not a standing invitation to patch
it — name the bug and the fix, then stop and wait, the same as any other
finding. An outage doesn't waive the rule; it's not an emergency-fix
exception, it's the default for every edit, with no case-by-case judgment
call about how obvious or urgent the fix seems.

## Working style

The user is teaching himself as this is built — Bash, Docker, Python, Postgres.
Explain reasoning briefly when making a non-obvious choice. Do not silently
refactor working code. Do not add features that were not asked for.

Plan first, build second.

**Code is keyed in by the user, not written by Claude.** For anything that is code
(`.py`, `.sql`, `.yml`, shell scripts, templates, config), do not edit or create
the file and do not run a build or install. Print the exact text and where it
goes, as numbered instructions, wait for the user to say it is done, then read the
file back and check it against what was given. This is how the user is learning.
Documentation (`.md`) is different: once the user approves a proposed change,
Claude makes the edit. Reading, diagnosing and running read-only checks are
unaffected.
