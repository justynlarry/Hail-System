# Hail-System — parking lot, carried into Phase 2

Open items deferred rather than dropped. Each carries the reasoning, because
several were argued through at length and the conclusion alone does not survive
the argument being forgotten.

Written 2026-09-11, at the close of Phase 1. Repo is at commit `9d48760` plus
whatever Phase 1's final commits added. Committed to the repository 2026-09-14;
before that it was carried between working sessions by hand, which is the
problem this file now exists to remove.

**Status and layout (2026-09-30).** Every item carries a `**Status:**` line:
`open`, `open (watch)` (no work attached, a trigger to notice), `open (parked)`
(gated on something named), `resolved`, `resolved (residuals)` (shipped, with
named leftover work), `deferred`, `dropped` or `superseded by <n>`. Items are
grouped by status: Open, Watch list, the parked permits workstream, the two
unnumbered notes, then Closed (resolved and dropped items) at the end.
Item numbers never change. Items 128 and 131 were informational and moved to
`docs/decision-log.md` (2026-09-30 entries); their numbers are left vacant.

---

## Read these first

**`docs/decision-log.md` is the authority.** `docs/hail-consolidated.md` and
`docs/data-sources.md` are derived summaries and have drifted in *both*
directions — `data-sources.md` once lagged four days behind a settled decision,
and `hail-consolidated.md` §7 once described a code fix that had not been
written. When they disagree with the log, the log wins.

This cost more time than anything else in Phase 1: a finding was re-derived
from scratch because it was read out of `data-sources.md` rather than the log,
where it had been recorded four days earlier.

---

## Open — items needing work

*49 items: `open`, `open (reopened)`, `open (parked)` on something other than item 70, and `deferred`.*

## 3. The base image is on an EOL operating system

**Status:** open

`postgis/postgis:16-3.4` is built on Debian bullseye, which went EOL
2026-09-07. The day after, the loader build began failing on expired apt
`Release` metadata; `apt-get -o Acquire::Check-Valid-Until=false update` is the
workaround now in `docker/loader.Dockerfile`.

This is standing, not a one-off. Every `postgis/postgis` tag checked — `16-3.4`,
`16-3.5`, `17-3.5` — is still bullseye-based, so there is no clean fix by
changing tags. The pinned client package (`postgis=3.5.2+dfsg-1.pgdg110+1`,
`pgdg110` = Debian 11) has to be bumped in the same move whenever upstream
rebases.

Risk is narrow but real: the container has no published ports and sits on an
internal Docker network reachable only by two other containers, so the attack
surface is the Postgres protocol from inside `hailnet`, not the Debian
userland. Options are the alpine variant, building from `postgres:16-bookworm`
plus the PostGIS extension, or waiting for upstream.

**When:** before RBI deployment. Not acceptable to ship to a box on a company
network and forget.

**Gate, 2026-09-30:** recheck when the production machine is set up; its hardware is still pending receipt. Not a Phase 5 fix. Verified 2026-09-30 that the running `postgis` container still reports Debian 11 (bullseye) and that `docker/loader.Dockerfile` still carries the workaround. The `web`, `app` and `ingest` images are `python:3.12-slim` and are not affected; the registry was not checked for a newer upstream tag.

**Update 2026-10-02.** A from-scratch `loader` build (`--no-cache`) has not been
run since the base's EOL date. The 2026-10-02 `docker compose build loader` hit
the layer cache throughout, so whether the `Check-Valid-Until=false` workaround
still builds against the current bullseye repos is untested. Expect to find out
when the production box builds the image; a failure there is this item's risk
arriving, not a new one.

## 9. The absence query has no schedule and nowhere to alert

**Status:** resolved (residuals) 2026-10-02

Ingest health is an **absence** query, not a status query:

```sql
SELECT max(finished_at) FROM ingest_runs
 WHERE run_mode = 'nightly' AND run_status = 'complete';
```

Older than ~30 hours is the alert. A status column cannot express this: a
crashed run leaves `running` forever and reads as healthy-in-progress, and a
run that never fired leaves no row at all.

`OnFailure=` catches a run that failed. **Nothing catches a run that never
fired**, which is the failure the system cannot report about itself. The
systemd units deliberately omit `OnFailure=` because there is nothing to notify
yet.

Also note the alert condition on skips was **inverted** from an earlier draft:
`rows_skipped > 0` fires during normal operation — the unquoted-comma CITY rows
recur — so it would be muted. The condition worth alerting on is a sustained or
sudden rise.

**When:** when there is a notifier. Irin is the intended destination; nothing
is wired up and no alert fires today.

**Resolved (residuals) 2026-10-02.** The absence query is built and shown.
`ingest.fetch_health()` takes the age of the last completed nightly
(`max(finished_at)` where `run_mode = 'nightly'` and `run_status = 'complete'`)
against `INGEST_STALE_AFTER` (30 hours, `tuning.py`), and `_ingest_health.html`
on the Recent Storm Days page reads "Ingest OK", "Ingest STALE: no nightly has
completed in N hours", or "no nightly run has ever completed". **Residual:**
nothing pushes it. Someone has to open the page, and a dead box shows no banner
at all. **When:** revisit once there is a notifier (Irin was the intended
destination). The note above about the inverted skip condition still stands.

## 11. Hail size names for email templates

**Status:** open

StormerSite renders hail as *"Golf Ball"*, *"Half Dollar"*, *"Ping Pong"*
alongside the inch measurement. An agent reads 1.75″ fine; a homeowner says
"golf ball sized."

`report_types.mag_unit` already distinguishes inches from mph, so the mapping
has somewhere to live. The email wording rule stands: the message claims a
**report**, never damage.

**When:** Phase 5, when templates are written.

## 15. Does the frequency cap have a hard floor?

**Status:** open

Decided in principle: a short window nobody can click past, plus a soft
warning above it. The actual numbers are unset, and the hard floor needs
enforcing in the database rather than the application, or it is not really a
floor.

**Depends on item 14** — the settings table is where the window value would
live.

**When:** Phase 5, when sending is built. *(`database-schema.md`, open
question 5)*

**Input from item 121, 2026-10-02.** Measured against the real data, sending per
storm over six weeks of HAIL sums to 5,628 agent-sends against 3,259 distinct
agents if consolidated: about 42% fewer. The cap should be designed around
windows of agents, not per-storm counts, and a consolidated email still has to
name the storm day(s) it refers to (the un-addressed half of item 106).

## 16. Merge field vocabulary — where is it stored?

**Status:** resolved 2026-10-08 (decided: in code, not a table)

Established that the merge-field list for email templates should be reference
data, not a hardcoded list, but it is not yet designed. Likely a small table:
placeholder name, source expression, whether it's required.

**When:** Phase 5, when templates are written — same phase as item 11 (hail
size names), which needs the same table. *(`database-schema.md`, open
question 6)*

**Resolved 2026-10-08 (`ef4eeb2`).** The vocabulary is `ALLOWED_VARS` in `hailsys/email/render.py`.
A template is checked against it when it is saved (`validate_template`), which rejects any
placeholder not on the list, so a typo cannot reach a recipient. A table was not needed:
every placeholder is tied to code that supplies its value, so a row without code behind it
would be a placeholder that cannot be filled. A placeholder is added by changing that list
and the code together. (Item 11, hail-size names, did not need the table either; hail size
goes through `hailsys.formatting.magnitude`.)

## 19. Does outreach ever fall back to the office email when an agent has none?

**Status:** resolved 2026-10-02 (decided: no)

`listingAgent.email` is frequently missing; `office_email_norm` and
`list_office_email_norm` exist so a batch can be pre-flighted against
`dnc_list` either way. Whether we would ever *send* to an office address is
unsettled.

Suppression already handles this correctly — the check runs against the
address actually used, not against a person, so a suppressed `info@` inbox is
safe by construction. **The frequency cap does not:** fifteen agents at one
brokerage with no email of their own all resolve to a single `info@` inbox,
each with its own `realtor_id`, so a per-realtor cap counts fifteen separate
sends and the shared inbox receives fifteen emails from one batch — and a
shared inbox is the least tolerant recipient on a list.

If this is ever built, two things change: the cap needs a per-address window
alongside the per-realtor one, and `send_log` needs to record whether the
recipient was a person or an office. Without that column, `realtor_id`
quietly stops meaning "who we emailed" and starts meaning "who this was
about" — a different fact under the same name.

**When:** Phase 5, when sending is built. *(`database-schema.md`, open
question 9)*

**Resolved 2026-10-02: decided, no.** Outreach never falls back to an office
address. A realtor with no email of their own gets no email. The only address
used is the agent's own listed email, even if that is a shared inbox the agent
chose to list. So the per-address frequency window and the person-or-office
column in `send_log` that this item described are not needed. Checked
2026-10-02: no email is shared by more than one `realtors` row (9,163 rows, all
with an email), so the shared-inbox cap problem is absent from the data today.
**Not enforced anywhere yet:** the Phase 5 send path must use the agent address
only and never an office one. `office_email_norm` stays in use for the
suppression flag only. Revisit if the decision changes.

## 26. PDF export with table and map

**Status:** open (parked) — gated on Phase 6

Depends on how the map got built; the three options are a headless-browser
print, a server-side static map render, or a `@media print` stylesheet.

**When:** after the map.

**Re-gated 2026-09-30:** PDF export is a Phase 6 item. No PDF code exists (checked 2026-09-30; the only hit is Leaflet's own `@media print`).

## 27. CSV header labels

**Status:** dropped 2026-10-02

`"Closest Report (mi)"` / `"Farthest Report (mi)"` (0 = inside the zip),
`mag_unit` folded into the magnitude values, timestamps as
`MM-DD-YYYY HH:MM:SS`. Applies to both the CLI and web exports, since they
share `ZIPS_COLUMNS`.

**Scope grown, 2026-09-26.** This item's text describes only the original
zip export. There are now five: the zip export, per-storm matched listings,
the bulk matched-listings export, the realtor list, and whatever `sql/024`/
`025`'s work eventually surfaces in a properties-facing export — all
sharing the same raw-column-name convention (`report_text`,
`max_magnitude`, `agent_dnc`, and so on). Fixing this is a wider job now
than when it was filed against one export.

**When:** before the PDF.

**Dropped 2026-10-02.** The developer reviewed the CSV headers and decided they
are fine as they are. Nothing was changed: the raw column names
(`report_text`, `max_magnitude`, `agent_dnc` and so on) stay across all the
exports. Revisit only if someone outside the developer starts reading the files
and the names get in the way, or when the PDF (item 26) is built and needs
display labels.

## 29. Extract the repeated filter parsing

**Status:** resolved 2026-09-18

Five routes now duplicate the `days`/`type`/`actionable` parsing block.

**When:** next time a route needs it.

**Found resolved 2026-10-02.** The description was stale. `854f5bc`
(2026-09-18) added `_window_from_args()` and `_actionable_from_args()`, each now
called from 8 places in `views.py`. What is left is the one-line
`request.args.get("type") or None` read (11 sites), accepted: extracting a
single `.get()` saves nothing. `storm_matches()` (`views.py:299`) reads
`actionable` itself because its default differs (on until the form is
submitted), so it is not a duplicate. Found by the 2026-10-02 sweep (item 154).

## 30. Bind-mount `hailsys/` into the `app` service

**Status:** resolved 2026-10-02

So one-off checks reflect the working tree rather than the last build. Cost
us time twice.

**When:** soon; dev-only.

**Findings, 2026-10-02.** Checked before changing anything. (1) Nothing runs
`app` unattended: both systemd units run `ingest`, and the only references to
`app` are `docs/command-ref.md` and `scripts/verify_zip_distances.py`'s
docstring, so it is safe to make `app` read the working tree; `ingest` must stay
baked (its timers should run a known artifact, `command-ref.md`). (2) **The
existing `./hailsys/web/static:/app/hailsys/web/static` mount (`9c566a0`,
2026-09-16) is read-write on purpose:** `scripts/build_coverage_geojson.py` and
`scripts/build_colorado_counties_geojson.py` write their GeoJSON into
`hailsys/web/static/` through `app`. A new `./hailsys:/app/hailsys:ro` mount
would make those writes fail unless that more specific read-write mount stays in
place, so it has to be kept, not replaced. (3) `scripts/` is baked into `app` too
and `verify_zip_distances.py` already works around it with `-v`; mounting
`./scripts:/app/scripts:ro` as well makes that workaround unnecessary. Proposed
and not yet applied: add the two read-only mounts to `app` and keep the static
one.

**Resolved 2026-10-02.** `app` now mounts `./hailsys:/app/hailsys:ro` and
`./scripts:/app/scripts:ro`, and keeps its read-write
`./hailsys/web/static` mount on top (the GeoJSON builders write there).
**Verified** with code written after the `app` image was built (2026-09-29):
`hailsys.web.jobs.PullInProgress` and `hailsys.rentcast.pull.create_pull` are
both visible through `app`, and a `touch` and `rm` in `hailsys/web/static`
succeeds. A first check ("does `jobs.py` exist") would have passed on the baked
image too, and a first edit left out the `hailsys` line; both were caught by
testing for a symbol that only the working tree has. `ingest` is unchanged and
still baked, on purpose. Not applied to the production box.

## 32. `StrictUndefined` in the Jinja environment

**Status:** resolved 2026-10-02

Missing template variables currently render blank rather than raising,
which has produced two silently-wrong pages.

**When:** soon.

**Resolved 2026-10-02.** `create_app()` sets
`app.jinja_env.undefined = StrictUndefined`. Every page was loaded with the
setting on and none raised `UndefinedError`; a March storm was pulled
end to end. Which two pages were silently wrong before is not recorded.

**Regressions found after resolving, fixed the same day.** The first
walk-through loaded every page, but only in one state each, and `StrictUndefined`
fails on a path, not a page. Three misses followed:
- `base.html` tested `session.emp_id`, undefined for a visitor with no session,
  so `/login` returned a 500 when signed out. Fixed with `session.get('emp_id')`.
- `/admin/` returned a 500 on every plain GET: `_render_admin()` passed
  `form={}` and the add-user form reads five keys from it. Fixed with
  `EMPTY_USER_FORM` in `admin.py`.
- `/search` with no address returned a 500: the blank-form render in
  `search.py` omitted `raw` and `outcome`. Fixed by passing `raw=""` and
  `outcome=None`.

The common shape is a variable that one code path never passes. A page-by-page
click-through does not find it; each form needs its GET, its error-POST and its
empty state, and signed-out pages need their own pass. Whenever the template
environment gets stricter, check those states, not just the pages.

## 34. A favicon

**Status:** resolved 2026-10-02

To stop the 404 on every page load.

**When:** whenever.

**Resolved 2026-10-02.** `base.html` links the existing RBi logo
(`static/images/RBi Logo - Black with Blue Highlights.png`, 1243x599) as the
icon, so every page that extends it stops requesting `/favicon.ico`. The logo is
about 2:1 and a favicon is square, so it scales down small; a square export
would read better in the tab, but no such asset exists. Not filed as an item.

## 43. Test scripts attribute to `emp_id 1` (system) by accident

**Status:** resolved 2026-10-02

`scripts/test_match.py`, `scripts/test_rentcast_pull.py` default `--emp-id
1` in their own usage examples — the bootstrap system account, not a real
operator. Harmless for a one-off manual check, but worth making a
deliberate choice (a dedicated test user, or a required flag with no
default) rather than a convenient accident that could get copied into
something that matters.

**When:** cleanup.

**Partly resolved 2026-09-23.** `scripts/test_match.py`'s usage example now
uses `--emp-id 2` and says why 1 is wrong. `--emp-id` was already a required
flag with no default there. `scripts/test_rentcast_pull.py` was not checked.

**Still partly open, 2026-09-24.** `scripts/test_rentcast_pull.py`'s usage
example still shows `--emp-id 1`. The flag itself is required with no
default, so this is the example text only. `test_match.py`'s example was
fixed 2026-09-23.

**Resolved 2026-10-02.** `scripts/test_rentcast_pull.py`'s usage example now
uses `--emp-id 2` and carries the same note as `test_match.py`. Both scripts
require the flag with no default, and both examples name a real operator. No
guard refuses `--emp-id 1`: nothing has been attributed to it since 2026-09-18,
and a required flag was the item's own bar. Past rows are not rewritten:
`emp_id 1` holds 4 `api_pulls` (all 2026-09-18) and 3,353
`storm_listing_matches`. Where those matches came from is not recorded.

## 45. Rebuild step belongs in the verification loop

**Status:** resolved 2026-10-02

Companion to the already-filed "Nothing rebuilds automatically" entry
(2026-09-18): that entry names the failure mode, this item is the standing
todo to make a build-and-recreate step a checklist item — or a script —
rather than something that has to be remembered fresh each audit.

**When:** process improvement, no deadline.

**Update 2026-10-02.** The documentation half exists: `docs/command-ref.md`,
*Which services see your edits*, and the conventions line in `CLAUDE.md`. No
script or checklist step does the rebuild yet, which is what this item asks for.

**Findings, 2026-10-02.** Measured how stale the baked images are. Built:
`ingest` 2026-09-24 20:13Z, `app` 2026-09-29, `web` 2026-09-30, `loader`
2026-09-09. About 40 files under `hailsys/` and `scripts/` have changed since the
`ingest` build, almost all web files `ingest` never imports. Of the ones it does
import (`hailsys/iem/*`, `hailsys/logconfig.py`, `scripts/iem_*.py`), the only
change is a comment in `logconfig.py` (`b99749e`); `tuning.py` gained a constant
`ingest` does not use (`0a2052d`). **So the nightly timer is behind the tree but
running functionally current code**, which is the case `command-ref.md` calls
deliberate and is why this was never a bug until it was checked. The check that
produced that answer was done by hand, with `git log --since=<image build time>`
over `ingest`'s import paths. Once item 30 is done, `app` stops needing rebuilds
and `ingest` is the only service whose baked code can drift; `web` and `app` need
a rebuild only when `requirements.txt` or a Dockerfile changes. Proposed, not yet
built: have `scripts/status.sh` print, per image, whether anything its code or
build depends on has changed since it was built, so staleness is visible without
anyone remembering to look.

**Resolved 2026-10-02.** `scripts/status.sh images` (also part of `all`, and it
never affects the exit status) prints, per image, when it was built and whether
commits made since, or uncommitted edits, touch anything it depends on, naming
the files. `ingest` depends on its import closure (`hailsys/iem/`,
`hailsys/logconfig.py`, `scripts/iem_*.py`, `requirements.txt`, its Dockerfile),
`web` and `app` on `requirements.txt` and `docker/app.Dockerfile` only (item 30
made their code live), `loader` on its Dockerfile. First run: `ingest` STALE for
`hailsys/logconfig.py` (a comment, so harmless), `app` and `web` OK, `loader`
STALE for its Dockerfile, which is a false positive: that edit was built at
20:16Z and committed at 22:01Z (item 101), and the check compares commit times.
`docs/command-ref.md` and `CLAUDE.md` now say only `ingest` runs baked code.
**A doc error found on the way:** `command-ref.md` said `ingest` imports
`db.py` and `tuning.py` and so goes stale on any change in the package; loading
both scripts shows it imports only `hailsys.iem.common`, `hailsys.iem.parse` and
`hailsys.logconfig`. Corrected. **Residual:** the ingest path list is a copy of
that closure and goes stale if ingest gains an import; the script's comment
carries the command to re-derive it.

## 51. Concurrent pulls by two users on one storm — duplicate spend

**Status:** resolved (residuals) 2026-10-02

Nothing stops two people clicking Pull on the same storm day within
seconds of each other; both would spend real RentCast calls for the same
zips. Low risk at five known users, but a real gap if headcount grows.

Since 2026-09-25 the storm list's Status cell doesn't offer "Pull again"
while a pull is running, which removes the easiest way to do this from that
page. The server still doesn't refuse a second pull, and two people can still
click Pull within seconds of each other.

**When:** low priority at current headcount.

**Resolved (residuals) 2026-10-02.** `sql/033` adds a unique partial index,
`api_pulls_one_running_per_storm` on `(storm_date, report_text)` where
`api_status = 'running'`, so the database refuses a second running pull for a
storm however the race falls. The pull's `api_pulls` row is now created in the
request (`create_pull()` in `rentcast/pull.py`, called from `start_pull()` in
`web/jobs.py`) before the thread starts, so a second click gets an immediate
message ("already running, started by NAME at HH:MM. Nothing was pulled.") and
spends nothing. Stale `running` rows for the storm (older than
`PULL_STALE_AFTER`) are cancelled in the same transaction first, so a pull whose
thread died cannot block its storm. If a pull thread crashes before `run_pull`
sets a final status, the row is marked `failed`. See `docs/decision-log.md`,
"One running pull per storm, enforced by a unique index".

**Verified:** `sql/guard_test.sql` passes 24 of 24 (the 18 existing checks plus
6 for the index: a second running row for the same storm is refused, another
type or a finished row is fine, cancelling frees the storm). With a fake
`running` row for 2026-10-02 HAIL on `hail-dev`, the confirm page was submitted;
afterwards no second `api_pulls` row and no `api_call_log` rows existed for the
storm and the web log had no errors.

**Residuals:** (1) the happy path through the new code, a real pull's thread
starting and `pull_id` handed to `run_pull`, has not been run end to end; the
next real pull is its test, watch `docker compose logs -f web` and expect
exactly one `api_pulls` row reaching `complete`. (2) The "already running" flash
was not observed by the person who ran the check. (3) Manual-zip pulls
(`storm_date NULL`) are outside the rule; only `scripts/test_rentcast_pull.py`
makes them. (4) A pull running longer than `PULL_STALE_AFTER` (10 minutes) could
be cancelled by a second click; a normal pull is far shorter. (5) Not applied to
the production box. Related: item 102.

## 53. Zero test coverage under `hailsys/web/`

**Status:** open

Of the 100 test cases in `tests/`, 88 cover `hailsys/iem/` and the ingest
scripts, and 12 (`tests/test_formatting.py`, 2026-09-22) cover
`hailsys/formatting.py`, the magnitude formatter. That is the first test file
for code the web app calls, but it sits outside `hailsys/web/`: nothing
exercises views, auth, the query modules the web app calls, or the filter's
registration in `create_app()`. Two of an earlier phase's own bugs
(`storm_matches()` passing the wrong keyword name, `login()` reading
`last_login_at` before it was selected) are exactly the shape a test would
have caught before a live check did.

**When:** open since Phase 2; no deadline set.

**Update 2026-10-02.** The suite is now 128 tests: `test_iem_parse` 48,
`test_iem_common` 23, `test_workstate` 19, `test_iem_backfill` 14,
`test_formatting` 12, `test_iem_ingest` 7, `test_activity_csv` 5. Two new files
touch code the web app calls (`queries/workstate.py`, `queries/activity.py`),
still none under `hailsys/web/`: views, auth and admin are unexercised. The gap
has now cost more than the two bugs above: switching Jinja to `StrictUndefined`
(item 32) surfaced three 500s no test could see (`/login` signed out, `/admin/`
on a plain GET, `/search` with no address), found by hand after the change.

## 55. Duplicate properties from RentCast address variants — one lot, two emails

**Status:** open

RentCast's `id` is derived from the address string, so a formatting change
upstream mints a new id for the same building (`docs/data-sources.md`). Two
`properties` rows for one lot can each carry a listing and an agent, produce
two matches, and lead to two emails about one property, possibly to two
different people. *(At the time this was filed, an exact check on
lower-cased `address_1`, `address_2` and zip found no duplicates in the 508
properties then in the table — since superseded, see below: absence there
was never evidence, exact match can't see "St" versus "Street", and at
16,407 properties the duplicates are real.)* Needs a dedupe rule — likely
address normalization or coordinate proximity plus unit — decided before
the first send.

**When:** Phase 5, before any email goes out.

**Measured 2026-09-24: 186 candidate pairs.**
```sql
SELECT count(*) FROM properties a JOIN properties b
  ON a.rentcast_id < b.rentcast_id
 AND ST_DWithin(a.geom::geography, b.geom::geography, 25)
WHERE a.year_built IS NOT DISTINCT FROM b.year_built;
```
**Distance is not what tells them apart.** 17 Clover Cir and 17 W Clover Cir
are a real duplicate at about 5.5 m apart: same agent, same year, $2,500 apart.
Distinct apartments sit 2.2 m apart. Proximity is only a pre-filter; the test
is **address normalization**:
- **Directional position:** N Wordsworth / Wordsworth N.
- **Suffix conflicts:** Superior Dr / Superior St at one point.
- **City aliases:** Security Widefield / Colorado Springs.
- **Truncation:** Drummond S.

**Apt and Unit stay distinguishing.** **Lot** is a duplicate when a no-Lot
record exists at the same address, and distinguishing when every record has
one: 205 N Murray Blvd has 13 legitimate lots at a single point. Still Phase 5,
before sending: an agent must not get two emails about one house. Related:
item 100, where shared points are *not* duplicates.

**The address-normalization key is built and live, 2026-09-28** (`sql/024_
address_key.sql`, `905895b`). `properties.address_key` via PostGIS's
`address_standardizer`: house number, directional (prefix or suffix
collapsed to one slot, position made irrelevant per the finding above),
street name, suffix, unit, zip — city deliberately excluded, exactly because
of the "Security Widefield / Colorado Springs" case already found here.
Backfilled: 16,237 of 16,407 properties keyed, 170 `NULL` at the time (287 of 25,219 as of 2026-09-30) (no house
number — vacant land, `TBD` roads, and rural addresses with a road number
after a comma that don't parse one either). Verified: every stored value
matches a fresh call, zero mismatches across the full table; `hail_app` can
call the function itself (needed a grant on `address_standardizer`'s
`us_lex`/`us_gaz`/`us_rules` lookup tables, missing in the first version of
the migration, added after review before being relied on).

**It's already finding real duplicates: 21 `address_key` groups, 42
properties.** Sampled and confirmed genuine — the same street address under
two different RentCast-supplied city labels, the exact pattern this item
already flagged:
```
10720 Briarglen Cir, Highlands Ranch, CO 80130
10720 Briarglen Cir, Littleton, CO 80130

1104 Hallam Ave, Colorado Springs, CO 80911
1104 Hallam Ave, Security Widefield, CO 80911
```
This is narrower than the 186 candidate pairs from the proximity check
above — exact-match on the normalized key, not a distance threshold, so it
won't catch two records that are the same house but where the standardizer
parses one input differently due to noise. Not compared against the 186
directly yet.

**Re-measured 2026-09-26, current figures, replacing the stale ones
above: 21 duplicate groups, 42 properties, every group a clean pair** (the
508-property, zero-duplicate finding at the top of this item was accurate
when filed and is now superseded — 16,407 properties, not 508). Every
group inspected by hand; **three causes, confirmed, not guessed:**
- **Directional position** — `4518 N Wordsworth Cir` / `4518 Wordsworth Cir N`.
- **City disagreement within one zip** — the large majority of the 21
  (Highlands Ranch/Littleton, Colorado Springs/Security Widefield,
  Brighton/Thornton, Denver/Lakewood, Denver/Wheat Ridge, Arvada/Golden,
  Castle Pines/Castle Rock, and more), all the same RentCast
  municipal-boundary variance the key was built to route around.
  `8557 Highway, 86, Kiowa` / `8557 State Hwy, 86, Kiowa` was recorded
  here as the same cause one level down — a name/suffix spelling
  difference the standardizer resolves to the same parsed street.
  **Correction, 2026-09-29: this characterization was wrong, resolved by
  re-checking after `027` ran.** The pair's pre-fix key (`8557||86||80117`)
  is gone from the table, confirming `027` fixed it — but the pair still
  collides under its corrected key, `8557||86|||80117`. There's no street
  name here for the standardizer to resolve two spellings of:
  `address_standardizer` drops "Highway"/"State Hwy" entirely for a rural
  route address, leaving only the house number and the bare route number.
  Not a coalesce artifact and not a spelling match — a fourth, distinct
  cause (see below).
- **A stray leading colon on one record** — `: 6637 E 149th Ave, Thornton,
  CO 80602` versus `6637 E 149th Ave, Thornton, CO 80602`, otherwise
  identical. A RentCast data artifact, not an address-format issue. Still
  present, unaffected by `027` (decision log, 2026-09-29) — still
  unexplained.

**Re-measured 2026-09-29, after `027` ran: 50 duplicate groups, 100
properties.** `properties` itself grew from 16,407 to 25,219 rows over the
same period, so a larger duplicate count is expected on its own; the 50
have not been diffed group-by-group against the earlier 21 to confirm all
of them are still intact inside it. All 50 inspected, not sampled — four
causes now, the fourth new:
- **39** city-label disagreement within one zip (as above).
- **7** directional position, same city both times (as above).
- **3** — **new cause: rural highway/route addressing.** The Kiowa pair
  above, plus two more in Peyton (`10985 E Hwy, 24` / `10985 E Us Hwy, 24`
  and `21295 E Hwy, 24` / `21295 E Us Hwy, 24`) — confirms this is a real,
  recurring pattern, not a Kiowa-only quirk.
- **1** the stray-leading-colon record, still the same pair from
  2026-09-26.

**A fourth, currently dormant risk, checked and recorded, not guessed:**
31 of 16,407 addresses carry both a prefix and a suffix directional
(`1280 W Oxford Ave S`, real Centennial/Littleton/Englewood street names,
not errors) — `address_key`'s `coalesce(predir, sufdir)` keeps the prefix
and drops the suffix for these. Checked whether this has caused an
incorrect merge: **it hasn't** — none of the 31 currently share a key with
a different address. See `docs/decision-log.md`, "Address identity: parsed
components, not string cleaning."

**Dedup stays at send time, per the original design.** `storm_listing_
matches` points at `listings`, not `properties` — a match is already keyed
to a specific listing by the time it exists, so deduplicating properties
earlier (at match time) would mean rewriting what a match points at, not
just filtering what's shown. The 21 existing pairs are **deliberately left
unmerged** for now — `address_key` exists precisely so a future send-time
step can collapse them into one email, not so today's pull or match
pipeline treats them as one property already.

**Still not done:** nothing reads `address_key` yet. It exists and is
populated, but the matcher, the match page, and the CSV exports still treat
every `properties` row as distinct — an agent can still get two emails about
one house today. Also open: whether Lot 13-at-one-point stays correctly
undeduplicated once this key is actually wired into anything (its own
`unit` field, not `address_key` alone, is what has to keep those distinct).

**Partly resolved, 2026-09-29 (decision log, "Item 55: send-time dedup,
scoped to what the data supports"): Part 1 decided, Part 2 deferred.**
Send-time grouping by exact `address_key` (one email per key, a
`DISTINCT ON`) is the agreed design — measured on the current 50
duplicate groups / 25,219 properties and one real storm (656 matched
properties, 654 distinct keys). Still not wired into the matcher, match
page, or exports — "still not done" above still holds. The broader
unit-plus-shared-agent case (126 groups) is designed but deliberately not
built; revisit only if a duplicate email is actually reported. No suffix
merging, ever — decided, not deferred, because a same-zip suffix
disagreement could be a genuine distinct street.

**Status, 2026-09-30: open.** The key exists and is populated (`sql/024`, `025`, `027`; 25,219 properties, 50 duplicate groups covering 100 properties, 287 NULL keys), and the send-time design is decided (Part 1, decision log 2026-09-29). But nothing reads `address_key`: the repository's only references are `sql/024`, `025`, `027` and docs. The matcher, the match page and the CSV exports all treat every `properties` row as distinct, and no send path exists. **The dedup is not built**, and this remains a blocker for the first send.

## 61. Force a password change on next login after an admin reset

**Status:** deferred 2026-09-30 — revisit at system completion

An admin reset sets a password the admin now knows. Nothing makes the user
replace it. A `must_change_password` flag, set by the reset and cleared by
`/account/password`, with the login redirecting there while it's set, would
close that.

**When:** Phase 4, if wanted.

**Prior decision, 2026-09-23: declined for now.** See `docs/decision-log.md`, "No forced
password change after an admin reset, for now". Reconsider before staff
accounts exist (Phase 7).

**Deferred, 2026-09-30.** Not dropped: the developer intends to revisit it at
system completion. (The 2026-09-23 marker on this item read "declined", which
overstated it.)

## 63. A reachable path to `hail-dev` for anyone but the developer

**Status:** resolved 2026-10-02

`web` publishes to `127.0.0.1:8000` only (decision-log 2026-09-14), so nothing
off the host reaches it without `tailscale serve` or a tunnel in front of it.
Needed before anyone else logs in — including the viewer test in item 58 if
it's run from another machine.

**When:** Phase 6, or sooner.

**2026-09-30:** no one else is on the tailnet (developer). `tailscale serve` on `hail-dev` fronts the app at `https://hail-dev.tail74972c.ts.net`, tailnet only, proxying `127.0.0.1:8000`. Item 110 (the Cloudflare tunnel) is the next step.

**Resolved 2026-10-02.** The path exists: item 110 (resolved 2026-09-30) put
`dev.roofbrokersinc-weather.com` behind a Cloudflare Tunnel with Access
(one-time PIN), `cloudflared` is up (45 hours at last check), and the developer
confirms outside users can now reach the app. This item sat parked as "gated on
110" for two days after 110 closed; found by the 2026-10-02 sweep (item 154).

## 64. Emailed password-reset link

**Status:** open

Today a forgotten password means an admin resets it by hand from `/admin`. A
self-service emailed link needs a sending path.

**When:** Phase 5, once sending exists.

## 83. In-house realtor and contacts database

**Status:** open

A searchable store of known realtors, beyond what `realtors` holds from
RentCast pulls, with a different email template for agents RBI already
knows. **Open question:** what system holds these contacts now.

**Partly answered, 2026-09-28.** It's a CSV export, `Final Realtor Database-
09-25.csv`, 4,187 unique contacts. **Per the developer, it's not an email
list at all** — it's RBI's record of clients who signed up for RBI's
roofing service at some point over the last 12+ years, and an updated copy
is coming. That explains why it barely overlaps with Constant Contact (item
122): it's a different kind of relationship (service/referral), not a
newsletter subscription. 675 of the 4,187 (16.1%) already appear in the
hail system's own `realtors` table, but none of the 675 are corroborated as
current recipients by either Constant Contact or the DNC list — see item
122. **"Signed up for our service" answered, 2026-09-28:** the realtor
called RBI directly and requested an inspection, of a property they
represent or their own roof. A voluntary, recipient-initiated engagement —
a clean fit for Constant Contact's own "implied consent via existing
business relationship" standard (decision log, "What 'signed up for our
service' means, and always including the CAN-SPAM footer regardless"). What
it does *not* do: exempt the storm-alert message itself from being
"commercial" under CAN-SPAM (item 120 still applies in full, by decision).
Still open: whether/how a "different template for known agents" distinction
gets built.

**When:** Phase 5.

## 84. Historical hail aggregation — which areas were hit hardest over n years

**Status:** open (parked) — gated on Phase 6

A query over existing `iem_data` and `report_zip_distances`, not a new
ingest. Overlaps `/territory` (decision log 2026-09-16, *Territory browse is
its own page*), which already groups a date range by city or zip with
report counts. What is new is ranking over many years, for example by
distinct storm days rather than raw report count, which the ~0.7%
natural-key dedup and busy single days would otherwise skew. Probably a
view of `/territory` or a map layer rather than its own page.

**When:** after Phase 4, alongside the map (item 22).

**Scoped 2026-09-30, Phase 6:** wanted is a multi-year storm history (when, where, how bad), not the matched listings. It reads `iem_data` (with `report_zip_distances` for zips) only, so item 49's 400-day clamp, which exists to bound matched-listing export size, need not apply to it. As things stand the clamp applies to every explicit date range, `/territory` included, so this needs its own route or an exemption.

## 85. Pull estimate shows "last pulled" times in UTC

**Status:** resolved 2026-10-02

`pull_estimate.html`'s "Pulled in the last 7 days" table formats
`z.last_pulled.strftime('%Y-%m-%d %H:%M')` with no conversion. `CLAUDE.md`
says to convert to `America/Denver` at display, and the storm list's
"last pulled" date already does (`.astimezone(display_tz)`). Needs the same
conversion, plus `display_tz` passed from `pull_estimate()`.

**When:** small; next pass over the pull estimate.

**Resolved 2026-10-02.** `pull_estimate()` passes `display_tz=DISPLAY_TZ`
and `pull_estimate.html` converts with `.astimezone(display_tz)`. Checked on
`/pull/estimate`: times show in local time.

## 87. Confirm RentCast's billing-period timezone

**Status:** open

`quota.fetch_usage` rolls the period over at midnight Denver time. Whether
RentCast rolls over on UTC or Denver time is unconfirmed. Near a boundary,
up to seven hours of calls could land in the wrong month's count.

**When:** before the quota figure is compared against a real invoice.

**Count half resolved 2026-09-24.** See `docs/decision-log.md`, "RentCast's
billing boundary timezone is unknown; our dashboard is the check". RentCast's
docs don't state it. Our period runs from Denver midnight on the billing day,
and the checks are /admin against RentCast's dashboard, plus their 85% email.

**Boundary half still open.** The count comparison can be made now, but all 19
logged calls fall between 2026-09-18 and 09-23, inside the period that started
the 9th, so nothing yet sits on either side of a rollover. Re-check after
2026-10-09, and watch for RentCast's 85% email as a second signal.

**When:** after the 2026-10-09 rollover.

**Developer note, 2026-09-30:** the recorded count is off because some pulls happened before usage tracking was set up. Tracking is correct now, and it will be checked against RentCast's own figure at the next billing cycle (rollover 2026-10-09).

## 95. No test for the `R` = RAIN / HEAVY RAIN composite key

**Status:** resolved 2026-10-02

CLAUDE.md lists "IEM `TYPECODE` is not unique" as a known trap, and the parser
does key on `(report_type, report_text)` tuples. But no test in `tests/`
exercises two rows sharing a `TYPECODE` with different `TYPETEXT`, so a
regression back to keying on `TYPECODE` alone would pass the suite. Every
other trap in that list has a test.

**When:** next time the parser is touched.

**Resolved 2026-10-02.** The item's premise was partly wrong: a pair test
already existed (`TestReportTypePair`, with `S`/`SNOW` and `S`/`HEAVY SNOW` in
its fixture). What was missing: the accepted pair was never checked in the
returned record, no test mixed members of different codes, and the fixture was
hand-written rather than tied to the real table. `TestSharedTypecodes` in
`tests/test_iem_parse.py` builds its valid set from `planning/report_types.csv`
and covers all nine shared codes (`2`, `5`, `6`, `7`, `I`, `J`, `R`, `s`, `S`).
It asserts each pair is accepted with both halves kept, `(R, SNOW)` rejects, and
`s` and `S` are different codes. A guard test fails if the seed stops
containing the `R` pair, so the others cannot pass vacuously. Not covered: the
seed CSV drifting from the live `report_types` table.

## 96. Has RBI answered the DNS ask?

**Status:** open

`phases.md` runs "the domain and email-sending setup" alongside every phase
from day one, because DNS changes at a small company can sit in an inbox for
weeks, and asks for "DNS access and existing subscription status" in
Phase 0. Nothing in the repo records the answer. Phase 5's first send needs a
sending subdomain with SPF, DKIM and DMARC.

**Narrowed considerably, 2026-09-28, from public WHOIS and DNS lookups — no
access to RBI's DNS needed.** See `docs/decision-log.md`, "RBI's domain, its
live mail setup, and the sending-subdomain plan." The domain is
`roofbrokersinc.com`; DNS is hosted directly at GoDaddy; RBI's live business
email is Microsoft 365 (MX and a hard-fail SPF both point at
`spf.protection.outlook.com`), which this project must never touch.
`send.roofbrokersinc.com` is confirmed free (no `A`/`CNAME`/`TXT`);
`mail.roofbrokersinc.com` is not (a live `A` record already answers). The
root domain's DMARC (`p=none`) won't block a new subdomain. **Direct, current
evidence of the prior Mailchimp use:** a live Mailchimp DKIM key still
resolves at `k2._domainkey.roofbrokersinc.com`, never cleaned up — confirms
Mailchimp was set up on the root domain itself, matching the "led to
blacklisting" note below. `justyn@roofbrokersinc.com` does not exist as a
mailbox.

**The one-shot ask is now a single, concrete request, not a checklist.**
Create a Cloudflare account, add `send.roofbrokersinc.com` itself as its own
Cloudflare zone, and Cloudflare hands back two nameservers. The entire ask of
RBI's contact becomes: *"please add these two NS records for
`send.roofbrokersinc.com`."* Everything downstream — SPF, DKIM for whatever
provider gets chosen, a DMARC record for the subdomain, and MX + Email
Routing to forward `justyn@send.roofbrokersinc.com` to Gmail with no
Microsoft 365 or Outlook involved — becomes self-service from there, with no
further requests. Also worth asking while the conversation is happening: the
Mailchimp history (bounces, complaints, blacklist notices, if known), and
what an unidentified root TXT verification token is for.

**When:** now: Phase 5 is current, and this is the item most likely to be
waiting on someone else.

**Status, 2026-09-30:** open. The ask to RBI is in progress and this is gated on their answer. Checked the same day with `dig`: no NS, TXT or A record exists for `send.roofbrokersinc.com`, so the delegation has not been added yet.

**Constant Contact, 2026-10-06.** Open questions that decide the wording of the
ask: (1) whether Constant Contact accepts `send.roofbrokersinc.com` for
self-authentication (its docs say "domain" throughout; ask support before
building on it); (2) whether RBI's own Constant Contact account is
self-authenticated at all. No `ctct1`/`ctct2`/`100`/`200`/`300` CNAMEs exist on
the root, so its mail may be going out as `@shared1.ccsend.com`; check the
From address and headers of one recent RBI send. Also: only one domain can be
self-authenticated per Constant Contact account. If RBI's existing account is
used instead of our own, that choice is hard to reverse.

**Evidence from the dev domain, 2026-10-06 (item 170).** A trial account
self-authenticated `roofbrokersinc-weather.com`, a root domain whose zone we
control, with the two `ctct1`/`ctct2` CNAMEs, and sent as that domain with the
From unchanged and DMARC passing on DKIM. So the mechanism works end to end for a
root domain. Still unconfirmed: whether Constant Contact accepts a **subdomain**
such as `send.roofbrokersinc.com` for self-authentication. The ask to Constant
Contact support stands.

## 100. New-construction properties share a subdivision point

**Status:** dropped 2026-10-02

RentCast geocodes some brand-new houses to a single subdivision point rather
than to the lot:
- 1843 Wildland Hts, 1876 Wildland Hts and 2115 Zipline Vw share one point.
- 4536 Hawk Haven Vw and 4913 Havenward Vw share another.

All were built in 2026. They are distinct houses, so this isn't a dedup case
(item 55). It affects **distance accuracy**: any distance to these is to the
subdivision point, not to the house. **Today it changes no match:** checked
2026-09-24, all five are listed as New Construction, which the matcher
excludes (decision log 2026-09-18). It would matter if one is resold, or
relisted as Standard, before RentCast re-geocodes it to the lot.

**When:** Phase 5, alongside item 55's address work.

**Dropped 2026-10-02.** The developer closed this: no email goes to new
construction, and nothing showed that a resale would keep the wrong point. Asked
whether the data supports the concern, so it was measured the same day against
the 25,219 `properties` rows. **Findings:**
- 190 points are shared by different street addresses (761 properties; unit
  suffixes stripped by a rough regex, so approximate).
- 221 of those properties are New Construction, and none has a match, so the
  matcher exclusion holds.
- The pattern is **not limited to new construction**, which the item assumed.
  Standard single-family homes share points too (73 properties, 36 matched
  listings; one group is 31 properties on one point, built 1969). Condos,
  townhouses and manufactured-home parks share points as well, about 300
  matched listings, probably because they are complexes. A resale therefore does
  not by itself bring a correct coordinate.
- **Why it is still dropped:** the error is about the size of the subdivision,
  against a 5-mile radius, so a match changes only for a storm report near the
  radius edge.

**When:** revisit if emails quote `nearest_miles` (item 136), or if any feature
starts depending on distances to the house.

## 101. Rebuild the `loader` image before its next use

**Status:** resolved 2026-10-02

The `loader` image was built 2026-09-09, the same day
`docker/loader.Dockerfile` last changed (`7a731b4`, the bullseye EOL
placeholder). Whether that build picked up the change isn't known. *(Answered 2026-10-02: it did. `docker history` shows the image's `apt-get`
layer, created 2026-09-09 20:16:21Z, already carries the
`-o Acquire::Check-Valid-Until=false` flag. The commit is stamped 22:01 UTC, so
the file was edited and built before it was committed. An earlier note here that
the build predated the change was wrong: a commit time is not an edit time.)* Rebuild
with `docker compose build loader` before the next migration or reference
load. See `docs/command-ref.md`, *Which services see your edits*; the
`iem_weekly_replay` timer's staleness is covered there too.

**When:** before the loader is next used.

**Resolved 2026-10-02.** Nothing to rebuild. `docker compose build loader` on
2026-10-02 reused every cached layer, and the image has `shp2pgsql` 3.5.2 and
`postgis 3.5.2+dfsg-1.pgdg110+1`, the pinned version. Not tested: a build from
scratch (`--no-cache`), so whether the EOL workaround still builds against the
current bullseye repos is unknown. That risk belongs to item 3, the base image.

## 102. `_throttle` isn't thread-safe

**Status:** open (watch) — accepted 2026-10-02, revisit on the trigger below

`hailsys/rentcast/client.py`'s `_throttle` keeps its last-request time in a
module global with no lock. Pulls run in background threads, so two pulls at
once could each stay under 20 requests/second and together exceed it. RentCast
would answer with 429s, which the client retries. So the cost is time, and
possibly extra calls counted, not data. Low risk while one person pulls at a
time. Related to item 51 (concurrent pulls on one storm).

**When:** with item 51.

**Update 2026-10-02.** Two things to know before fixing this. `web` runs
`gunicorn --workers 2`, which is two processes, each with its own copy of
`_last_request_time`; a `threading.Lock` would fix two pulls inside one worker
and nothing across the two workers, so the full fix needs shared state (for
example a Postgres advisory lock, at a database round trip per request). And
item 51's fix (one running pull per storm) removes the most likely trigger, two
people on one storm; two different storms pulled at the same time can still
overlap, at up to about 40 requests a second, and RentCast would answer with
429s that the client retries.

**Accepted 2026-10-02.** Measured against the 38 completed pulls in `api_pulls`:
the fastest ran at 2.5 requests/second, the average is 1.18, and the largest
was 94 calls. RentCast's limit is 20/second, and the throttle's 50 ms floor
never binds because each request takes about half a second. Reaching the limit
would need about eight pulls in flight at the fastest rate seen, which five users
and item 51's one-running-pull-per-storm rule rule out. All 525 logged zips ended
in 200. **No retry of any kind is recorded:** the 9 zips with more than one call
are all zip 80134 with 531 to 533 listings, two pages of up to 500, so pagination
accounts for every extra call (`calls_made` counts retries too, so a retried zip
would show a count its listings do not explain). The web log, which only reaches
back to 2026-09-30 15:24 UTC and holds 3 pulls, has no `rentcast_retry` line.
Not known: whether RentCast bills a 429. Not fixed: a `threading.Lock` would
cover one gunicorn worker of the two (`web` runs `--workers 2`), and an advisory
lock adds a database round trip to every RentCast call to guard a case that has
not occurred.

**When:** revisit if (a) the number of people who can pull grows enough that
several pulls could overlap, (b) anything starts fetching zips concurrently
within a pull, which would multiply the per-pull rate, or (c) a
`rentcast_retry status=429` line appears in the web log (`docker compose logs web
| grep "status=429"`), or `calls_made` exceeds what a zip's listing count
explains.

## 105. `postgis` has no log rotation

**Status:** resolved 2026-10-01

Checked 2026-09-24: `postgis`'s container log is `json-file` with no
`max-size` or `max-file`, so it grows without limit. It is the only other
long-running container; `app`, `ingest` and `loader` run with `--rm`. Adding
the same `logging:` block as `web` means recreating the database container
(`docker compose up -d postgis`), which is a brief outage, so plan it rather
than doing it mid-use.

**When:** before production deployment.

**Resolved 2026-10-01, found closed 2026-10-02.** `4e51bdd` added the same
`logging:` block `web` has (`json-file`, `max-size: 20m`, `max-file: 5`) to the
`postgis` service. Verified 2026-10-02: `docker-compose.yml:32-36` has it, and
`docker inspect` on the running container reports the same `LogConfig`, so it
is live, not just in the file. The item stayed open because the fix went in
with an unrelated commit and nothing updated the parking lot. Not checked on
the production box.

## 106. The match page gets long, and no row says which report it matched

**Status:** dropped 2026-10-02

One storm's page can hold hundreds of listings. 2026-08-26 TSTM WND GST had a
single report and matched 949 listings, every listing within 5 miles of it
from the 12-zip pull. Rows are grouped by agent and show only the nearest
distance and the worst magnitude, so on a storm with several reports nothing
says which report a listing was matched to.

Originally described as "lists every match ever made, ungrouped". Checked
2026-09-24: the page is scoped to one storm day and type (its heading says
which), and is grouped by agent. Its length on a one-report storm with a big
pull is what made it look unbounded.

**When:** Phase 5, before the first real batch is worked from the match page.
Related to item 92 (no CSV export on this page).

**Partly resolved 2026-09-24** (`d1b08c5`, `28a0195`). Length: the page is
condensed and every agent group's columns line up (decision log, "Match page
and activity panel: aligned columns, condensed, two columns"), so a long page
scans faster; the row count is unchanged. The page's matches can also be
downloaded as a spreadsheet (item 92). **Not addressed: which report a listing
matched.** The page and `/storms/matches.csv` both show the nearest distance
and the worst magnitude across the storm day and type. The bulk export's
grain, one row per listing per storm day and type, says which storm, not which
report. That half stays open here.

**Considered and declined, so this doesn't read as unfinished:** a
storm-date/type column on each row (the page heading already names both —
one storm, one type per page, so a per-row repeat would be redundant, not
informative); collapsing rows by agent, sorting agents by listing count,
and adding on-page filters (all three declined together — unclear anyone
works this page on screen rather than exporting it, so building
scan-and-filter tooling for a use pattern that may not exist wasn't worth
it).

**When (the declined items specifically):** if someone actually works this
page on screen rather than exporting it.

**Dropped 2026-10-02.** The developer closed this: how the page will actually be
used is unknown until people work it, and that is better learned than guessed.
Nothing was changed. The un-addressed half stays as stated above: nothing says
which report a listing matched, only the nearest distance and the worst
magnitude across the storm day and type. **When:** after the first real batch is
worked from the match page, or if someone asks which report a listing matched;
and when item 121's consolidated sends are designed, since an email that names
the storm day(s) it refers to needs that attribution.

## 107. CSV export missing on the activity page

**Status:** resolved 2026-10-02

Split from item 92, 2026-09-24. Phase 2's outline says "CSV export on every
list." The storm list and territory have one (`/export.csv`), and the matched
listings now do (`/storms/matches.csv`, `/exports`); `/activity` doesn't. The
matched listings were the list Phase 5 acts on and the likeliest one someone
would want in a spreadsheet; `/activity` is the list that is still without one.

**When:** unphased.

**Resolved 2026-10-02.** `/activity.csv` (`activity_csv()` in `views.py`),
linked from `/activity` only, not the dashboard, whose feed is truncated. One
flat file with a `kind` column (`new_storm`, `pull`, `match_run`); a column that
does not apply to a kind is blank. `activity.feed_rows()` does the flattening
and the Denver conversion, and the route goes through `_csv_response`, so
`csv_safe` applies and the web still has one CSV writer. A first login (no
earlier login to measure from) returns a header-only file, accepted: it matches
the page's "no activity yet". The file includes staff names, as the page does.
`tests/test_activity_csv.py` (5 tests, stdlib, no Flask or database) covers
`feed_rows()`.

## 111. Tap targets under 44px

**Status:** dropped 2026-10-02

The 2026-09-25 pass raised the storm-days Status cell's actions
(`.pull-link`, `.inline-action button`, `.action-disabled`) to about 44px
under 40rem, and form controls to 16px so iOS doesn't zoom on focus. What is
still smaller, estimated from the CSS and not measured: the nav links at
phone width (about 38px), the pagination links (about 34px), the admin
`.row-actions` buttons (about 29px), the `.filters` Apply button and Download
link, and the `+` expand button (24px, which meets WCAG 2.2's 24px minimum but
not 44px).

**When:** if anyone works from a phone; before Phase 6 exposes the app.

**Dropped 2026-10-02.** The developer checked the app on a phone and judged it
fine for what it is used for. The sizes listed here are CSS estimates and were
never measured, so some may still be under 44px; nothing was changed.
**When:** if a user reports difficulty tapping on a phone, or before the Phase 6
pilot users start.

## 114. The responsive pass has not been recorded as checked

**Status:** resolved 2026-10-02 (limited)

The 2026-09-24 and 2026-09-25 responsiveness passes (decision log, "CSS
responsiveness pass") were written with no browser available: the agent's
environment had none, and `hail-dev` has no headless browser either. The
developer reported looking at the result in a browser afterwards. Which pages,
widths and devices, and what was seen, were not reported into this record, so
**no specific check is recorded as passed.** To confirm, at 360, 768 and
1024px and on a real iPhone:

- Storm days: no page-level horizontal scrollbar; the table scrolls inside its
  box; the Status cell's actions are separate taps.
- Territory city view: stacks at 768px; the table scrolls in its box; a row
  still expands; the map draws at full width when stacked.
- Territory zip view, `/activity`, the flash box and the activity panel: inset
  by the same gutter as the filter bar.
- Match page: the shadow shows beside the body rows and disappears at the scroll
  end.
- Admin: settings rows stack below 768px; users and history tables scroll in
  their own boxes.
- Filters on storm days, territory and exports: each label stays with its
  control when the bar wraps, including the date inputs on iOS.
- iOS Safari: focusing a filter, date or password input doesn't zoom the page.
- Login: still centred, and the page doesn't scroll when the keyboard opens
  (`100dvh`, which only shows on a real phone).

**When:** before Phase 6 exposes the app.

**Resolved 2026-10-02, limited.** The developer checked the app on a phone and
it looks right. Which pages, widths and browser were not recorded, so the
checklist above was not walked and no specific point on it is recorded as
passed. Accepted on that basis. **When:** same trigger as item 111.

## 119. Mainstream email-sending providers all prohibit this use case

**Status:** closed by decision 2026-10-06; qualified 2026-10-07: valid only for the warm pools, not yet enforced (item 179)

Fetched the current AUP/ToS directly from SendGrid, Postmark, Mailgun,
Resend, and Amazon SES, 2026-09-28: every one prohibits sending to a
recipient who hasn't opted in, with no B2B or professional-contact
exception. See `docs/decision-log.md`, "Mainstream email-sending providers
all prohibit this use case; a different category might not." This is the
same wall the prior Mailchimp attempt hit (item 96), under a different name.
A different category — cold-outreach/sales-engagement platforms (Instantly,
Smartlead, lemlist, Apollo), built around CAN-SPAM's opt-out model rather
than requiring prior consent — might fit, but it's unconfirmed whether any
of them offer a plain single-email API suitable for this codebase, versus
requiring their own campaign-builder UI and a pool of rotated, warmed-up
mailboxes.

**When:** before a provider is chosen for Phase 5 — get the exact use case
in writing from whichever one it is, first.

**Self-hosting reassessed, 2026-09-28, against the smaller warm audience
(item 122) — not decided, still weighed.** See `docs/decision-log.md`,
"Self-hosting reassessed against the smaller, warmer audience (no decision
made)." A concrete build estimate (VPS, Postfix, DKIM/SPF/DMARC on
`send.roofbrokersinc.com`, `smtplib`) and its genuine advantage over
Constant Contact (the DNC check can run in the same transaction as the
send) are recorded there.

**Closed 2026-10-06 by decision, not by evidence.** Constant Contact is the Phase 5
provider; see `docs/decision-log.md`, "Constant Contact chosen as the Phase 5
provider". The item's own trigger (the use case in writing from the provider) has
not happened. Constant Contact's terms for this use case are unconfirmed, and the
dev trial is the test. If the trial or RBI's account hits a policy wall, this item
reopens.

**Qualified 2026-10-07.** Constant Contact's own policy bars scraped and
public-directory addresses (item 179), and the 2026-09-28 decision log scoped
Constant Contact to the warm pools only (the Constant Contact-active realtors and
the service-request clients). The 2026-10-06 closure did not carry that scope. It
holds for those pools; it does not cover listing agents who are in `realtors` only
because RentCast returned them. Fully closed when the pools are imported with a
recorded basis per realtor and sending is gated on it.

## 120. Email templates need CAN-SPAM's footer requirements built in

**Status:** open

Checked actual draft outreach copy ("...Our inspections are free...")
against `15 U.S.C. § 7702` and `16 CFR § 316.3`, 2026-09-28: it reads as a
"commercial electronic mail message," not an exempted "transactional or
relationship message" (it fits none of that definition's five categories).
See `docs/decision-log.md`, "Is this a 'commercial' email under CAN-SPAM?
Yes, and here's what that requires." CAN-SPAM is an opt-out law, so this
doesn't block sending — it requires four things in every message: a
non-deceptive subject line, a clear ad notice if not obvious from context, a
valid physical postal address for RBI, and a working opt-out honored within
10 business days. None of this exists in `email_templates` or item 16's
still-undesigned merge-field vocabulary.

**When:** item 16, when the merge-field vocabulary and templates are
designed — this is a concrete requirement for that design, not a separate
task.

## 121. Real send volume, measured: consolidating across storms is a real lever, throttling a personal account isn't

**Status:** resolved 2026-10-02

Measured against the real database, 2026-09-28, HAIL only, 2026-08-13
through 2026-09-22: one storm alone (2026-08-13) touched 1,415 distinct
agents; per-storm sending over the six-week window sums to 5,628 agent-sends,
against 3,259 distinct agents if consolidated — about a 42% reduction, from
1,594 listings genuinely recurring across more than one storm (not
duplicate data — the period was an active one). See `docs/decision-log.md`,
"How much HAIL-storm listing/agent overlap exists, and what consolidating
sends would save."

**Every one of these numbers dwarfs a personal account's daily limits**
(500/day Gmail, ~300/day Outlook.com) many times over, on the single
smallest storm in the data, let alone the whole window. **Throttling a
personal account to stay "well below" that threshold was considered and
rejected** — the published caps aren't the flagging threshold, a personal
account has no bounce/complaint visibility, the backlog math doesn't close
(82 days to clear just this one past window at a conservative 40/day, while
new storms keep arriving), and slowing down doesn't cure a category
prohibition that every provider's AUP (item 119) states as "no unsolicited
bulk email," not "no more than N per day." See `docs/decision-log.md`,
"Throttled sending from a personal account: considered and rejected."

**What's real:** consolidating an agent's sends across storms in a period,
on top of a genuine warmup schedule (`phases.md`) once a real provider is in
place, not instead of one. This needs a send design that still names which
specific storm day(s) a consolidated email refers to (item 106; the "claims
a report, never damage" rule in `CLAUDE.md`), and it needs the frequency cap
(items 15, 19) built around windows of agents, not per-storm counts.

**When:** item 15/19, when the send queue and frequency cap are designed —
this is a concrete input to that design, not a separate task.

**Resolved 2026-10-02.** The developer decided: nothing is sent from a personal
account, a provider is used. That settles the throttling question. What this
item found that is still live (consolidating an agent's sends across storms, and
building the frequency cap around windows of agents) is carried into item 15
and not lost. Which provider is still open (item 119).

## 126. Realtor deduplication rule — decided, not built; supersedes the 2026-09-01 decision

**Status:** open (parked) — gated on item 83 (the RBI realtor import)

**Supersedes** `docs/decision-log.md`, "No realtor deduplication beyond
exact normalized email" (2026-09-01). The rule: dedupe on matching
normalized email, and on matching name plus phone, keeping the most recent
record. Not implemented — `realtors` still has 7,082 rows on the old,
no-dedup basis.

**Open sub-questions, not yet answered:**
- **Link, don't delete.** `listings.realtor_id`, `send_log.realtor_id` and
  `dnc_list.realtor_id` all point at `realtors` rows; merging two rows
  means repointing three FKs' worth of history, not removing a row.
- **Phone match scope** — the agent's own phone only, or the office phone
  too (a shared office line would over-merge distinct agents).
- **How DNC applies across a merged group** — if one of the pre-merge
  identities was suppressed, does the merged identity inherit that, and
  does merging ever need to *split* a suppression back out.

**When:** after the RBI realtor import (item 83) lands — merging now,
against the pre-import data, would just need redoing.

## 129. The Constant Contact export may be missing ~9 months of unsubscribes

**Status:** open

`rbi-constant-contact-dnc-list-09-28-2026.csv`'s newest recorded unsubscribe
is 2025-12-13, but the export itself was pulled 2026-09-28 and Constant
Contact is still RBI's active marketing vendor — so either nobody has
unsubscribed from anything RBI sent in the last nine months, or the export
was date-filtered somewhere upstream and is quietly missing that whole
window. Ask RBI's marketing contact whether the export was filtered by
date, and if so, pull an unfiltered one before relying on this list.

**When:** before the first send.

**Checked 2026-10-02.** `do not contact file constant contact.csv` (received
that day) holds the same 719 addresses as the 2026-09-28 export: 719 of 719 in
common, none new to `dnc_list`, newest recorded unsubscribe 2025-09-16. The
2025-12-13 date in this item is from the Airtable list, not Constant Contact.
This does not show the export was filtered: the file has no unsubscribes in
2021 or 2024 and one in 2025, so a quiet nine months is plausible. It does not
show completeness either, since two pulls returning the same set only shows the
tool is consistent. Not known: whether Constant Contact tracks other
suppression statuses this export omits. **Next:** ask RBI to read the total
count, newest date and any other suppression statuses from the Constant Contact
UI. Close when that matches 719 / 2025-09-16, or import what differs.

**Related, 2026-10-06.** Someone who unsubscribes through Constant Contact's own
footer lands in Constant Contact's suppression list, not in `dnc_list`. With
Constant Contact as the provider (decision log 2026-10-06) nothing copies those
back. Needs a pull of Constant Contact's suppressions into `dnc_list` (a sync,
user-initiated or scheduled, never a send path). Settle the design with this
item's export question: both are about whether `dnc_list` matches Constant
Contact's.

**Update 2026-10-07:** the spike showed the sync is more than housekeeping; see item
182.

## 130. `dnc_list.realtor_id` isn't maintained automatically

**Status:** open

The DNC import's commit SQL never sets `realtor_id` — today's 108 linked
rows came from a one-time manual backfill (`UPDATE 108`), run once against
the realtors that existed at the time. A realtor added to the hail system
*after* their suppression was recorded stays unlinked, silently — the
suppression itself still works (`dnc_list.email_norm` is the enforcement
key, `realtor_id` is documented as "convenience only, never a requirement"
in `database-schema.md`), but anything that reports "which of our known
realtors are suppressed" by joining on `realtor_id` rather than email would
undercount. Decide between a periodic backfill job and setting it on
every `realtors` insert.

**When:** with the send path — whichever report or check first needs
`realtor_id` to be trustworthy.

**Measured 2026-09-30:** 118 `dnc_list` emails match a `realtors` row, but only 108 have `realtor_id` set. The 10-row gap is this item's undercount, already occurring.

## 133. The DNC upload has no `MAX_CONTENT_LENGTH`

**Status:** resolved 2026-10-02

`dnc_upload` reads the whole file into memory (`upload.read()`) with
nothing capping its size. Fine at hundreds of rows; worth a limit before
someone uploads something much larger by mistake.

**When:** if a large file ever actually arrives.

**Resolved 2026-10-02.** `create_app()` sets `MAX_CONTENT_LENGTH` to 10 MB
(about 70,000 rows against today's ~760). It is app-wide, not in the view:
`CSRFProtect` parses `request.form` before the view runs, so a per-view limit
would come too late. Every POST is capped; the others are tiny. Over the limit,
Werkzeug raises 413 before the view and a handler shows the `csrf_error.html`
page with an upload-too-large message. Tested: a normal file previews, a file
over 10 MB gets the 413 page. A first version had the key misspelled
(`MAX_CONTENT_LENTGTH`); Flask accepts any config key, so it ran with no limit
and no error. Caught in review, not by a test.

## 134. No way to un-suppress through the UI

**Status:** open

`dnc_list.removed_at`/`removed_by` exist specifically for this and are
already documented as the reversal path (`database-schema.md`), but
nothing in the admin UI writes them — reversing a mistaken suppression
today means doing it by hand at the database.

**When:** if someone is suppressed by mistake and needs to be un-suppressed.

## 135. The email copy is a first draft, not final

**Status:** open

See `docs/decision-log.md`, "Email copy and merge fields: first draft, not
final." Images, the testimonials block, and the offer list from the old
Mailchimp newsletter are expected additions — the current draft is short
on purpose (the trust material competes with the storm-specific hook if
placed above the fold), but "short by design" and "short because it isn't
finished yet" need to stay distinguishable, and right now this is mostly
the second one.

**When:** before the first real send.

**Update 2026-10-08 (`ef4eeb2`).** The first real template is in the repo
(`hailsys/email/templates/hail_alert.html` and `hail_alert_subject.txt`): logo, a storm data
panel, a table of listings (one or several storms, one or several listings), the schedule button
and a second link for existing customers, the "Most of These Are Small" band, a testimonial, the
CRA and BBB badges, and a one-line advertisement notice. Constant Contact adds the postal address,
Unsubscribe and Update Profile itself. It has been test-sent to a Gmail inbox and looks right.
Still open: marketing sign-off on the factual claims (80% certifiable as-is, "most hail repairs
come in under the insurance deductible", the $35 five-year certification, the attributed Yelp
quote), and the badge and logo images, which are hosted in the dev trial's Constant Contact
Library and need re-uploading to the production account (the URLs are settings).

## 136. Open question: should `nearest_miles` always appear in the email?

**Status:** open

"Hail was reported 4.8 miles from your listing" is a much weaker hook than
"0.3 miles," and may undercut the message rather than strengthen it for
the far end of the match radius. Options: always show it, only show it
under some distance threshold, or drop it from the copy entirely and rely
on the fact that a report happened at all. Not decided.

**When:** with the template build.

## 138. Re-run the RentCast/DNC/in-house comparison once the updated in-house realtor list arrives

**Status:** open

The comparisons in items 55/83/122 were run against `realtors` at 7,082
rows and `Final Realtor Database-09-25.csv`. As of 2026-09-29, `realtors`
has grown to 9,163 (this year's hail dates were added), and a fresh
in-house realtor export is still pending — the file on hand is still the
09-25 one. Checked against the current data as an interim reference point,
**not recorded as a finding, since the in-house list it's half-built on is
about to be superseded:**
- 118 of 9,163 RentCast realtors are on the current DNC list (up from 83 of
  7,082 — the DNC list itself didn't change, the larger realtor pool
  caught more matches).
- 780 RentCast realtors also appear in the (still-old) in-house list, of
  which 4 are already suppressed — up from 675 of 7,082 last time, for the
  same reason.

**When:** as soon as the updated in-house realtor list is available —
re-run all three comparisons (item 122's pattern) against current data,
not these interim numbers.

## 140. Match page: mark, don't hide, listings past the freshness threshold

**Status:** open

Once a listing-freshness threshold exists (decision log, item 55 area —
threshold work in progress as of 2026-09-29, `sql/028`), the match page
needs to decide what to do with a listing older than it. Marking is
preferred over hiding: a stale listing is still evidence the storm hit
that address, and hiding rows would make the page's count disagree with
what the export contains.

**When:** with the send path, once the freshness setting itself lands.

## 143. Structured address input for search

**Status:** open (parked) — gated on `address_searches` showing the need

`/search` takes one free-text line. Census also offers `/locations/address`
with street, city, state and zip as separate fields. Worth building only if the
log shows `no_match` rows that are really a missing city or state. Check:
`SELECT outcome, count(*) FROM address_searches GROUP BY 1`, then read the
`no_match` rows.

**When:** after the search page has been used for a few weeks.

## 144. Fuzzy suggestions for mistyped addresses

**Status:** open (parked) — gated on `address_searches` showing typo misses

`address_key()` standardizes structure, not spelling, so a typo in the street
name is a `no_match`. `fuzzystrmatch` is installed; `pg_trgm` is **not**
(extensions installed 2026-10-01: `address_standardizer`,
`address_standardizer_data_us`, `fuzzystrmatch`, `postgis`,
`postgis_tiger_geocoder`, `postgis_topology`), so trigram suggestions mean
installing it, which needs a yes first. Candidates would come from
`properties` and `geocode_cache`, since Census has no suggest endpoint.

**When:** only if the `no_match` log is mostly typos.

## 145. A local TIGER load, as a swap behind `geocode.py`

**Status:** open (parked) — gated on Census limits or uptime becoming a problem

The better end state, per `docs/decision-log.md` "Address search: Census
Geocoder, keyed through `address_key()`": no external dependency or rate limit,
and the same TIGER data. Not done because the container lacks the tools, the
generated loader script needs rewriting and uses an older vintage (`rd22`,
`TIGER_RD18`), and it would be repeated on the production box. If it happens,
`geocode.geocode()`'s return shape is the interface to keep. It would also need
`postgis_tiger_geocoder`, which is still installed (item 94, dropped with the
extension left in place).

**When:** if `service_error` becomes routine, or Census throttles us.

---

## Watch list — triggers only, no work attached

*30 items, all `open (watch)`. Nothing to do unless the named trigger is observed.*

## 2. `nws_issuer` is NOT NULL and unguarded

**Status:** open (watch)

`iem_data.nws_issuer` is `NOT NULL`, but `iem_parse.parse_row` returns
`_clean(row["WFO"])`, which is `None` for an empty WFO field. No reject reason
covers it, so such a row would raise `IntegrityError` mid-batch and end the run.

Never observed in 22 years of archive. Left unpatched **deliberately** rather
than guessed at — adding a sixth reject reason for a case that has never
occurred would weaken the closed-enumeration argument that keeps
skip-and-continue from drifting into swallowing whatever goes wrong.

**When:** if a run ever fails naming `nws_issuer`.

## 8. `WALL CLOUD` and `ICE STORM` are not in `report_types`

**Status:** open (watch)

Two real IEM type pairs absent from the curated 37: `('X', 'WALL CLOUD')` from
2010-08-04 and `('5', 'ICE STORM')` from 2006-12-20. Both rejected as
`unknown_report_type` during the 2003–2015 backfill — the first time that
reason fired against real data.

Two rows in 22 years, both outside coverage, neither roof-relevant. `raw_row`
holds them verbatim so nothing is lost.

The choice is to add them to `report_types` with `roof_relevant = FALSE` (an
`INSERT`, plus a replay of that window to admit the rows), or leave them
rejected. A *recurring* version — a pair appearing in nightly data — would be a
seed gap to fix rather than a parser bug.

**When:** decide if a third unknown type ever appears.

## 12. Is a "storm" a first-class entity, or just a query?

**Status:** open (watch)

The UI concept is *"Hail — August 24 — 14 neighborhoods."* That groups many
individual reports into one event. Currently that grouping is a query
(`GROUP BY date, report_text`), not a table.

A real `storm_events` table would let someone name an event, attach a campaign
to it, and report on it as a unit. `send_log` would reference the event as
well as the match. The cost is a clustering rule — what makes two reports part
of the same storm? Same day? Same day and type? Spatial proximity?

Deferring is safe as long as the query-based grouping stays consistent. But if
it becomes a table later, matches made before it exists have no event to
belong to — a backfill problem created by waiting, not avoided by it.

**When:** Phase 2, if the storm browser needs to name or campaign against an
event rather than only group by query. *(`database-schema.md`, open question 1)*

## 18. Retention policy for `raw_payload`

**Status:** open (watch)

The `JSONB` of every RentCast response is cheap at current volume but grows
without bound. No policy set — probably fine indefinitely, worth revisiting if
`listings` gets large.

**When:** revisit only if `listings` size or storage becomes a real cost. A
trigger item, not a deadline. *(`database-schema.md`, open question 8)*

## 31. Derive column lists from `cur.description`

**Status:** open (watch)

Rather than maintaining them alongside the SQL. `ZIPS_COLUMNS` and
`ZIPS_SQL` have drifted twice, and the symptom is a blank cell, not an
error.

**When:** when a third projection drifts.

## 33. `REPORT_POINTS_SQL`'s `LIMIT` keeps the lowest `iem_id`, not the most recent reports

**Status:** open (watch)

Because `DISTINCT ON` pins the `ORDER BY`.

**When:** if the map ever hits the 2000 cap.

## 35. Reverse direction of the radar analysis — signatures with no report

**Status:** open (watch)

Needs event clustering; five overlapping radars re-detecting every ~5
minutes make raw counts meaningless.

**When:** only if the forward result raises a question it can answer.

## 38. Per-event-type radius — needs wind analysis first

**Status:** open (watch)

Split out of item 13, which item 13's resolution note didn't settle. Hail
cores are narrow, straight-line wind is broad, and a downburst is very
local, so one radius for every event type is a simplification. When there
is evidence, this belongs as a column on `report_types` next to
`roof_relevant` and `min_magnitude` — same kind of per-type judgment,
already version-controlled, queryable from SQL.

**When:** when there's wind analysis to base a number on. *(`tuning.py`'s own
comment already points here.)*

## 41. Index on `report_zip_distances (zcta5)` — for address lookup

**Status:** open (watch)

The table's only index today is the `(iem_id, zcta5)` primary key, which
serves the `d.iem_id = i.iem_id` join `storms.py` runs. A `zcta5`-first
index would serve a different access pattern — "every report near this one
zip" — which nothing queries yet but item 23's address-lookup tool would.

**When:** when item 23 is built.

**Updated 2026-10-01:** item 23's tool shipped, but it queries `iem_data.geom`
(GiST) directly, not `report_zip_distances`, so this index is still not needed.

## 42. A complete pull where every zip failed still counts as pulled

**Status:** open (watch)

`run_pull` marks `api_status = 'complete'` once it's iterated every zip,
regardless of how many individual zips returned a non-200 and zero
listings. A pull that technically finished but got nothing back reads the
same as one that worked.

**When:** edge case; revisit if it's observed for real rather than reasoned
about.

## 44. A pull job produces two feed lines

**Status:** open (watch)

`hailsys/web/jobs.py`'s `_pull_and_match` runs `match_storm` right after
`run_pull`, so one click surfaces as a pull line and a separate match-run
line in the activity feed. Accurate — both things happened — but reads as
more activity than one decision produced.

**When:** acceptable for now; revisit if the feed gets noisy.

## 52. Re-check for NULL property coordinates as more zips are pulled

**Status:** open (watch)

`properties.geom` is generated from `list_latitude`/`list_longitude`; a row
missing either produces a NULL `geom`, silently dropping that property from
any spatial join. Worth a periodic check as more zips get pulled and the
`properties` table grows past its Phase-3 size.

**Re-checked 2026-09-26 — figure was stale.** Still zero, but against
16,407 properties, not the 1,964 this item originally cited. No property
has a NULL `list_latitude`/`list_longitude` or a NULL `geom` today.

**When:** periodic, as pull volume grows.

**Re-checked 2026-09-30:** still zero NULL `geom`, now against 25,219 properties (16,407 on 2026-09-26, 1,964 when filed).

## 54. Missing agent email — watch it as a rate

**Status:** open (watch)

`listingAgent.email` is frequently missing (`docs/data-sources.md`), and a
listing with none gets no `realtor_id`, so it has nobody to send to. Measured
2026-09-22: the 2026-09-21 pull returned 231 new listings and **40 (17%)** had
no agent email. Across all 508 listings it is 49 (9.6%), and it varies a lot by
zip — 80014 9 of 277, 80103 8 of 40, 80105 4 of 45, 80135 0 of 68, 80136
28 of 78 (36%). Of the 49, 30 carry an office email, which is the question
item 19 leaves open. One pull is a small sample; track the rate as more zips
are pulled before treating 17% as typical.

**When:** watch as pulls accumulate; it decides how much outreach is
reachable at all, so settle it before Phase 5.

**Re-measured 2026-09-30:** 3,927 of 26,093 listings (15.0%) have no `realtor_id`. The 9.6% figure above (49 of 508 listings) is what it was when filed.

## 56. Backfill progress and ETA should count reports, not ID range

**Status:** open (watch)

`scripts/backfill_zip_distances.py` batches by `iem_id` range and reports
progress and ETA as a fraction of that range. `iem_id` runs 1 to 398,134 for
177,515 reports — the range is 2.2 times the row count (presumably ids
consumed by inserts that did not land), and nothing guarantees the ids are
evenly spread over time. So the logged percent complete and ETA are measured
against ids that do not exist. Count the reports still needing rows up front
and report progress against that.

**When:** only if the script runs again — a ceiling change or a TIGER reload
(`scripts/verify_zip_distances.py` covers the check afterward).

## 108. Match-page column widths are positional

**Status:** open (watch)

`style.css` sizes the matched-listings columns with
`.agent-group th:nth-child(n), td:nth-child(n)` rules, one per column, nine
today (Address, Zip, Type, Built, Price, Nearest, Max, Reports, MLS), and they
sum to 71rem. Nothing ties the numbers to the headings in `matches.html`. Add,
remove or reorder a column there and every width after it lands on the wrong
column, with no error: the table still renders, just with an address-wide Zip
column and a narrow Address.

**When:** if those columns change.

## 109. The match page on a phone: one column at a time

**Status:** open (watch)

The nine fixed columns total 71rem, and `table-layout: fixed` can't shrink
below that, so on a phone each agent group scrolls sideways inside its own
`.table-scroll`. The Address column alone (22rem) is wider than a 360px
phone's content area (about 19.5rem), so it shows one column at a time. The
right-edge shadow on `.table-scroll` is there so the scroll is discoverable.

**Considered and declined 2026-09-24: stacked cards under 40rem** (each row a
block of label/value pairs, from `data-label` attributes on the cells).
That page is desktop scan-and-compare work, and cards would give up the
side-by-side comparison it exists for. Smaller options if it comes up: one
shared scroll container for all agent groups, so a phone scrolls sideways once
and not once per agent; or a narrower Address column with a sticky first
column, which would change the widths in item 108.

**When:** if anyone actually works it on a phone.

## 112. The territory layout on tablets and landscape phones

**Status:** open (watch)

- **Landscape phone:** under 48rem the territory map stacks below the table at
  `height: 50vh; min-height: 14rem`. On a phone in landscape that is nearly
  the whole visible height, and a one-finger drag on a Leaflet map pans the map
  instead of scrolling the page, so the page can get stuck on the map.
- **Tablet landscape (about 1024px):** the split stays side by side and the
  nine-column table gets about 45% of the width, so it scrolls sideways inside
  its box. Stacking at a wider breakpoint would fix that, and would stack some
  desktop windows too.

**When:** if anyone uses the territory page on a tablet or phone.

## 113. Small CSS leftovers

**Status:** open (watch)

- The `.table-scroll` shadow is drawn as a background, which cells with their
  own background paint over, so it doesn't show behind a table's header row.
- `.admin-form` caps the settings form at 24rem even on a desktop, so its
  table is cramped there too. The label-above-value stacking only applies
  under 48rem.
- `td.addr { min-width: 12rem }` no longer does anything on the match page,
  because `table-layout: fixed` ignores it. It is not dead everywhere:
  `.table-scroll td.addr` would apply to any other table with an `addr` cell.
- `h1 ~ table` is now unmatched by any template. The corrected rule is kept as
  a guard for a future page.

**When:** if any of them gets in the way.

## 115. Nothing writes `'cancelled'` except the sweep

**Status:** open (watch)

`api_pulls.api_status` allows `'cancelled'` (`sql/008`), and until 2026-09-25
nothing wrote it. Now only `sweep_stale_pulls()` does, for a pull whose
process was lost (item 47). There is no user-facing cancel: once a pull is
running, nobody can stop it, and its cost is spent as it goes. So `'cancelled'`
in `api_pulls` currently means "lost", never "someone chose to stop it". If a
cancel is ever built, it either needs its own status or a column that says
which kind of cancel this was.

**When:** if a cancel affordance is ever wanted.

## 117. An all-types pull can't be recorded

**Status:** open (watch)

`api_pulls` has a `storm_link_paired` CHECK: `storm_date` and `report_text` are
both set or both NULL. `pull_start` accepts a missing `type`
(`report_text = ... or None`), so a `POST /pull` without one would insert a
storm date with no report type, fail the constraint in the pull thread, and
log `event=pull_job_failed`. The banner would then say it lost track of the
pull after about 15 seconds. The Pull links always send a type and `/match`
requires one, so nothing in the UI reaches it; found 2026-09-25 while
diagnosing item 116.

**When:** if a pull of every type is ever wanted, or `/pull` is reachable from
anywhere else.

## 118. The live pull banner: limits

**Status:** open (watch)

The banner under the storm-list heading replaced the "Pull started" flash
(decision log, "A live banner under the heading replaces the \"Pull started\"
flash"). **Confirmed working end to end by the developer in the browser,
2026-09-27:** this was the display they had been trying to fix all along, and
it now updates on its own from "Pulling…" through to the finished message,
with no refresh. What it still doesn't do:
- **It shows only the pull the user just started.** Another user's running pull
  appears in the Status cells and the Activity page, not in the banner.
- **It is one-shot.** Any reload of the storm list clears it, a marker older
  than 10 minutes is ignored, and leaving the page and coming back before the
  pull finishes loses it (the Status cell still shows the state).
- **It polls up to 200 times** (10 minutes), then stops.
- **A re-pull of an already matched storm ends with "0 new matches"**, because
  `matches_created` counts new rows only.

**When:** the rest, if it bothers anyone.

## 123. 11 non-Land properties have no house number and can't be deduplicated by address

**Status:** open (watch)

`address_key` is `NULL` for 170 properties (item 55). 159 are `property_type
= 'Land'`, already excluded from matching by `_MATCH_SQL` — vacant land has
no roof, so this doesn't matter for outreach. **11 are not Land** (9 Single
Family, 1 Condo, 1 Manufactured) — real, matchable properties with an
address RentCast gave with no leading house number (a rural road-and-number
format, or a literal `Tbd` placeholder), so none of the dedup work in item
55 can ever apply to them, even after it's wired in.

**When:** known gap, revisit if one of the 11 is ever actually matched and
emailed about.

**Re-measured 2026-09-30:** 23 non-Land properties have a NULL `address_key` (11 when filed), out of 287 NULL keys overall.

## 125. `address_key`'s `house_num` can hold a range, not just a number

**Status:** open (watch)

`3440-3450 W 55th Pl` (one of the 21 duplicate groups, item 55) standardizes
to `house_num = '3440 3450'` — a range, not a single number. Nothing
downstream currently assumes `house_num` is a single integer-like value, so
this isn't breaking anything today, but it's a shape the key can take that
isn't obvious from the column's own name.

**When:** if it ever breaks a comparison or a parse.

## 137. `agent_first_name` (splitting `agent_name` on the first space) has known failure modes

**Status:** open (watch)

Fails on titles (`"Dr. Susan Clark"` gives `"Dr."`) and on people who go by
two given names. Checked against the current data (item 16's decision-log
entry): 3 single-word names exist today, all of which render as the whole
name and read fine as a greeting — no titles or two-given-name cases found
in the current 7,082. Not a problem yet, but the in-house realtor import
(item 83) is expected to be messier than RentCast's data, and that's where
this is more likely to actually bite.

**When:** if the in-house realtor import makes this worse.

## 141. RentCast street names sometimes carry an embedded comma

**Status:** open (watch)

A meaningful share of `property_address` values put a comma inside what
should be one continuous street name before RentCast's own city/state/zip
commas — `13456 Via, Varra`, `County Road, 22`, the highway addresses
already covered under item 55 (`8557 Highway, 86`). RentCast's own
`address_1`/`address_2` split breaks at that comma too, so the street name
lands partly in each field. Degrades `address_key` and any other parsing
built on the raw string. Reported at roughly 1,721 addresses (6.9%) of the
current table; **that count is as reported, not independently reproduced
this session** — two different reproduction attempts landed at 24.2% (too
broad, catches the ordinary Unit/Apt comma) and 1.66% (too
narrow), neither matching. The phenomenon itself is confirmed real via
direct examples; the precise count needs its exact method double-checked
before it's cited elsewhere.

**When:** known limitation, not currently blocking anything.

## 142. One property has house number 0

**Status:** open (watch)

`0 County Rd, 102 Lot 3, Elbert, CO 80106` — confirmed, a single row,
`address_key` `0||102||LOT 3|80106`. Not investigated further; noted in
case it ever causes a downstream surprise (a `0` house number failing a
truthiness check somewhere, for instance).

**When:** note only.

## 146. `geocode_cache` rows never expire

**Status:** open (watch)

Census address data changes slowly and `hail_app` cannot update or delete cache
rows, so a re-geocoded or renumbered address keeps its old point until someone
with admin access removes the row. Watch for a search whose point is visibly
wrong.

**When:** note only, until a wrong cached point is reported.

## 147. The Census request rate is self-imposed

**Status:** open (watch)

`RATE_LIMIT_PER_SECOND = 2` in `hailsys/geocode.py` is a conservative guess;
Census publishes no limit we could verify. Only cache misses reach the network.
Raise it only on evidence, and watch for HTTP 429 in the web log.

**When:** a run of 429s, or bulk geocoding is ever wanted.

## 148. `BENCHMARK = "Public_AR_Current"` is a versioned name

**Status:** open (watch)

Census retires benchmark names over time. A sudden rise in `service_error` or
`no_match` in `address_searches` is the signal. Check
`https://geocoding.geo.census.gov/geocoder/benchmarks` before changing it.

**When:** that rise.

## 153. Agent email and phone are visible to viewers

**Status:** open (watch) — accepted 2026-10-01, revisit on the trigger below

`MATCHES_COLUMNS` includes `agent_email` and `agent_phone`; `/storms/matches.csv`
and `/exports/matches.csv` are `login_required`, viewers included; and
`/storms/matches` renders both fields under each agent heading
(`matches.html:50-51`). Only the realtor list (`/exports/realtors.csv`) is
`sender`/`admin`. That split was deliberate on 2026-09-24 (decision log, "Who can
download what", matching Phase 4's done-when that a viewer can browse and
export).

**Accepted 2026-10-01, not resolved.** RBI is a small office, anyone who would
hold a viewer account already has this data by other means, and restricting it
later is a small change: column filtering on the exports plus a role check on the
one template block.

**The realtor export's `sender`/`admin` restriction is about bulk extraction, not
about the contacts themselves.** One file with every agent's email and phone is a
different exposure from reading them a storm at a time. Do not "fix" the
inconsistency by loosening the realtor export, or by tightening the rest without
reading this.

**When (revisit trigger):** a viewer account is issued to anyone outside RBI: a
contractor, a part-time hire, a partner agency. The premise above then no longer
holds. Identified in review and not filed at the time (decision log, "Review
findings: decisions, and a gap in the review loop").

---

## Deferred workstream: permits

*Items 70–82, 97 and 98, all gated on item 70 ("until the system is running"); item 74 is resolved and sits in the Closed section. 14 items here. A future workstream, not a backlog.*

## 154. Fixes land in commits that don't close their parking-lot item

**Status:** open

Item 105 (`postgis` log rotation) was fixed in `4e51bdd` on 2026-10-01 inside an
unrelated commit, and stayed open until a sweep on 2026-10-02. Item 29 (filter
parsing) was mostly fixed by `854f5bc` on 2026-09-18 and was still open two
weeks later. "Findings get filed" covers problems found; nothing covers a fix
that lands without updating its item.

**When:** at each phase close, and before Phase 6. Run the sweep: for each open
item, check its named files, functions and symptoms against the repo and git
history.

## 155. The pull estimate offers "Pull 0 zips"

**Status:** resolved 2026-10-02

Seen 2026-10-02 on `/pull/estimate` for 2026-10-02 HAIL, a storm with no zips in
coverage: "0 zips, about 0 RentCast requests" with an enabled "Pull 0 zips"
button. A click would record an `api_pulls` row for nothing, and workstate would
then read the storm as pulled. No money is spent.

**When:** with the next pass over the pull estimate.

**Resolved 2026-10-02**, the same session it was found. `pull_estimate.html`
shows "No zips in coverage for this storm, so there is nothing to pull" instead
of the button when `zip_count` is 0, and `pull_start()` refuses a zero-zip POST
with a flash, so a hand-built request is covered too. Committed in `0b3d7a3`
with the item 51 work. **Verified:** the page shows the note and no button
(developer, reloaded on `hail-dev`); a zero-zip POST through Flask's test client
as an admin returned a 302 to `/` with the "no zips to pull" flash, left
`pull_watch` unset, and `api_pulls` stayed at 40 rows.

## 156. Four light-mode colour pairs are below WCAG AA contrast

**Status:** open

Found 2026-10-02 while adding dark mode, by computing contrast ratios for the
text and background pairs in `style.css`. These four were already below 4.5:1
(the AA minimum for body text) in the original light colours; dark mode did not
cause them and meets 4.5 for every pair.

| Pair | Ratio |
|---|---|
| link / accent text (`--accent` `#2b7cd3`) on the page background | 4.26 |
| the same on `--panel` (filter bars, admin panels) | 3.94 |
| the "stale" badge (`#64748b` on `#f1f5f9`) | 4.34 |
| white text on the blue filled buttons (`--accent-fill` `#2b7cd3`) | 4.26 |

Candidate fix, checked numerically and not applied: light-mode `--accent` and
`--accent-fill` `#1f6fc4` (5.09 on white, 4.70 on panel, 5.09 as a button
background under white text) and the stale badge text `#566376` (5.57). That
darkens the blues slightly in light mode, so it is a visible change. The
`--accent-fill-hover` value would need the same check.

**When:** whenever, or before Phase 6 if anyone outside the developer reads the
app in light mode.

## 157. `database-schema.md` was behind `sql/029`–`033`

**Status:** resolved 2026-10-05

Found 2026-10-05 in a docs review. The schema doc, last touched 2026-09-29, did
not mention `sql/029`/`030` (append-only triggers on `send_log` and
`email_templates`), `031` (`geocode_cache`, `address_searches`), `032` (role
search_path) or `033` (one running pull per storm). It still said append-only was
"application code only" and open question 10 was deferred, and its table count
("Twenty-one") was already wrong: `sql/` has 27 `CREATE TABLE`s.

**When:** before Phase 5 send work, so the doc matches the database that work
will rely on.

**Resolved 2026-10-05**, commit `3a595aa`: both guard triggers, the address
search section, the search_path note and the `033` index are written up, question
10 is marked resolved, and the count is 27 (counted from `sql/`, not a live
database). Not checked against the running schema.

## 158. Docs drift: `CLAUDE.md` schema path, `phases.md` Phase 4 date

**Status:** resolved 2026-10-05

Found 2026-10-05. `CLAUDE.md` said to read `database-schema.md`, which is
`docs/database-schema.md` (no copy at the root). `phases.md` said Phase 4 closed
2026-09-23; it closed 2026-09-24 (first marked closed 09-23, then held open a
day).

**When:** with the next docs pass.

**Resolved 2026-10-05**, commit `3a595aa`.

## 159. Dark mode (`2a86732`) had no documentation

**Status:** resolved 2026-10-05

Found 2026-10-05. The commit added `theme.js`, theme variables and a header
switch; no doc mentioned it.

**When:** with the next docs pass.

**Resolved 2026-10-05**, commit `3a595aa`: decision-log entry "Dark mode:
per-browser, follows the OS by default". `command-ref.md`, the README and
`hail-consolidated.md` were not changed.

## 160. `db-schema-diagram.md` and `hail-consolidated.md` lag the migrations

**Status:** resolved 2026-10-05

Found 2026-10-05. `db-schema-diagram.md` stopped at `sql/023`; a grep found
nothing for `029`–`033` in it and nothing for `029`, `030`, `031` or `033` in
`hail-consolidated.md`. Read against the SQL the same day: everything the
diagram drew was correct, but it lacked `dnc_import_batches`, `dnc_import_rows`,
`geocode_cache`, `address_searches`, `properties.address_key`,
`listing_freshness_days`, the guard triggers and the `sql/033` index, and said a
trigger fired on 4 columns that now fires on 5. The consolidated brief counted 23
tables (27 in `sql/`), said append-only was still open (question 10), said the
DNC import had not happened, and its `sql/` layout stopped at `023`.

**When:** before Phase 5 send work, together with item 129.

**Resolved 2026-10-05.** `191a7e0`: the diagram draws `sql/024`–`033`, and the
consolidated brief's wrong statements were corrected. `a4071a9`: the brief was
re-synced through 2026-10-05 (§2 Phase 5 groundwork, §5, §6, §7, §8, §9, §10,
§11). **Not re-checked:** `schema-review.md` is still written against
`001`–`023`, and `docs/phases.md`'s Phase 5 checklist still says the legacy DNC
import is not done (see item 161).

## 161. `docs/phases.md` Phase 5 section still said the legacy DNC import was not done

**Status:** resolved 2026-10-05

Found 2026-10-05 while re-syncing the consolidated brief. The Phase 5 starting
point said "The legacy DNC lists are not yet imported", and its "Not started"
list and checklist still carried the DNC import, while the same section's
progress notes recorded it done on 2026-09-28/29. Its progress also omitted
`sql/029`/`030` (append-only triggers), address search, the Cloudflare Tunnel
and `sql/033`.

**When:** with the next docs pass, before Phase 5 send work.

**Resolved 2026-10-05.** The starting point, the progress list, the "Not started"
list and the DNC checklist line are corrected, and the missing work is added.
The commit is the one that carries this entry; see `git log -- docs/phases.md`.

## 162. `docs/schema-review.md` covers `sql/001`–`023`, not `024`–`033`

**Status:** resolved 2026-10-05

Found 2026-10-05 while re-syncing the diagram and the consolidated brief. The
review prompt says "001 through 023" (line 14), lists what each of those files
does (through `023`), and says `018`–`023` are all wrapped in a transaction.
Ten migrations have landed since: `024`/`025`/`027` (`address_key`), `026` (DNC
import staging), `028` (listing freshness), `029`/`030` (append-only triggers,
which carry the project's most load-bearing rule), `031` (address search), `032`
(role `search_path`) and `033` (one running pull per storm). A re-run of the
prompt as written would skip all of them, including the two guard functions.

**When:** before Phase 5 send work, and before the first migration after `033`.

**Resolved 2026-10-05.** The prompt now covers `sql/001`–`033`, describes each
new migration, expects 27 tables, runs `guard_test.sql` separately (the build
loop is `sql/[0-9]*.sql`), and checks the append-only triggers, the five-column
settings history, `address_key`, the DNC staging tables, the `033` index and the
role search path. **Not run at that time.** The review was run on 2026-10-05 in a
throwaway container the same day; its findings are items 164–167.

## 163. The schema-review build instructions would have overwritten the real roles

**Status:** resolved 2026-10-05

Found 2026-10-05 while rewriting `docs/schema-review.md`. Its build step created a
scratch *database* and ran every `sql/*.sql` file into it. Roles are cluster-wide,
not per-database: `sql/010` ends with `ALTER ROLE ... PASSWORD`, and `sql/032` runs
`ALTER ROLE ... SET search_path`. Run inside the real cluster, with the prompt's
`HAIL_INGEST_PASSWORD=scratch HAIL_APP_PASSWORD=scratch`, it would have reset the
real `hail_ingest` and `hail_app` passwords to `scratch` and broken the web app
and the nightly ingest. Nothing was run that way; it was found by reading.

**When:** before anyone next runs the review prompt.

**Resolved 2026-10-05**, commit `5bcae52`: §1 now says to build in a throwaway
cluster or container, never against the running `hail-dev` or production cluster,
and the build loop skips `guard_test.sql`. Not tested: the throwaway-container
route was written from reading, not run.

## 164. `sql/guard_test.sql` fails on an empty build

**Status:** resolved (residuals) 2026-10-05

Found 2026-10-05 running the schema review in a throwaway `postgis/postgis:16-3.4`
container. The test takes its rows from the first `users`, `realtors` and
`storm_listing_matches` rows, so on a fresh build it stops at its first insert
(`realtor_id` not null). That is its stated design, but nothing told the
reviewer, and `schema-review.md` said to run it right after the build. Seeded
with one row each (plus a report type, an `iem_data` row, a property and a
listing), it passed 24 of 24.

**When:** if the schema is ever built in CI, or by anyone else, make the test seed
its own rows.

**Resolved 2026-10-05**, commit "Docs: schema review run; ..." (`git log --
docs/schema-review.md`): `schema-review.md` §1 now carries the seed statements
and says the test fails on an empty database. **Residual:** the test itself is
unchanged and still not self-seeding.

## 165. `schema-review.md` had three wrong statements

**Status:** resolved 2026-10-05

Found 2026-10-05 by running it. It said to expect 27 tables (a build has 30 in
`public` plus `spatial_ref_sys`: the 27 and `us_gaz`, `us_lex`, `us_rules` from
`address_standardizer_data_us`); that the guard test reported 18 of 18 (24 now,
with `033`); and, in §3, that duplicate constraint names across tables are a
database-wide problem, which is true of index names but not `CHECK` constraints
(`finished_has_timestamp` is on `match_runs` and `ingest_runs`,
`removal_is_complete` on three tables).

**When:** with the next docs pass.

**Resolved 2026-10-05**, the same commit as item 164.

## 166. `settings.listing_freshness_days` was undocumented in `database-schema.md`

**Status:** resolved 2026-10-05

Found 2026-10-05 by comparing every column in the built schema to the doc. `sql/028`
added the column to `settings` and `settings_history` and widened the history
trigger to five columns; `database-schema.md` described four. It was missed when
the doc was updated for `029`–`033` earlier the same day, because the check was a
grep for those migration numbers. It was the only undocumented column in the
built schema; the reverse direction (documented columns the DDL lacks) was only
checked at table level.

**When:** with the next docs pass.

**Resolved 2026-10-05**, the same commit as item 164: both tables' sections and
the trigger definition describe the fifth column.

## 167. Four foreign keys carry Postgres's default names

**Status:** open

Found 2026-10-05. The convention is `fk_{table}_{target}`; these were declared
inline and got generated names: `settings_history_changed_by_fkey` (`sql/020`),
`match_runs_emp_id_fkey` (`sql/022`), `dnc_import_batches_uploaded_by_fkey` and
`dnc_import_rows_batch_id_fkey` (`sql/026`). Harmless to behaviour. The admin
route maps some CHECK names to messages, but nothing was found that maps these
FK names.

**Decision:** leave as applied. A rename is a new migration for a cosmetic gain,
the same call already made for `match_runs_storm_idx` (`schema-review.md` §2).

**When:** with the next migration that touches one of these four tables, or the
next time the FK naming convention is enforced across the schema.

## 168. Constant Contact tokens: encryption at rest, and tokens never in logs

**Status:** resolved 2026-10-06 (`5e4983b`)

The Constant Contact OAuth2 grant (decision log 2026-10-06) is the first live
credential stored in the database; every secret so far lives in `.env`. A dump,
backup or stray SELECT would otherwise hold something that acts on RBI's account
(access token about 24 hours, refresh token 180 days unused). Decided: encrypt in
Python before insert, key from `.env`; pgcrypto rejected because the key would sit
in query text and item 99 means logging is unconfigured. Needs the `cryptography`
package (approved 2026-10-06, not yet installed). Rule that goes with it: a token
never appears in a log line or an exception message, including a failed refresh.
Needs a test.

**When:** before `sql/034` and the Constant Contact module are written.

**Resolved 2026-10-06.** `hailsys/constantcontact/tokens.py` encrypts with Fernet
before insert, key `HAIL_TOKEN_KEY` from `.env` (`web` only), `cryptography==50.0.2`
installed. A token never reaches a log line or an exception: every message is fixed
text, `from None` drops the cause, `Grant` and `Issued` hide their token fields from
`repr()`, and only a short `error` code is taken from an error response. Tests
(`tests/test_cc_tokens.py`, `tests/test_cc_oauth.py`) fail if any of that regresses.
Checked live: the stored columns are ciphertext, and no token string appears in the
web logs.

## 169. Rotating refresh tokens need serializing

**Status:** resolved 2026-10-06 (`5e4983b`), one residual

Each refresh invalidates the previous token, so two concurrent refreshes (two web
requests) lose the grant, and recovery is re-running the authorization by hand.
Plan: `pg_advisory_xact_lock`, re-read the latest token row, refresh only if still
expired, insert, commit, and only then use the new token. Persist-then-use is the
whole discipline; the lock is the part it does not cover. Note that item 51 chose
an index over an advisory lock for pulls because the index gave the guarantee;
here no index can, since the failure is on Constant Contact's side.

**When:** with the refresh path in the Constant Contact module.

**Resolved 2026-10-06.** `oauth.get_access_token` takes `pg_advisory_xact_lock`,
re-reads the latest row, refreshes only if still stale, inserts, commits, and only
then returns the token; if the provider answered but the insert fails it logs
CRITICAL and says to reconnect. A test pins the order lock, call, save, commit.
Checked live: two refreshes 34 seconds apart gave rows 82 and 83, each with a new
access and refresh token, so the stored refresh token works for the next refresh.

**Residual:** the lock has not been exercised by two requests refreshing at the
same moment. **When:** before the first send, or when a second process can refresh.

## 170. Does Constant Contact set Reply-To on a rewritten From?

**Status:** resolved 2026-10-06, scoped to a self-authenticated domain on a trial account

Without self-authentication Constant Contact rewrites the visible From to
`@shared1.ccsend.com` (paid) or `@shared2.ccsend.com` (trial). Unknown whether
replies still reach the verified address. A campaign that expects replies needs
this answered. Trial limit: about 100 sends total, length stated as 14 or 30 days
(confirm at signup).

**When:** the trial header test: one send to yourself, read the raw headers.

**Partly answered, 2026-10-06.** A test email went out through Constant Contact and
was replied to. The reply's headers: `From: justynlarry@gmail.com` (the recipient
replying), `To: justyn@roofbrokersinc-weather.com`, and `In-Reply-To` and
`References` naming a `...@synd.ccsend.com` Message-ID, so it answers a Constant
Contact message. A reply goes to the original's `Reply-To` if it has one and to its
`From` otherwise, so the original named `justyn@roofbrokersinc-weather.com` in one
of the two. That address is on a Cloudflare zone with Email Routing, and the
reply's `X-Forwarded-For: justyn@roofbrokersinc-weather.com rbi.justyn@gmail.com`
shows it forwarded to and delivered at `rbi.justyn@gmail.com`. So replies do reach
the verified address.

**What this does not settle:** which of `From` or `Reply-To` carried it, and
whether the original was signed by our domain or by `ccsend.com`. The DKIM and SPF
results in the pasted headers are Cloudflare re-signing the forwarded *reply*, not
Constant Contact's signature on the original. `roofbrokersinc-weather.com` is the
dev domain, not `roofbrokersinc.com` or `send.roofbrokersinc.com`, so it says
nothing yet about RBI's sending identity (item 96).

**When:** read the raw headers of the original message in the receiving inbox
(`From`, `Reply-To`, `DKIM-Signature` `d=`, `Authentication-Results`); that closes
this item.

**Resolved 2026-10-06, from the original message's raw headers.**
`From: "Roof Brokers, Inc." <justyn@roofbrokersinc-weather.com>` and
`Reply-To: justyn@roofbrokersinc-weather.com`: the From was not rewritten and
Reply-To was set explicitly to the same address, so a reply reaches the sender
(it went to that address and was forwarded to the Gmail inbox). Two DKIM signatures
passed: `d=roofbrokersinc-weather.com` selector `ctct1` (aligned with the From, so
DMARC passed) and `d=auth7.ccsend.com`. SPF passed for `in.constantcontact.com`,
which is not the From domain, so DMARC passed on DKIM alone, consistent with
Constant Contact not supporting SPF alignment. The DNS agrees: `ctct1` and `ctct2`
`_domainkey` CNAMEs on `roofbrokersinc-weather.com` point at
`100._domainkey.dkim1.ccsend.com` and `200._domainkey.dkim2.ccsend.com`, the two
self-authentication records.

**Scope, so this is not over-read.** The account is a free trial; production will
be paid, and paid behaviour is not tested. The domain was self-authenticated, so
this does not show what a rewrite looks like. The earlier claim that an
unauthenticated sender is rewritten to `@shared2.ccsend.com` (trial) or
`@shared1.ccsend.com` (paid) was conditional and is not contradicted; it is just
untested. It was a campaign sent from the UI (`X-Campaign-Activity-ID`), so it
says nothing about per-recipient API sends. A trial account can self-authenticate
a domain.

## 171. `tests/test_cc_tokens.py` fails under the host Python

**Status:** open

Found 2026-10-06. The file imports `cryptography` at the top, and the host Python
does not have it (the package is in the `web` and `app` images only), so
`python3 -m unittest discover -s tests`, the run `README.md` documents, now ends
with an error instead of passing. `tests/test_cc_tokens_db.py` avoids this with a
try/except around its imports and skips when the package or the database is
missing. The pure test should do the same. Until then the container run
(`docker compose run --rm --no-deps -v ./tests:/app/tests:ro web python -m
unittest tests.test_cc_tokens`) is the one that works, and README's test
instructions and its "100 cases" count are stale for both new files.

**When:** with the next edit to `tests/test_cc_tokens.py`, and before anyone
relies on the host run as the whole suite.

## 172. `scripts/status.sh images` reports an image stale when the file changed before the build

**Status:** open

Found 2026-10-06. After `requirements.txt` was edited (17:29:08Z) and `web` and
`app` rebuilt from it (image created 17:29:22Z), `status.sh images` still printed
`STALE: requirements.txt` for both, and the installed package matched the file.
The cause is in `cmd_images`: it lists every uncommitted file plus every commit
newer than the image, and cannot tell an edit made before the build from one made
after it. A file changed and then built from stays "stale" until committed, and
committing it after the build keeps it listed, because the commit is newer than
the image. The output is a false positive, not a missed change, so it errs safe,
but it trains the reader to ignore the line it exists to show.

**Considered:** comparing each file's mtime to the image's creation time for
uncommitted files, and the commit time for committed ones, instead of listing
them all.

**When:** the next time `status.sh images` is read and the answer matters, for
instance before trusting `ingest` for a nightly run after a change.

## 173. Constant Contact's auth server rejects Python's default User-Agent

**Status:** resolved 2026-10-06 (`5e4983b`)

Found 2026-10-06 on the first Connect click: the token exchange failed with
`Constant Contact refused the request (403, unknown)` and no `error` field. Tested
with dummy credentials: `Python-urllib/3.x`, urllib's default agent, got a 403 HTML
page from Cloudflare, which sits in front of `authz.constantcontact.com`; the same
request with a custom `User-Agent` reached the OAuth server and got a 401 JSON
`invalid_client`. The real credentials were never checked, so the failure looked
like a credentials problem and was not one. Fixed with `USER_AGENT` in
`hailsys/constantcontact/oauth.py`, sent on the token and account requests, and a
test that fails if either request goes without it. The agent states who we are; it
does not imitate a browser.

**Related finding in the same response:** the error JSON is Okta-shaped
(`errorCode`, `errorSummary`), not `error`. `_error_code` reads both now (item 174).

**When:** the next outside API integration. A bare 403 from a provider behind
Cloudflare is worth a User-Agent check before the credentials.

## 174. `_error_code` accepts only lowercase letters and underscores

**Status:** open

Found 2026-10-06. `hailsys/constantcontact/oauth.py` line 84 matches
`r"[a-z_]{1,40}"`, so an error code with a capital or a digit (Okta's own
`E0000011` style) is logged as `unknown`. The match is deliberately narrow so
nothing but a short code can reach the log; the fix is to widen it to
`r"[A-Za-z0-9_]{1,40}"`, which still excludes spaces and punctuation. Not yet
made; the current tests use lowercase codes and pass either way.

**When:** the next edit to `oauth.py`, or the first time a real error shows up
as `unknown`.

## 175. The connection bar cannot see a revoked or lapsed grant

**Status:** accepted 2026-10-06

The green/red bar on the Admin and Storm Days pages (`hailsys/queries/ccstate.py`)
reads the database only, by design: a page load must cost nothing and must not
fail because Constant Contact is down. So it shows "connected" whenever a token
row exists, and keeps showing it after a grant is revoked on Constant Contact's
side or its refresh token lapses, until a refresh fails. How long an unused
refresh token lives is not in the documentation read (`server_flow.html`); the
180 days used earlier in this project is unverified.

**Accepted** because nothing sends yet, and a failed refresh is not silent: it
logs an error (critical if the provider answered but the new token could not be
saved) and an admin gets the message on `/cc/`.

**When:** before the first real send. Find out what a dead grant looks like, check
the refresh-token lifetime with Constant Contact, and decide whether the bar
should warn on the age of the last issue.

## 176. `.env` was world-readable, and one empty `HAIL_TOKEN_KEY=` line was written

**Status:** resolved 2026-10-06 (no commit: `.env` is untracked)

Found 2026-10-06 while adding the Constant Contact settings. `.env` had mode `644`
and holds the database passwords, the Flask secret, the RentCast key and now the
Constant Contact secret and the token encryption key. Set to `600`. Separately,
`echo "HAIL_TOKEN_KEY=$(docker compose run ...)" >> .env` ran with the inner
command failing, which expands to nothing, so `echo` appended an empty
`HAIL_TOKEN_KEY=` line anyway. Removed with `sed`. The generator now runs through
`docker run` on the built image (compose refuses to start while a required
variable is missing, which the new variable was) and writes only if the key came
back non-empty; the command is in `.env.example`.

**When:** the production OptiPlex needs the same: `.env` at `600`, and the key
backed up outside the box (losing it means re-authorizing by hand).

## 177. The schema docs do not know about `oauth_tokens`

**Status:** open

`sql/034` (2026-10-06) made 32 tables. `docs/database-schema.md`,
`docs/schema-review.md` (covers `sql/001`-`033`) and `docs/db-schema-diagram.md` do
not mention it, nor its trigger and its grant shape (SELECT, INSERT, DELETE for
`hail_app`; nothing for `hail_ingest`). The same lag as items 160 and 162.

**When:** as one pass over the three docs, with the next migration or before the
next schema review.

**Widened 2026-10-07.** Since this item was filed the schema has gained `cc_sync_runs`
(`sql/035`), `cc_sync_conflicts` (`sql/036`) and `sent_emails` (`sql/037`), and
`send_log` has a new `email_id` column with its own frozen-column trigger. That is five
new or changed objects the three schema docs do not mention. Do it as one pass.

## 178. The test email had an unsubscribe link in the body but no `List-Unsubscribe` header

**Status:** resolved 2026-10-07 for API-sent campaigns; one difference unexplained

Found 2026-10-06 reading the raw headers of a Constant Contact test message
(item 170). The body carries an unsubscribe link (reported by the sender); the
headers carry no `List-Unsubscribe` or `List-Unsubscribe-Post`, and the DKIM
`h=` list does not sign them either. The body link meets CAN-SPAM's opt-out
requirement (item 120). The header is a separate thing: it is what lets Gmail and
Yahoo show their own unsubscribe button, and their bulk-sender rules ask for the
one-click form at high volume. A test message to one recipient may simply not get
it; real campaign sends were not checked, and per-recipient API sends were not
tried at all.

**When:** before the first real send. Send a campaign to a Gmail inbox with a
second recipient and read the headers again, and check how an API send behaves.

**Resolved 2026-10-07 for campaigns sent through the API.** The raw headers of the
spike's real send (activity `b12ac1a0-...`, delivered to Gmail) carry
`List-Unsubscribe: <https://audience.constantcontact.com/preferences/unsubscribe?...>`
and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, and both DKIM signatures
(`d=roofbrokersinc-weather.com` selector `ctct1`, and `d=auth7.ccsend.com`) list them
in `h=`. SPF passed for `in.constantcontact.com` and DMARC passed on the aligned DKIM.
The earlier UI-sent test message had neither header; why is not known. The body
footer (address, Unsubscribe, Update Profile) is added by Constant Contact.

## 179. Constant Contact's permission policy: sends must be gated to a recorded basis

**Status:** open

Found 2026-10-06 reading Constant Contact's email permission policy
(`knowledgebase.constantcontact.com`, article 5305; re-read the page itself, this is
from a fetched summary). Acceptable permission is express opt-in, or implied
permission "through a client or customer relationship" (business cards, a sale, an
inquiry, membership). Barred: addresses "obtained by surfing the internet or
'scraping' web pages", and any "distribution list or mailing list", including
"public directories". A violation "may result in the termination of your account".
Cold B2B outreach to named individuals is not addressed either way.

Listing agents that RentCast returns are third-party data with no relationship to
RBI, so most of `realtors` is outside the policy. The decision log (2026-09-28)
already scoped Constant Contact to the warm pools: about 339 Constant Contact-active
realtors, and about 675 realtors who called RBI for an inspection. The owner states
that the people to be emailed are currently opted in. Not yet checkable here:
`realtors` has no column for the basis, the client list is not imported, and the
Constant Contact export on file is a two-year-old snapshot, all "Implied", none
"Confirmed". A violation on RBI's existing account could cost RBI its newsletter
account, which is why the hail system uses a separate account.

**Decided 2026-10-07:** the audience is restricted to realtors from RBI's in-house
database and the Constant Contact list; the hail system stays on its own Constant
Contact account (the dev trial today).

**When:** before the first send. Needs a consent-basis column or table (`sql/035`),
the imports (item 129's fresh export, and the in-house database, item 83), and a
send list built only from realtors that have a basis and are not in `dnc_list`.

## 180. Send design: one email per realtor per send, listing every affected property

**Status:** open; the rendered-email table is built (`bb443a7`), the send engine is not

Constant Contact has no single-recipient send: the API is contacts, lists and
campaigns (`POST /emails`, `PUT /emails/activities/{id}` with `contact_list_ids`,
`POST .../schedules` with `"scheduled_date": "0"`). Limits: 4 requests per second and
10,000 per day. Design: one campaign per realtor per send action, HTML rendered here
and carrying every matched property for that realtor (distance, hail size, date), so
an agent with three affected listings gets one email, not three, which also covers
the duplicate-email gap noted in `docs/phases.md`. `send_log` keeps one row per
match; the rows of one email share one `provider_message_id`.

**Order of operations per recipient:** build the list excluding `dnc_list` and
realtors without a basis; insert the `send_log` rows `queued` and commit; re-check
`dnc_list` immediately before the call; create the campaign; store the activity id
in `provider_message_id` BEFORE scheduling; schedule. A crash after scheduling then
leaves an id to reconcile against Constant Contact, and a rerun must never create a
second campaign for rows that already carry one. Runs in a background thread with a
progress banner like pulls; the send itself is a human click, never a timer.

**When:** after the spike (item 181) and the eligibility work (item 179).

**Decided 2026-10-07, after the spike.** Addressing one realtor: one list per send,
deleted once the campaign reports `DONE` (option A; a shared queue list was
rejected because it forces strictly serial sends). Per realtor: `GET` the contact
by address; not found: `POST /contacts` with the send list (`implicit` or `explicit`
by the recorded basis, item 179); `unsubscribed`: write to `dnc_list` and skip;
found and active: bulk add to the send list and poll the activity until `completed`;
create the campaign (take the `primary_email` activity by role), attach the list,
store the activity id, schedule, poll to `DONE`, then delete the list (also
asynchronous). Roughly 8 to 10 calls per realtor: about 1,000 a day at the 10,000
call limit.

**The record is ours.** `send_log` keeps one row per match with the address used and
the Constant Contact activity id, whatever happens to the list or the campaign.
**Decided 2026-10-07:** an append-only table of the rendered subject and HTML, with
the campaign, activity, contact and list ids, one row per email, linked to its
`send_log` rows, because Constant Contact will not return the HTML and a sent
campaign can be deleted on its side. Not yet designed (columns, how `send_log`
points at it, and the guard); that is its own migration.

**Built 2026-10-07 (`bb443a7`): `sent_emails` (`sql/037`).** One row per email: the exact
subject and HTML as rendered; `batch_id` (one send click, the progress banner counts by
it); `sync_run_id` (the unsubscribe sync that cleared the send); `permission_asserted`
(`implicit`, `explicit`, or `none` for an existing active contact, i.e. what we told
Constant Contact); the Constant Contact ids (contact, list, campaign, activity) and the
progress markers (`scheduled_at`, `list_deleted_at`) write-once; the activity and campaign
ids unique, so a rerun cannot create a second campaign; one email per realtor per batch.
`send_log` gained `email_id` (NOT NULL, foreign key; frozen by a second small trigger so
the audited `send_log_guard()` of `sql/029` is untouched). Still to build: the send
engine that fills it in the order above, and the template rendering.

**Decided 2026-10-07: several storms in one email, which matches are eligible, a cap.**
- **Scope.** The send screen lets a sender tick more than one storm day, with a choice to
  combine them into one email per realtor or to send one email per storm. A human ticks what
  goes out; there is no automatic grouping by date window.
- **Events.** A listing carries a list of hail events (date, size, distance). One storm is the
  case of one event per listing. `send_log` already has one row per match (one storm report
  against one listing), so an email covering two storms for a listing is two rows sharing one
  `email_id`; no schema change.
- **Eligible matches.** A match is eligible if it has no `send_log` row other than a failed
  one (failed sends can be retried) AND its storm is no older than **30 days**
  (`match_max_age_days`, a setting).
- **Per-realtor cap.** No more than one email to a realtor in **14 days** (`email_cap_days`, a
  setting), with a visible override. A capped realtor's matches stay unsent and go in the next
  eligible email; nothing is lost.
- **Wording.** The email states facts (the reports, dates, sizes). It does not claim that
  repeated storms make damage more likely; the copy already says a weather report is not a
  finding of damage.
- Both numbers live in the `settings` table, so they appear on the Admin page with the change
  history already built for the radii and the quota.

**Renderer built 2026-10-08 (`ef4eeb2`):** `hailsys/email/render.py` produces the subject and HTML
that go into `sent_emails`. Still to build: the query that turns "eligible matches" into the
renderer's input, eligibility (item 179), the send engine that fills `sent_emails` and `send_log`,
and the send screen.

**Built 2026-10-08:** the send-list query (`hailsys/queries/sendlist.py`, `33a1c4d`, rules and
counts per rule, one event per listing per Denver day), the `stale_listing` rule (item 187,
`943650d`), and the two settings (`sql/038`, `3b91ae4`).

**Send engine decisions, 2026-10-08.** Each email has its own content, so each gets its own
list and campaign: about 10 calls per email (read contact, create if absent, create list, add
and poll, create campaign, update, schedule, poll, delete list). A 437-email day is about 4,400
calls and 25 minutes; a 1,400-realtor day exceeds Constant Contact's 10,000 calls a day.
- **Background thread**, as the RentCast pulls do; progress is read from `sent_emails` by
  `batch_id`.
- **An unknown outcome** (a timeout after a campaign POST): stop that email, mark it "needs
  review", never retry automatically.
- **Auth or quota errors stop the whole batch;** any other failure stops only that email. A
  failed unsubscribe sync stops everything before a row is written.
- **Daily call budget: send what fits and queue the rest.** The rows stay `queued` in
  `sent_emails` and `send_log`; a person resumes them with an explicit click on a later day
  (nothing resumes by itself). Needs a count of today's calls, which does not exist yet, and
  the reset time of Constant Contact's daily limit is unverified (item 183).
- Build order: template store (`email_templates` is empty and `sent_emails.template_id` is NOT
  NULL), CC campaign calls, engine with a fake CC, rehearsal on the dev trial, send screen,
  status sync.

**Phase A built 2026-10-08 (`hailsys/email/send.py`, `queue_batch`).** Writes `sent_emails` and
one `send_log` row per match as `queued`; never commits, never calls Constant Contact; refuses
unless an unsubscribe sync finished OK within 15 minutes; `allowed_emails` is a required
argument, so until item 189 exists a caller must name the recipients.
**Finding while building it:** `sent_emails.permission_asserted` is frozen at insert and NOT
NULL, but whether we assert `implicit` (new contact) or `none` (existing contact) is only known
after asking Constant Contact. So Phase B starts with a read-only pre-check (one `GET` per
recipient) whose answers are passed to `queue_batch`; it also stores `cc_contact_id` for
existing contacts early and skips anyone already unsubscribed in Constant Contact, even if our
DNC list has not caught up.

## 189. No eligibility gate: nothing yet stops an email to an address outside the warm pools

**Status:** open (parked 2026-10-08, by developer decision)

Constant Contact's permission policy bans scraped and public-directory addresses (item 119,
closed only for warm pools). The send list does not yet check that a realtor is a service
client or a Constant Contact subscriber, so a real send would email any listing agent with a
non-DNC address. The planned fix is a basis table keyed by `email_norm` (`service_client` /
`cc_subscriber`, with source batch, as-of date and detail), loaded by an admin upload like the
DNC import, and the engine refusing any recipient without a row. Blocked on the fresh
Constant Contact export (item 129) and a usable in-house client list. Today 7 of the 440
Sept 22 realtors are in the client file; the Constant Contact pool adds more.

**When:** before the send screen is usable for anything but our own test addresses. The engine
is built and rehearsed on test addresses only until then.

## 190. Address search as an office tool: jurisdiction, permit issuer and adopted codes

**Status:** open (parked 2026-10-08, by developer decision: the email work comes first)

**The use.** Staff now look an address up in printed books to learn which jurisdiction it is in,
who issues its permit, and which building codes that jurisdiction enforces. The books are out
of date and miss newer builds. `/search` already geocodes (item 124) and the boundary tables
are loaded, but nothing reads `municipal_boundaries` or `county_boundaries` yet (checked
2026-10-08), so `/search` states no jurisdiction.

**What is known and what is not** (see `docs/data-sources.md` §5 and decision log 2026-09-23):
- Jurisdiction by point-in-polygon is the strong part: DOLA boundaries current, known-answer
  points correct (Civic Center Park, Aurora Municipal Center, Highlands Ranch = Douglas).
- **Newer builds are the open risk.** The Census Geocoder matches street ranges, which lag for new
  subdivisions, and its points are interpolated to about a block. A house too new for the books
  may be too new for Census. Not tested. RentCast properties we already hold carry their own
  coordinates and could be looked up directly.
- **Permit issuer is only partly known:** 50 of 82 issuers have an official who-issues page; 32
  are unverified (item 98), PPRBD covers several El Paso towns, Pueblo may be a second regional
  issuer. Showing an unchecked department name would be a confident-looking error.
- **Adopted building codes are not researched at all.** Which edition of the IRC/IBC, and which
  local amendments, each of the roughly 82 issuers enforces has not been gathered, and codes
  change on adoption cycles. This is a data-collection and upkeep job (a table keyed by issuer
  with the edition, adoption date, source URL and a verified-on date), not something derivable
  from the boundary data. Nothing may be shown to staff as a code requirement without a
  source and a verified-on date.

**Plan when picked up:**
1. Read-only trial first (item 78's idea with our own known answers): about 20 addresses staff
   know, including new builds and ones near a city line; count geocoder and polygon misses.
2. If acceptable: build the jurisdiction lookup, the near-boundary warning (item 77) and the
   correction table (item 75, Hudson at minimum).
3. Issuer and code columns appear only for issuers that have been verified (items 97, 98); the
   rest read "not verified". Answers state their data date ("boundaries as of the last DOLA
   load").

**Related:** items 23 and 124 (geocoding), 70–82 (permits and jurisdiction), 97, 98.

**When:** after the Phase 5 email work; Phase 6 with address search. Start with the trial
(step 1); it decides whether the rest is worth building.

## 191. No count of Constant Contact calls per day; `max_emails` is a stand-in

**Status:** open (found 2026-10-09, building Phase B)

An email costs about 10 calls (item 180) and Constant Contact allows 10,000 a day, so a
1,400-realtor day cannot finish. `deliver_batch(max_emails=N)` stops after N emails and leaves
the rest `queued` for an explicit later run, but N is a number a person types, not a count of
calls made today. Nothing records calls, and the reset time of the daily limit is unverified
(item 183).

**When:** before a send larger than about 900 emails (9,000 calls), and before the send screen
offers a "send all" button. Until then the screen must pass a `max_emails` it has checked by hand.

## 192. The send screen needs a sender identity in `.env` (`CC_FROM_NAME`, `CC_FROM_EMAIL`)

**Status:** open (found 2026-10-09)

`deliver_email` takes `sender` = `{from_name, from_email, reply_to}` and passes it to
`campaign_fields`. Nothing reads it from configuration yet, and the From address must be a
verified sender in the Constant Contact account (item 183). Neither variable is in `.env` on
`hail-dev` or on the production box.

**When:** when the send screen is built (piece 4, the dev-trial rehearsal needs it first).

## 194. The sender identity is hard-coded in `docker-compose.yml`; a user should be able to choose it in the UI

**Status:** open, accepted for now (2026-10-09, by developer decision)

Item 192 put `CC_FROM_NAME`, `CC_FROM_EMAIL` and `CC_REPLY_TO` (and the `EMAIL_*` asset URLs and
contact address) in the `web` service's `environment:` block, which is where the repo keeps
non-secrets. That is fine for the rehearsal and the pilot, but one fixed From address is not the
long-term shape: a user who wants a campaign to go out from their own address should be able to
pick it from the UI. Probably a drop-down on the send screen filled from the `users` table (a
sender name, From and reply-to address per user, or a list of allowed senders an admin
maintains). `deliver_email` already takes `sender` as an argument, so the engine does not
change; only where the dict comes from does.

**Constraints to settle when it is built:**
- Constant Contact only sends from a **verified** sender address on the account (item 183), so a
  user's address cannot be offered until it is verified there. An admin-maintained list of
  verified senders is safer than every user's email.
- The chosen sender must be recorded with the send (today `sent_emails` stores the rendered
  subject and HTML but not the From address), because `sent_emails` is the record of what was
  sent and a from-address that lives only in a form field is lost.
- Changes to the list should go through `settings_history` or similar, like the other settings.

**When:** before more than one person sends, or when someone other than the developer asks to send
from their own address. Until then `sender_from_env()` in `hailsys/email/deliver.py` is the source.

## 193. Phase B was keyed in with slips that would have failed on the first send

**Status:** resolved 2026-10-09 (caught before commit by diffing against the verified copy and
running the tests)

`hailsys/email/deliver.py` as first keyed in had: `cc_activity_ide` in `_ID_COLUMNS` (every
activity-id save would fail the assert); `_mark` ending in a bare `conn.execute` with no
commit; `found["permissoin"]` (KeyError); two dropped lines in `_put_contact_on_list` (no
`add_to_list` after a 409, no contact id stored after a create); `conn.execute(+_EMAIL_SQL, …)`
(TypeError on every call); outcome `"need_review"` for `"needs_review"` (KeyError in the batch
counter); and `run_send` missing the send-list and `lookup_contacts` block (NameError). The
`in_cap` edit in `sendlist.py` was also not yet in. All fixed; 79 tests pass.

**Lesson:** the test suite finds these, and the diff against the scratchpad copy found them
first. Key in, `py_compile`, run the suites, before anything is committed.

## 181. Constant Contact behaviours to verify before the send design is locked

**Status:** spike done 2026-10-07; paid-account checks remain (item 183)

Not established by the documentation read on 2026-10-06 (one page returned a 404):
- does the unsubscribe footer get added to custom-code HTML sent through the API,
  or must the HTML carry the tag (and does the API send carry a `List-Unsubscribe`
  header, item 178)
- what happens when a contact who previously unsubscribed is added or re-added
- limits on the number of campaigns, and whether API-created campaigns can be
  removed (each realtor-send is its own campaign)
- that a custom-code (format 5) activity is accepted on a trial account
- that `from_email` and `reply_to_email` must be verified, and the error when not
- how many API calls one recipient really costs
- the trial's send cap (about 100) and length (14 or 30 days)
- what differs on a paid account, which production will be

**When:** the spike, build step 0: a throwaway script sends a few messages to
addresses we own, and the answers are recorded here. Repeat on the production
account once it exists.

**Answers from the spike, 2026-10-07** (`scripts/cc_spike.py`, dev trial; the account
address `justyn@roofbrokersinc-weather.com` is CONFIRMED with roles DEFAULT_FROM and
REPLY_TO):
- `POST /emails` with `format_type` 5 HTML is accepted with no unsubscribe tag and no
  physical address, and answers 200 with TWO activities, `primary_email` and
  `permalink`. Select by `role`, never by position.
- `PUT /emails/activities/{id}` with `format_type`, `from_name`, `from_email`,
  `reply_to_email`, `subject`, `html_content` and `contact_list_ids` returns 200.
  `GET` on an activity does not return `html_content`: keep what we rendered.
- `POST .../schedules` with `"scheduled_date": "0"` answers 201 `[]`; the status was
  `DONE` within moments for a list of one.
- Constant Contact adds the footer itself to custom HTML: the account's physical
  address, Unsubscribe, Update Profile, and a "Trusted Email from Constant Contact"
  badge (trial; check the paid account).
- `POST .../tests` answers 204; test sends and drafts did not move the trial counter.
  One real recipient used one send (99 to 98).
- One realtor-send took about 6 calls. Documented limits: 1,000 lists per account,
  50 lists per contact, 4 requests per second, 10,000 per day.
- `POST /contacts/sign_up_form` creates a contact with `permission_to_send:
  "explicit"` on its own; see item 182.

**Still open:** `POST /contacts` (permission values, behaviour on an unsubscribed
address); adding an existing active contact to a list; deleting lists and campaigns;
our own plain-text part (read the full `text/plain` of a delivered message; the
first paste was truncated); personalization tag syntax; whether a list containing an
unsubscribed contact is skipped at send time; `GET /contacts?status=unsubscribed`
pagination and an `updated_after` filter (for item 182's sync); paid-account
differences.

**Spike results, final (2026-10-07).** Adds to the answers above:
- **Adding a person.** `POST /contacts` creates only: 201 for a new address, 409
  ("Email already exists for contact <id>") for an existing one, including an
  unsubscribed one, which stays unsubscribed. It accepts `permission_to_send:
  "implicit"` and records it as such (`opt_in_source: "Account"`), and the 201 includes
  `list_memberships`, so create and join are one call. `sign_up_form` stamps `explicit`
  and re-subscribes (item 182).
- **Existing active contact.** `POST /activities/add_list_memberships` with
  `{"source": {"contact_ids": [...]}, "list_ids": [...]}` answers 201; it is a bulk
  activity (`GET /activities/{id}`, `state` `initialized` then `completed`). Done in the
  same second for one contact; the docs say 30 seconds to 15+ minutes at volume. It
  does not touch `permission_to_send`.
- **Implicit contacts are delivered**, with the same aligned DKIM, DMARC pass and
  one-click `List-Unsubscribe` as explicit ones. The plain-text part is generated and
  complete (the table is flattened to one line). `[[trackingImage]]` becomes a
  per-contact pixel; the unsubscribe link and headers carry the contact id
  (`X-CTCT-ID`) and the activity id (`X-Campaign-Activity-ID`).
- **Unsubscribe feed.** `GET /contacts?status=unsubscribed&limit=N` lists them (address,
  `opt_out_date`, `opt_out_source`); `updated_after` works (a time after the opt-out
  returns none). It filters on the contact's `updated_at`, so use an overlap.
- **Per-send reports.** `GET /reports/email_reports/{activity}/tracking/bounces` (codes
  B, D, F, S, V, X, Z) and `.../tracking/optouts` (an event log, minute resolution, one
  entry per click) work for `primary_email` activities.
- **Deletes.** `DELETE /emails/{campaign_id}` answers 204 for a draft AND for a sent
  campaign. `DELETE /contact_lists/{id}` answers 202 with an activity (asynchronous);
  the contacts remain. Constant Contact is therefore not our record of what was sent.

## 182. Constant Contact's API re-subscribes an unsubscribed contact: read before write

**Status:** sync built 2026-10-07 (`2884880`); not yet wired to the Admin page or the send click

Observed in the spike. After a contact clicked Unsubscribe, `GET /contacts` showed
`permission_to_send: "unsubscribed"` with `opt_out_source`, `opt_out_date` and no list
memberships. A later `POST /contacts/sign_up_form` for the same address answered `200
"updated"` and the contact was `explicit` again, on the list, with a new `opt_in_date`;
nothing asked the person. Constant Contact's documentation says the same ("even if a
contact is currently unsubscribed, POST /contacts/sign_up_form may set the contact's
email permission policy as having given you explicit permission") and warns that opting
a contact back in without their own action violates anti-spam and telemarketing law.
`sign_up_form` also stamps `explicit` on every new contact it creates, which is an
attestation by us (item 179).

**Rules:**
1. Never call `sign_up_form` for an address without first reading its state. If it is
   `unsubscribed`, do not add it; record it in `dnc_list`.
2. Prefer `POST /contacts` for new contacts: the docs say it returns 409 when the address
   already exists. Its `permission_to_send` values and its behaviour for an unsubscribed
   address are not documented; test them (item 181).
3. Pull Constant Contact's unsubscribes into `dnc_list` BEFORE building a send list or
   adding anyone. Read-only toward Constant Contact; the only write is `dnc_list`.
   Run it on demand at send time (mandatory, inside the click) and from an admin button.
   A nightly timer in front of that is optional and only narrows the window further.
4. Refuse a send when the last successful sync is older than a set limit.

**When:** before the first send, with item 129's fresh export; the sync is a build step
of its own.

**Decided and built 2026-10-07 (`2884880`).** `hailsys/constantcontact/unsubs.py` and
`sql/035` (`cc_sync_runs`). Answers to the open design questions: an address that is
unsubscribed in Constant Contact but whose `dnc_list` row an admin removed is a
**conflict**: counted, reported, never undone (the read-before-write rule still blocks
that person); the sync runs **inside every send click and a failure stops the send**, so
rule 4's age limit is replaced by that; `added_at` takes Constant Contact's opt-out date,
as the DNC importer does with its source's date. Incremental pulls use the last good run's
start minus one day. Still to do: the Admin button and last-good-sync line, the call from
the send path, and the optional nightly timer with its own `hail_sync` role (item 168).

## 183. Constant Contact behaviours to verify on the paid production account

**Status:** open

The spike ran on the free trial (item 181). What may differ on the paid account that
production will use, to check when it exists and repeat the relevant probes there:
- the "Trusted Email from Constant Contact" badge in the footer (trial: present)
- API and send limits (trial: about 100 recipients in total)
- the physical address in the footer comes from the account profile; confirm it is
  RBI's and where it is edited
- paging of `GET /contacts?status=unsubscribed` beyond one page (not exercised)
- bulk-activity and list-delete timing at real volume (docs: 30 seconds to 15+ minutes)
- that the CNAME self-authentication and the `ctct1` DKIM alignment carry over to the
  production sending domain (item 96)

**When:** when the production account is created, before the first production send.

## 184. A one-character typo in the API throttle hung a request; no test ran its arithmetic

**Status:** resolved 2026-10-07 (`497d9a7`)

Found 2026-10-07 on the first click of "Sync unsubscribes now": the request took 30
seconds, gunicorn killed the worker ("WORKER TIMEOUT"), Cloudflare showed an Internal
Server Error, and the run row was left `running` with nothing fetched. Cause, in
`hailsys/constantcontact/api.py`: `wait = MIN_INTERVAL = (time.monotonic() -
_last_request)`, where the second `=` should have been `-`. `_last_request` starts at
0.0 and `time.monotonic()` is the host's uptime (688,711 seconds, about 8 days), so the
first request tried to sleep for the uptime. The tests had patched `MIN_INTERVAL` to 0
and `sleep` out, so the throttle's arithmetic never ran; the same gap hid a dropped
`raise` for an unexpected success status in `request()`. Both are fixed, with
`ThrottleTest` (three cases with a faked clock) and a 206 test. Also found in the same
pass: `unsubs._record` returned `None` for an ordinary already-present address after an
indentation slip, which the new database test caught.

**The lesson recorded:** a test that mocks away the thing it names does not test it.
The run table did its job: the orphaned `running` rows were closed as abandoned by the
next run, and no `dnc_list` row was half-written.

**When:** nothing open. Applies to any new helper that sleeps, retries or reads a clock.

## 185. The sync runs inside the web request; its failure log carries the exception text

**Status:** accepted 2026-10-07

`POST /cc/sync` runs the whole sync in the request. gunicorn kills a worker at 30
seconds; a first full pull at the dev size takes about a second, and production's
roughly 600 unsubscribed contacts is two pages of 500, so it should stay well inside
that, but this has not been measured at production size. A killed request leaves the
run `running`, which the next run closes as abandoned and which holds no lock (the
advisory lock dies with the connection). Separately, an unexpected exception in the route
is logged with `logger.exception`, which writes the exception's message. Nothing we
raise carries a token (messages are fixed text), but a library error could in principle
carry data. Accepted: full tracebacks for truly unexpected failures are worth more than
the risk, and the route shows the admin only a generic message.

**When:** if a sync ever takes more than about 20 seconds, or when the nightly worker
exists (item 168), move the run to a background thread with a progress line, as pulls do.

## 186. A test email whose link pointed at an `azurewebsites.net` preview host was not delivered

**Status:** resolved by decision 2026-10-07 (circumstantial: one trial)

Found 2026-10-07 while adding a "Schedule a new inspection" link to the email template. A
test send identical to one that had arrived, except for a new link to
`roofbrokersinc-preview.azurewebsites.net/Orders/Create`, was accepted by Constant Contact
(204) but never reached the inbox. The same email with that link pointed at
`www.roofbrokersinc.com` ("test B") arrived. `azurewebsites.net` is a free hosting domain that
spammers use heavily, so filters, Constant Contact's or Gmail's, treat links to it with
suspicion. It is one trial with one difference between the two messages; which system dropped
the first was not determined. The production site serves the same page
(`www.roofbrokersinc.com/Orders/Create` redirects to the login page and returns to Orders/Create
after sign-in).

**Rule:** no `azurewebsites.net` (or other free-hosting) links in outgoing mail. The template takes
the link as a setting (`schedule_url`); the value is `https://www.roofbrokersinc.com/Orders/Create`
in dev and production alike. A 204 from a test send means accepted, not delivered.

**When:** before the first real send, send one message containing every link in the final
template to a Gmail inbox and confirm it arrives.

## 187. The send list ignored `listing_freshness_days`; a send needs a fresh RentCast pull first

**Status:** open (rule being built 2026-10-08)

Found 2026-10-08 while adding the eligibility settings (`sql/038`). `sendlist.py` (commit
`33a1c4d`) kept every listing with `list_status = 'Active'`. That column stays `Active`
indefinitely for a sold listing (`sql/028`'s own comment), so it does not say the listing is
still on the market. `settings.listing_freshness_days` (7) exists for this and nothing read it
for sending (item 140 is the match-page half). The rule is `stale_listing`: a listing whose
`list_last_seen` is NULL or older than that many Denver days before the send is dropped,
counted after `inactive_listing`.

**Consequence found with it:** on 2026-10-08 the 656 listings matched to the 2026-09-22 storm
were last seen between 09-21 and 09-29 (591 on 09-29). With a 7-day threshold **all of them
are stale**. That is the intended design (pull, then send within a week), but it means a real
send needs a new RentCast pull for the storm first, which costs requests (item 50 quota), and
the send screen must say "N listings dropped as stale; pull again to refresh them" rather than
show an empty list with no reason.

**When:** with the send screen; the rule itself is built first.

## 188. `sql/038` (storm age limit, email cap) is applied to `hail-dev` only

**Status:** open

`match_max_age_days` (30) and `email_cap_days` (14) were added to `settings` and
`settings_history` on 2026-10-08, with the settings trigger re-created (`sql/038`, commit
`3b91ae4`). Applied to `hail-dev`. The production OptiPlex is not racked and has none of the
Constant Contact migrations either (034–037). The schema docs do not describe 038's columns
(item 177).

**When:** with the production build; apply 034–038 in order and run `sql/guard_test.sql`.

## 70. Permits as a source — corroboration first, roof age later

**Status:** open (parked) — until the system is running

Open-data roofing permits exist for Aurora, unincorporated Adams and
unincorporated Douglas, and each surges after a known hail day
(`docs/data-sources.md` §5). First use is internal corroboration: ranking,
confidence and what the UI shows a sender. "No roof permit on record since
<date>" comes later, if ever, and only where the jurisdiction's records go
back that far. Never "your roof is X years old" (decision log 2026-09-23,
the claim rule). Items 71–84 are the prerequisites and follow-ons.

**Commercial aggregators (noted 2026-10-08; PermitStack read from its public pages, not tested).**
**Shovels** is from research outside the 2026-09-23 session and not re-verified: free tier 1 year
of history, 10 results per query, no downloads; Basic $599/month for full history; updates on the
1st and 15th; its jurisdiction CSV was a dead end. Coverage of our 82 issuers, roofing
identification and terms were never checked. **PermitStack** (permit-stack.com; permitstack.com is
an unrelated California firm), read 2026-10-08 from its home and coverage pages: REST API with SDKs
and bulk city CSVs; claims 110M+ permits, nightly refresh; each permit auto-classified into one of
20 categories (roofing is one); fields seen are `permit_number`, `category`, address,
`estimated_value`, lat/lon, full-text search of descriptions, contractor (phone/email on
Developer, $79/month, and up); **no material field shown**. Plans: Free 100 requests/day and the
last 30 days only; Indie $29; Developer $79 (10,000/day, 50,000-row CSV); Business $149; bulk city
CSVs $39-$349 one-time. Redistribution or bulk delivery needs a custom Enterprise plan; terms text
not read. Colorado coverage (28 rows, data through 2026-10-07): PPRBD, Denver, Boulder, Douglas,
Arvada, Westminster, Aurora, Centennial (access blocked), Parker, Adams, Weld, Fort Collins;
Lakewood and Thornton absent; Longmont and Larimer thin. It overlaps the open-data sources already
graded; its additions are the grade-C cities (Denver, Arvada, Westminster, Weld) and perhaps
coordinates for PPRBD. Neither aggregator is chosen or ruled out.

**What our own open data says about the roof** (scan of the 2026-09-23 snapshots, read-only):
Douglas `DESCRIPTION` is a material category on 90% of 75,703 roofing permits (composition 65,767;
tile/slate 1,157; metal 1,100; built-up 286; membrane 209; wood 63), `JOB_VALUATION` on 72,965,
`SCOPE_OF_WORK` empty. Aurora's free-text description names a material or method on 35,057 of
36,634 (often with squares, e.g. "REROOF WITH EPDM 220 SQS"), `valuation` on 36,628. Adams'
description names a material on about 9,075 of 16,672 (some say Class 4 impact shingles), no
valuation. Free text is typed by contractors and unchecked, so it needs parsing and is
incomplete; the claim rule still applies (permits never become "your roof is X years old").

**PermitStack tested on its free tier, 2026-10-08** (about 18 of 100 daily requests; results in
`data/research/permitstack_trial_2026-10-08/`, gitignored: `jurisdiction_summary.csv` and
`sample_roofing_records.csv`, 500 records). Sample = 50 roofing permits per jurisdiction from the
last 30 days (page size is capped at 50).
- **Terms (section 4):** commercial use allowed ("any lawful purpose"); results may be stored and
  cached indefinitely; no attribution; barred are reselling the data as a dataset or competing
  API and bulk-downloading the whole database; exports never carry owner names or mailing
  addresses. Nothing on outreach either way (ask support@permit-stack.com if it matters).
- **Fields:** permit number, status, address, `description_raw`, `estimated_value`, filed, issued
  and completed dates, `contractor_name`, lat/lon with `location_source`. **No material field:**
  `enrichment.materials` was empty on every record (450 records, 54 with an enrichment object at
  all). Material exists only as description text.
- **Per jurisdiction (description / value / contractor / lat-lon of 50; roofing permits in all
  history):** Douglas 50/50/0/50 (material category on all 50; 75,787); Centennial 50/50/0/50
  (material words on 37; 51,505); Parker 50/0/0/47 (26; 4,521); Adams 50/0/0/21 (19; 16,459);
  Westminster 50/0/50/45 (11; 38,678); Arvada 50/50/50/50 (10; 45,317); Boulder 50/0/48/50 (2,
  "Roofing Replacement Permit"; 28,382); **PPRBD 50/50/49/50 (6; 373,187), coordinates derived,
  description only "REROOF"**; Denver 1/1/0/50 (bare address and filing date; 51,383); Fort
  Collins 0/0/49/48 (26,732). Weld has 0 roofing permits classified, Colorado Springs 0, Larimer
  2; Lakewood and Thornton are not covered.
- **Free tier shows the last 30 days only** (403 `feature_locked` for older dates; Indie $29/month
  unlocks history). Rate limit 30 requests/minute, 100/day. `state=` is ignored by the
  jurisdictions list (995 rows nationwide, 27 in Colorado).
- **Judgement:** no better than our open data for material; its value is coverage. It adds PPRBD
  with coordinates (the free PPRBD export had none), Centennial, Arvada, Westminster and Parker.
  Douglas, Centennial and Parker carry the most material text.

**Denver tested for later fill-in (2026-10-09, 3 requests):** 50 roofing records each from 14 Sep,
24-26 Sep and 6-7 Oct are equally bare. Status advances (15 of the 50 sampled from 14 Sep are FINAL), but
description, value and contractor stay empty on `ROOFSIDE` permits. The two records with a
description were not reroofs (a pergola, roof framing repair) and matched the roofing category
only by type. So the gap is in what Denver publishes, not in timing; PermitStack cannot add what
the source lacks. Only the last 30 days could be tested.

**Still unverified:** history depth and whether older records carry richer descriptions (needs a
paid month, Indie $29), contractor contact details (Developer, $79), and Shovels (not tested).

**When:** parked until the system is running. If permits are pursued, the cheapest next test is one
Indie month to check history depth for PPRBD, Arvada, Westminster and Centennial, and to see
whether older Denver records have descriptions. Check Shovels the same way.

## 71. Records (CORA) request to Aurora for full roofing-permit history

**Status:** open (parked) — gated on item 70 ("until the system is running")

Aurora's open-data permits start 2021-09-24, which looks like a rolling five
years. A raw snapshot was taken 2026-09-23 (`data/raw/permits/aurora/`) so
nothing more drops off unseen, but anything older than the window needs a
Colorado Open Records Act request. Whether the window really rolls is still
unconfirmed: re-check the earliest `InDate` on a later day.

**When:** only if roof age (item 70's later half) is pursued.

## 72. Commercial-use terms for permit data

**Status:** open (parked) — gated on item 70 ("until the system is running")

Aurora publishes a disclaimer with an indemnity clause and no licence grant.
Adams and Douglas publish no terms at all. Whether RBI may use permit data
commercially is a question for RBI's attorney, or for each jurisdiction.

**When:** before permit data appears in anything agents see.

## 73. RBI's business map, to set the permit-adapter order

**Status:** open (parked) — gated on item 70 ("until the system is running")

The top-three ranking used in the 2026-09-23 research counted stored
`properties`, which reflects which 5 zips had been pulled, not where RBI
works. By area, unincorporated El Paso, Weld and Adams lead. The build order
should follow where RBI actually wins work.

**When:** before any permit adapter is built.

## 75. Correction and override table for jurisdiction

**Status:** open (parked) — gated on item 70 ("until the system is running")

Three known cases where the polygon answer is not the whole answer:
- The Hudson source error `03782` → `37820` (decision log 2026-09-23).
- Municipalities that contract their building department out to the county
  or a regional office.
- El Paso, which may have a regional building issuer (unverified).

A table applied on read keeps each correction visible and attributable, and
survives a reload. A fix inside the loader would be silently re-applied, or
silently lost.

**When:** before address search states jurisdictions.

## 76. DOLA boundary lag and refresh cadence

**Status:** open (parked) — gated on item 70 ("until the system is running")

DOLA republishes nightly, but that only dates the publish. How far behind
the real annexations it runs is measurable from the newest `cl_re_date` in
the 1,911-row `Municipal_Boundary` layer. Settle how often
`fetch_municipal.py` + `load_municipal.sh` should run.

**When:** before address search states jurisdictions.

## 77. Near-boundary confidence flag

**Status:** open (parked) — gated on item 70

Flag an address whose point sits close to a municipal boundary:
`ST_Distance` on geography to the nearest boundary, starting at a ~30 m
threshold and tuned against real misses (item 78). Geocoded points and
boundaries each carry error, and an answer 10 m from a line should say so.

**When:** with address search. **Depended on item 23** (geocoding), which
resolved 2026-10-01: `/search` geocodes through Census, so the point exists.

## 78. Jurisdiction accuracy test against the permit datasets

**Status:** open (parked) — gated on item 70

The downloaded permit datasets say which department issued each permit, so
they serve as ground truth. Run our address → point → jurisdiction path
over their addresses and count disagreements. This is also what tunes item
77's threshold.

**When:** with address search. **Depended on item 23** (geocoding), which
resolved 2026-10-01. The points are interpolated along the street, accurate to
roughly a block (decision log, "Address search").

## 79. County assessor parcels as a geocoding-free upgrade

**Status:** open (parked) — gated on items 77 and 78

A parcel polygon places an address in a jurisdiction without an
interpolated point. Whether the counties publish parcels openly has not been
checked. `Colorado_Public_Parcels` on gis.colorado.gov exists but was not
examined.

**When:** only if near-boundary misses (items 77, 78) prove common.

## 80. Adams keyword precision; Douglas missing coordinates

**Status:** open (parked) — gated on item 70 ("until the system is running")

**Adams:** after 2016, roofing is mostly identified by keywords in
`Description` with a blank `TypeOfWork`. That filter (16,368 hits) has not
been checked for false matches. **Douglas:** 51% of roofing permits have no
`LOCATION` and need address matching before any spatial use.

**When:** with those adapters.

## 81. Denver: RESCON vs. ROOFSIDE, and the 2017 known answer

**Status:** open (parked) — gated on item 70 ("until the system is running")

Not examined 2026-09-23 because Denver was not in the top three. Open
question: are reroofs in the RESCON layer, or under a separate ROOFSIDE
type? The known-answer test is 18,475 roof permits in 2017, 54.6% above
2016 (`docs/data-sources.md` §5).

**When:** if Denver enters the build order (item 73).

## 82. The annexation layer as boundary-change history

**Status:** open (parked) — gated on item 70 ("until the system is running")

DOLA's 1,911-row `Municipal_Boundary` layer keeps each annexation with its
ordinance number and `cl_re_date`. That answers "when did this land join the
city," which matters if a permit predates an annexation and was issued by
the county.

**When:** only if permit history needs that question answered.

## 97. Does the Pikes Peak Regional Building Department publish permit data?

**Status:** open (parked) — gated on item 70 ("until the system is running")

PPRBD issues permits for 74% of currently stored properties (item 74), so it is
the permit source that would matter most if permits are ever built (item 70).
Whether it publishes permits as open data, in what format, with what history,
coordinates and terms, and how roofing is identified, has **not been checked**.
Its own permit search (pprbd.org) is the place to start.

**When:** parked with the permits work (item 70); first, if permits are
pursued, since it covers the most properties.

## 98. 32 permit issuers have no who-issues source

**Status:** open (parked) — gated on item 70 ("until the system is running")

The 2026-09-24 jurisdiction count (item 74) found official pages for 50 of 82
issuers. The other 32 were not looked up before web search rate-limited:
- **Cities:** Denver, Lakewood, Arvada, Thornton, Westminster, Commerce City,
  Northglenn, Broomfield, Boulder, Longmont, Lafayette, Louisville, Fort
  Collins, Loveland, Greeley, Castle Rock, Parker, Centennial, Lone Tree,
  Littleton, Englewood, Golden and Wheat Ridge.
- **Smaller towns:** Frederick, Wellington, Edgewater and Lakeside. No page from
  Lakeside itself was found.
- **Counties:** Larimer, Boulder, Gilpin, Pueblo and Fremont. Pueblo may be
  served by the Pueblo Regional Building Department, which would be a second
  regional issuer. Unconfirmed.

Most are large cities that almost certainly run their own departments, so the
issuer count is unlikely to change. But each needs its own official page before
an adapter is built for it. Colorado DFPC's statewide building-department list
could settle many at once, but it returned 403 to both the fetch tool and curl.

**When:** parked with the permits work (item 70); per issuer, before that
issuer's adapter.

---

## Also worth carrying

**Confidence tiers are 85% "high" by volume.** COCORAHS and TRAINED SPOTTER
alone are 53% of the archive. The tier is a property of the *reporter*, and its
meaning depends on event type — an automated station measures wind and
precipitation well and does not size hail at all. So the tier is a **filter,
not a headline**: a UI should show source names and let a human read them.

**Most hail days are thin.** 382 of 1,468 Denver-local hail days (26%) carry
exactly one report. A confidence label that leans on report count will read
"thin" more often than not, and needs a single-report tier that reads honestly.

**The natural key deduplicates ~0.7% of reports** that differ only in fields
outside it — on 2021-01-14, ten pairs differing only by a stray period in
`REMARK`, all one office re-transmitting a product. Correct behaviour, but it
means a "3 reports" label counts stored rows rather than reports IEM received.
Conservative direction, and clustered on event days rather than uniform.

---

## Not on the roadmap

Ideas recorded so they are findable, not because they are owed. Nothing here
has a **When:** — each has a **trigger** instead, and absent that trigger the
right amount of work to do on it is none.

### Radar-derived hail size (NOAA MRMS / MESH)

NOAA publishes MRMS MESH — a gridded estimate of maximum hail size — free,
with an archive going back years. It would answer the sparse-coverage problem
directly: 382 of 1,468 Denver-local hail days carry exactly one report, and
the HAIL distribution skews to the eastern plains rather than the Front Range
metro. A grid has no such gaps, because it does not depend on someone being
outside and choosing to call it in.

**Why it is not a near-term item, beyond the data volume.** National grids
every two minutes is heavy, and heavier than anything this system currently
touches — but the cost that matters is not storage. **MESH is a radar-derived
estimate, not a report**, and this project's central claim is that a report
was filed. "Hail of this size was reported near this listing" survives a
homeowner, an agent, or an attorney asking where the number came from; "our
radar model estimated 1.75 inches over your roof" is a different sentence
making a different promise, and it is the sentence a contractor-built system
would reach for. Adopting MESH is therefore a change to the product's claim
first and an ingest problem second. It would need its own decision-log entry
on that point alone, and the tiered confidence label would need somewhere
honest to put an estimate alongside human reports without the two blurring.

Two things to verify rather than trust if the trigger ever fires: how far back
the archive actually runs (operational MRMS is much younger than the LSR
archive, and older coverage comes from a separate reanalysis), and the file
format, which is GRIB2 and would mean a new dependency in a project whose
entire runtime is currently `psycopg`.

**Trigger:** sparse coverage turning out to be a real operational limit in the
pilot — storms RBI knows happened that the browse cannot show — rather than a
known property of the data we have already accounted for. Phase 6 at the
earliest.

---

## Closed

*58 items: `resolved`, `resolved (residuals)`, `dropped`. Kept, not deleted, because the reasoning is the point. Collapsed; expand to read.*

<details>
<summary>Closed items (resolved and dropped)</summary>

## 4. `county` has case variants and no normalized column

**Status:** resolved 2026-09-14 65f5e36

`iem_data.county` is free text: `EL PASO` has 10,299 rows and `El Paso` has
2,549, and **64 county groups differ only by case**. `iem_data` carries a
generated `report_source_norm` for exactly this problem on the source field,
but nothing equivalent for county.

Browse-by-county (open question 4) has to `upper()` both sides or it splits
every county in two and silently halves the counts.

Three county sources exist and would need reconciling: `nws_geo_code` (UGC,
null before mid-2022, but unambiguous where present), `iem_data.county` (this
one), and `properties.county_fips` (Census). UGC is the stable one — `COC041`
cannot be miscased.

Adding a `county_norm` generated column is a schema change to a post-backfill
file, so it is an additive migration, not an edit to `004_weather.sql`.

**When:** Phase 2, when browse-by-county is built.

**Resolved 2026-09-14.** See `docs/decision-log.md`, "County comes from TIGER
county polygons" — `tl_2025_us_county` is loaded as `county_boundaries` and
county is derived spatially, by the same path as the ZCTA load. That entry
supersedes the `county_norm` proposal here: it fixes the pre-2022 UGC gap and
the report-location-versus-affected-zip mismatch, neither of which a
normalized column would have touched. Kept here rather than deleted, per this
file's own rule that the reasoning is worth more than the conclusion.

## 5. Workable hail days swing 10–42 per year

**Status:** dropped 2026-09-30

Within `coverage_zips`, at ≥1.00″, within 5 miles: **542 distinct hail days
across 22 years**, averaging ~25/year but ranging from 10 (2022) to 42 (2023).

**This is a capacity question, not a cost question.** The monthly RentCast
ceiling was explicitly reframed: a heavy hail year is a good year for RBI, and
the per-send cost is negligible against the volume of business a hail season
produces. So the ceiling is a **runaway-bug tripwire**, not a budget
constraint. Do not re-litigate it as a budget question.

What it does affect is throughput — a 2023 means four times the outreach volume
of a 2022, through the same small number of people, with a frequency cap in the
way.

**When:** Phase 2 capacity planning.

**Dropped 2026-09-30.** Capacity is a non-issue: if volume is high, the business is doing well and can afford it (developer).

## 6. Aggregated one-row-per-zip export

**Status:** resolved 2026-09-14 578709d

`scripts/export_storm_zips.py` currently emits one row per **report-zip pair**.
A single report falls within the radius of several coverage zips, so a busy day
produces far more rows than zips — 64 pairs across 45 zips from 10 reports on
2026-06-24.

That was deliberate: it shows the shape of the data. The aggregate is what the
RentCast step will actually want.

**Build it as a `--format pairs|zips` flag on the same script, not a separate
module.** The aggregate is the same query with `GROUP BY c.zcta5` and different
projections — `count(*)`, `min`/`max(magnitude)`, `min(distance_miles)`,
`min`/`max(local_time)`, `string_agg(DISTINCT report_source, ', ')`. Same
joins, same filters, same coverage rule, same local-date logic. A separate
module would duplicate all of it and the two would drift, which is exactly the
argument that produced `iem_common.py`.

**One sub-question to settle when building it:** what "nearest distance" means
for a zip touched by two separate cells 30 miles apart. `min()` answers "how
close did hail get" but discards that there were two distinct events. A `max()`
alongside it, or a distinct-report count, covers that.

**When:** when the RentCast step needs a zip list to spend against.

- **PL-06** — Aggregated one-row-per-zip export. Resolved 2026-09-14, see
  decision-log "The storm query lives in `hailsys/queries/storms.py`".

## 7. mPING arrives as `PUBLIC`

**Status:** dropped 2026-09-30

Several reports in the archive carry remarks like *"Report from mPING: Half
Dollar (1.25 in.)"* — mPING is a crowd-sourced phone app where a user taps a
hail-size icon. It flows through `SOURCE = PUBLIC`, the same label as a walk-up
phone call to a forecast office.

Two materially different collection methods under one label, both rated `low`
in `report_sources`. Arguably correct — neither is trained — but they fail
differently: mPING gives a size from a fixed picklist with GPS coordinates, a
phone call gives a verbal estimate and a described location.

Only findable in `remark`, which means it cannot be filtered on cleanly.

**When:** Phase 2, if the confidence display turns out to need the distinction.

**Dropped 2026-09-30.** Moot: the radar study kept `confidence_tier` out of the UI (decision log 2026-09-16), so the display never needed the mPING distinction.

## 10. Do IEM reports arrive after their event date?

**Status:** resolved 2026-09-30

The ingest window filters on `VALID`, the time the event happened, not on when
IEM received the report. A report entered days after its storm falls outside
every nightly window and is **never fetched** — not late, permanently missed.

The weekly replay (`iem_backfill.py --mode replay` over the last 30 days,
Sundays at 11:00 UTC) closes that gap and is simultaneously the measurement: a
replay that inserts rows is the evidence that late entry happens. If it inserts
nothing for months, that is worth knowing too.

A manual run of the replay unit was performed on 2026-09-11; **its insert count
was not recorded here.** Check `ingest_runs` for `run_mode = 'replay'`.

**When:** check after a few weeks of replay runs.

**Answered 2026-09-30.** Four weekly replays (`ingest_runs` 18, 21, 32, 42) inserted 6, 0, 106 and 3 rows, 115 in total, so reports do arrive after their event date and the weekly replay earns its keep. The 2026-09-11 count this item said was unrecorded is 6. Nightlies ran continuously 09-11 to 09-21, so run 32's 106 is not an outage backfill. The developer accepted these numbers as the answer.

## 13. Buffer radius default — value, storage, and the per-type asymmetry

**Status:** resolved 2026-09-21 2e0932e

`storm_listing_matches.radius_used` records what was used per match, but
nothing states a default, or where it would live — a constant in config, a row
in a settings table, or a per-user preference.

Also unanswered: does the radius vary by event type? Hail swaths and
straight-line wind damage do not share a footprint. This leaves an asymmetry
unaddressed: `report_types.min_magnitude` already gives the magnitude floor a
per-type column; the radius has no equivalent. Same shape of question,
answered two different ways — either the radius belongs on `report_types`
next to `min_magnitude`, or `min_magnitude` belongs wherever the radius
default eventually lives.

**Depends on item 14** — whether there is a settings table at all.

**When:** Phase 2 capacity planning, alongside item 14. *(`database-schema.md`,
open question 2)*

**Resolved 2026-09-21.** See `docs/decision-log.md`, "`tuning.py`: both the
zip radius and the match radius are `5.0` miles, and there is no settings
table yet" (2026-09-10) — the value and storage half is settled:
`DEFAULT_MATCH_RADIUS_MILES` is its own constant in `tuning.py`, kept
separate from `DEFAULT_ZIP_RADIUS_MILES` even though both start at 5.0 miles,
so the two can diverge without a rename. Not a settings table — item 14
stays open for that. The per-type asymmetry was never part of that decision
and remains unresolved; carried forward as item 38.

## 14. Is there a settings table at all?

**Status:** resolved 2026-09-22 6a43191

Radius default, frequency-cap window, monthly API ceiling, warmup send limit —
none of these has a home today. A settings table means tuning them without a
deploy, the same argument that already justified putting `roof_relevant` in
`report_types` instead of in code.

**When:** Phase 2 — items 13 and 15 (frequency-cap floor) are both blocked on
this being decided first. *(`database-schema.md`, open question 3)*

**Resolved 2026-09-22.** Yes. `sql/018` created the single-row `settings`
table, and `sql/020` and `sql/023` put the radii, the RentCast billing day and
the quota in it, each change logged to `settings_history` by trigger. See
`docs/decision-log.md`, "Radii are read from `settings` per request" and
"RentCast quota: settings, warn and allow, usage from `api_call_log`". The
frequency-cap window (item 15) will need a home there too.

## 17. What happens to a listing that goes inactive after a match?

**Status:** resolved 2026-09-30

A match points at a listing that may since have sold. Does the browser still
show it? Does it still get emailed? Probably worth surfacing `list_status` at
send time and letting the sender decide, but the rule is unstated today.

**When:** Phase 3, when matches start getting made against real listings.
*(`database-schema.md`, open question 7)*

**Resolved 2026-09-30, by developer answer.** "Active at match time" is the whole rule: `matcher.py:59` filters `l.list_status = 'Active'`, and there is no send-time re-check. Related: item 140 (marking listings past the freshness threshold).

## 20. Should append-only be enforced by the database, not just convention?

**Status:** resolved 2026-09-30 3726c08

`send_log` and `email_templates` are append-only by convention and by code —
no trigger, no rule, no `REVOKE`. Every other rule this project treats as
load-bearing lives in the database; this is the one exception.

Options, cheapest first: `REVOKE UPDATE, DELETE` from the application role
(but that also blocks the legitimate provider-status update on `send_log`); a
`BEFORE UPDATE OR DELETE` trigger allowing only status columns to change; or
splitting status updates into a separate table so the log itself is
genuinely insert-only.

**Explicitly deferred to Phase 5** in `database-schema.md` — deciding now
would mean designing against a guess of the real update pattern.

**When:** Phase 5. *(`database-schema.md`, open question 10)*

**Reframed 2026-09-30: this is a live gap, not only a Phase 5 design question.** `CLAUDE.md` states as a non-negotiable rule that `send_log` and `email_templates` are append-only. Checked 2026-09-30: `hail_app` holds `INSERT, SELECT, UPDATE` on **both** tables, and no trigger guards either (the only non-internal triggers are `layer_integrity_checks`, `trg_last_admin`, `iem_data_compute_zip_distances` and `trg_log_settings_change`). The rule holds by convention alone today. Both tables are empty, so nothing has been lost, but the gap should be closed before the first send; it is the design decision described above, now with a deadline.

**Closed 2026-09-30.** `sql/029` (`send_log`) and `sql/030` (`email_templates`) enforce append-only with `BEFORE UPDATE` and `BEFORE DELETE` triggers; `DELETE` and `TRUNCATE` are revoked from `hail_app`. Applied to `hail-dev`, and `sql/guard_test.sql` passed 18 of 18. The production OptiPlex was not checked. **Residual, accepted:** the table owner (`hail_admin`) can still `TRUNCATE`, and can disable the triggers. See decision log 2026-09-30.

## 21. Should ingest widen past `state=CO`?

**Status:** resolved 2026-09-17 9f0027a

Filed under this heading rather than the source's own — *"Out-of-state reports
are excluded permanently"* — because that title states a fact, not a question,
unlike the other eleven `database-schema.md` open questions. A closed thing
filed as PL-21 would read as owed when it isn't. Full body, quoted, before
deciding where it belongs:

> The ingest queries `state=CO`. A storm report a few miles into Wyoming or
> Nebraska is never fetched, never stored, and therefore never matched.
>
> **No buffer radius recovers this.** The radius widens the search *around a
> stored report*; these reports do not exist in `iem_data` to widen around.
> Every other coverage question in this schema is a read-time tuning
> parameter — this one is decided at ingest, which makes it the exception.
>
> The exposure is real but narrow: hail does not stop at a survey line, and a
> storm three miles into Wyoming that crosses into a covered ZCTA produces
> listings we would want and reports we do not have. `coverage_zips` runs to
> the northern border, so the affected band is the top edge of the territory.
>
> Options, cheapest first: add the neighbouring states to the query and let
> `coverage_zips` keep filtering at read time, which costs storage and
> nothing else; or switch to a bounding box, which is what
> `docs/data-sources.md` already recommends for production and which ignores
> state lines entirely.
>
> **Same shape as the archive floor** — quiet, permanent, and cheap to widen
> later, because the `iem_data` natural key makes re-ingest idempotent. A
> wider re-run inserts only what is new. The reason to settle it deliberately
> is that nothing will ever surface the gap: a report that was never fetched
> leaves no row, no reject, and no count to notice.

**Decided: stays in the open-question registry, retitled, not folded into the
decision log alone.** The `state=CO` filter is already a decision-log entry —
*`state=CO`, not a WFO list* (2026-09-04), which chose the parameter over a
WFO list. That entry already names this exact consequence and defers it here,
in its own words: "**Open consequence, not settled scope**... Carried as open
question 12 in `docs/database-schema.md`." So the current CO-only behavior is
settled and belongs to that entry, not this one. What's still undecided is
only whether to widen the geographic scope — add neighboring states, or move
to a bounding box — and neither has been chosen. That's a real open question,
just misfiled under a declarative title in the source document.

**When:** before Phase 3 — `coverage_zips` already reaches the border, and a
RentCast pull near it would spend real money against a report set already
known to be incomplete. *(`database-schema.md`, open question 12; decision-log
2026-09-04)*

**Resolved 2026-09-17.** See `docs/decision-log.md`, "Ingest stays
state=CO-only; no widening" — RBI is licensed only in Colorado, so an
out-of-state report has no business use regardless of geographic proximity.
That is a licensing constraint on the business, not a data gap on our side,
so neither of the two widening options above is being built. Closed, not
deferred — the `state=CO` filter (2026-09-04) stands as originally chosen.

## 22. Map — side-by-side with the storm list

**Status:** resolved 2026-09-16

Leaflet from a CDN, no build step, consistent with the server-rendered
decision. Three layers: the 183 coverage zips as a static pre-generated
GeoJSON fixture in `static/`, simplified once by a script and cached by the
browser; report points for the selected range, colored by `report_source`; and
a 5-mile `L.circle` per report, in metres so it stays true at every zoom. Zip
data on hover, not labels — 183 labels collapse into mush at metro zoom. No
reprojection needed: everything is already 4326, which is what Leaflet
expects.

**Why the fixture matters more than it sounds.** The service area is stable,
so the polygons aren't per-request data. That moves the `ST_Simplify`
tolerance from a runtime decision made on every query to a one-time choice
made while looking at the output — and it keeps the rule that simplification
is display-only, never applied to the geometry the matching uses.

**Known caveat:** color-by-source is honest about what's stored, not how it
was collected. See item 7 (PL-07) — mPING taps and phone calls both arrive as
`PUBLIC`.

**When:** close of Phase 2.

**Resolved 2026-09-16.** See `docs/decision-log.md`, "Map: Leaflet, no tile
layer", and "Vendor Leaflet into `static/`" (2026-09-17). Built on
`/territory`, with coverage polygons as a static fixture, report points
coloured by normalized source, and 5-mile `L.circle` rings.

## 24. Replace the "Last N days" dropdown with a date picker

**Status:** resolved 2026-09-18 854f5bc

Explicit start and end dates, rather than a fixed set of ranges (30/90/365).

**When:** with item 10, which needs range parsing anyway — a territory browse
grouped by zip or city is naturally scoped to an explicit date range, not one
of three preset day counts.

**Resolved 2026-09-18 (`854f5bc`).** `index()` switched from a `?days=` dropdown to explicit `?start=&end=`; date inputs are on the storm list, territory and exports pages, and `?days=` survives only as a URL shortcut. Confirmed by the developer on 2026-09-30 as what this item meant.

## 25. The match view can only show listings already pulled

**Status:** resolved 2026-09-21 67bf831

RentCast pulls cost money and the monthly ceiling has no settled home yet
(open question 3 in `database-schema.md`; item 5's "runaway-bug tripwire"
framing), so a date toggle on the match view filters existing
`properties`/`listings` rows — it does not trigger a new pull.

The page must say so explicitly, or a date range with no pull history reads
as "no listings were affected" rather than "this range was never queried."
Those are different facts and the second one is silent by default: an empty
result set looks the same either way unless the page names which one it is.

**When:** Phase 3, when the match view is built.

**Resolved 2026-09-21.** See `docs/decision-log.md`, "The match page states
its own coverage gaps" — the match page lists unpulled zips by number, links
to a pull estimate for them, and shows the date of the oldest listing data,
so "never queried" and "queried, nothing there" no longer read the same.

## 28. Vendor Leaflet into `static/` instead of the CDN

**Status:** resolved 2026-09-17 e22c02f

**When:** before prod deployment.

**Resolved 2026-09-17.** See `docs/decision-log.md`, "Vendor Leaflet into
`static/`, off the `unpkg.com` CDN" — `leaflet.css`/`leaflet.js` plus the two
marker images the CSS references are committed under
`hailsys/web/static/`, and `base.html` loads them locally. Done ahead of the
"before prod deployment" window stated here rather than waiting for it; kept
here per this file's convention rather than deleted.

## 36. `CLAUDE.md` is stale

**Status:** resolved 2026-09-18 f8913fd

Claims the ingest scripts and test suite don't exist.

**When:** next docs pass.

**Resolved 2026-09-18.** `CLAUDE.md` said "What does not: the ingest scripts
themselves, any web UI, any RentCast client, any sending path, the
`report_sources` seed, and any test suite" through 2026-09-13; commit
`f8913fd` (2026-09-18) rewrote the current-phase text and the claim is gone.
Checked against `git log` on 2026-09-22, when the item was closed out — it was
resolved by that rewrite, not by a docs pass aimed at it. The current-phase
text was brought up to date again in `d23d5c9` (Phase 4 current). Kept here per
this file's convention rather than deleted.

## 37. Verify whether the 10 zip-less coverage zips are the already-removed rows

**Status:** resolved 2026-09-30

If so, there's no silent-match problem.

**When:** next docs pass.

**Resolved 2026-09-30.** Verified against the database: `coverage_zips` has 183 rows and none of the ten zips named in the decision log (80502, 80522, 80539, 80632, 80638, 80901, 80225, 80523, 80639, 80213) is in it, so there is no silent-match problem. The 10 were reported and excluded at load (`load_coverage.sh`: 183 inserted, 10 reported).

## 39. Admin page — settings table, users and roles, role_required

**Status:** resolved 2026-09-22

Concrete Phase 4 shape, elaborating item 14: a single-row typed settings
table (zip radius, match radius, `hail_pair_ceiling_m()` shown read-only
since raising it needs a migration and a recompute), a users-and-roles
admin page sharing the same screen, and a `role_required` decorator —
routes currently check only `login_required`, nothing checks role. Settings
read per request, not cached, so they're correct across Gunicorn workers
with no invalidation to get wrong. Needs a change history.

**When:** Phase 4. *(`docs/decision-log.md`, "Admin settings page: Phase 4,
and not every number is a setting")*

**Resolved 2026-09-22.** `/admin` is built as its own blueprint, admin-only
through one `before_request` check: users and roles (add, deactivate,
reactivate, change role, sign out, reset password), sign out everyone, and the
settings table (`sql/018`, `sql/020`) with zip and match radius, the ceiling
shown read-only, and `settings_history` written by trigger. `role_required`
exists and guards `/pull/estimate`, `/pull` and `/match`. Last-admin
protection landed with it: `sql/019`'s deferred constraint trigger refuses any
change that leaves zero active admins. See the 2026-09-22 decision-log entries.

## 40. "Matched, found nothing" is indistinguishable from "never matched"

**Status:** resolved 2026-09-23 9b3fad3

A match run that inserts zero rows (nothing was in range) leaves no trace —
`storm_listing_matches` gets no new rows either way, so the badge stays
`Pulled, not matched` whether or not anyone has actually clicked Match.

**When:** Phase 4, or alongside the admin work.

**Resolved 2026-09-23.** See `docs/decision-log.md`, "Match runs are
recorded, so an empty match is visible". `sql/022` adds `match_runs`, and a
completed empty run on a pulled storm reads "Matched, none in range", with
Match still offered. On a never-pulled storm it stays "Not pulled" with its
Pull link. Verified through `/match` on 2026-08-22 HAIL. **Not yet seen:** the
pulled-storm case itself, which needs a storm that was pulled but has no
listings in range (item 89).

## 46. No "pull again" affordance — Pull link only shows on Not pulled

**Status:** resolved 2026-09-23 306056d

Once a storm moves off `Not pulled`, there's no button to re-pull it — by
design, since a duplicate pull spends real money, but there's also no
deliberate path for the case where a re-pull is actually wanted (stale
listing data, a partial failure).

**When:** Phase 4, alongside the admin work.

**Resolved 2026-09-23.** See `docs/decision-log.md`, "Re-pull goes through the
estimate page; stale storms are greyed, not blocked". Every state past "Not
pulled" offers "Pull again", or "Pull" for a storm matched off another
storm's pull, through `/pull/estimate`, which already shows cost and
recent-pull warnings. Storms past the claim window show it greyed, and
`/pull/estimate` is deliberately not gated on staleness.

## 47. Stale `'running'` pulls after a restart — needs a sweep

**Status:** resolved (residuals) 2026-09-25 54cc7f2

`hailsys/web/jobs.py`'s own docstring already names the gap: `daemon=True`
means a thread dies with its process, leaving `api_pulls` stuck at
`'running'` after a restart mid-pull. `api_call_log` shows how far it got,
but nothing marks the run dead — and workstate.py's `'running'` still
counts as pulled, so the Pull link stays hidden for that storm
indefinitely.

**When:** before background pulls are relied on for real operations.

**Checked 2026-09-24:** no pull is currently stuck at `'running'`, so there
are no existing rows to clear. The sweep is still needed for the next restart
mid-pull. Diagnosing one also needs item 99 (logging), since the thread's
`info` lines are dropped.

**Resolved 2026-09-25** (`54cc7f2`; age rule and docstring in the commit
after). See `docs/decision-log.md`, "Stale pulls are swept at startup and
marked cancelled".
`sweep_stale_pulls()` in `jobs.py`, called from `create_app()`, marks any pull
still `running` after 10 minutes as `cancelled`, sets `finished_at`, and logs
an `event=stale_pull_swept` warning for each. `workstate.py` no longer counts
`cancelled` as pulled, so the storm reads as not pulled again, as `failed`
does, and Pull is offered. The calls already spent stay in `api_call_log`, so
a re-pull spends again.

**A pull orphaned early is covered too.** The 10-minute floor stops a
restarted worker cancelling another worker's live pull, so a pull orphaned in
its first 10 minutes isn't swept at that restart. `workstate.py` now counts a
`running` pull only while it is under `PULL_STALE_AFTER` (10 minutes, one
constant shared with the sweep, which takes it as a query parameter). Past
that the storm falls back to "Pulled, not matched", with Match and re-pull
offered, before any sweep has run. `jobs.py`'s module docstring is updated.

**Match runs too (`6d6ba63`).** The sweep also sets `match_runs` rows still
`running` after the same 10 minutes to `'failed'`, with `finished_at` and an
`error_detail`, and logs `event=stale_match_run_swept`. `match_runs` has no
`'cancelled'`, and its CHECK requires `finished_at` unless a run is running.
`workstate.py` counts a running match like a running pull, so an orphaned one
would otherwise hold its storm on "Pulling...".

**Still open:**
- `create_app()` runs the sweep, so anything that builds the app (tests, a
  script) runs the `UPDATE`, and so does every gunicorn worker. It is
  idempotent and floored at 10 minutes, but it is a write on import.
- `finished_at` holds the sweep time, not when the pull died.
- **Verification.** The sweep was verified 2026-09-25 with a backdated
  `'running'` row (as reported by the developer): a restart logged
  `event=stale_pull_swept` and the row read `'cancelled'` with `finished_at`
  set; the test row was deleted. It has not met a genuine orphan. The label
  rule was tested with synthetic rows (a pull 1 minute and 9m59s old reads
  "Pulling..."; exactly 10 minutes, 3 days, and a missing start time read
  "Pulled, not matched"), and the sweep's age parameter binds, checked with a
  read-only `SELECT`.

**When:** if the write on import ever surprises a test or script, or the first
time a storm is seen stuck on "Pulling...".

## 50. Monthly RentCast quota tracker

**Status:** resolved 2026-09-23

The plan is 1,000 requests/month flat, overage billed after. Nothing in the
system tracks usage against that ceiling — `api_pulls`/`api_call_log`
record what was spent, but nothing sums it against a billing period or
warns before overage. Needs a billing-period start date and a decision
between warn-and-allow versus hard block. Related to, but more concrete
than, item 14's "monthly API ceiling" line.

**When:** Phase 4.

**Resolved 2026-09-23.** See `docs/decision-log.md`, "RentCast quota: settings,
warn and allow, usage from `api_call_log`". `sql/023` adds the billing day and
quota to `settings`, and `hailsys/queries/quota.py` sums `api_call_log` over
the period. Usage shows on the admin page, the pull estimate (with an
overage warning) and the storm list for senders and admins. **Warn and allow,
not a hard block.** Open follow-ups: RentCast's own rollover timezone
(item 87), and calls from a key-rejected pull not reaching `api_call_log`
(item 88).

## 57. A "Pulling…" work state for running pulls

**Status:** resolved (residuals) 2026-09-25 54cc7f2

`workstate.py` treats any pull that is not `failed` as pulled, so a pull that
is still `running` reads "Pulled, not matched" until its match run lands. The
2026-09-21 pull ran under 2 seconds (22:52:49.18 to 22:52:51.13 UTC) for 4
zips, so the wrong label lasts about that long. Related to item 47: a pull
stuck at `running` after a restart would also read "Pulled, not matched"
indefinitely. (Once `running` gets its own label, an orphan reads "Pulling..."
indefinitely instead, which is why the two were built together.)

**When:** low priority while pulls take seconds; revisit if they get longer,
which scales with zip count.

**Resolved 2026-09-25** (`54cc7f2`; label order and match runs in `6d6ba63`).
See `docs/decision-log.md`, "A running pull reads \"Pulling...\" and the storm
list polls it" and "Pull and Match return to the filtered list; a running pull
or match outranks the other labels". A running pull reads "Pulling..."
(`workstate.PULLING`), and the storm-days Status cell is one fragment,
`_status_cell.html`, used by `storms.html` and by `/storms/state`. While a pull
runs the cell carries `data-poll="1"`, and `storms.js` asks `/storms/state` for
the same cell every 3 seconds, up to 40 times, replacing it, until the reply
has no `data-poll`. "Pull again" isn't offered while a pull is running.
`running` outranks Sent and Matched, so a re-pull of a matched storm reads
"Pulling..." too, and a running `match_runs` row counts the same way, so the
cell stays on "Pulling..." through the pull and its automatic match. A dead
pull or match stops reading "Pulling..." after 10 minutes (item 47), and the
badge has its own colour (`.badge-pulling`, purple).

**Confirmed by the developer in the browser, 2026-09-25:** the Status cell's
"Pulling..." display works. The line under the page heading, which they had
been trying to fix all along, is a separate thing (item 118) — confirmed
working too, 2026-09-27.

**Still open:**
- After 2 minutes the cell stops updating and needs a refresh. That is the
  poll cap, deliberate.
- The server doesn't refuse a second pull while one is running (item 51).
- The JS has not been run under `node` (none on `hail-dev`), so the browser
  confirmation above is the only check of the polling, and what exactly was
  watched wasn't recorded.
- A few milliseconds separate a pull finishing from its match run being
  inserted. A poll landing exactly there would stop early. Unlikely against a
  3-second interval.

**When:** nothing scheduled.

## 58. Viewer gating not yet tested with a real viewer account

**Status:** resolved 2026-09-23

`role_required` on `/pull/estimate`, `/pull` and `/match`, and the `can_pull`
gating on `storms.html`, were written 2026-09-22 but have not been exercised
by a viewer login — nothing in that day's work has been exercised by a
non-admin account. That test is Phase 4's done-when: a viewer can browse and
export but cannot trigger a pull. Create a viewer account and check both
halves — the greyed Pull/Match text in the UI, and a 403 from `/pull` (and
`/pull/estimate`, `/match`) when requested directly.

**When:** next session; closes Phase 4's outline.

**Partly verified 2026-09-23.** See `docs/decision-log.md`, "Phase 4's done
condition verified". With CSRF on and a valid token, a session for the real
`testview` account got 403 on `/pull`, `/match`, `/pull/estimate` and
`/admin/`, and 200 on `/` and `/export.csv`. The greyed UI was confirmed in
rendered pages. **Still open:** the session was set up in the Flask test
client, not by `testview` signing in with a password. One real sign-in closes
this.

**Resolved 2026-09-23.** Signed in as `testview` with its password: Pull shows
greyed out. With the route-level 403s above, both halves are covered, and
Phase 4 is closed.

## 59. The CSRF error handler returns 302, indistinguishable from success

**Status:** resolved 2026-09-23 d677846

`handle_csrf_error` in `create_app()` flashes "That form expired…" and
redirects back with a 302 — the same status a successful POST returns. A
person sees the flash; anything checking status codes (a test, a script, a
future fetch-based form) sees success. Returning the redirect is right for a
browser, but the failure should be distinguishable somewhere — a 400 with a
rendered page, or at least a log line.

**When:** Phase 4, small.

**Resolved 2026-09-23.** See `docs/decision-log.md`, "CSRF failures return 400
with a rendered page". The handler renders `csrf_error.html` with status 400.
Verified: a tokenless POST gets 400 and the error page.

## 60. Self-action guard for admins

**Status:** resolved 2026-09-23 d677846

An admin can deactivate, demote, or sign out their own account from the users
table. `reset_password` already refuses the admin's own row and points at the
change-password page; the other actions don't. Last-admin protection
(`sql/019`) only catches the case that leaves zero active admins — with a
second admin present, an admin can still lock themselves out in one click.

**When:** Phase 4, before the phase closes.

**Resolved 2026-09-23.** See `docs/decision-log.md`, "An admin cannot
deactivate, demote or sign out their own account". Refused server-side in
`deactivate_user`, `change_role` and `boot_user` (joining `reset_password`),
and the admin's own row shows "your account" instead of controls. Verified:
all three refused on the acting admin's row, users unchanged.

## 62. `scripts/*.py` still default `--radius` to `tuning.py` constants

**Status:** resolved 2026-09-24 0f628f4

The web app reads radii from `settings` per request (2026-09-22), but
`export_storm_zips.py`, `test_estimate.py`, `test_match.py` and
`verify_zip_distances.py` still import `DEFAULT_*_RADIUS_MILES` from
`tuning.py`. Once an admin changes a radius, the CLI and the web app disagree
by default — the same two-sources shape the `settings` table exists to remove.

**When:** Phase 4, when convenient.

**Resolved 2026-09-24.** See `docs/decision-log.md`, "Scripts keep the
`tuning.py` radius constants; the app never reads them". Kept on purpose: the
UI's CSV export reads `g.settings`, and `verify_zip_distances.py` wants a fixed
radius. `tuning.py`'s comment now says the constants are for `scripts/` only.

## 67. Header bar doesn't wrap on phones

**Status:** resolved 2026-09-23 d8d91cc

`.site-header-right` is `white-space: nowrap` and the nav links can't shrink,
so at phone widths the header alone is wider than the viewport and every page
scrolls sideways — the matched-listings overflow fix (2026-09-22) can't stop
that on its own. Lives in `base.html`/`style.css`, shared by every page.

**When:** Phase 4, cosmetic.

**Resolved 2026-09-23** (`d8d91cc`). `.site-header` and `.site-header-left`
wrap, with a 640px breakpoint that tightens padding and lets the nav wrap.
Checked by rendered markup only, not in a browser at phone width.

## 68. Admin users table overflows narrow screens

**Status:** resolved 2026-09-23 d8d91cc

Eight columns plus the fixed 15rem Actions group. Same fix as the
matched-listings tables: wrap it in `.table-scroll` so it scrolls in its own
box instead of pushing the page.

**When:** Phase 4, cosmetic — same pass as the matched-listings overflow fix.

**Resolved 2026-09-23** (`d8d91cc`). The Users table is wrapped in
`.table-scroll`. Checked by rendered markup only, not in a browser.

## 69. `.action-disabled` has no spacing next to the badge

**Status:** resolved 2026-09-23 d8d91cc

The greyed Pull/Match text sits flush against the status badge. The link and
button it replaces get `margin-left: 0.5rem`, `font-size: 0.8125rem` and
`white-space: nowrap` from `.pull-link`/`.inline-action`; giving
`.action-disabled` the same three lines makes the two states line up.

**When:** Phase 4, cosmetic.

**Resolved 2026-09-23** (`d8d91cc`). `.action-disabled` has the same margin,
size and `nowrap` as `.pull-link`/`.inline-action`. Checked by rendered
markup only.

## 74. Real jurisdiction count from the inventory

**Status:** resolved 2026-09-24 b92841b

`output/jurisdiction_inventory_2026-09-23.csv` (680 rows, 183 zips) includes
slivers where TIGER and DOLA edges disagree. Excluding rows under 0.5% of a
zip, the 2026-09-23 run gave 71 municipalities and 13 unincorporated
counties. That is a starting figure only: Hudson is counted under two codes
(item 75), and the CSV is regenerable output, not tracked.

**When:** with the permits work (item 70).

**Resolved 2026-09-24.** See `docs/data-sources.md` §5, "Jurisdiction count":
82 issuers after filtering (range 68–82 by threshold), from 88 jurisdictions
with whole towns in large rural zips kept. The Pikes Peak Regional Building
Department is the only regional merge, and no town checked delegates to its
county. PPRBD + Aurora hold 88% of current properties, skewed by 11 of the 15
pulled zips being in El Paso County. The 0.5% threshold proposed here drops
Kiowa, Calhan and Garden City, which are whole towns, so it can't be used on
its own. 32 issuers are still unverified (item 98).

## 86. The storm list silently drops days past 50 rows

**Status:** resolved 2026-09-24 9e5cce1

`index()` passes `limit=50` to `storms.fetch_recent_days`, so a wide date range
returns the 50 rows the query orders first, and nothing on the page says more
exist. Found in the Phase 4 audit: a 2024–2026 range didn't reach 2024-05-30.
Same silent-truncation shape as item 33. Either say "showing 50 of N" or
page.

**When:** before anyone relies on a long date range.

**Resolved 2026-09-24.** See `docs/decision-log.md`, "The storm list pages at
50 storm days". Page links carry the active filters, and an out-of-range
`?page=` clamps to the last page.

## 88. Calls from a key-rejected pull don't reach `api_call_log`

**Status:** resolved 2026-09-24 ddd77a0

On `RentCastAuthError`, `pull.py` adds the attempts to
`api_pulls.actual_api_calls` but writes no `api_call_log` row, so the usage
figure (which reads the log) leaves them out. Today both totals agree. It only
matters if RentCast bills rejected-key requests, which is unconfirmed.

**When:** if a real invoice disagrees with the usage figure.

**Resolved 2026-09-24.** See `docs/decision-log.md`, "Unreadable RentCast
responses fail loudly, with an attempt count" and "Every aborted zip's calls
reach `api_call_log`". The bad-key abort and the unclassified catch-all now
both write this zip's row: attempts, or 1 when unknowable. Unreadable bodies
raise `RentCastResponseError` instead of escaping and leaving the pull stuck
at `'running'`.

## 92. CSV export missing on the matched-listings and activity pages

**Status:** resolved 2026-09-24 28a0195

Phase 2's outline says "CSV export on every list." The storm list and
territory have one (`/export.csv`); `/storms/matches` and `/activity` don't.
The matched listings are the list Phase 5 acts on, and the likeliest one
someone will want in a spreadsheet.

**When:** Phase 5, before the first real batch is worked from the match page.

**Matched-listings half resolved 2026-09-24** (`28a0195`). See
`docs/decision-log.md`, "CSV exports: three projections, suppression
optional, snapshot". `/storms/matches` links to `/storms/matches.csv`, and
`/exports` adds a bulk match export over a date range and a realtor list.
**The `/activity` half is still open** and moved to item 107, so this item
can close.

## 93. The activity feed shows other users' names to viewers

**Status:** resolved 2026-09-24 fbc8338

`hailsys/queries/activity.py` joins `users` for first and last name, so
`/activity` and the storm list's feed show every signed-in user, viewers
included, who ran each pull and match run. Intended for a five-person office,
where "who pulled this" is useful. But it is another user's data behind
`login_required` alone, and nothing records it as a choice. Worth a
decision-log line: keep as is, or show names to senders and admins only.

**When:** next decision-log pass; before staff accounts exist (Phase 7) at
the latest.

**Resolved 2026-09-24.** See `docs/decision-log.md`, "The activity feed
shows who did what, to everyone". Kept as is, deliberately, for an office of
five with admin-created accounts. Revisit if the account model changes.

## 99. Logging is unconfigured anywhere in the app

**Status:** resolved 2026-09-24 976ea0d

Nothing calls `logging.basicConfig` or sets up a handler, so every
`logger.info` is dropped at Python's default WARNING threshold. Only
warnings and errors reach the container log, via Python's fallback handler.
A 12-zip pull and its match run on 2026-09-23 left **zero** `event=` lines
over 24 hours. This is why the item-88 failures were invisible, and it blocks
diagnosing anything a background thread does, item 47 included. The likely
fix is a single `logging.basicConfig(level=logging.INFO, ...)` in
`create_app()`, writing logfmt to stdout as the ingest already does.

**When:** before background pulls are relied on.

**Resolved 2026-09-24.** See `docs/decision-log.md`, "Web logging: shared
logconfig module, level and logger in the line, rotated json-file".
`hailsys/logconfig.py` configures INFO to stdout in every worker, and `web`'s
container log is rotated at 20 MB × 5. `event=` lines from the pull thread and
the matcher now appear in `docker compose logs web`.

## 104. Move the ingest onto `hailsys/logconfig.py`

**Status:** resolved 2026-09-24 7d05611

The ingest still sets up its own logging: a `basicConfig` in
`hailsys/iem/common.py` (line 127 when filed; that code has since moved, see the note below), shared by both ingest scripts. Moving it onto
the shared module keeps one place to change the format. Their journald format should stay
a bare `%(message)s` unless that's decided otherwise, since journald already
records the unit and the time. Needs an `ingest` rebuild (`docker compose build
ingest`), because that image bakes the code in.

**When:** whenever the ingest is next touched.

**Resolved 2026-09-24 (`7d05611`).** `hailsys/iem/common.py:122-125` now delegates to `hailsys/logconfig.py` (`include_logger=False`), and both ingest scripts call `configure_logging()`. The `ingest` image was built 2026-09-24 20:13 UTC, four minutes after the commit, so it includes the change. Not in scope and unchanged: `scripts/backfill_zip_distances.py` and `scripts/test_rentcast_pull.py` still call `logging.basicConfig` themselves.

## 116. RentCast values that don't fit their columns abort a pull

**Status:** resolved (residuals) 2026-09-25 5933ebc

**Resolved 2026-09-25** (`5933ebc`). See `docs/decision-log.md`, "RentCast
values a column can't hold are stored as NULL and logged". Two listings broke
pulls: **615 Remington St, Fort Collins, 80524 (`bathrooms` 150)** and **5331 S
Delaware St, Littleton, 80120 (`bathrooms` 912)**. `properties.bathrooms` is
`NUMERIC(3,1)`, which holds at most 99.9, so `NumericValueOutOfRange` rolled
back the whole zip and ended the pull after its calls were spent, and a re-pull
failed the same way. Pulls 41 and 78 (2026-08-14 HAIL, 4 calls each) and 79
(2026-07-06 NON-TSTM WND GST, 13 calls) spent 21 calls for nothing. `_fits()`
in `upsert.py` now stores `NULL` and logs `event=field_out_of_range` (id, field,
value) for a `bathrooms`, `bedrooms` or `yearBuilt` its column can't hold, and
the listing is kept, with the original in `raw_payload`. Both storms were
re-pulled successfully afterwards (pull 80: 17 zips, 3,040 listings, 930 new
matches; pull 81: 26 zips, 3,323 listings).

**Still open:**
- The limits are what the column can **hold**, not what is plausible, so 99
  bathrooms is stored as a real value.
- Only three fields are guarded. `square_footage` and `lot_size` are `INTEGER`
  (about 2.1 billion), which no real listing reaches.
- Any other bad listing, or another failure inside the upsert, still aborts the
  zip and the pull. A savepoint per listing was considered and not built: it
  would let a pull succeed with rows missing, so it would have to count and
  report them.

**When:** the next time a pull aborts in the upsert.

**Watch, 2026-09-30:** reopen if another listing aborts a pull in the upsert (this item's own "when it happens again"). No recurrence so far: no pull has failed since 2026-09-25, and 33 have completed. The three failed pulls this item names (41, 78, 79) are in `api_pulls` as described.

## 122. The current DNC list is checked in against the realtor pool and Constant Contact — and the import is done (2026-09-29)

**Status:** resolved 2026-09-29

Compared `rbi-dnc-list-09-28-2026.csv` (719 unique emails, the **full,
current** DNC list — old `Created At` dates on individual entries, 2017 to
2025, mark when each was added, not the export's freshness), RBI's client/
service list (item 83, 4,187 unique), the hail system's `realtors` table
(7,082 unique), and a two-years-old Constant Contact export (2,432 "Active,"
613 "Unsubscribed," all "Implied" permission, none "Confirmed"),
2026-09-28. See `docs/decision-log.md`, "Comparing RBI's existing contacts,
the hail system's realtors, and the legacy DNC list" and "Constant Contact
cross-reference: the client list and CC are different relationships, and the
CC copy is two years old."

**83 of the current 7,082 hail-system realtor emails (1.2%) are already on
the DNC list** — concrete evidence, not just the general rule, for why the
DNC import (`CLAUDE.md`'s non-negotiable rules; `phases.md`'s Phase 5
checklist — there is no dedicated numbered parking-lot item for this) has
to land before any send. 66 more (0.9%) show as unsubscribed in the
two-year-old CC export, a separate signal worth checking against a fresh CC
export before treating as authoritative.

**The client list and Constant Contact are almost entirely different
people** (1 of 4,187 overlaps) — expected, once it was clarified that the
client list is a 12+-year service/referral record, not an email list (item
83). **339 hail-system realtors were CC-Active two years ago** — the
best-corroborated warm-start subset found so far, though "were active then"
is not the same claim as "are active now." Zero overlap between the client
list and the DNC list is real, not a bug (52 shared domains, zero exact
matches).

**At the time of writing (2026-09-28):** no import into `dnc_list` — this was read-only
comparison only. *(Stale as of 2026-09-30, see the note at the end of this item.)* The full email-level breakdowns were saved outside the
repo, not committed. A current Constant Contact export would meaningfully
sharpen the 339 figure and is RBI's own vendor account, not blocked
externally.

**When:** before any send — the comparison narrows the work, it doesn't
replace the import.

**Two more DNC sources cross-referenced, 2026-09-28: `reference/Airtable-
DNC-List.csv` and `reference/rbi-constant-contact-dnc-list-09-28-2026.csv`.**
See `docs/decision-log.md`, "The Airtable DNC list was populated from
Constant Contact, and the two have since drifted apart." Airtable was
populated directly from a Constant Contact export on 2025-10-21 (confirmed,
not inferred — 94% of its rows share one bulk-import timestamp, and the
shared entries' dates match Constant Contact's to the minute once timezone
is accounted for), then diverged: 40 entries added to Airtable since, only
there; 26 entries in Constant Contact's current DNC list that predate the
sync and never made it into Airtable. **The real import source is the union
of all suppression files found so far** (the original DNC export, this
Constant Contact copy, and Airtable), not any single one.

**Import done, 2026-09-30.** Batch #1 (the Constant Contact export, 719 rows) was committed 2026-09-29 15:26 UTC through the admin upload (`3ebb5e7`), and batch #3 (Airtable, converted by hand to the Constant Contact shape; 735 rows, 40 of them new) at 15:27. `dnc_list` holds 759 suppressions, all `source = 'legacy_import'`, none removed, 108 linked to a realtor. See the decision log, "DNC import: admin upload, staged and previewed before commit". This item's first section still describes the state on 2026-09-28 and is kept for the reasoning. **Residuals:** item 129 means the loaded union may still be incomplete (the Constant Contact export's newest unsubscribe is 2025-12-13), and the original `rbi-dnc-list-09-28-2026.csv` was judged the same data as the Constant Contact copy "by its early rows" without a recorded full comparison. `CLAUDE.md` still says the legacy lists are not yet imported; that is stale.

## 132. `dnc_list.email_raw` can carry leading whitespace; only `email_norm` is trimmed

**Status:** dropped 2026-09-30

`email_norm` (the enforcement key) is always `lower(trim(email_raw))`, so
suppression itself is never affected by stray whitespace in `email_raw` —
but the raw value shown in any admin view or audit is whatever the source
file had, untouched.

**When:** cosmetic.

**Dropped 2026-09-30.** Latent only: no `dnc_list` row currently has whitespace in `email_raw` (checked 2026-09-30), and suppression runs on `email_norm` regardless.

## 139. Production setup: migrations must run in strict numeric order before the first pull, and nothing enforces that

**Status:** resolved (residuals) 2026-09-29 b645e31

There is no automatic migration runner — `docker-compose.yml`'s own
comment shows the real mechanism, one file at a time:
`docker compose run --rm loader psql -v ON_ERROR_STOP=1 -f
/repo/sql/0NN_name.sql`. `docs/server-setup.md` documents the SELinux
volume-mount config for `./sql` but not an explicit "apply these in
order" step. This matters concretely for `address_key` (item 55,
`sql/024`/`025`/`027`): on `hail-dev`, real `properties` data existed for
months between `025` (the under-coalesced version) and `027` (the fix),
which is exactly what let the bug touch real rows. On a fresh production
database, running `001` through `027`-and-beyond in order *before the
first RentCast pull* closes that gap entirely — `properties` starts
empty, and by the time any row exists, the generated column is already
computing with the final, fixed function. Skip a file, run them out of
order, or start pulling before the sequence finishes, and the same class
of bug (or worse — a genuinely missing grant, a genuinely missing table)
can resurface with nothing to catch it. See `docs/decision-log.md`,
"`address_key`: coalescing every field, not just the directional," for
the full reasoning.

**When:** before the production OptiPlex's software setup (`CLAUDE.md`'s
caveat that Phase 0's physical/software build is still open) — add an
explicit, ordered migration checklist to `docs/server-setup.md` rather
than relying on numeric filenames and care alone.

**Resolved for the documentation half, 2026-09-29 (`b645e31`):** `docs/server-setup.md`, "Database Migrations", states the order and the before-first-pull rule. Developer, 2026-09-30: sufficient. **Residual:** nothing enforces the order. A runner script is deferred until production hardware exists.

## 23. Address lookup — "did this address get hit?"

**Status:** resolved 2026-10-01 4554b8d

A one-off tool: paste an address, get the reports near it. Different unit of
analysis from everything else built — the coverage-zip join drops out
entirely, since the question is whether a report fell within X miles of one
point, not which of our zips were in range.

The blocker is geocoding. Nothing in the system converts a street address to
coordinates, and RentCast only returns them for properties it already knows,
which covers listings but not an arbitrary address a coworker types in. A
standalone version needs a geocoder — a new external dependency, an API key,
and a rate limit to respect. A manual lat/long entry form needs none of
that and is a single `ST_DWithin`.

**When:** Phase 3, when addresses have coordinates attached.

**Blocks items 77 and 78**, and with them all jurisdiction and address-search
work (items 70–84; decision log 2026-09-23): a jurisdiction can only be
stated for an address once the address is a point.

**Re-gated 2026-09-30:** address search is a Phase 6 feature (see item 124). No geocoder or address route exists yet (checked 2026-09-30).

**Resolved 2026-10-01.** `/search` shipped, geocoding through the Census
Geocoder API rather than a manual lat/long form or a local TIGER load. See
`docs/decision-log.md`, "Address search: Census Geocoder, keyed through
`address_key()`". Items 77 and 78 are no longer gated on this item, only on
item 70.

## 124. Address search (Phase 6) must run typed input through `address_key()`

**Status:** resolved 2026-10-01 4554b8d

Whenever a "look up this address" feature gets built, the typed input has
to go through `address_key()` too, or the comparison is against
differently-shaped strings — the function's own comment already says this
(`sql/024`). `fuzzystrmatch` is installed (confirmed) and would cover
typos a user might make; `address_key()` won't — it standardizes structure,
not spelling.

**When:** Phase 6, when the search feature is built.

**Re-gated 2026-09-30:** Phase 6, as this item already said; recorded with item 23.

**Resolved 2026-10-01.** The lookup and the `geocode_cache` key both call
`address_key()` in SQL; nothing in Python reimplements it. Typo tolerance, which
`address_key()` does not give, is item 144.

## 94. The TIGER geocoder extension is installed, and `tiger` is on the search path

**Status:** resolved 2026-10-02

`postgis_tiger_geocoder` and `postgis_topology` came with the
`postgis/postgis:16-3.4` image; nothing decided to add them. The `tiger`
schema is on the default `search_path` (`"$user", public, topology, tiger`),
and it holds empty tables named `county`, `place`, `zcta5`, `edges` and
others, all SRID 4269. A typo'd or unqualified table name could silently
query an empty NAD83 table instead of failing, the same silent-SRID shape
CLAUDE.md warns about. Options: drop the two unused extensions, or take
`tiger` off the search path for `hail_app` and `hail_ingest`.

**Side question resolved 2026-09-26; item reopened 2026-09-30, see below.** The TIGER geocoder was already installed, as this
item describes — unintentional, came with the base image, and stays that
way for now. What's changed: `address_standardizer` and
`address_standardizer_data_us` were *not* installed and now are (`sql/024`,
item 55), a deliberate addition, not the same extensions this item is
about. The `tiger` schema itself is untouched; the silent-empty-table risk
this item describes is still real and still open, just no longer confused
with the (separate, now-resolved) question of whether address parsing was
available.

**When:** before production deployment, alongside item 3 (the base image).

**Reopened 2026-09-30.** The 2026-09-26 marker closed only a side question, whether address parsing was available (`address_standardizer`, `sql/024`). The risk this item describes is unchanged. Verified 2026-09-30: `search_path` is still `"$user", public, topology, tiger`, and `postgis_tiger_geocoder` and `postgis_topology` are still installed. Gated, with item 3, on "before production deployment".

**Dropped 2026-10-01.** The developer closed this on the strength of the
address-search decision: Census is the better geocoder, so the TIGER geocoder
will not be used (`docs/decision-log.md`, "Address search: Census Geocoder").
Nothing was changed. `postgis_tiger_geocoder` and `postgis_topology` are still
installed and `tiger` is still on the search path, so the silent-empty-table
risk above stands, accepted. Item 3 (the base image) or item 145 (a local TIGER
load would need the extension) is where it would come back.

**Reopened and resolved 2026-10-02.** Dropped on 2026-10-01 with the risk
accepted; reconsidered the next day because the fix is one reversible
statement per role. `sql/032` sets `search_path = "$user", public` for
`hail_app` and `hail_ingest`. The extensions stay installed (item 145).
Checked first that nothing depends on `topology` or `tiger` being on the path:
no `.py` or `.sql` file references either schema; `ST_SimplifyPreserveTopology`
is a `public` PostGIS function; `standardize_address()` is in `public`;
`topology.topology` has 0 rows; the `tiger` tables are empty. Not covered:
`hail_admin`, the database-level default on `weather-property`, and
`template_postgis` (inherited by new databases) all keep the old path.
Applied to `hail-dev` 2026-10-02 (`pg_db_role_setting` shows the new path for
both roles); not checked on the production box.

**When:** apply `sql/032` on the production box at deployment, with item 3.

## 65. `scripts/create_user.py` — bootstrap-only, or retire?

**Status:** resolved 2026-10-01

It predates `/admin` and never sets `created_by`, so every account it creates
has no author. The admin page now does the same job with attribution. Either
keep it strictly for bootstrapping the first admin (and say so in its
docstring) or remove it.

**When:** before deployment.

**Resolved 2026-10-01.** Kept, strictly for bootstrapping. The docstring says
so, and `--created-by` is now required unless no non-system user exists yet, so
it can no longer create an unattributed account by accident. See
`docs/decision-log.md`, "`create_user.py` stays, as a bootstrap path".

## 1. Which role sees operational views

**Status:** resolved (residuals) 2026-10-01 0a2052d

Open question 11. The three application roles are defined as **cost stages** —
`viewer` browses free data, `sender` spends money and reputation, `admin`
manages users and nothing else. Ingest health is not a cost stage: it costs
nothing to look at, but "did last night's ingest run" is an operator question,
not a browsing one.

So the role model has no seat for the person who checks whether the system is
working. Today that person is Justyn at a psql prompt, which is fine while the
operator and the entire user base are the same person and stops being fine at
Phase 6.

`hail_app` currently holds `SELECT` on `ingest_runs` and `iem_ingest_rejects`,
which is a provisional answer rather than a decided one. Note this is not new
with those tables — `api_pulls` and `api_call_log` have the same unanswered
question and have simply never been read by anyone else.

**When:** Phase 2, when a UI exists.

**Checked against the 2026-09-14 decision-log entries** (schema-review
mapping): still open. The Tailscale-access entry touches the same territory —
device/network auth versus application identity — but its "unchanged by this"
paragraph explicitly declines to say which role sees `ingest_runs`; it only
confirms the application still needs its own login. Not resolved by that
entry or any other from that day.

**Decision, 2026-09-30:** everyone should see ingest health, not one role. No web route reads `ingest_runs` or `iem_ingest_rejects` today (checked 2026-09-30), so the work is to build one. `hail_app`'s provisional `SELECT` on those two tables (`sql/010`) stays. `api_pulls` and `api_call_log` have the same unanswered question and are not covered by this decision.

**Resolved 2026-10-01.** The Storm Days page shows ingest health to every signed-in role (decision log, "Ingest health on the Storm Days page"). **Residual:** `api_pulls` and `api_call_log` have the same unanswered who-sees-it question.

## 49. No cap on export date-range width

**Status:** resolved (residuals) 2026-10-01 a2afa9c

*(Resolved 2026-09-25: capped at 400 days, and the cost measured and accepted.
Reopen conditions are at the end.)*

`/export.csv` and the underlying `storms.py` queries accepted any start/end
range with no upper bound. Fine at the time; the 2019 wide-range timing finding
("Performance: the spatial join was the cost, not the hardware") shows what
an unbounded range can cost, and `report_zip_distances` fixes the specific
cause found, not the general absence of a limit.

**Larger since 2026-09-24.** The bulk match export (`/exports/matches.csv`,
decision log "CSV exports: three projections, suppression optional,
snapshot") took the same unbounded range, and it is the heaviest query the
web app runs: `storm_listing_matches` joined to `iem_data`, `report_types`,
`listings`, `properties`, `realtors` and two `dnc_list` joins, grouped per
listing, storm day and type. Three things make it worse than the storm-list
case:

- **Pressing Apply on `/exports` runs it.** `count_matches` wraps the whole
  projection in `count(*)`, so the count beside the download costs the same
  as the download, before anyone downloads anything.
- **Any signed-in account can ask for it.** The match exports are
  `login_required`, viewers included. Only the realtor list is
  sender/admin.
- **The file is built whole in memory.** `_csv_response` writes to a
  `StringIO` and returns it, so one wide request holds the entire file in a
  worker's memory.

A single one-report storm already matched 949 listings (item 106), so a
season could be many thousands of rows.

**Resolved 2026-09-25.** `_window_from_args` now clamps an explicit
`start`/`end` to `MAX_RANGE_DAYS` (400, which covers the 365-day claim window
with room to spare), after the `days` fallback and the reversed-pair swap so it
sees two valid, ordered dates. It clamps rather than returning a 400, so a
bookmarked URL keeps working, and the storm list, territory and `/exports`
flash "Range limited to 400 days." (the map, CSV and fragment routes share the
helper but don't flash, so a message can't surface on some later page). See
`docs/decision-log.md`, "Explicit date ranges are capped at 400 days".
Before that, only the `days` shortcut was checked against `DAY_RANGES`, and the
date pickers could ask for any span.

**Measured 2026-09-25, on `hail-dev`, read-only**, through `count_matches` and
`fetch_matches` themselves:

| Range | Rows | Count | Fetch | CSV | Python memory peak |
|---|---|---|---|---|---|
| 30 days | 4,765 | 0.29s | 0.35s | 1.2 MB | 11 MB |
| 90 days | 5,671 | 0.20s | 0.44s | 1.4 MB | 12 MB |
| 400 days | 5,671 | 0.19s | 0.40s | 1.4 MB | 12 MB |

The realtor count took 0.01s. **This is not a stress test:** `storm_listing_matches`
held only 11,575 rows, all from recent storms, so 400 days returned the same
rows as 90. Single user, warm cache, the dev VM (the OptiPlex was not
measured). It shows the cost today is trivial, and says nothing about a year of
real matches, which could be ten to a hundred times larger. Memory came to
roughly 2 KB of Python per row.

**Closed on that basis, deliberately, with no further code.** The three
concerns above stand as a description of how it *could* go wrong: the count
runs the whole projection, any signed-in account can trigger it, and the file
is built in memory. At today's size none is worth building against. Options
considered and not built: a hard row ceiling that refuses with a message
(the likeliest first step, about 50,000 rows), and streaming the response from
a server-side cursor (only worthwhile far above that).

**Reopen if** a count or fetch on `/exports` takes more than about 2 seconds,
or a range returns more than about 10,000 rows (twice today's largest). Rate
limiting for an internet-facing app belongs to the Cloudflare tunnel work
(item 110), not the application.

**Reopened 2026-09-30.** The cap shipped (`54cc7f2`, `MAX_RANGE_DAYS = 400` at `views.py:28`, clamp at `views.py:79-98`), but this item's own reopen conditions ("more than about 2 seconds", "more than about 10,000 rows") are now met. Measured 2026-09-30 on `hail-dev`, read-only, through `exports.count_matches` and `fetch_matches` as `hail_app` (all report types, 5.0 mi radius, DNC excluded, one run each, warm cache):

| Range | Rows | Count | Fetch |
|---|---|---|---|
| 30 days | 1,126 | 0.08s | 0.06s |
| 90 days | 11,868 | 0.57s | 0.62s |
| 400 days | 29,870 | 2.51s | 2.89s |

`storm_listing_matches` held 97,370 rows, against 11,575 when this item was closed. The options already considered (a hard row ceiling, about 50,000 rows; streaming from a server-side cursor) are the next step. Not built; documentation only.

**Resolved 2026-10-01.** Two query fixes (`NOT EXISTS` for the DNC filter, `::numeric` on the radius comparison): the 400-day count went from 2.51 s to about 0.72 s and the fetch from 2.89 s to about 1.4 s (decision log, "Matched-export latency"). The 2-second trigger is cleared. **Residual:** the row trigger is not (11,868 rows at 90 days, 29,870 at 400), so the row ceiling (about 50,000) and streaming remain the next options. Reopen if a count or fetch passes 2 s again.

## 66. Username convention and its security implications

**Status:** resolved (residuals) 2026-09-30

Usernames are free-form at creation (stripped and lower-cased, nothing else).
Settle the convention — and what it gives away, e.g. whether a username is
guessable from a name or email — before real staff accounts exist, since
changing it afterward means renaming live logins.

**When:** before real accounts get created.

**Resolved 2026-09-30** as residual risk accepted, not solved (`first.MILI`; decision log, "hail-dev reachable through a Cloudflare Tunnel; items 66, 103, 110 and 127 closed"). Usernames are identifiers, not secrets; the convention reduces guessability and does not remove it. Mitigated by Access, rate limiting and the item 127 fix.

## 89. "Matched, none in range" on a pulled storm has never been seen

**Status:** resolved 2026-10-01

The rule (`match_ran and pulled`) is verified only on its other half: a
never-pulled storm with empty runs stays "Not pulled". Showing the badge
itself needs a storm that was pulled but has no listings within the match
radius.

**When:** the first time a pull comes back with nothing in range, or with a
deliberate test.

**Closed 2026-10-01 on a reading, not a sighting.** `_label()` checks `matched` (rows in `storm_listing_matches`) before `match_ran and pulled`, and 5 zero-created re-runs in `match_runs` all belong to matched storms. The badge itself has still never been seen (decision log, "Matched, none in range": verified by reading).

## 90. An app-wide login check instead of per-route `@login_required`

**Status:** resolved 2026-10-01 605f0b5

A `before_request` that redirects any request without `g.user` to `/login`,
except `/login`, `/logout` and static files, would make `@login_required`
redundant everywhere and close the forgotten-decorator gap, as the admin
blueprint's hook already does for `/admin`. Considered and deferred
2026-09-23 (decision log, "`role_required` alone where a route needs a
role").

**When:** the next time a route is added outside the admin blueprint, or
Phase 6, before staff use the system.

**Resolved 2026-10-01.** `require_login` is registered after `load_current_user`, whitelists endpoint names (`main.login`, `static`), and the decorators stay (decision log, "App-wide login hook").

## 91. `_MATCH_SQL`'s all-types branch is dead

**Status:** resolved 2026-10-01 eacefc1

`match_storm` now requires `report_text`, so the
`%(report_text)s::text IS NULL OR …` branch in `_MATCH_SQL` can't run. It's
harmless, but it suggests an all-types path that no longer exists.

**When:** cleanup.

**Resolved 2026-10-01.** Branch removed; the same construct is kept where the filter is genuinely optional (decision log, "A dead branch removed from `_MATCH_SQL`").

## 103. Revisit the `testview` account before Phase 6

**Status:** resolved 2026-09-30

`testview` stays an active viewer for role testing (decision log 2026-09-24).
That's acceptable while the app is reachable only over Tailscale. When Phase 6
exposes it to staff, and possibly beyond the tailnet (item 63), deactivate it
or give it a strong password nobody reuses.

**When:** Phase 6, before the app is exposed.

**Resolved 2026-09-30.** `testview` is deactivated, not deleted, since it may own rows in `api_pulls`, `storm_listing_matches` and `match_runs` (decision log, "hail-dev reachable through a Cloudflare Tunnel; items 66, 103, 110 and 127 closed").

## 110. A Cloudflare tunnel for access from outside the tailnet

**Status:** resolved (residuals) 2026-09-30

Decided 2026-09-14 to revisit at Phase 6 (decision log, the Tailscale-access
entry): today the app is reached over Tailscale only, and `web` publishes to
`127.0.0.1:8000`. A tunnel is what puts it in front of real phones on real
networks, which is what the responsive pass (decision log, "CSS
responsiveness pass") was written for. It comes with its own checklist, none of
it done: who can log in (item 63), what to do with the `testview` account
(item 103), and how much a signed-in viewer can ask of the database (item 49).
The realtor CSV, every agent's email and phone in one file, is sender/admin
only for the same reason.

**When:** Phase 6, with items 63 and 103.

**Resolved 2026-09-30.** `dev.roofbrokersinc-weather.com` through a locally managed Cloudflare Tunnel with Access (one-time PIN) in front (decision log, "hail-dev reachable through a Cloudflare Tunnel; items 66, 103, 110 and 127 closed"). **Residual:** rate limiting is 5 requests per 10 s on `/login` with a 10 s block, which is burst protection only, since the free plan caps the period at 10 s; a paced attacker is unaffected. A second tunnel and hostname are planned for the production machine.

## 127. Login skips `verify_password` for an unknown user — timing-based username enumeration

**Status:** resolved 2026-09-30

`views.py:295-299` (297-299 when filed; the file has since shifted): when the username doesn't exist, `verify_password` is
never called, so an invalid username returns measurably faster than a valid
username with a wrong password (scrypt verification has a real, deliberate
cost; skipping it entirely is fast). An attacker can use response time
alone to enumerate valid usernames without ever seeing a different error
message. The "one message for every failure" comment is true of what's
*shown*, not of how long the response takes to arrive. Fix is a dummy hash
comparison on the no-user path, so both branches cost about the same.

**When:** before the app is reachable beyond Tailscale — group with items
110 (Cloudflare tunnel) and 103 (`testview`'s account), not the general
code-review backlog, since exposure is exactly what turns this from a
theoretical gap into a real one.

**Resolved 2026-09-30.** Every login path now costs one scrypt against `DUMMY_PASSWORD_HASH` (decision log, "hail-dev reachable through a Cloudflare Tunnel; items 66, 103, 110 and 127 closed"). **Do not cite the first timing run** (18.2 / 2.1 / 2.2 ms): those were CSRF rejections, not logins. Valid, all 401: 106.6 / 107.1 / 105.8 ms.

## 149. Match page: CSV download link at the top as well as the bottom

**Status:** resolved 2026-10-01 958cd67

The link was at the bottom of the matched-listings page since 2026-09-24 (`28a0195`); it is now also beside the counts at the top. Both carry the page's query string, so the download matches the filters on screen (decision log, "Match page: the CSV download link also at the top").

## 150. Header greeting by first name

**Status:** dropped 2026-10-01

**Declined, not deferred.** It would need `emp_fname` in the session, and widening the session for a display string was judged not worth it. The header keeps `session.user_name` (decision log, "Header greeting by first name: declined").

## 151. Export filenames: `report_text` reached `Content-Disposition` unsanitised

**Status:** resolved 2026-10-01 a4376cc

`report_text` came from the query string with only `/` and space replaced, so a double quote could close the quoted filename. Replaced with a whitelist (`_filename_label`) plus validation against `storms.fetch_report_types` (`_export_report_type`, 400 on an unknown type) on `/export.csv`, `/storms/matches.csv` and `/exports/matches.csv`. **Not changed:** `scripts/export_storm_zips.py:116` (command-line input) and the DNC upload's stored filename (admin only). See decision log, "Export filenames and report-type validation".

## 152. CSV formula injection in the exports

**Status:** resolved 2026-10-01 8ae4b7c

`_csv_response` builds files with `csv.writer`, which quotes a cell but does not
neutralise one that starts with `=`, `+`, `-` or `@`; a spreadsheet opening the
file may evaluate it as a formula. None of the text columns exported today
begins with one (checked 2026-10-01: `agent_name`, `agent_office_name`, `city`,
`property_address`, `agent_phone`, `agent_email`, `list_mls_number`, 0 rows
each), so there is no live hit. The text comes from RentCast and from DNC
uploads, which is outside our control. The usual fix is to prefix such a cell
with a single quote. Identified in review and never filed (decision log, "Export
filenames and report-type validation").

**When:** before the exports are used by anyone but the developer, or if the check
above ever returns a row.

**Correction 2026-10-01.** The check above covered only the columns the web match
exports write. The command-line `export_storm_zips.py` pairs export also writes
`iem_data.remark`, a free-text field from IEM, and 31 of its values begin with
`+`, `-` or `@` (`+SN AT OBSERVATION.`, `@ NWS BOULDER OFFICE.`), so there *was* a
live hit, though not in a web export.

**Resolved 2026-10-01.** `csv_safe` (`hailsys/formatting.py`) prefixes an
apostrophe to a string cell starting with `=`, `+`, `-` or `@`; numbers and
dates keep their types. `_csv_response` applies it, `/export.csv` now goes
through `_csv_response` instead of its own writer, and
`scripts/export_storm_zips.py` applies it. **Limits:** a tab or carriage return
at the start of a cell is not covered; a phone number written `+1 303 ...` would
gain an apostrophe (none exist today); and it must not be used on a CSV that is
loaded back into the database. See decision log, "CSV formula injection fixed".

**Deliberately not covered:** the three writers in `scripts/build_reference_tables.py`
(`report_types.csv`, `qualifiers.csv`, `sources.csv`) and the radar analysis writer
`docs/analysis/radar-verification-2026-09/reduce.py:45`. They are machine-read evidence
extracts, not files a person opens in a spreadsheet; the curated `planning/*.csv`
seeds they inform are what `load_reference.sh` loads with `\copy`. An apostrophe on
a machine-read file would become part of the data, so adding `csv_safe` there would
be a bug, not caution.

## 48. Badge CSS classes derive from `workstate.py` label strings

**Status:** resolved 2026-10-02 11dda0e

`storms.html` builds `badge-{{ row.work_state.state | lower | replace(' ',
'-') | replace(',', '') }}` — the CSS class is computed from the label text
itself, not a stable key. Renaming a label in `workstate.py` (`NOT_PULLED`,
`PULLED`, etc.) silently breaks styling with no error anywhere.

**When:** if a label ever needs to change wording.

**Added 2026-09-25:** `PULLING` ("Pulling...") gives the class `badge-pulling`
(`_status_cell.html` strips the dots). It first shipped with no rule in
`style.css`, so it showed as an unstyled badge, the failure this item
describes; `.badge-pulling` (purple) was added in the follow-up commit
(item 57).

**Resolved 2026-10-02.** Wider than this item described: the status cell did not
only derive the class from the label, it also branched on label text in six places
to decide which links to show, so a rewording would have changed the *actions*,
not just the colour. Each work state now has a stable key, a label and a CSS
class (`workstate.LABELS`, `CSS_CLASSES`); the template branches on the key and
the label is only displayed. The class names are unchanged, so `style.css` is
untouched. `tests/test_workstate.py` fails if a key lacks a label or class, a
class lacks a rule, or the template contains label text. See decision log, "Work
states have a key, a label and a CSS class".

</details>
