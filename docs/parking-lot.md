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

*63 items (counted 2026-10-19 across the whole file, below the Closed heading excluded): `open`, `open (reopened)`, `open (parked)` on something other than item 70, and `deferred`. Items filed after 153 sit after the permits section, not in this block.*

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

**Checked 2026-10-13.** The numbers are no longer unset: `email_cap_days` is a setting (14) on the
Admin page with its change history, and the send list drops a realtor emailed in that window
(`within_cap`), with a visible override on the send screen. That is the soft half. The hard half
is not built: the override is an application choice, and nothing in the database refuses a second
email to a realtor inside the window. **Still open** for that reason. **When:** before the override
is used on a real send, or if a duplicate email is ever reported.

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

**Status:** open; narrowed 2026-10-13 (the send and Constant Contact screens are tested; see the last paragraph)

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

**Checked 2026-10-13.** The suite is 493 tests. The send screens are covered by
`test_send_screen` (70), `test_send_jobs` (19) and `test_cc_sync_route` (11, `POST /cc/sync` and
`GET /cc/`), which sign in by writing the session and stub every route that could write. Still
with no test that reaches them: `/admin`, `/pull` and `/match`, `/search`, `/exports`,
`/territory`, `/activity`, `/account` and the storm pages. `/login` is touched only as a
signed-out redirect. Not closed, because the older half of the gap is the one that has already
cost bugs. **When:** before any of those screens is changed again.

## 55. Duplicate properties from RentCast address variants — one lot, two emails

**Status:** built 2026-10-13, one gap accepted (see the last status paragraph)

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

**Status, 2026-10-13: built, one gap accepted.** Send-time dedup (Part 1) is in
`hailsys/queries/sendlist.py`; the matcher, the match page and the CSV exports still treat every
`properties` row as distinct, by design (dedup stays at send time, above). Part 2 (unit plus
shared agent, 126 groups) stays deferred. What was built:
- **One event per house per Denver storm day.** Hits that share an `address_key` on one day are
  one event, whichever listing row they came through; their `match_ids` merge, so every match
  still gets its own `send_log` row. The event shows the address of the best-ranked listing
  (most recently seen, then lower `listing_id`). A NULL key never merges (287 of 25,362
  properties have none).
- **One agent per house.** Of the agents still eligible for one house on one day, only the
  best-ranked is emailed; the others are counted as `duplicate_address`. This runs after every
  other rule, so an agent another rule would have dropped (DNC, cap, and later the eligibility
  gate of item 189) cannot take the house away from one who can be emailed.
- **A house already emailed that day is dropped** (`key_emailed` in the SQL, also
  `duplicate_address`, checked after `already_emailed` and before the DNC rule), so the losing
  agent is not picked up by the next send. Failed sends do not count.
- **Measured 2026-10-13** on 50 duplicate groups (104 listing rows): 22 had no live matched
  listing on both sides, 22 were one agent on both rows (one email listing the same house twice
  before), 6 were two agents. On the real 2026-09-22 storm: one match dropped (`1104 Hallam
  Ave`, `andwgmz01@gmail.com` kept over `edersoto1@yahoo.com`, a same-day tie broken by the
  lower `listing_id`) and one merge (`4518 N Wordsworth Cir`, one agent, two listings). The
  list builds in 0.06 s (`properties_address_key_idx` exists).
- **Tests:** 19 new (17 on made-up rows, 2 on the real SQL: queue the Hallam winner in a rolled
  back transaction and check the twin is not picked up, and that a failed send does not cover
  its house); 15 mutations run, 14 caught.

**Accepted gap:** the same-day condition inside the `key_emailed` SQL is not covered (a mutation
dropping it passes), because proving it needs the same house on two storm days with a send in
between. **When:** the first duplicate email is reported, or any change to that SQL.

**Known limits:** a winner dropped later, at queue time (`unsubscribed_in_cc`, an unrenderable
email), leaves that house without an email in that send. The tie-break is arbitrary but stable.
No suffix merging, ever (decided above).

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

**Status:** open; reframed 2026-10-12: decide the sending domain (see the last paragraph)

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

**Reframed 2026-10-12, from the developer.** The production computer's sending domain will be
different from the company's domain (`roofbrokersinc.com`), and which domain it will be is not
decided. So this item is no longer only "waiting on RBI's answer": the first job is to decide
the sending domain. The `send.roofbrokersinc.com` plan and the one-shot NS ask above were
written for the company's domain and stand only if that domain is used. Whatever is chosen
still needs SPF, DKIM and DMARC, the Constant Contact self-authentication question, and the
single-domain-per-account limit above. Development carries on meanwhile on the dev domain
(`roofbrokersinc-weather.com`, item 170). **When:** before the production server sends
anything; the choice also decides what the DNS ask to RBI says, or whether there is one.

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

**Status:** mostly met (checked 2026-10-13); the postal address rests on item 183

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

**Checked 2026-10-13, against the template that exists now** (`hailsys/email/templates/hail_alert.html`,
`email_templates` has its first row since the 2026-10-09 rehearsal):
- **Non-deceptive subject:** met. The subject names the storm and the listing address.
- **Advertisement notice:** met. The footer reads "This is an advertisement."
- **Working opt-out:** met on the rehearsals: Constant Contact adds the Unsubscribe link and a
  one-click `List-Unsubscribe` header (seen in the received messages), and the unsubscribe sync
  brings opt-outs into `dnc_list` before every send.
- **Valid postal address:** not ours to build. Constant Contact adds it from the account
  profile (the trial's was seen in the footer). Whether it is RBI's, and where it is edited, is
  an open check on the paid account (item 183).

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

**Added 2026-10-20 (owner's direction):** the focus is hail. No wind analysis is planned for now, so this
item stays a watch item and nothing is built toward it. Radar work (items 205, 206, Phase 5b) covers hail only.

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

**Status:** built and rehearsed on `hail-dev` (checked 2026-10-13); open only for the eligibility
gate (item 189) and for the production box, which has none of it

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

**Phase B built 2026-10-09 (`20a6e66`, `hailsys/email/deliver.py`)** and **rehearsed on
`hail-dev` 2026-10-09 (`44a846a`).** `scripts/rehearse_send.py --send 1` made the first real send:
one email to `rbi.justyn+t2@gmail.com` for the dev-only seed rows of `sql/dev_rehearsal_seed.sql`
(2026-09-19 storm). Result: `sent_emails` 1 row (`scheduled_at` to `list_deleted_at` about 17
seconds), `send_log` 1 row `sent` with the activity id, `cc_sync_runs` 418 `ok` just before, list
deleted. The message arrived; headers show DKIM (both `roofbrokersinc-weather.com` and Constant
Contact's domain), SPF and DMARC passing, and a one-click `List-Unsubscribe`. The positive
address was an existing subscribed contact, so `permission_asserted` was `none`; the
create-a-new-contact path (`implicit`) has not been run against the real API, only against the
fake. The run also wrote the first `email_templates` row. Not yet done: the send screen (piece 5).
The rehearsal rows are permanent on `hail-dev` and nowhere else; the production box has none of
the Phase B code, the compose variables or the seed.

**Send screen (piece 5) built 2026-10-09** (`e2d9fab` preview, `3d72711` confirm and start,
`5645f90` history; `/send`, admin only; a mandatory allow-list; one global advisory lock
`send:run`; `hailsys/web/sendjobs.py` is the only door to the engine). The **UI send was
rehearsed the same day** with `sql/dev_rehearsal_seed_2.sql` (realtor `rbi.justyn+t3@gmail.com`,
dev only): `sent_emails` 883 `sent`, the activity id in the stored row matches
`X-Campaign-Activity-ID` in the received message, DKIM, SPF and DMARC pass, one-click
`List-Unsubscribe` present. Items 194 (sender choice in the UI) and 196 (pre-queue failures
leave no trace) came out of it.

**Status sync (piece 6) built 2026-10-09 (`53f8d56`)**: `sql/039_cc_status_runs.sql` (applied to
`hail-dev` only), `hailsys/constantcontact/statuses.py`, a **Check for bounces** button on
`/send/batches` (POST `/send/check`, admin only, read-only toward Constant Contact), and a
Bounced state in the history pages. It rechecks every email still `sent` within 14 days, one GET
per email. Code meanings, from Constant Contact's bounces report guide (not from a live
bounce): **B** and **S** address does not exist (also written to `dnc_list` as `hard_bounce`);
**D**, **F**, **X**, **Z** recorded as `bounced` only; **V** vacation autoreply is *delivered*,
so not a bounce; an unknown code is stored as X. **Known limits:** (1) **no complaints
endpoint was found**, so `complained` is never written automatically (`Z`, blocked, is the
nearest signal); (2) ~~the shape of a non-empty bounces response is unverified~~ **verified
2026-10-12** (see below); (3) one GET per email
runs inside the click, which is fine at rehearsal volume and counts against item 191's call
budget at real volume.

**Bounce rehearsal run 2026-10-12** (`sql/dev_rehearsal_seed_3.sql`, batch
`1f9452e5-a6e4-49a4-b171-09fd9cb263fd`, email 1808, to the made-up address
`rbi-bounce-test-7f3k9q@gmail.com`, `hail-dev` only). It exercised the create-a-new-contact path
against the real API for the first time (`permission_asserted` `implicit`). The bounce was reported
about 7 minutes after sending and the check recorded it (`cc_status_runs` 132, `send_log` `bounced`).
The raw record carries `email_address` and `bounce_code` as the parser expects, plus `contact_id`,
`campaign_activity_id`, `tracking_activity_type`, `first_name`, `current_email_address` and
`created_time` (all ignored), and `_links` is `null`. **The code was X**, a real X and not the
parser's fallback, for a mailbox that does not exist, so the B/S guess in the guide was wrong for
this case and nothing reached `dnc_list`: item 198.

**Resume (piece 5d) built 2026-10-12.** A **Resume queued emails** button on `/send/batch/<id>`
(POST `/send/batch/<id>/resume`, admin only, with the count guard that `/send/start` has). It runs
`deliver.run_resume` in a background thread under the same `send:run` lock: `check_resumable`
first, which refuses a batch with nothing queued or whose oldest queued email is more than 7 days
old (`RESUME_MAX_DAYS`); then the unsubscribe sync, so anyone who unsubscribed since queueing
reaches `dnc_list` before the per-email check; then `deliver_batch`. Both `queued` and
`in_progress` emails are resumed; `needs_review` never is. 28 tests; five mutation checks, all
caught. **Known limits:** (1) a failed sync on resume leaves only a log line and a failed
`cc_sync_runs` row, no trace on the history page (the same class as item 196); (2) the daily call
budget is still manual, since the person clicking chooses the number (item 183); (3) a queued
email past 7 days has no way out, because a "discard the rest" action has not been built and
queued rows block their matches from any new send (item 199).

**Rehearsed against the real API 2026-10-12** (`sql/dev_rehearsal_seed_4.sql`, batch
`6f593ef4-afe9-4d98-84d2-c80a6a19af9d`, `hail-dev` only): `--send 1` of two emails sent `+t4` and
left `+t5` queued; **Resume queued emails** in the browser then sent `+t5`, after unsubscribe sync
run 1604, with its own campaign and no second campaign for `+t4`. Both messages arrived, Constant
Contact's `sends` reports show both, and the `bounces` reports are empty. The running `web`
container had to be restarted to pick up the new route.

**Checked 2026-10-13: the three known limits of 5d above are superseded.**
- The daily call budget is no longer manual: every attempt is counted and the engine stops at the
  limit (item 191). The status sync's one GET per email is counted too.
- A queued email past the resume window can now be discarded (item 199, rehearsed 2026-10-13).
- A failed sync on resume still leaves only a log line and a failed `cc_sync_runs` row: filed as
  item 200.
Also built since: one email per house, deduped on `address_key` (item 55).

Still open on this item: the eligibility gate (item 189), and the production box.

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

**The allow-list goes away with the gate (developer, 2026-10-13).** The mandatory allow-list on
the send form is a stand-in for this item: it exists because nothing else says who may be emailed.
When the gate exists, the form drops the allow-list and the preview becomes the list: the
realtors that pass every rule, including having a recorded basis. With no row in the basis table
no one is eligible, so an empty table sends nothing. `queue_batch`'s required `allowed_emails`
argument goes at the same time.

**Requirement from item 55 (2026-10-13):** the gate must be a per-hit rule inside
`sendlist._reason`, not a check after the list is built. The one-agent-per-house dedup runs
after every per-hit rule, so with the gate in `_reason` it prefers the agent who is in our own
database. A gate applied later could remove the winner and leave the house with no email.

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

**Status:** built 2026-10-13 (found 2026-10-09, building Phase B); the reset time of the
daily limit is still unverified (item 183)

An email costs about 10 calls (item 180) and Constant Contact allows 10,000 a day, so a
1,400-realtor day cannot finish. `deliver_batch(max_emails=N)` stops after N emails and leaves
the rest `queued` for an explicit later run, but N is a number a person types, not a count of
calls made today. Nothing records calls, and the reset time of the daily limit is unverified
(item 183).

**When:** before a send larger than about 900 emails (9,000 calls), and before the send screen
offers a "send all" button. Until then the screen must pass a `max_emails` it has checked by hand.

**Built 2026-10-13.**
- **Count.** `api.request` records every HTTP attempt in `cc_api_calls` (`sql/040`; method and
  the first path segment only, never an id; append-only, `hail_app` has INSERT and SELECT only)
  *before* making it. Retries, 429s and status polls are included. If the insert fails the call
  is not made, which is safe because nothing was sent; recording after would risk failing
  after a campaign POST succeeded. `hailsys/constantcontact/budget.py` holds `DAILY_LIMIT`
  (10,000), `HEADROOM` (200), `used()`, `remaining()` and `exhausted()`.
- **Window.** The last 24 hours, not a calendar day: the reset time is unverified, and a rolling
  count is never lower than any calendar-day count, so it can be too cautious (the second
  morning) but cannot overshoot.
- **Engine.** `deliver._deliver_all` stops starting new emails once `used >= limit - headroom`
  and returns `budget_stop: True` with the number left; they stay `queued` for the resume button
  on a later day. The email cap (`max_emails`) is checked first. A 429 from Constant Contact
  still stops the batch as before.
- **Screens.** `/send/`, the confirm page and a batch page with queued emails show "calls in the
  last 24 hours: N of 10000, about M more usable". The confirm page also warns (it does not
  block) when the estimate exceeds what is usable. The figures reach the screens through
  `sendjobs.calls_budget()`, so the screens still never import the engine.
- **Tests:** 31 new (6 in `test_cc_api`, 9 in `test_cc_budget`, 5 in `test_send_deliver`, 8 in
  `test_send_screen`, 3 in `test_send_jobs`). Every test that could reach `request` stubs the
  recorder, because the table cannot be cleaned. 24 mutations run, 24 caught (13 on the count
  and the engine, 11 on the screens; one confirm-page gap, the figure itself, was found and
  closed with a test).

**Still open:**
- **The reset time** of Constant Contact's daily limit (item 183). When it is known, change
  `WINDOW` in `budget.py` to a calendar day in the right timezone.
- **`CALLS_PER_EMAIL = 10` is a floor.** The polls in `wait_for_activity` and `wait_until_done`
  each count, so a slow activity costs more. The real figure per email can now be read from
  `cc_api_calls` after the first real send; revisit the estimate then.
- **Counting starts at `sql/040`.** Calls made before it was applied are not in the table.
- **Not on the production OptiPlex:** `sql/040` is applied on `hail-dev` only, like the rest of
  the Phase B code and `sql/039`.
- **No "send all" button exists,** so the trigger "before the send screen offers one" still
  stands; the budget stop makes it safe to add.

## 192. The send screen needs a sender identity in `.env` (`CC_FROM_NAME`, `CC_FROM_EMAIL`)

**Status:** resolved on `hail-dev` 2026-10-09 (`44a846a`); **not applied to the production box.**
The three `CC_*` sender variables and the five `EMAIL_*` variables are in the `web` service's
`environment:` block of `docker-compose.yml` (non-secrets live there, not in `.env`), read by
`deliver.sender_from_env()` and `render.EmailSettings.from_env()`; both load in the running
container and the rehearsal send used them. The From address worked as a verified sender on the
dev trial account. Making the sender a UI choice is item 194. **Superseded 2026-10-14:** these
values are now in the database (`settings`, `email_images`) and edited on the admin page; the
environment variables and `sender_from_env()` / `from_env()` are gone (item 203 piece 1, decision log
2026-10-14). Original finding below.

(found 2026-10-09)

`deliver_email` takes `sender` = `{from_name, from_email, reply_to}` and passes it to
`campaign_fields`. Nothing reads it from configuration yet, and the From address must be a
verified sender in the Constant Contact account (item 183). Neither variable is in `.env` on
`hail-dev` or on the production box.

**When:** when the send screen is built (piece 4, the dev-trial rehearsal needs it first).

## 194. The sender identity is hard-coded in `docker-compose.yml`; a user should be able to choose it in the UI

**Status:** open, **partly done 2026-10-14**: the sender (`from_name`, `from_email`, `reply_to`) and the
`EMAIL_*` values are no longer in `docker-compose.yml`; they are in `settings`, edited by an admin on the
admin page with changes in `settings_history` (item 203 piece 1). Still open: a **choice** of sender per
user or per send, and recording the sender with the send. Original text below.

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

## 195. A mutation check committed two false rows to `cc_sync_runs` on `hail-dev`

**Status:** resolved 2026-10-09 (rows removed by the developer through the loader; table and guard
verified afterwards)

While proving the `/send` preview's "writes nothing" test, Claude mutated a throwaway copy so the
preview committed an `INSERT INTO cc_sync_runs`, and ran it with the copy mounted over the real
`web` container. The container's database is the real `hail-dev` one, so the test's requests
committed **runs 459 and 460**: `status = ok`, `fetched = 0`, `watermark` NULL,
`triggered_by` the system user, 2026-10-09 19:12:49 UTC. The test did catch the mutation.

**Effect while they existed:** the Admin Constant Contact page would have shown run 460 as the
last good sync, and the send gate (an `ok` sync within 15 minutes) would have been satisfied for
15 minutes by a sync that never ran. The next real sync's watermark would have moved 6 minutes
later, which the 1-day overlap in `unsubs.OVERLAP` absorbs. No send was made in that window.

**Removed** by a single transaction as the loader's superuser: disable `trg_cc_sync_runs_guard`,
delete exactly run ids 459 and 460 (only if they matched the false-row signature), re-enable the
trigger, and abort unless 9 rows remained. Verified after: 9 rows, latest `ok` run is 418 (the
real rehearsal sync), trigger enabled (`O`), and a superuser `DELETE` of run 418 is still refused.
This was an exception to the table's "never deleted" rule, made for rows that were never a real
sync; it is recorded here and not as a precedent.

**Lesson:** a mutation check whose mutation *writes* must run inside a transaction that is rolled
back, or against a scratch database, never against `hail-dev` through the app's own connection.
`cc_sync_runs`, `sent_emails`, `send_log` and `email_templates` are append-only, so a mistake there
cannot be undone with a normal `DELETE`.

**When:** none, resolved. Revisit if a test harness for the web app gets its own database.

## 196. A send that fails before it queues anything leaves no trace except a log line

**Status:** open (found 2026-10-09, planning the send history page)

`sendjobs._run` runs `deliver.run_send` in a background thread and catches any exception, logging
`event=send_job_failed`. If the failure comes before `queue_batch` commits (the unsubscribe sync
raising, a refusal for a stale sync, a recipient lookup failing), no `sent_emails` row exists, so
the history pages (`/send/batches`, `/send/batch/<id>`) have nothing to show. The sync case is
softened by showing the latest `cc_sync_runs` row on the history page; any other early failure is
visible only in the `web` container log. A person who clicks Send now and sees an empty history has
no way to tell "nothing was eligible" from "it failed".

**Fix when needed:** a small `send_runs` table written by the thread (started, finished, status,
fixed error text, batch id once it exists), like `cc_sync_runs`, and shown on the history page.

**When:** before anyone other than the developer sends from the UI, or the first time a send
fails silently in practice, whichever is first.

## 197. A mutation check let a GET reach the real bounce check and write `cc_status_runs` on `hail-dev`

**Status:** resolved 2026-10-10 (test fixed; the row was kept by decision)

While mutation-testing the new `/send/check` route (piece 6), Claude changed the route to accept
GET. `test_the_check_is_post_only` sent `GET /send/check` without stubbing
`statuses.run_check`, so under the mutation the request ran a real bounce check on `hail-dev`:
**`cc_status_runs` run 86**, `ok`, checked 2, bounced 0, `triggered_by` 2, 2026-10-09 23:31 UTC,
and two read-only GETs to Constant Contact. Nothing else changed: `send_log` rows 584 and 883
still `sent`, no `hard_bounce` rows in `dnc_list`, other row counts the same. The test did catch
the mutation. It is the same class as item 195: Claude checked the route tests for unstubbed
writers beforehand and wrongly judged them all stubbed.

**Row 86 is kept.** Unlike the false rows of item 195 it is a true record of a check that ran and
found nothing, and deleting from an append-only log needs the guard trigger disabled. It was not
a human click; this entry is the explanation.

**Fixed:** `test_the_check_is_post_only` now patches `statuses.run_check` and asserts it is not
called, so a wrongly allowed GET cannot reach the real check. Claude now lists every route and
function a mutation can reach, and confirms each is stubbed, before running it.

**When:** none, resolved. Revisit with item 195: if the web tests get their own database, both
stop being possible.

## 198. A nonexistent mailbox bounced as code X, so it was not suppressed

**Status:** open (found 2026-10-12)

Bounce rehearsal, email 1808 (item 180): Constant Contact reported `bounce_code` X (not B or S)
about 7 minutes after the send to `rbi-bounce-test-7f3k9q@gmail.com`, a made-up Gmail address.
`statuses.py` writes `hard_bounce` to `dnc_list` only for B and S, so X left `send_log` `bounced`
and `dnc_list` untouched (0 rows). One data point: it is not known whether X is how Gmail's
rejection is always reported, or what other providers return.

**What the send list does with a bounced row** (read in `hailsys/queries/sendlist.py`, 2026-10-12):
`send_status <> 'failed'` counts as sent, so `bounced` blocks that same *match* for good
(`already_emailed`) and blocks that *realtor* for the 14-day cap (`within_cap`). Nothing else.
After 14 days a new match for the same address (a new storm, or another of the agent's listings)
is eligible again, and gets emailed and bounces again. The immediate pre-send `dnc_list` check in
the engine is also `dnc_list` only (not re-read this session).

**When:** accepted for now (developer decision, 2026-10-12): not enough data to design a rule.
Revisit after the first real send, once the bounce check has run on it. Look at which codes come
back, and whether any bounced address is eligible for a second email. Settle it before a second
send goes out.

## 199. A queued email past the resume window has no way out

**Status:** built and rehearsed 2026-10-13 (found 2026-10-12, with piece 5d of item 180); the
`in_progress` limit below is open

`deliver.check_resumable` refuses a batch whose oldest queued email is more than 7 days old.
Queued `send_log` rows count as emailed in `sendlist` (`send_status <> 'failed'`), so those
matches are blocked from every new send, and the realtor stays inside the 14-day cap until the
rows age out. Nothing on the history page turns them into `failed`. A discard action would do
what `deliver._fail` does for a refused email: mark the remaining `queued` rows `failed` with a
reason, which makes the matches eligible again.

**When:** before the first real send, or the first time a send is left partly queued, whichever
comes first. A person would click it, never a timer, and it needs its own guard, test and
admin-only route.

**Built 2026-10-13.** `deliver.discard_queued` / `count_untouched`, `sendjobs.discard_queued`
(takes the global `send:run` lock, so it can't overlap a send or resume) and
`POST /send/batch/<id>/discard` (admin only, CSRF, a required confirm box, and an `expected`
count that must still match). The button sits on the batch page under the resume form.
- **Allowed at any time, not only past the 7-day window.** It only touches emails that are
  provably untouched, so a window adds a rule and no safety.
- **Provably untouched** means state `queued` with no `cc_list_id`, `cc_campaign_id`,
  `cc_activity_id`, `scheduled_at`, `list_deleted_at` or `error_detail`. One statement marks
  both `sent_emails.error_detail` and the `send_log` rows `failed` ("Discarded by user N on
  DATE"), so the matches are eligible for a new send.
- 31 tests; 16 mutations run, 14 caught. The two that can't be: dropping the `scheduled_at` /
  `list_deleted_at` conditions (the `sql/037` CHECK constraints already require an activity id
  and a list id first) and dropping the explicit unlock (closing the connection frees the lock).

**Rehearsed 2026-10-13 on dev.** `sql/dev_rehearsal_seed_5.sql` (+t6, +t7, TEST-REHEARSAL-7/8) and
`rehearse_send.py --send 0` queued batch `6d7912e1-...` (2 emails, nothing scheduled: one
Constant Contact read for the sync and contact lookups, no writes there). After a `web` restart,
the discard form showed on the batch page; ticking the box and clicking Discard set both
`send_log` rows and both `sent_emails.error_detail` to "Discarded by user 2 on 2026-10-13" in
one update, status `failed`, no Constant Contact id touched, and the send preview listed both
realtors as eligible again. The refusal paths (stale count, send running) are covered by tests
only, not clicked.

**Known limit, open:** an `in_progress` email (a list or campaign exists, the schedule call
unknown) is never discarded. Marking it `failed` could make its matches eligible again and
email the agent twice. Past the window the resume refuses it too, so it stays stuck until
someone checks Constant Contact by hand. **When:** the first time a real send leaves one stuck;
the fix would be a look at the campaign's status in Constant Contact before deciding.

## 200. A failed unsubscribe sync on a resume leaves only a log line

**Status:** open (found 2026-10-12 with piece 5d of item 180; filed 2026-10-13)

`sendjobs._run_resume` runs `deliver.run_resume`, which syncs unsubscribes before it sends
anything and raises if the sync fails. Nothing has been sent in that case, which is the safe
outcome. But the only trace is the log line `event=resume_job_failed` and a `failed` row in
`cc_sync_runs`; the batch page still shows the emails queued and says nothing. A person who
clicked Resume sees "Resume started", and then nothing happens. The same class as item 196
(a send that fails before it queues anything).

**When:** with item 196, when the history page gets a way to show a job that failed before
it started. Until then the person has to look at the log.

## 201. Web UI cosmetic pass: make each page more user-friendly and better looking

**Status:** open (filed 2026-10-13, by developer request)

The developer will go through the pages one at a time and list what to change. No page has been
reviewed yet, so there is no list. Related small items already filed: 108 (match-page column
widths), 109 (the match page on a phone), 112 (the territory layout on tablets), 113 (CSS
leftovers) and 156 (four light-mode colour pairs below WCAG AA).

**When:** after the send-screen work of the 2026-10-13 discussion (template list, template
builder, drip campaigns) has settled, so a page that is about to change is not polished twice.

## 202. Template list, and a template choice on the send page

**Status:** built 2026-10-13 (filed the same day, by developer request); browser check pending

Today there is one template: `hail_alert`, the file in `hailsys/email/templates/`.
`queue_batch` always calls `templatestore.ensure_current`, and the send page shows that one
sample, which is clunky once there are several. Wanted:
- **A list of rows:** name, description, subject, report type, version, created by and date,
  last used, and how many sends used it; 10 to a page, with next and previous.
- **A template choice** on the send form, carried through `run_send`, `queue_batch` and the
  engine as a `template_id` (today the engine has no such argument).
- **A `description` column** on `email_templates`. The guard of `sql/030` freezes every other
  column, so the migration replaces the guard to leave `description` editable the way
  `is_active` is.

**When:** first of the 202 to 204 group (items 203 and 204 both make templates plural).

**Built 2026-10-13.**
- **Migration `sql/041`:** `email_templates.description` (300 characters at most). It can be
  edited **only until the template has been used in an email**; the guard of `sql/030` was
  replaced to refuse a change once a `sent_emails` row refers to the template (a queued, sent or
  discarded email counts as use). The built-in's description was set in the migration. A new
  version copies its predecessor's description. `sql/guard_test.sql` passes 29 of 29.
- **The engine takes a template.** `templatestore.load` gives the built-in from its files (no
  row is written by a preview; `ensure_current` still saves it when a send is queued) and any
  other name from its active row. `template_name` flows from the send form through
  `start_send`, `run_send` and `queue_batch`; an unknown or retired name is refused before
  anything is written.
- **The list is on the send page,** in the same form as the storm days, so one Preview picks
  both: a radio per template, with name, description, rendered subject, version, creator and
  date, last used and sends; 10 to a page (`templatelist.PAGE_SIZE`, a constant, not a setting).
  Previous and Next are submit buttons in the form, so the ticked days and the choice survive a
  page change; the choice is also carried in a hidden field for when it is on another page. A
  radio click wins over the carried value. Each row expands to its own sample, rendered with the
  first realtor on the list or, before a preview, stand-in storm data. An unknown name falls back
  to the built-in with a message; a fresh database still offers the built-in. The built-in is
  flagged when its files differ from its saved version.
- **The description editor:** a text box and Save on each row of a template nobody has used, in
  a separate POST form (a GET form cannot hold it), admin only. The chosen template, the days and
  the page survive the save.
- **The choice goes through review, confirm and start** (a hidden field; confirm shows the name
  and description), and a refused form sends the choice back to the page.
- **Tests:** 64 new (2 on the description copy, 5 on `load`, 7 on `set_description`, 4 on
  `queue_batch`, 1 on `run_send`, 10 on the list query, 9 on the description route, 26 on the
  send page and its form); 46 mutations run on the new code, 45 caught, and the 1 left is an equivalent mutant (the
  route's own `commit()`, which leaving the `with` block already does). Gaps the first runs found
  in my own tests were closed, not accepted.

**Not built (as agreed):** a "show retired" toggle (later); a version guard (a template changing
between the preview and the start), which waits for item 203, since nothing can create a new
version from the UI before then.

## 203. A template builder in the web UI, and email images managed from the UI

**Status:** open. **Piece 1 done 2026-10-14** (`sql/042`; `email_images` and the Email section of the admin
page; the contact email, schedule link and sender moved from the environment to `settings`; decision log
2026-10-14). **Pieces 2 and 3 done 2026-10-15** (`hailsys/email/blocks.py`, `sql/043`,
`templatestore.save_blocks`; decision log 2026-10-15). **Piece 4 done 2026-10-15** (the admin-only
`/templates` editor: list, new, edit, preview, save, retire; `hailsys/web/emailtpl.py`, `blockform.py`,
`preview.py`, `templates.js`; cosmetics in item 221). **Piece 6 done 2026-10-15** (`sql/044`,
`hailsys/email/testsend.py`: "Send a test to <your address>" in the editor; one live test on the dev
trial: accepted, inbox, campaign deleted; decision log 2026-10-15). **Piece 5 done 2026-10-16**
(formatting help: the guide, the toolbar, error positions that quote the place; the two-column layout
of item 221; commits `1efca54`, `b290f51`, `7bb85d1`; decision log 2026-10-16). **All six pieces are
done**; what remains is follow-up items 217, 220 and 225, and the developer's own feedback from using
the editor. (filed 2026-10-13, by developer request; shape agreed the same day)

Staff should be able to create a template in the UI, with an image that is already in Constant
Contact, bold, italic and coloured text, and the other parts of the current template.
**Decided 2026-10-13: a block builder.** A template is an ordered list of blocks that the system
renders into the same email-safe table HTML as today; a person never writes markup. Rejected:
a free-HTML rich-text editor (its output breaks in Outlook and Gmail, loses the conditionals in
`hail_alert.html`, and invites template injection) and Constant Contact's own editor (it will
not return the HTML, and `sent_emails` must hold the exact HTML, item 180).
- **Blocks for v1:** image, heading, text paragraph, button, divider, the storm data panel and
  the listings table (both system-rendered), badges, and the footer.
- **Rich text inside a paragraph:** bold, italic, a colour from a palette and links, converted
  server-side to inline-styled spans from an allow-list; anything else is stripped.
- **Locked blocks:** the advertisement notice and the contact line cannot be removed (item
  120). Constant Contact adds the unsubscribe and the postal address.
- **Versions:** editing saves a new version that supersedes the old one, as `email_templates`
  already requires. The sandboxed renderer and its fixed variable list (`ALLOWED_VARS`) stay
  the authority; the builder generates the template, so no one writes template syntax.
- **Images:** a named-image table (`email_images`: unique name, https URL, alt text, who and
  when, retired not deleted; free-hosting domains refused as the renderer already does) with an
  "Email images" section on the admin page. No Constant Contact calls; a picker that lists the
  Constant Contact library can come later on an explicit button, since each call counts against
  item 191's budget. The same admin section takes the other values that are environment
  variables today (`EMAIL_*`: the logo, the badges, the contact email, the schedule URL).
- **A "send me a test" button** using Constant Contact's test-send endpoint (the spike found it
  answers 204 and does not move the trial counter).
- **Out of v1:** drag and drop, free layout, styling beyond the palette, image upload.
- **Audience:** admin only.
- **Pieces, in order:** (1) `email_images` and its admin section; (2) the block model and
  renderer, seeded with the current template; (3) storage and versioning; (4) the editor form
  and preview; (5) the rich-text sanitizer (the security-sensitive part: many XSS tests);
  (6) the test send.
- **Changed 2026-10-15:** the core of piece 5 (the inline markup converter and its allow-list) moved
  into piece 2, because the current template needs inline formatting (bold navy, a styled link) for
  the block-built version to render identically. Piece 5 is now the editor's side of it (formatting
  help, error display; done 2026-10-16). The palette has seven colours (`slate` was added: the "since 1992" line and
  the secondary link use it).
- **Known limit:** email-client rendering cannot be tested here beyond sending test messages;
  the constrained blocks are what keep it safe.

Related: 120 (the footer), 194 (the sender identity, same idea), item 16's merge-field
vocabulary (`ALLOWED_VARS` is it).

**When:** after 202. Piece (1) can start at once, since it is standalone.

## 204. Drip campaigns: an initial email, then follow-ups

**Status:** open, **parked 2026-10-20 by developer decision: only piece 7's dev rehearsal remains** (filed 2026-10-13, by developer request; the owner asked for it). **Plan agreed
2026-10-16**, Level 1. **Piece 1 (the RentCast probe) done 2026-10-16**; the check method changed
as a result (below). **Pieces 2 and 3 done 2026-10-16** (`sql/045`, `sql/046`, the by-id client, the check log writer, the quota sum, the cheaper-method choice). **Piece 4 done 2026-10-19** (`hailsys/queries/dripfollowup.py`, `queue_batch(send_list=...)`). **Piece 5 done 2026-10-19** (`sql/047`, `hailsys/rentcast/dripstep.py`, `hailsys/email/dripstep.py`, `sendjobs.start_drip_step`). **Piece 6 done 2026-10-20** (commits `313df2e` to `8021288`; `/campaigns`, the follow-up part of the `/send` confirm page, the due banner). Piece 7 (these docs and a dev rehearsal) is next. **No campaign has been run yet**: nothing in `sent_emails` came from one.

**The ask:** an initial email about a storm, then N follow-ups with a chosen number of days
between each (1 to 2, 2 to 3, 3 to 4). Each follow-up must re-check, with RentCast, that the
listing is still active, so a sold property's agent is not emailed. The developer expects the
RentCast call count to stay flat or fall over a campaign.

**Decided 2026-10-13: Level 1 first, and it must be truly one click.**
- **Level 1.** A campaign stores its steps, gaps and a template per step, and its recipients
  are fixed at step 1 (so there is no allow-list to type). When a step is due, a banner says so
  ("Follow-up 2 is due for up to N recipients"); one button runs the whole step in a background
  job: a fresh RentCast check, the unsubscribe sync, the send list, queue, send, within item
  191's call budget. **Level 1 keeps both `CLAUDE.md` rules intact** (nothing sends
  automatically; no automatic RentCast call), because a person's click starts every step.
- **Level 2** (a daily job runs due steps itself) only on need. It would supersede two
  non-negotiable rules in `CLAUDE.md` by a written decision, behind a settings switch
  (`auto_followups`, default off), with a global kill switch, a per-campaign pause, a step and
  volume cap, failure-closed behaviour (no fresh RentCast check means no send) and an automatic
  resume across days. **Not before items 189 and 129**: repeat mail to an unvetted list, or to
  someone who unsubscribed in the possibly missing nine months, is worse than one mail.

**Each step re-checks before sending:** the unsubscribe sync and the DNC list, that the listing
was seen within the freshness window (the existing `stale_listing` rule), no bounce, and the call
budget. The 14-day cap has to apply between campaigns, not within one.

**Open questions:**
- **RentCast.** A pull is per zip, so a shrinking recipient list does not shrink the cost
  proportionally. Whether RentCast offers a cheaper lookup per listing is not checked; read its
  documentation and `hailsys/rentcast/client.py` before promising "flat or falling".
- **The new chain.** The click must run pull, then send, as one background job; pulls and sends
  are separate jobs today.
- **Volume.** About 10 calls per email: 440 recipients across 3 steps is about 13,200 calls, more
  than one day, so a step can span two days. In Level 1 the person resumes on the second day.
- **Data model.** Something like `drip_campaigns` plus steps linked to each step's `batch_id`.

**Plan, 2026-10-16 (Level 1).**

*Found while planning.* RentCast documents a lookup by id, `GET /v1/listings/sale/{id}`, returning
`status`, `lastSeenDate` and `removedDate`; the id is the one stored as `rentcast_id`. Its page says
nothing about billing, so one call per request is assumed. A zip pull costs one call per zip per 500
listings however short the recipient list; a by-id check costs one call per listing, so it is cheaper
only while the recipients are few. `send_log` has no unique key on `match_id` (the "already emailed"
rule is in `sendlist.py`), so a second send for a match is allowed by the schema and a follow-up needs
its own list builder, with the 14-day cap exempting the campaign's own earlier steps. RentCast usage
is summed from `api_call_log`, which needs a `pull_id` and a zip, and the pull estimate reads the same
table, so by-id checks logged there would make a zip look recently pulled.

*Decided with the developer 2026-10-16.*
- **The check (changed 2026-10-16, after the probe):** the cheaper of two methods, chosen by the system
  and shown with both costs before the click. *By id* (one call per distinct listing) when the listings
  to check are fewer than the zips involved; otherwise a *zip pull* (status Active, today's code, logged
  to `api_pulls` and `api_call_log` with no storm date, so the one-running-pull-per-storm index does not
  apply; no matching is run). A listing counts as active only if RentCast says Active with no
  `removedDate` and `lastSeenDate` is inside the freshness window (a zip pull: it came back in the
  Active results); a failed, unreadable or 404 check, or a failed zip, excludes the listing (nothing is
  sent on a guess); a RentCast auth failure ends the job before any send. *(The first decision was by id
  only; see the probe results.)*
- **Its log:** a separate append-only table, `drip_listing_checks`, for by-id calls only; the quota sums
  it with `api_call_log`.
- **Tables (`sql/045` adds the two settings; `sql/046` the tables):** `drip_campaigns` (only `status` changes: active, paused, cancelled,
  complete), `drip_steps` (frozen after creation except `batch_id`, set once), `drip_recipients`
  (the step-1 list, never grows; `stopped_at` and a reason: unsubscribed, bounced, no_active_listings,
  manual, replied), `drip_listing_checks`, and `drip_step_pulls` (the zip pulls a step spent calls on). A campaign's matches are the `send_log` rows of its
  step-1 batch. Guard tests in `sql/guard_test.sql`.
- **Limits are admin settings**, in `settings` with changes in `settings_history`, bounds in the
  database as for the radii: most follow-ups per campaign (default 3, 0 to 10; 0 turns campaigns off)
  and longest gap in days (default 7, 1 to 30). A change affects campaigns started afterwards.
- **Starting:** an optional "Make this a campaign" part on the existing `/send` confirm screen: number
  of follow-ups, a gap and a template for each. Step 1 sends as today.
- **A step is due** on the previous step's last send date (Denver) plus the gap, and never expires; the
  screen says how late it is. A banner on `/send` is display only. The step screen shows, with no
  RentCast call on page load, the listings to be checked (about that many calls), the quota (warn and
  allow), the expected emails and the Constant Contact budget; one button, "Check listings and send",
  runs the chain under `sendjobs`' lock: check, unsubscribe sync, follow-up list, contacts lookup,
  `queue_batch`, `deliver`, setting `drip_steps.batch_id` in the queue's transaction. A job that dies
  before the queue can be re-run (the calls are logged); after it, the existing Resume takes over.
- **Replies are invisible to the system** (they go to `settings.reply_to`, forwarded to Gmail).
  Level 1: a per-recipient "Stop for this recipient" on the campaign page, and a paste box that stops
  many addresses at once. Stopping follow-ups does not add anyone to `dnc_list`. Reading a mailbox is
  item 226.
- **Rules kept:** every step starts with a click; RentCast calls happen only in that click's job and
  are logged; the suppression checks are the existing ones; the 14-day cap applies between campaigns.

*Pieces.* (1) a read-only probe of the by-id endpoint, three calls (done); (2) `sql/045`, `sql/046` and guards (done);
(3) `get_sale_listing`, the check log, the quota sum and the cheaper-method choice (done); (4) the follow-up
list builder (done); (5) the chained job (done); (6) screens (done); (7) docs and a dev rehearsal.

*Piece 4 decisions, 2026-10-19* (developer: all as recommended).
- **Cap:** applies only to emails outside this campaign (another send or campaign inside the cap window
  holds the recipient back from that step; the campaign's own steps do not count). The step has an override
  like `/send`, off by default. A held-back recipient misses that step and stays in the campaign.
- **Storm age:** `match_max_age_days` is not applied to follow-ups; the listing rules guard the content.
- **Stopping:** the job, not the builder, writes the stop. `bounced` and `unsubscribed` for those recipients;
  `no_active_listings` only when every listing is definitely dead (inactive or not found) and none is
  unconfirmed or held back. A check error only skips the recipient for that step.
- **Dead listings:** a listing found inactive or not found in an earlier step is not checked again and stays
  out (fewer calls).
- **Agent changed (item 227):** the listing is dropped; see that item.
- **A zip pull cannot be judged by `list_last_seen`.** That column is RentCast's `lastSeenDate`, not our pull
  time: against our first-pull times it trails by up to 65 hours (99% within 38) across 26,289 listings, so
  no window separates a sold listing (absent from the pull, row unchanged) from a live one at a 1 to 3 day
  gap. Instead the job (piece 5) writes one `drip_listing_checks` row per listing after a zip pull:
  returned listings are classified by `dripcheck.classify` (0 calls, the pull's calls are in
  `api_call_log`), and listings the pull did not return are outcome `not_found`, 0 calls, detail "not returned
  by the zip pull". The builder reads only that table, so both methods feed one rule set. The job must
  also refuse to send if the pull was incomplete (a zip that failed would make its listings look absent).

*Piece 5 as built, 2026-10-19* (developer: all as recommended).
- **`drip_step_runs` (`sql/047`).** One row per attempt to run a step: inserted `running`, updated once to
  `complete`, `nothing_to_send`, `failed` or `abandoned`, with the method, calls, emails and a reason of 300
  characters at most that never holds response text. The database allows a run only on an active campaign
  and an unsent step, one `running` row per step, and sets both timestamps. A finished row never changes.
- **`run_step` order** (`hailsys/email/dripstep.py`): pre-flight (campaign active, next unsent step,
  template active, RentCast key set; nothing spent or written), unsubscribe sync, run row, re-check, rebuild
  the list, then one transaction (`queue_batch(send_list=...)`, step marked sent, stops written, campaign
  `complete` on the last step), close the run row, deliver. The run row is closed before delivery, so a
  crash during delivery leaves nothing `running`; Resume takes over. The sync is run again before queueing
  if the re-check took more than 10 minutes (`queue_batch` refuses a sync older than 15).
- **Re-check** (`hailsys/rentcast/dripstep.py`): a definite answer under 24 hours old is reused; ten failed
  by-id lookups in a row end the step (a fixed limit, not a setting); an auth or malformed-request failure
  ends it at once; a failed zip in a pull marks only its own listings `error` and the step goes on. A
  listing with no zip on file can only be checked by id, so it forces that method.
- **Nobody left to email:** nothing is queued, the stops are written, the run ends `nothing_to_send`, and
  the campaign is left for a person to cancel.
- **`sendjobs.start_drip_step`** takes the same `send:run` lock as sends and resumes, so a step and a send
  never overlap; the pre-flight runs before the thread starts so the route can explain a refusal.
- **Known limits.** A failed unsubscribe sync now closes the run row `failed` ("the unsubscribe sync failed
  (ExceptionName)") before re-raising (piece 6.1; it used to leave no record). A zip pull asks for Active
  listings only, so a listing that went inactive is not returned and is recorded `not_found`, not
  `inactive`; both are excluded, only the logged word differs.

*Piece 6 as built, 2026-10-20* (developer: all six decisions as recommended).
- **Start (D1).** The "Follow-ups (optional)" part of the `/send` confirm page (`dripstart.parse_plan`,
  `hailsys/web/send.py`). The campaign is created inside the step-1 queue transaction
  (`deliver.run_send(campaign=...)`), so a failed send leaves no campaign and a campaign always has a first
  batch. The name is required (1 to 100), prefilled "Storm <date(s)>".
- **Screens** (`hailsys/web/drip.py`, admin only; reads in `hailsys/queries/dripscreen.py`, changes in
  `hailsys/email/dripactions.py`): `/campaigns` (list, next follow-up and how late), `/campaigns/<id>` (steps
  with due dates, run history, recipients, pause / resume / cancel, stop one, paste box),
  `/campaigns/<id>/step/<n>` (the preview and the button), and a banner on `/send` listing due follow-ups.
  Opening any page calls neither RentCast nor Constant Contact.
- **The click (D2, D3).** "Check listings and send" posts the numbers the preview showed (listings to check,
  emails at most) and the start refuses if either changed. The pre-flight refuses while the previous step's
  batch has untouched queued emails (item 231). The run row is written before the sync, so a failed sync is
  on the campaign page.
- **One rule for the method.** `rentcast.dripstep.plan_method` picks by-id or zip pull (a listing with no zip
  forces by-id); the preview and `run_step` both call it, so the screen cannot disagree with the job.
- **Due date.** The Denver date of the latest delivery in the previous step's batch (the first batch for
  follow-up 1) plus `gap_days`. A step with an undelivered predecessor has no due date. It never expires; the
  page says how many days late.
- **Stopping (D5).** Per recipient and by paste box, with a required reason: `replied` or `manual`
  (`stopped_by` is the person). Neither adds anyone to `dnc_list`. A stopped recipient stays stopped; a
  cancelled or complete campaign refuses further stops.
- **Pause / resume / cancel (D4).** Active to paused, paused to active, either to cancelled (final, with a
  confirm). Pausing or cancelling while a step runs makes that step's queue transaction fail on the
  `drip_steps` trigger ("campaign is not active"), which rolls back the whole queue and closes the run
  `failed`. This follows from the trigger and has not been exercised in a test. Cancelling does not touch
  queued emails of earlier steps: resume or discard them from the batch page.
- **Not built.** (1) The cap override from the piece 4 decision: the follow-up screen always applies
  `email_cap_days`. `run_step` accepts `override_cap`; only the box is missing. *When:* add it the first
  time a person needs to reach someone emailed inside the window. (2) No per-send test addresses on a
  follow-up: the recipients are the campaign's list, so a rehearsal uses a campaign made from test addresses.
- **Checks.** 1,447 tests at the end of piece 6; each sub-step mutation-checked (the web layer about 55
  mutations).

*Running a campaign (operator note).*
1. On `/send`, pick the storm days, the template and the addresses as for any send. In "Follow-ups
   (optional)" tick "Make this a campaign", name it, and fill the rows in order (a gap in days and a template
   for each follow-up). The limits are on the admin page (`drip_max_followups`, `drip_max_gap_days`). Send.
2. Resume or discard any queued remainder of that send from its batch page; a follow-up is refused while the
   previous step has untouched queued emails.
3. When a follow-up is due, `/send` shows a banner and `/campaigns` shows it with how late it is. Open the
   step, read the preview (listings to check, RentCast calls, emails at most), set "Send at most" and press
   "Check listings and send". It runs in the background; the campaign page shows the run.
4. When someone replies, stop them on the campaign page (reason `replied`), one at a time or by pasting
   addresses. A reply is not an unsubscribe: it does not add anyone to the do-not-contact list.
5. Pause if you need time; cancel to end it. Nothing is sent without a click.

*Probe results, 2026-10-16* (`scripts/rentcast_by_id_probe.py`, three calls, RentCast dashboard
36 to 38 of 1,000).
- A listing last seen that morning: 200, `status` Active, `removedDate` null, `lastSeenDate` that day
  (our row had 2026-10-09).
- A listing our database still held as Active, last seen 2026-09-16: 200, `status` **Inactive**,
  `removedDate` 2026-09-17. A removed listing is a normal answer, not an error.
- A made-up id: **404**, body `error`, `message`, `status`. The by-id function must keep this distinct
  from "no listing" (the zip client turns a 404 into an empty list).
- Billing: the two 200s moved the dashboard by 1 each and the 404 by 0 (the count may lag; treat "a 404
  is free" as likely, not certain).
- The response has `history`, `listingAgent` and `listingOffice` (item 227); the only header of note was
  a trace id, so no rate-limit or usage headers.
- Our database holds only `Active` listings (26,297), never refreshed by anything but a pull.

*Why the method changed.* By id is one call per listing. Matched listings per storm day (test rows
excluded): 7,317 (84 zips), 3,618 (40), 2,596 (30), 1,975 (20 zips, 1,415 agents), 1,392 (15). A
by-id check of the 1,975 would be about 1,975 calls against a plan of 1,000 a month; re-pulling its 20
zips costs about 20 to 40 (past pulls: 94 zips, 94 calls; 45 zips, 45 calls). By id wins only when
there are fewer listings than zips.

*Not verified.* Whether a 404 is really free; whether a zip pull inside a campaign step finishes in a
useful time for the largest storms; the Constant Contact calls per email (this item says about 10; it decides how many follow-ups fit in a
day); whether any trigger blocks a second `send_log` row for a match (the guard tests in piece 2).

**When:** the dev rehearsal (piece 7) before the first production campaign, using only the developer's
own test addresses (it spends a few real RentCast calls and sends through Constant Contact). Level 2 still
not before 189 and 129.

**Related:** items 189, 191, 203, 226, 227.

## 226. Read replies from a dedicated Gmail inbox to stop follow-ups

**Status:** open (proposal, filed 2026-10-16; not decided)

Replies to the campaign emails go to `settings.reply_to`, forwarded to a Gmail account. Level 1 of the
drip campaigns (item 204) cannot see them, so a recipient who replies "not interested" is stopped only
by hand (a Stop button and a paste box). The idea: a Gmail account created for production that handles
only this reply-to address, read by the system through the Gmail API, stopping follow-ups to anyone who
writes back.

Parked because it is large: Google OAuth, a new dependency and mailbox permissions. A dedicated account
removes the worry about reading mixed mail. Still to design: out-of-office replies and forwards must
not be treated as a reply that matters; and a reply is not an unsubscribe, so what it should do to
`dnc_list` is a separate decision.

**When:** campaigns are in real use and stopping by hand is a burden, or the production reply-to address
is created (item 96), whichever comes first.

**Related:** items 204, 96, 129.

## 227. A follow-up goes to the original agent even if the listing's agent changed

**Status:** decided 2026-10-16, partly built (found the same day, in the by-id probe for item 204)

RentCast's lookup by id returns `listingAgent` and `listingOffice` for the listing as it stands now.
A drip follow-up (item 204) is addressed to the realtor of the step-1 email. If the listing was taken
over by another agent between steps, the follow-up still goes to the first one, who may no longer have
the listing. `listings.realtor_id` is set at the last pull, so a follow-up that re-checks by zip pull
would refresh it and one that checks by id would not. The probe printed the field names only; whether
`listingAgent` carries an email, and how it maps to `realtors`, is not checked.

**Decided 2026-10-16 (developer): drop the listing.** If the listing's current agent is not the address
the step-1 email went to, the listing is left out of that recipient's follow-up; reminding a former
listing agent of a listing they lost is worse than saying nothing. If it was their only listing, they get
no follow-up. Not moved to the new agent.
- **By id:** built in `dripcheck.classify` (piece 4.0). It compares `listingAgent.email` in the answer, trimmed
  and lower-cased, with the recipient's address. Another agent is outcome `inactive` (`rc_status` stays
  `Active`, the detail says why and holds no address); an answer that does not name the agent is `error`
  and the listing is left out.
- **By zip pull:** the pull refreshes `listings.list_agent_email`, so the follow-up list builder (piece 4)
  must compare `list_agent_email_norm` with the recipient's address and drop the listing on a difference
  or a missing address. Not built yet.

**When:** the zip-pull half is done when the list builder (piece 4 of item 204) is. Close this item then.

**Related:** items 204, 55.

## 228. The RentCast zip client has no unit tests

**Status:** open (found 2026-10-16, while writing the by-id tests for item 204)

`get_sale_listing` has 20 tests (`tests/test_rentcast_by_id.py`: retries and the cap, 404, auth, unreadable
answers, nothing from a response body in a log). The older zip search in `hailsys/rentcast/client.py`
(paging, retries, the 404-to-empty-list rule, the abort paths that feed `api_call_log`) has none; it was
verified by real pulls. A change to the shared pieces (`_throttle`, the error classes, back-off) is
covered on the by-id side only.

**When:** before the next change to `client.py` that touches the zip search or the shared error classes,
or when item 204's piece 5 makes a zip pull part of a chained job.

**Related:** items 204, 88.

## 229. Nothing tests that the cap override turns the cap off

**Status:** open (found 2026-10-19, mutation-checking item 204's `queue_batch` change)

Replacing `cap_days=None if override_cap else cap_days` in `queue_batch` with `cap_days=cap_days` fails
no test. `tests/test_send_queue.py` checks only that the override leaves `already_emailed` in force, and
`tests/test_send_screen.py` only that the argument is passed through. A change that made the override do
nothing would go unnoticed. The test needs a realtor emailed inside the cap window on a different storm,
which is more fixture than the check was worth that day.

**When:** the next change to `queue_batch` or the cap rule, or the item 189 eligibility work.

## 230. A by-id lookup may return a relisted house's newer listing

**Status:** open (watch; found 2026-10-19, planning item 204 piece 4)

RentCast's `id` is a property id and a relisted house reuses it (`CLAUDE.md`, data traps). The drip check
looks a listing up by that id, so after a relisting it may answer with the newer listing (new list date,
possibly a new agent) rather than the one we emailed about. The agent comparison (item 227) catches the
common case; nothing compares list dates, and the probe did not test a relisted property.

**When:** if a follow-up ever names a house whose listing changed, or when piece 7's dev rehearsal can
include a property known to have been relisted.

**Related:** items 204, 227.

## 231. Drip follow-up counts queued emails as emailed

**Status:** resolved 2026-10-20 in `7a58a05` (found 2026-10-19, planning item 204 piece 6)

`dripfollowup._HITS_SQL` joins `send_log` with `send_status <> 'failed'`, so a step-1 email still `queued`
(for instance with `max_emails` at its default of 5, most of a large batch stays queued) is treated as sent,
and its recipient would get a follow-up to an email never delivered. The same holds for any later step's batch.

**Fix, built 2026-10-20 (piece 6.1):** `dripstep.preflight` refuses while the previous step's batch (the first
batch, for step 1) still has `queued` emails: "N emails of the first send are still queued: resume or discard
them first". Only untouched emails count (`error_detail IS NULL`, the test `_deliver_all` uses). A
`needs_review` email is parked with its `send_log` rows still `queued` and neither Resume nor Discard touches
it, so counting it would block a campaign for good; the builder treats it as emailed. Tested on real rows in a
rolled-back transaction (`QueuedBeforeQueryTest`).

**When:** done.

**Related:** items 204, 180.

## 232. The backup host's disk is old: 66,811 power-on hours

**Status:** open (watch; accepted 2026-10-20)

**Evidence:** `scripts/hw-acceptance-test.sh` on `pbs-prod01` (Dell OptiPlex 3020, 500 GB HDD,
`/dev/sda`), 2026-10-19: 14 passed, 1 warning, 0 failures. The warning fires when an HDD passes
`HDD_HOURS_WARN=35000` (line 69, checked at line 504). This disk is at about 7.6 years. The sector checks
(pending, offline-uncorrectable, reallocated) raised nothing. The script's "healthy now" wording is fixed
text in the warning, not a separate verdict; the SMART and sector results are what support it.

**Decision:** accepted as the backup target, not as primary storage. The backup is the only copy of
`pve-prod01`'s VM, so a failed disk means no backup, not lost data.

**When:** before the first production send, check that a backup has completed and a restore has been tried.
Also rerun the acceptance test (or `smartctl -a`) on `pbs-prod01` every six months, and replace the disk or
add a second backup target at once if reallocated or pending sectors appear.

**Related:** decision log 2026-10-20.

## 233. Backups: single-disk datastore, retention, encryption, consistency, restore test

**Status:** open (filed 2026-10-20)

**Evidence:** datastore `prod` is on the root filesystem of `pbs-prod01`'s single 500 GB disk (decision log
2026-10-20): if it fills, the OS fills, and there is no redundancy. No retention, prune or garbage-collection
schedule is set. Backups are not yet encrypted client-side. VM 101 has only had an empty test backup.

**To do:**
- Add a second 500 GB HDD, reinstall PBS on a ZFS mirror, move the datastore off root. The earlier
  OptiPlex-plus-second-SSD plan is out.
- Set retention, prune and GC.
- Decide the client-side encryption key before backups leave the site (item 235).
- Postgres-consistent backups: guest-agent filesystem freeze, or a `pg_dump` hook.
- After the OS install: install `qemu-guest-agent`, enable `fstrim.timer`, then test-restore VM 101 to VMID
  9100 with the network disconnected. The restore target must support x86-64-v3.

**When:** before the first production send (as item 232 already says), and before backups go offsite.

**Related:** items 232, 235; decision log 2026-10-20.

## 234. Irin alerts for PBS disk usage and backup job failures are unconfirmed

**Status:** open (filed 2026-10-20)

**Evidence:** `CLAUDE.md` records Irin enrollment as not confirmed; nothing records alerts for `pbs-prod01`'s
disk or for backup job failures.

**When:** before the first production send; with item 233's single-disk datastore, a full disk must alert.

**Related:** items 9, 233, 236.

## 235. Offsite sync is not built

**Status:** open (filed 2026-10-20)

**Evidence:** no sync user/token, Remote or sync job exists.

**To do:** create a sync user and token with `DatastoreReader`; add the Remote and the sync job on the home PBS;
decide which home PBS pulls and confirm it is untagged (tagged machines cannot reach shared nodes); settle the
encryption key first (item 233).

**When:** after item 233's retention and key decisions.

**Related:** items 233, 237; decision log 2026-10-20 (Tailscale entries).

## 236. PVE email notifications do not arrive

**Status:** open (filed 2026-10-20)

**Evidence:** the backup notification sent through the `mail-to-root` target on 2026-10-20 never arrived.

**To do:** a real SMTP notification target, then a test send.

**When:** before relying on any backup or job-failure notice (item 234).

**Related:** items 234, 233.

## 237. Production hosts: hardening and housekeeping

**Status:** open (filed 2026-10-20)

**To do:**
- Disable Tailscale key expiry on `pbs-prod01` and `pve-prod01`.
- Remove the stale `rbi-hail01` node from the personal tailnet before the new VM ever joins Tailscale
  (MagicDNS name collision).
- Confirm Tailscale SSH is off on `pve-prod01`.
- Harden sshd on both hosts: keys only, no root password login.
- Internal DNS for `rbi.internal`; then switch PVE's PBS storage from the IP to the name.
- Tell RBI about Tailscale on the machines on their LAN.

**When:** before the production VM is started or joins Tailscale; the RBI notice before anything is racked
at their office.

**Related:** decision log 2026-10-20.

## 238. Multi-market readiness: what is hard-coded for RBI, Colorado and Denver

**Status:** open (filed 2026-10-20, by developer direction; not urgent)

The developer wants anything specific to RBI or Denver to work like a toggle, so a license or a SaaS
offering stays possible (decision log 2026-10-20, point 5). Checked 2026-10-20 against the code and the
database. **Already generic:** `coverage_zips` is data and `load_coverage.sh` takes the zip list as an
argument (decision log 2026-09-09); `zcta_boundaries` holds all 33,791 national ZCTAs; the report-to-zip
distances, pulls, matching, geocoding, drip campaigns and the email engine work for any zips; MRMS and NWS
warnings cover the whole country.

**Hard-coded today:**
1. **Storm ingest state.** `IEM_STATE = "CO"` in `hailsys/iem/common.py`, and `assert_single_state` raises on
   any other state. Another market needs the state(s) as a setting, plus a backfill.
2. **The time zone.** `DISPLAY_TZ = ZoneInfo("America/Denver")` in `hailsys/tuning.py` and `'America/Denver'`
   in SQL strings (`storms.py`, `activity.py`, `workstate.py`, `exports.py`, `ingest.py`, `search.py`,
   `scripts/`). The storm day is the Denver calendar day. One market per install is fine; two markets in one
   install need a time zone per market.
3. **The map.** `map.js` sets the view to Denver; `static/coverage.geojson` and `colorado_counties.geojson`
   are Colorado's (`scripts/build_coverage_geojson.py` regenerates the first).
4. **Tuning evidence.** Radii, per-type `min_magnitude` and the 1.00 in cutoff were derived from Colorado
   data (`tuning.py`'s own header says so), and MESH reads high against reports here. Another region needs
   its own calibration; the Phase 5b parameter sets are built so it can have one.
5. **Brand and content.** "Roof Brokers has certified roofs for Denver Metro real estate agents" in the
   built-in template and `blocks.py`; sender, images and addresses are already settings (item 203).
6. **List defaults.** `DEFAULT_REPORT_TEXT = "HAIL"` in `hailsys/web/views.py`: Storm Days, Territory and the map's
   points open on HAIL (developer, 2026-10-22; "All" is still a choice). A market with no `HAIL` report type would
   open on empty pages. Move it into settings with the rest.
7. **The cold season.** `COLD_SEASON_MONTHS = (10, 11, 12, 1, 2, 3)` in `hailsys/queries/findings.py` (added 2026-10-23,
   slice 6): the months in which radar hail with no report anywhere that day is flagged on the findings panel as more likely a
   radar artefact than hail. October to March is Front Range reasoning (developer, 2026-10-23); another market's hail season,
   and its winter radar artefacts, differ. Move it into settings, per market, with the rest.
8. **`storm_states` now feeds two jobs.** The radar warnings (Phase 5b) and, since 2026-10-23, the CoCoRaHS ingest
   (`hailsys/cocorahs/ingest.py`, `states()`), read `settings.storm_states`. A new state needs a CoCoRaHS backfill
   (`scripts/cocorahs_ingest.py backfill`) as well as the radar warnings backfill. The IEM report ingest still
   uses the `IEM_STATE` constant (point 1). The CoCoRaHS export is public and keyless, so a new state costs requests, not money.

**Rule for new work:** no new constant of this kind (decision log 2026-10-20). Phase 5b takes the time zone,
state and bounds from settings and data.

**When:** before a second market, a second state, or a license or SaaS conversation, whichever comes first.
Items 1 to 3 are the work; item 2 is the largest.

**Related:** items 203, 216; decision log 2026-09-09, 2026-10-20.


## 239. The analysis MESH decoder cannot read 8-bit files, and S3 resets connections

**Status:** resolved 2026-10-21 (found in Phase 5b slice 0; fixed in slice 1, `c5e346a`)

`docs/analysis/storm-shape-mesh-2026-10/storm_shape/mesh/mesh_lib.py` asserts `nbits == 16` and unfilters PNG
rows assuming 2-byte samples. The `MESH_Max_60min` file stamped 2023-05-10 16:00 UTC is packed at 8 bits and
stopped the slice 0 window check with `AssertionError`. Every 1440-minute file inspected was 16-bit, but
nothing guarantees that a quiet day is. S3 also reset the connection three times in about 100 requests, which
ended the first slice 0 run.

**Fixed:** `hailsys/radar/mesh.py` reads the bit depth from the PNG and the packing section, handles all five PNG
filters at either depth, and raises `MeshError` instead of asserting; `hailsys/radar/fetch.py` retries with
backoff and writes to a `.part` file before renaming. Tests: `tests/test_radar_mesh.py`,
`tests/test_radar_fetch.py`, with a golden test against the analysis decoder on the 2026-06-09 06:00 UTC file.
`mesh_lib.py` itself is left as it was (analysis code).

**Not done:** a real 8-bit file has not been decoded end to end against a reference. The 2023-05-10 date can be
checked once a nightly or backfill run has fetched it.

**Related:** items 205, 211; decision log 2026-10-21; `phases.md` Phase 5b.

## 205. Likelihood tiers: rank outreach by radar swath and distance instead of one circle

**Status:** open (proposal, filed 2026-10-13; not decided)

**Evidence:** decision log 2026-10-13, *Findings, not a decision: report-based circles against
radar hail (MRMS MESH), 60 storms*, and `docs/analysis/storm-shape-mesh-2026-10/mesh60/summary.md`.
- Today's circles hold about 56–67% of radar hail area, and only about 14% of circle area has
  radar hail.
- 90% of actionable hail reports sit on radar hail.

**The proposal.** Rank instead of cut off:
- **Tier 1:** inside the swath at a high value, near a report.
- **Tier 2:** inside the swath at a lower value, or near a report where radar is weak.
- **Tier 3:** inside today's 5-mile circle with no radar hail. Reached last.

Every house the circle reaches today stays reachable. Radar decides the order and adds the swath
beyond the circle. The cutoff is a setting, with an admin page access point. Inspections set the
boundaries.

**Size, by area, from the 60 storms (houses not counted).**
- 70% of circle area has no MESH ≥ 0.75 in, so it would be Tier 3.
- The swath beyond the circles adds +16% at 1.00 in and +29% at 0.75 in.

**Blocks adoption.** Detail is in the decision-log entry.
1. Tiering on radar *value* conflicts with decision log 2026-09-16 (radar size is not a severity
   signal). That entry has to be superseded on evidence, or the tiers have to use swath presence and
   distance only.
2. Reaching beyond the circle changes the product's claim from "reported" to "estimated". The
   *Radar-derived hail size* entry under *Not on the roadmap* requires its own decision-log entry for
   that.
3. The send unit is a realtor email listing several properties (2026-10-07), so realtors need an
   ordering rule.
4. "Where radar is weak" is undefined; there is no foothills evidence yet.
5. There is no inspection data.

Correction to the original framing: the early volume limit is our own warm-up plan (`phases.md`).
The only provider limit in code is Constant Contact's 10,000 API calls a day (`budget.py`).

**When:** the first inspection results exist, or before any send ranking or radar-based reach is
built, whichever comes first. Not before Phase 5 sending works on the circle as it is.

**Related:** items 35, 191; decision log 2026-09-16, 2026-10-07, 2026-10-13; *Not on the roadmap:
Radar-derived hail size (NOAA MRMS / MESH)*.

**Added 2026-10-14.** Three points.
- **Open question: a softer email for the outer tier?** Tier 3 houses are inside the circle with no
  radar hail. They may deserve different wording from Tier 1, or no email until the inner tiers
  are done. Undecided. It touches the template choice (items 202, 203) and the claim question in
  blocker 2.
- **Send order under warm-up throttling.** This is the use the tiers are for: while the warm-up
  limits how many emails go out, the tiers decide who is reached first. It applies only if the
  tiers are adopted.
- **Related:** item 206 changes the footprint itself (radar core, margin, circle as fallback);
  this item only ranks. They can be combined, and 206's core, margin and circle would be these tiers.

## 206. Radar-shaped storm areas: a radar core plus a margin, with the circle as the fallback

**Status:** open (proposal, filed 2026-10-14; not decided)

**Evidence:** decision log 2026-10-13, *Findings, not a decision: report-based circles against
radar hail (MRMS MESH), 60 storms*; analysis files in `docs/analysis/storm-shape-mesh-2026-10/`
(in the repo; CSVs and download caches are not, see its README; a full copy is on the workstation at
`~/hail-analysis-archive-2026-10-13/`).
- **Reports are good markers.** 485 of 536 actionable hail reports (90%) have MESH at or above
  1.00 in within 5 km.
- **The circle is the wrong shape.** It holds about 56% of the radar hail area pooled (median 67% per
  storm), and a median of only 14% of each circle's area has radar hail. Real swaths are narrow
  strips along the storm's path, a few km wide against a 16 km circle diameter.

**The proposal.** Replace the per-report circle as the storm's footprint with:
- **Core:** the MRMS MESH swath for the storm window, keeping only patches within a few km of a
  filed report. (A swath with no report near it is not used: nobody has said hail fell there.)
- **Margin:** a buffer around the core, "a little larger, just in case".
- **Fallback:** today's circle, only where radar cannot help: before the archives start (Iowa State
  2019-08-20; NOAA AWS 2020-10-14, the MRMS version 12 date), during outages, and in the mountains.

**Next step:** prototype on the 60 storms already processed. Report the same measures the 60-storm
study used (coverage, precision, area) for core plus margin against the circle. No matcher change.

**Limits already known.**
- **Mountains are untested.** None of the 60 storms is centred west of longitude -105.06, and only 29
  actionable hail reports lie west of -105.1. The one far-west case, the S0025 west piece, had 0 of 13
  reports confirmed by MESH.
- **MESH is uncalibrated** and read high against reports here (median 1.76 in against 1.25 in
  reported). The core threshold and margin width are placeholders until items 207 and 208 exist.
- **The product's claim changes** from "reported" to "estimated". The same blocker as item 205, point 2.
- **Value-based cutoffs conflict with decision log 2026-09-16.** The core should use presence of
  radar hail, not its value, unless that entry is superseded on evidence.

**When:** the prototype can be picked up at any time. **No matcher change** until it has a
decision-log entry, and not before the inspection data (items 207, 208) can set the threshold and
margin. Phase 5 sending works on the circle as it is.

**Related:** items 205, 207, 208, 210; decision log 2026-09-16 and 2026-10-13.

**Addendum 2026-10-14: prototype run on the 60 storms.** Analysis only; no matcher change. Evidence
is in `docs/analysis/radar-areas-2026-10/` (`summary.md`, `validation_by_source.txt`, maps).
Placeholders throughout:
- core = MESH ≥ 1.00 in patches with an actionable report within 5 km (presence, not value);
- margin = 3 km;
- outer = the warning polygons (decision log 2026-10-14).

*Tiers, checked against independent ground reports* (not used by MESH or the reports). These are
large-hail (≥ 1 in) points per 100 km², and the large-hail share of all ground points in the tier:

| Tier | Area km² | mPING + CoCoRaHS: per 100 km² / share | CoCoRaHS only: per 100 km² / share |
|---|---|---|---|
| Core | 15,743 | 0.94 / 52% | 0.159 / 60% |
| Margin (3 km) | 19,334 | 0.58 / 34% | 0.114 / 33% |
| Outer (warnings) | 137,748 | 0.05 / 13% | 0.012 / 20% |
| Rest of the 30 km domain | 202,054 | 0.004 / 6% | 0.002 / 10% |

- **The ranking holds with CoCoRaHS alone, so the validation does not depend on mPING's
  research-licensed data (item 215).** The CoCoRaHS sample is small: 232 points, 67 of them ≥ 1 in.
- **Margin width:** with a 1 km margin, CoCoRaHS-only density is higher in the margin than the core
  (8 points), but the size share still ranks correctly.
- **Core coverage:** core plus margin (35,077 km²) hold 78% of the independent large-hail points.
- **Today's circles:** they hold 95%, partly by co-location, since ground reporters cluster where the
  reports are. 58% of circle area lies outside core and margin, which measures item 205's Tier 3.
- **Narrow margin:** density falls fast past 1–3 km (per 100 km²: 0.91 at 1 km, 0.58 at 3 km, 0.42
  at 5 km), so the margin should be narrow.

*Decided 2026-10-14 (developer): unanchored swaths become their own tier.* The 17.5% of MESH
≥ 1 in area with no report within 5 km is kept as a separate tier, provisionally worded "radar-indicated
hail in your area". The claim wording follows item 205, point 2. It is refined once inspection data
exists (item 208).
- At a 3 km margin the tier is 3,285 km², with 0 large-hail ground points (7 small mPING points;
  no CoCoRaHS).
- Only 0.2% of today's circle area falls in it.
- **Ground reports cannot validate it,** because nobody reported there; inspections can.

*Fallback shape: the circle, stretched along the HRRR direction when widening.* This replaces "plain
circle" as the fallback for pre-2020 storms and radar gaps.
- At equal area, a capsule along the HRRR right-mover does not beat the circle: 57% vs 56% of MESH
  hail. It does beat a perpendicular control (48%), on 38 of 52 storms.
- When outreach goes wider, stretching each circle ± L along the HRRR right-mover gains 20–25% more
  MESH hail per km² added than enlarging the radius: 82 / 70 / 57 vs 69 / 57 / 46 km² per 1,000 km²
  at L = 4 / 8 / 16 km. That is close to the fitted direction (84 / 72 / 59).
- With L = 8 km, recall rises from 56% to 72%.
- The pre-2020 benefit is inferred, not tested: there is no MESH before 2020-10-14.

## 207. Inspection export from RBI

**Status:** open (requested from RBI, confirmed by the developer 2026-10-20; **delivery date unknown**; filed 2026-10-14)

RBI has done about 20 to 30 inspections a day since 2014, each rated low, moderate, high or severe.
That is the ground truth that everything in items 205 and 206 is waiting for.

**What the export must contain.**
- **Every inspection, including the no-damage ones.** Without the zeros, damage rates cannot be
  computed. **Why this matters most (owner, 2026-10-22; developer, 2026-10-23):** two houses next door to each other can
  get the same storm, one roof totalled and the other undamaged. No weather source resolves that (MESH is a 1 km grid,
  and hail swaths are patchy at the scale of a block), so the likelihood levels can only rank areas and can never
  predict a roof. A file of jobs won or damage found alone would make every level look better than it is; the
  no-damage inspections are what let the damage rate per level be measured honestly. Ask RBI for them explicitly,
  and for the roof's age and material where recorded (RentCast `year_built` is the only national roof-age proxy).
- **The inspector's estimate of the damage's age.** Older damage can be wrongly attributed to a newer
  storm, and the damage-age estimate is the only guard against it.
- The address (exact, not a zip), the inspection date and the rating.

**Fallback if the damage-age estimate is missing:** use only inspections that have exactly one
qualifying storm before them. How "qualifying" is defined is still to be settled. This is a cut,
not a correction, and it will drop part of the data.

**Ask RBI before the export is pulled.** Do not request or pull it first. The file holds addresses
and homeowner information: **keep it out of git**. The remote is public, which is why every `*.csv`
is ignored repo-wide.

**When:** ask RBI before the export is pulled, and before any threshold or margin is set (items 206,
208).

**Related:** items 205, 206, 208, 209.

## 208. Inspection calibration: set the radar core threshold and margin width from damage rates

**Status:** open (parked on item 207)

Once the export in item 207 exists: join each inspection to the storm that most likely caused it, then
choose the MESH threshold for the core and the margin width (items 205, 206) from observed damage
rates, instead of the placeholders (1.00 in and 0.75 in lines, an unspecified margin). Questions to
answer:
- How does the damage rate (moderate or worse) fall off with the MESH value and with distance from the
  swath?
- Is the circle's outer part (Tier 3) worth reaching at all?
- Does the relationship differ in the foothills?
- **The "radar-indicated hail" tier (item 206 addendum, decided 2026-10-14):** what damage rate do
  unreported MESH swaths carry, and does it justify the tier, its place in the order and its wording?
  Ground reports cannot answer this: nobody reported there.
- **The warning-polygon outer tier and the HRRR-stretched fallback:** do they pay off in damage rates,
  or only in radar hail? Inspections outside core and margin, but inside a warning polygon or the
  stretched capsule, answer it.

**When:** the export arrives. Not before.

**Related:** items 205, 206, 207; decision log 2026-10-13, point 6; decision log 2026-10-14.

## 209. Structured inspection capture going forward

**Status:** open (to raise with RBI; filed 2026-10-14)

Whatever the old export looks like, new inspections should be recorded so they can be used:
- A fixed severity scale (the same four levels, always).
- The exact address and the inspection date.
- **No-damage inspections kept**, not dropped.
- The inspector's damage-age estimate.

This is a request to RBI about how they record, not something this system can force. It also costs
nothing now and makes item 208 repeatable later.

**When:** raise with RBI now, with item 207.

**Related:** items 207, 208.

## 210. Extra weather sources for the storm footprint

**Status:** assessed 2026-10-14 (decision log 2026-10-14). Findings recorded; nothing adopted.

**Result, per source** (detail in `docs/analysis/weather-sources-2026-10/summary.md`):
1. **NWS warning polygons:** an outer bound (98% of MESH hail inside) and a storm-level severity tag;
   not a shape (Jaccard 0.09). From 2004, hail tags from 2010-04; live. The outer tier in item 206's
   prototype.
2. **MRMS rotation tracks, POSH, SHI:** SHI and POSH are MESH in another form and add nothing.
   Rotation tracks are partly independent (velocity, not reflectivity) and **not tested in Part B**.
   RadarQualityIndex (not in the original list) is the candidate for the mountains question; also
   not tested.
3. **CoCoRaHS:** 79% agreement; 6 large-hail points that reports and MESH both missed across 60 storms;
   thinner in 2025–2026 in that sample (not over the full year: 496 and 251 points in the box in 2025 and
   2026; decision log 2026-10-23).
   **Terms read 2026-10-22** (data usage policy, https://www.cocorahs.org/Content.aspx?page=datausagepolicy,
   read through a fetch tool; the developer to read it once in full): data freely available to governments,
   academia, the private sector and the public; Creative Commons Attribution 4.0; the source to be
   acknowledged when displayed; provided "as is". So CoCoRaHS may be used beyond the internal check, with
   attribution on any map or page that shows it. Six-year precision study: decision log 2026-10-23.
4. **mPING:** 90% agreement; 3 missed points, 7 confirmations beyond the circles. **Research licence
   only** (item 215).
5. **NCEI Storm Events:** a 99% duplicate of our IEM hail reports; no value.
6. **HRRR winds (and Denver soundings to 2022-07-09):** direction to a median of 19–23° with the
   right-mover. The basis for item 206's fallback capsule.
7. **Commercial hail maps:** out of scope, not assessed.

**Still open:** rotation tracks; RadarQualityIndex for the mountains; a 1 km small-hail test for
lowering confidence; terms of use for the other sources (CoCoRaHS read 2026-10-22, above; mPING is
research only, item 215).

**When:** reopen for any of the open points above if item 206's prototype or item 208's calibration
needs it.

**Related:** items 7, 205, 206, 208, 215; decision log 2026-10-14.

## 211. The 60-minute MESH check: 27 cells are unexplained

**Status:** open (filed 2026-10-14)

A 60-minute maximum built from 30 Iowa State two-minute files (2026-06-08, 21:02 to 22:00 UTC) matched
NOAA's `MESH_Max_60min` file on 249 of 276 cells at or above 0.75 in. The largest difference was 5 mm,
and both had 76 cells at or above 1.00 in. **It is not a decoder problem:** the hand-written decoder and
pygrib agree on every cell (0 of 225,000, and 0 of 402,290 in the 60-storm run). The likely cause is
a difference in time window or file stamp between the two products. **Untested:** `validate_window.py`
(in `storm_shape/mesh/`) was written for this and never run to completion, and the 60-storm run could
not confirm that a file stamped H covers exactly the hour before it.

**Why it matters:** the 60-storm study (item 205's evidence) uses hourly `MESH_Max_60min` files.

**Update 2026-10-21 (Phase 5b slice 0):** NOAA's own products agree with each other. On 2026-06-08 the
`MESH_Max_1440min` file equals the maximum of the 24 hourly `MESH_Max_60min` files ending at its stamp on every
cell, so the stamp marks the END of the window (decision log 2026-10-21). That supports the likely cause above
(a window or stamp difference against the Iowa State two-minute build) but does not test it: the 27 cells remain
unexplained. Evidence: `docs/analysis/mesh-window-2026-10/`.

**When:** before the 60-minute product sets a threshold or a window for any decision, or whenever
`mesh60` is rerun.

**Related:** items 205, 206; `docs/analysis/storm-shape-mesh-2026-10/mesh60/summary.md`.

## 212. Documents drifted behind the Constant Contact send-path build

**Status:** resolved 2026-10-14 (found and checked 2026-10-14, after the user asked whether all `.md` files were up to date)

Nothing updates the documents when a migration or a feature lands (item 154 is the same
problem for parking-lot items). The send path was built between 2026-10-05 and 2026-10-13 and most
documents last moved on or before 2026-10-05.

**Fixed 2026-10-14** (`CLAUDE.md` and `database-schema.md` in `081dc39`; the rest in the commit after it).
- `CLAUDE.md`: the Phase 5 paragraph said "No sending code", "`send_log` and `email_templates` are
  empty" and 759 suppressions. Rewritten (the send path, 760 suppressions, 7 rehearsal `sent_emails` rows to test
  addresses); `sent_emails` added to the append-only rule.
- `docs/database-schema.md`: lacked six of the 36 tables (`sent_emails`, `cc_sync_runs`, `cc_sync_conflicts`,
  `cc_status_runs`, `cc_api_calls`, `oauth_tokens`) and `send_log.email_id`, `email_templates.description`,
  `settings.match_max_age_days` and `email_cap_days`. Added.
- `README.md`: said "no sending path exists yet", "100" tests, and that `app` bakes its code into the image (only
  `ingest` does). Corrected, with the test-run instructions (item 171).
- `docs/phases.md`: the Phase 5 section stopped at 2026-10-05; progress to 2026-10-13 added, the old "Not started"
  list marked as of 2026-10-05, and the checklist annotated built, partly built or not built. The MESH note under
  "Not on the roadmap" now cites the 2026-10-13 findings.
- `docs/data-sources.md`: section 4 said the provider was TBD. Rewritten for Constant Contact; section 7 (MRMS
  MESH) added.
- `docs/db-schema-diagram.md`: a send-path section added (`sent_emails` and the Constant Contact tables).
- `docs/schema-review.md`: migrations now listed through 041; table counts and guard-test coverage updated; 034 to
  041 marked as not yet reviewed.
- `docs/hail-consolidated.md`: synced to 2026-10-14 (36 tables, the send path, decisions, traps, sources, file
  layout through `sql/041`). Sections 4, 9 and 12 to 13 were not re-checked.

**Checked and current enough:** `docs/command-ref.md`, `docs/server-setup.md`, `config/README.md`, the
analysis READMEs.

**An idea, not decided:** a test that fails when a table in the database is not named in
`database-schema.md`, like the `tests/test_workstate.py` checks (item 48).

**When:** done; the recurring risk is the idea above, or a sweep after each phase.

**Related:** items 154, 180, 191, 199, 202; decision log 2026-10-06 to 2026-10-13.

## 213. The parking lot's layout does not match its own header

**Status:** open (found 2026-10-14)

The header says items are grouped by status: Open, Watch list, the permits workstream, then Closed. In
fact:
- The block under "Open" holds 19 resolved items (16, 19 and 29 among them), 4 dropped, 1 built and 1 closed by decision,
  and the count line under it (49) counted headings, not the statuses it names.
- Items filed after 153 (154 to 212) sit after the permits section, mixing open, built and resolved items.
- The Watch list says "30 items, all `open (watch)`" and has 30, but item 102, also `open (watch)`, sits in the Open block.

The count line was corrected on 2026-10-14 to count by status across the whole file (50, with 213 and 214);
the layout was not touched, and item numbers never change.

**When:** the next parking-lot pass: regroup by status, or change the header to say what the layout is.

**Related:** items 154, 212.

## 214. `sql/guard_test.sql` does not test the guards in `sql/039` and `sql/040`

**Status:** open (found 2026-10-14, while updating `docs/schema-review.md`)

`guard_test.sql` covers `029`, `030`, `033` to `037`, the description rule in `041`, `042` (images and email settings) and `043` (`blocks`). It has no check for
`cc_status_runs_guard()` (`039`: refuses `DELETE`, any change to a finished run, and any change to the identifying
columns of a running one) or `cc_api_calls_guard()` (`040`: refuses every update and delete), and the grant
revocations on both tables are not tested either. The two guards are the same pattern as the tested ones
(`cc_sync_runs`, `cc_sync_conflicts`), so the risk is low, but an untested guard is an unverified claim.

**When:** with the next edit to `guard_test.sql`, and before `039` and `040` are applied to the production box.

**Related:** items 180, 191, 212; `docs/schema-review.md`.

## 215. mPING is research-licence only; ask before any product use

**Status:** open (filed 2026-10-14)

mPING's API registration offers one licence type, "Research". The licence text was not read.
This system's use would be commercial (RBI's outreach). The 2026-10-14 assessment used mPING as
research only, and its raw points are kept out of git (`docs/analysis/weather-sources-2026-10/`
README). The maps in that folder and in `docs/analysis/radar-areas-2026-10/` were regenerated without
mPING on 2026-10-14: mPING is used for internal validation only.

The key is in `.env` as `MPING_API_KEY` and `MPING_USER_NAME`. `.env.example` does not list them.

**When:** before mPING enters any product code path, ask mPING (their Contact page) whether
commercial use is allowed, and on what terms. If it is research only, mPING stays a research input.

**Related:** items 7, 210; decision log 2026-10-14.


## 216. The territory map draws a fixed 5-mile circle, not the configured radius

**Status:** resolved 2026-10-22 (Phase 5b slice 5; filed 2026-10-14)

`hailsys/web/static/map.js:85` draws every report's circle with a hard-coded `radius: 8046.72` (5 miles).
Both radii are admin settings (`settings.default_zip_radius_miles` and `default_match_radius_miles`,
`sql/020`, each up to 10 miles, match ≤ zip):
- the page fetches its points with `zip_radius_miles` (`views.py`, `map_points`);
- the matcher uses `match_radius_miles` (`matcher.py:100`).

Today all three agree at 5.0, so nothing is wrong yet. **If an admin changes either radius, the map
still shows 5-mile circles**, and a staff member reading the map is told the wrong reach. The code
comment on that line says a pixel-sized marker "would be a lie about how far 5 miles is"; the
hard-coded radius is the same lie in a different form.

**Fix (applied 2026-10-22):** the Territory view sends `match_radius_m` (from `settings.default_match_radius_miles`) as
`data-match-radius-m` on the map, and `map.js` draws every circle from it; with no number it draws none and logs an
error. No hard-coded radius is left in the page. Tests: `tests/test_radar_map_web.py` (a changed setting changes the
attribute) and `tests/js/map_check.js` (the radius used is the one sent, none guessed, no `8046` in the source).

**When:** the next edit to `map.js` (the radar layers planned after Phase 5 will touch it), or before
anyone changes a radius on the admin page, whichever comes first.

**Related:** `sql/020`; decision log 2026-10-13 (UI access point rule).

## 217. The footer phone and the built-in template's links are template content, not settings

**Status:** open (accepted 2026-10-15)

In the block builder (item 203, pieces 2 and 3) the footer's phone number is a setting of the footer
block (default `(303) 750-1900`), so it is not hard-coded in code. The other fixed text of the
built-in template (the `Account/LoginRegister` button address and the birdeye "See more reviews"
address) now lives in the seed `HAIL_ALERT_BLOCKS`
(`hailsys/email/blocks.py`) as block settings. None of these is an admin-page setting.

Accepted: a template author edits them per template in the editor (piece 4). A site-wide change
(RBI's phone or review page moves) means editing each template, saved as a new version.

**When:** a second template needs a different value, or RBI changes the phone or either link: then
decide whether they become `settings` columns with an admin-page access point.

**Related:** item 203; decision log 2026-10-15.

## 218. A broken image address fails a render with a generic error

**Status:** open (accepted 2026-10-15)

A block template names images by name (`images['logo']['url']`). `save_blocks` refuses a name that is
missing or retired, and an image in use by an active template cannot be retired (piece 3), so a
missing image should not reach a send. What can still happen: the render fails with only the class
name (`render.render` reports "template could not be rendered (UndefinedError)" on purpose, because
the message can quote context values), so the cause is not visible.

**When:** piece 4's preview: show the real cause (which image name is unavailable) in the preview,
which does not send, rather than the generic message.

**Related:** items 203, 202; `hailsys/email/render.py`.

## 219. Saving a template and retiring an image in the same instant can slip past the guard

**Status:** open (accepted 2026-10-15)

`save_blocks` checks that its images are active, and `set_image_active` checks that no active block
template uses the image, but neither holds a lock across the other. If an admin saves a template that
uses an image while another admin retires it, both checks can pass, and the template then names a
retired image. The failure is loud, not silent: every email of that template fails to render and is
skipped as "unrenderable" (ids only), and the template's preview shows the error.

Closing it: `save_blocks` takes `FOR SHARE` on the image rows it uses, and `set_image_active` takes
`FOR UPDATE` on the image row before its template check.

**When:** more than two people edit templates and images at once, or a template ever fails to render
because of a retired image.

**Related:** items 203, 218.

## 220. Move the built-in `hail_alert` from its files onto blocks

**Status:** open (filed 2026-10-15)

The built-in template still renders from `hailsys/email/templates/hail_alert.html` and
`hail_alert_subject.txt`, and `ensure_current` saves it on the first send. `HAIL_ALERT_BLOCKS`
builds the same email, and the golden test (`tests/test_email_blocks.py`) requires identical
output in four situations. Leaving the file-based path alone kept the rehearsed send path
unchanged while the builder was built.

**When:** after the first production send, once the editor has been used for a real template.
Then retire the files: the built-in becomes a saved block template, and `load_files`,
`TEMPLATE_NAME`'s special case and the "file_changed" badge go.

**Related:** items 202, 203; decision log 2026-10-15.

## 221. The template editor needs a cosmetic pass

**Status:** resolved 2026-10-16 for the first pass (`7bb85d1`); filed 2026-10-15, after the developer's
click-through of piece 4: "everything seems to work in the UI, it'll need to be cleaned up a bit
cosmetically, but it's a good start". The developer called it "really 'programmer friendly' not user
friendly" and asked for the preview beside the form.

Piece 4 was built for function, with the site's existing styles and a handful of new classes
(`.tpl-*` in `style.css`). Known roughness:
- the editor is one long page of plain fields: 17 blocks of the built-in template stack with no way
  to fold them;
- labels and help text are bare, and the system blocks (headline, greeting, storm panel, listings)
  are only marked by a dashed border and a note;
- Save appears only at the foot of the blocks and after the preview;
- the preview sits below the form, so a change is made in one place and seen in another;
- the list page has two small text links (Edit, Retire) and no sorting.

**Resolved 2026-10-16 (first pass).** Two columns, the form on the left and the preview on the right,
sticky and stacking below 1100 px; a sticky action bar with one Save, Show preview and the test send;
a collapsible formatting guide; the storm blocks and the divider as one-line rows; a summary in each
block's heading; small settings side by side; button-styled links on the list page. Not done: folding
blocks, sorting the list.

**When (the rest):** the developer said he would use the editor and give more feedback; revisit then,
and before the editor is shown to anyone but the developer.

**Related:** item 203; decision log 2026-10-15 (piece 4), 2026-10-16 (piece 5).

## 222. The editor's JavaScript is checked in a container, not in the normal test run

**Status:** open (accepted 2026-10-15)

The host and the `web` image have no Node. `hailsys/web/static/templates.js` is therefore not run by
`python -m unittest discover`. Its behaviour is checked by `tests/js/templates_check.js` (57 checks
against a fake page), run in a throwaway `node:22-alpine` container:
`docker run --rm -v ./hailsys/web/static:/s:ro -v ./tests/js:/t:ro node:22-alpine node /t/templates_check.js`.
The Python suite checks only that the script's ids, selectors and header match the page and the server.
A change to `templates.js` that is not followed by the container run is unchecked.

**When:** `templates.js` is 198 lines after the toolbar (2026-10-16). Before it grows past about 200,
decide whether to split it (the toolbar is separable) or to add a Node step to the normal test run.

**When:** if the script grows past about 200 lines or a second script appears, add Node to a test image
and run the JS check from the normal suite.

**Related:** items 203, 221; decision log 2026-10-15 (piece 4).

## 223. The test send's hourly limit can be passed by two clicks in the same instant

**Status:** open (accepted 2026-10-15)

`testsend.send_test` counts an admin's tests in the last hour, commits (so no transaction stays open
across the Constant Contact calls), and only then makes them. Two requests at the same instant can both
read a count of four and both go on, so six tests are possible instead of five. Closing it needs a lock
held across the check and the log row, which would mean holding a transaction open over HTTP calls.
Accepted: the recipient is always the clicker's own address, so the cap guards against a stuck button
or a loop, not against abuse of someone else's inbox.

**When:** if the cap ever protects anything other than the admin's own inbox (for instance, if a typed
recipient is ever allowed), or the call budget is found to be spent by test sends.

**Related:** item 203; decision log 2026-10-15 (piece 6).

## 224. An old git stash is the only record of two backfill fixes

**Status:** open (found 2026-10-15)

`git stash list` holds `stash@{0}: On main: pre-merge 2026-09-09: dup backfill fixes + test typo fix`:
changes to `scripts/iem_backfill.py` (about 20 lines) and `tests/test_iem_parse.py` (one line). Nothing
in the parking lot or the decision log mentions it, and a stash is not part of the repository's
history. It was noticed on 2026-10-15 and mentioned in chat twice before this entry, which is not a
record. Whether the fixes were later made another way is not known.

**When:** at the next work on the IEM ingest or backfill: read `git stash show -p stash@{0}`, apply the
parts still wanted as a commit, then drop the stash.

**Related:** `scripts/iem_backfill.py`, `tests/test_iem_parse.py`.

## 225. The editor's formatting toolbar needs JavaScript

**Status:** open (accepted 2026-10-16)

The B, I, Colour, Link and Insert field controls above the rich text boxes are hidden until
`templates.js` adds the class `js` to the form (`.tpl-toolbar` is `display: none` without it). With
JavaScript off, or if the script fails to load, the editor still works: the boxes take the typed tags
and the guide above the form lists them. Only the buttons are missing. Also, the toolbar edits the box
at the browser's last cursor position, so a box that was never clicked takes the edit at its start.

**When:** if anyone who edits templates turns out to use the editor without JavaScript, or the cursor
position proves confusing in use.

**Related:** items 203, 221, 222; decision log 2026-10-16.

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

**Status:** built and wired (checked 2026-10-12); only the optional nightly timer is not built

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

**Checked 2026-10-12: all but the timer is built and in use.** The **Sync unsubscribes now**
button, the last-good-sync line, the latest failed run and the conflicts table are on the
Constant Contact page (`/cc/`, `hailsys/web/cc.py`, admin only). `run_send` and `run_resume` both
sync first and stop on failure; `queue_batch` refuses unless the sync finished OK just before; a
contact unsubscribed in Constant Contact is skipped at queue time and again at delivery. The one
gap found was that `POST /cc/sync` had no tests; `tests/test_cc_sync_route.py` (11 tests, the sync
always stubbed; four mutation checks, all caught) now covers it. The optional nightly timer and its
`hail_sync` role are **not built, and not needed** while every send syncs first.

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
- the plan's contact cap and monthly send limit, against the real contact count (see "Plan tier" below)
- whether API and OAuth access, and the 10,000-calls-a-day limit the send engine stops at
  (item 191), are the same on the plan chosen; the reset time of that limit is still unverified
- whether unsubscribed contacts count toward the plan's contact cap (we hold 760 suppressions)

**Plan tier (considered 2026-10-15, not yet chosen).** Constant Contact is used here as a
delivery pipe through the API: the template builder (item 203), eligibility, suppression
(`dnc_list`) and sending (`/send`) all live in this system, so the plan's editor, automation,
segmentation, social, SMS, ads and events features go unused, and nothing may send
automatically. Lite (about $10.20/month: 5,000-contact cap, sends of 5 times the contacts per
month, 2 users) covers one developer and one sender. The deciding number is contacts. Item 121
measured 3,259 distinct agents over a six-week HAIL window (1,415 from one storm), so a busy
season could pass Lite's 5,000 cap. Standard (about $29.75/month) has no cap. The prices and
limits above come from Constant Contact's pricing page as pasted on 2026-10-15; they are not
verified against the account. Start on Lite only if the contact count the send path actually
creates in Constant Contact stays under 5,000; otherwise start on Standard.

**When:** when the production account is created, before the first production send. Choose the
plan after counting contacts, and confirm the API and call-limit bullets above before paying.

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

**Update 2026-10-15.** The editor's test send (item 203, piece 6) now does this in one click. A test of
the built-in template's content reached a Gmail inbox the same day (accepted by Constant Contact at
22:10 UTC, in the inbox, not spam). **Links, 2026-10-16:** the developer followed the links in that message and reported that they work.
Repeat the test send for any new template, and send one with its final links, before the first real send.

## 187. The send list ignored `listing_freshness_days`; a send needs a fresh RentCast pull first

**Status:** resolved 2026-10-08 (`943650d`, the `stale_listing` rule; checked 2026-10-13). A real send
still needs a fresh RentCast pull for the storm first, by design; the send screen counts the
dropped listings under "RentCast has not seen the listing recently (pull again)"

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


## 240. Radar zones are cut at the crop box edge

**Status:** open, accepted (filed 2026-10-21, Phase 5b slice 2)

`hailsys/radar/load.py` stores cells only inside the coverage extent plus 0.5 degrees (`BOX_MARGIN_DEGREES`),
so a hail patch that runs past that edge is clipped, and its core, margin and radar-indicated areas end in a
straight line. On 2026-05-21 the stored box ends at -103.205 and the analysis's 30 km domain around that
storm's reports reaches past it. The 0.5 degrees is there so the widest allowed margin (50 km, `sql/048`) always
fits inside the box. Hail beyond the box is outside RBI's coverage, so staff lose nothing, but the map will show
the edge.

**When:** slice 5 (the map layer), where it first becomes visible; or when a coverage zip is added near the
edge of the box.

**Related:** `docs/analysis/radar-areas-2026-10/`; decision log 2026-10-20.

## 241. The radar database tests need the admin role and skip under the standard test command

**Status:** open (filed 2026-10-21, Phase 5b slice 2)

`tests/test_radar_zones.py` and `tests/test_radar_load.py` insert parameter sets, radar rows and reports, which
`hail_app` cannot do, so under the documented test command they skip (44 tests). A break in `zones.py` or `load.py`
would pass the default suite. They run with `-e PGUSER=hail_admin -e PGPASSWORD=...` (command in each
docstring).

**When:** before the nightly radar job (slice 4): add the run command to `docs/command-ref.md`, or give these
tests a role of their own.

**Update 2026-10-21 (slice 3):** 90 tests now skip under `hail_app` (the warning tests joined). Running as
`hail_admin` hid a real bug: `current_states` read `settings WHERE id = 1`, but `hail_ingest` may read only the
`storm_states` column, so the first real run as `hail_ingest` failed with "permission denied for table
settings". Fixed, and tests that run the flows under `SET LOCAL ROLE hail_ingest` and `hail_app` now cover it
(they fail with the bug put back). The admin-only tests are still not in the standard command.

## 242. `.env` line 35 is not a valid shell variable name

**Status:** open (filed 2026-10-21)

The line `PERMIT-STACK-API=...` has hyphens, so `. ./.env` fails on it and bash prints the value in the error
message (it appeared in a session transcript on 2026-10-21). Docker Compose reads the file without trouble.
Read single values with `grep '^NAME=' .env | cut -d= -f2-` meanwhile.

**When:** the next time `.env` is edited: rename it to `PERMIT_STACK_API` and change the one place that reads it.
Consider rotating that key.

## 243. `settings.storm_states` has no admin-page field

**Status:** resolved 2026-10-26 (filed 2026-10-21, Phase 5b slice 3; closed by slice 7: the admin page's Hail zones section edits it, logged to
`settings_history`)

`sql/050` adds `settings.storm_states` (default `CO`), the states whose NWS warnings the radar layers fetch. It
changes only by an admin `UPDATE` on `settings` (logged to `settings_history` by the trigger, tested in
`sql/guard_test.sql`). CLAUDE.md says anything an operator may change has an admin-page access point; the
developer accepted the gap for a few sessions (2026-10-21).

**When:** Phase 5b slice 7 (admin settings for the radar layers), together with the threshold, margin and
report radius. Not yet used by the IEM report ingest (item 238).


## 244. `ingest.run` forwarded all its keyword arguments to the downloads

**Status:** resolved 2026-10-22 (found by the first real nightly run, fixed in Phase 5b slice 4)

`hailsys/radar/ingest.py` `run(..., **kw)` passed every extra keyword to both `preload_warnings` and `run_days`.
`scripts/radar_ingest.py` passes `pause`, so the first real nightly (run 4454) failed with `TypeError: download() got
an unexpected keyword argument 'pause'`; `box` and `set_id` would have been forwarded to the downloads the same way. The
tests had never passed `pause` to `run`, and ran the day loop and the warnings fetch with stubs that accepted
anything.

**Fixed:** `run` names `set_id`, `box`, `pause` and `sleep` (day loop only); `**network` (attempts, backoff,
timeout) goes to every download. `tests/test_radar_ingest.py` now has tests that fail with the old behaviour. The
failed run's row is kept, as history.

**Related:** items 241, 245; `docs/command-ref.md` "Radar jobs".

## 245. The radar ingest tests commit, and their cleanup collides with a live radar job

**Status:** open (filed 2026-10-22, Phase 5b slice 4)

The job commits after each day, so `tests/test_radar_ingest.py` commits too (on 1999 dates) and cleans up on a
separate connection. The cleanup `DELETE FROM radar_runs WHERE run_id > <newest when the test started>` also matches
a live job's run row; the delete fails on the days that reference it, so the test errors and may leave rows. A test
run during a nightly or a backfill is therefore unsafe. A warning is in the test's docstring and in `command-ref.md`.

**When:** before any automated test run (a scheduled one, or CI), or when the tests are next edited: track the run
ids the tests create instead of deleting by id range.

**Related:** items 241, 244.

## 246. A radar day keeps the crop box it was loaded with

**Status:** open, accepted (filed 2026-10-22, Phase 5b slice 4)

`load.crop_box` makes the box from the active `coverage_zips` when a run starts, and `radar_days` stores the box a
day was loaded with. If coverage zips are added later, days already held keep their older, smaller box, so their
cells and zones stop at the old edge even where the new zips are (the same straight-edge effect as item 240). Nothing
detects it. `load.delete_day` and a refetch (`radar_day.py --refetch`) fix a day; there is no command for all days.

**When:** the next time coverage zips are added, or when a second territory is set up (item 238): compare each held
day's box with `crop_box`, and add a `radar_ingest.py` mode that refetches the days that differ.

**Related:** items 238, 240; `hailsys/radar/ingest.py` docstring.


## 247. NOAA's MESH file holds impossible values on two days, which draw as false "radar-indicated" hail

**Status:** resolved 2026-10-22 (filed and fixed the same day, Phase 5b)

Found while summarising the six-year backfill. `radar_days.max_mm` for **2022-01-03** and **2022-01-11** is 1,007.7 mm
(39.7 in); no hail has been measured above about 8 in (203 mm). 2022-01-03 has 1,854 cells above 10 in (227 distinct values,
spread over lat 38.18 to 41.49, lon -106.22 to -103.22, in January) and 2022-01-11 has 27. They are in NOAA's file: the decoder and
loader store what the file says (the decoder matches the analysis decoder, `tests/test_radar_mesh.py` golden test). Effect:
a false `radar_indicated` zone of 2,397 km² on 01-03 and 27 km² on 01-11, about 3% of the 73,685 km² of that layer over six
years. No `core` is affected (no report anchors them). The next largest values are 8.8 in (2026-08-08) and 7.0 in (2026-06-22),
plausible for MESH, which reads high.

Also noted, not part of the fix: 28 cold-season days (December to March) have MESH of 1 in or more, nearly all with no report
near them. Some may be real late-winter hail; some may be the same kind of false echo. Treat their `radar_indicated` zones with
suspicion.

**Fix agreed (developer, 2026-10-22):** a fixed plausibility ceiling of 10 in (254 mm) in code, like the 0.50 in floor. A day
with any cell above it is recorded with status `suspect`: no cells, no zones, shown in the status line and the picker, and
rebuilds and the nightly job leave it alone. Cold-season days stay, and the findings panel will flag them.

**Fixed 2026-10-22** (`sql/052`; `hailsys/radar/load.py` `CEILING_IN`/`CEILING_MM`; ingest, scripts and status updated). A day with a value above
10 in (254 mm, exactly 254.0 mm still counts as hail) is recorded `suspect`: it keeps `max_mm` and `cells_in_box`, stores no cells and builds no zones;
it counts as held (the nightly does not retry it, the weekly rebuild skips it), and runs, `status.sh radar` and the Storm Days radar detail show a
suspect count. 2022-01-03 and 2022-01-11 were re-fetched (`radar_day.py --refetch`) and are now `suspect`; their cells and the false 2,397 km² and
27 km² radar-indicated zones are gone. Tests: `tests/test_radar_load.py` (the boundary at 254.0 and 254.1 mm, the evidence kept, a day not fetched again,
a re-fetch after delete), `test_radar_ingest.py`, `test_radar_health.py`, `test_radar_zones.py`, and `sql/guard_test.sql`.

**Not fixed, still open:** the cold-season days (December to March) with MESH of 1 in or more and no report. The findings panel (slice 6) will flag them.

**Related:** items 211, 239, 240; `docs/analysis/mesh-window-2026-10/`; decision log 2026-10-22.

## 248. The radar timing study's opening line counts days skipped by `--limit` as "already done"

**Status:** open, cosmetic (analysis script only; the results are unaffected)

Found in the two-day trial on thor, 2026-10-22. With `--limit 2` and an empty `--out`, `radar_study.py` printed
"495 days listed, 2 to do (493 already done)" when no day had been run. The count is `len(set(days)) - len(todo)` at
`docs/analysis/radar-timing-study-2026-10/radar_study.py:337`, worked out after the limit has cut `todo` down, so days
left out by the limit are reported as done. The full run without `--limit` printed the right count ("493 to do (2 already
done)").

**When:** the next edit to `radar_study.py`, or before it is reused for another study.

**Related:** `docs/analysis/radar-timing-study-2026-10/README.md`.

## 249. The radar timing study drops neighbouring hours at the local-day boundary, and its CSV does not give its units

**Status:** open, minor (analysis script; the summary's results are not changed in substance)

Found reading the 495-day run on thor, 2026-10-22. `radar_study.py` loads only the 24 hourly files of a report's local day
(`docs/analysis/radar-timing-study-2026-10/radar_study.py:105`); the report's own hour is chosen at `:189` and its neighbours at
`:192`, so near local midnight they fall among the previous or next day's files and are treated as missing. In that run: 5 reports
with no own-hour value (all stamped exactly 06:00 UTC, local midnight, so their own file is the last of the day before), 26 with no
hour-before value and 64 with no hour-after value (a few of these are NOAA's 12 missing files). Separately, the radar columns of
`reports_timing.csv` (`prev_*`, `own_*`, `next_*`) are in mm while `mag` is in inches, and the header does not say so.

**When:** with item 248, at the next edit to `radar_study.py`, or before it is reused for another study.

**Related:** item 248; `docs/analysis/radar-timing-study-2026-10/README.md`.

## 250. Four more daily MESH files hold values above 10 in; check whether hail-dev loaded them

**Status:** resolved 2026-10-23 (checked on hail-dev and fixed with `radar_day.py --refetch`; no code change)

Found in the CoCoRaHS precision study on thor, 2026-10-23 (`~/hail-analysis/cocorahs_precision/`), which decodes the daily
`MESH_Max_1440min` file over the box lat 37 to 41.5, lon -106.3 to -101.4 (wider than hail-dev's crop box). Cells above 254 mm:
- 2020-12-23: 125 cells, max 415 mm, lat 40.85 to 41.49, lon -105.44 to -104.92;
- 2020-12-28: 417 cells, max 596 mm, lat 40.07 to 41.36, lon -105.75 to -104.19;
- 2022-07-31: 3 cells, max 326 mm, about lat 40.95, lon -102.93;
- 2024-08-22: 1 cell, max 368 mm, lat 37.12, lon -102.59.

The hourly `MESH_Max_60min` files flag the same four days (timing study, 5 hourly files). Item 247 found only 2022-01-03 and
2022-01-11 in `radar_days`, presumably because the bad cells on these four days lie outside hail-dev's crop box (item 246). The two
December 2020 days are on the northern Front Range and may overlap it; if so, hail-dev is drawing false radar hail on them. Not
checked: thor has no database.

**Result, 2026-10-23 (hail-dev):** 2020-12-23 (415.3 mm, 16.4 in) and 2020-12-28 (596.1 mm, 23.5 in) were `loaded` with their false
zones built (14 and 62 patches, none near a report): both lie inside the crop box. Each was re-fetched (runs 9080 and 9081) and is now
`suspect`, with its cells, zones and builds gone; no `loaded` day is above 254 mm (2,194 loaded, 4 suspect, 2 missing). 2022-07-31
(max 1.5 in) and 2024-08-22 (max 1.9 in) hold ordinary values inside the box, the bad cells being outside it (item 246), so they were
left alone. The ingest image's radar code was checked byte for byte against the repo before the refetch (its "STALE" flag was
file timestamps only). Why they were `loaded` at all is not established: they were fetched on 2026-10-22, the day the ceiling was added.

**When (as filed):** the next session on hail-dev. Check `radar_days.status` and `max_mm` for these four days; any that is `loaded` with a
value above 254 mm is re-fetched (`radar_day.py --refetch`) as item 247 did, and becomes `suspect`. If the coverage zips grow, the
same check applies to the wider box.

**Related:** items 246, 247; decision log 2026-10-22.

## 251. The findings panel's listing matrix takes about 8 seconds a day

**Status:** open, not urgent (filed 2026-10-23, Phase 5b slice 6)

`findings.listing_matrix` (`hailsys/queries/findings.py`) places about 19,700 held houses by radar layer and by distance to
the nearest actionable report. Measured on `hail-dev` under `hail_app`: 2024-05-30 in 7.3 s, 2023-06-21 in 8.2 s, a day
with no hail in 1.0 s. The panel loads it when a radar day is picked and shows "Loading findings…" meanwhile; the page
stays usable. The first version ran for over two minutes: Postgres folded the single-use `layers` and `reports` CTEs into
the per-house subqueries and redid the `ST_Union` for every house. They are now `MATERIALIZED` (commented in the SQL).
What is left is probably the distance subquery, about 20,000 houses against about 85 reports on the spheroid; not
profiled. The cost grows with houses times reports, and 19,715 is only the listings from 36 pulls on 27 storm days.

**Ideas, none tried:** cut reports to those within a degree box of the house before the spheroid distance; cache the
matrix per day and parameter set (it only changes with a pull or a rebuild); compute it when a pull finishes.

**When:** the panel is too slow in daily use, or before anyone but the developer uses it, or when the held listings
grow by several times (more pulls, a second market).

**Related:** items 238, 250; slice 6 in `docs/phases.md`.

## 252. Slice 6 (findings panel): built; mutation check found one real bug, three survivors accepted

**Status:** resolved 2026-10-23 for the build (`30e126a`); three mutation survivors and one coverage limit
accepted, open with the triggers below.

Built 2026-10-23: `hailsys/queries/findings.py`, `/territory/findings`, `_findings.html`, the box in `_map_panel.html`, the
picker hook in `map.js`, `style.css`; tests `test_findings.py` (62), `test_findings_web.py` (20), the isolation tests in
`test_radar_map_web.py` (now also forbidding email, matching, RentCast and export code from importing `findings`, and
allowing only `territory_findings` to use it), and 49 checks in `tests/js/map_check.js`. Default suite 2026-10-23: 1,875 tests,
1 known error (`test_iem_parse.TestSharedTypecodes`).

**Mutation check (64 mutations, one at a time):** 47 killed at the first pass, 17 survived. 14 survivors were real test gaps
and are closed with new tests, each confirmed killed. **One was a real bug** the tests would have missed: after a new parameter
set is added every day keeps its old build, so `day_overview` called a day built with both sets "older set"
(`built_with_other and not built`); fixed by a test before it shipped.

**Accepted survivors:**
- `x.miles <= 1` and `<= 3` against `<` (the matrix band edges): a point exactly 1 or 3 miles away cannot be reproduced to the
  last digit on a spheroid, so the tests pin both sides of each edge to within 1 to 2 m, not the edge itself. The convention
  (the upper edge belongs to the lower band) is commented above `BANDS`.
- Removing `@login_required` from `territory_findings`: an equivalent mutant, the global `require_login` before-request guard
  already covers every route; the decorator stays for consistency with its siblings.

**Accepted limit:** there is no active viewer account on `hail-dev`, so the "every role may read it" tests cover only the roles
that exist (admin so far). The route is `login_required`, the same as the map's radar route; viewer access itself is not
verified.

**When:** the band edges, if "within 1 mile" ever carries a contractual or advertised meaning; the viewer limit, when a viewer
account exists on `hail-dev` and before any production use.

**Related:** items 238 (7), 250, 251; `docs/phases.md` Phase 5b.

## 253. CoCoRaHS nightly ingest: where the data comes from, and whether absence can be recorded

**Status:** resolved 2026-10-24 (filed 2026-10-23, from the design entry of the same date; source answered 2026-10-23; absence answered 2026-10-24: CoCoRaHS is not usable as an absence signal; the ingest itself is built, `51ec755`)

The likelihood-map design (decision log 2026-10-23, point 7) needs a nightly, idempotent CoCoRaHS ingest. The precision study used CSV
exports pulled by hand on the workstation; nothing yet says how a nightly job gets the same data (an export URL, an API, rate limits, key
or terms), whether the export carries the entry time (the late-entry rebuild needs it), or whether the daily precipitation reports, which have a
hail field, can say where hail did **not** fall. If absence can be recorded, "neither" and "radar only" areas could be checked directly, which
no source does today. The usage policy (CC BY 4.0, attribution when displayed) is read by the fetch tool only; the developer has not read it in full.

**Answered 2026-10-23 (two small public requests, no key):** the source is CoCoRaHS's own export,
`data.cocorahs.org/cocorahs/export/exportreports.aspx`, with `ReportType` (Hail, Daily, Multi-Day, SigWx), `Format=csv`, `State`, `StartDate`,
`EndDate`, `ReportDateType` (Report Date or Report Timestamp), `TimesInGMT` and a date-format code. A one-day Colorado Hail request (2024-05-30)
returned HTTP 200 and 25 reports in the same columns as the study files, each with `EntryDateTime` and `DateTimeStamp`, so the late-entry
rebuild has the entry time. The `Report Timestamp` mode looks meant for changes and late entries; untested, confirm in phase 1. No rate limit,
terms or automated-use text appears on the export page, and the fetch tool could show only the home-page summary of the usage policy (free to
governments, academia and the private sector; CC BY 4.0; acknowledge the source when displaying); the full policy page may say more about
automated use, which is the practical reason for the developer's own read. The nightly job would make one request a night for one state,
identify itself with a contact in the User-Agent, and cache the result.

**Absence, partly answered:** the Daily export has precipitation and snow columns only (`TotalPrecipAmt`, `NewSnowDepth`, ...), no hail field, so
"no hail fell here" cannot be read directly. It does show where observers are: 905 Colorado stations filed a daily report on 2024-05-30 against 25
hail reports. A station inside a radar core that filed rain but no hail report is weak evidence that no hail fell there (weak because observers do
not always file hail). A script to measure that is `docs/analysis/cocorahs-station-coverage-2026-10/` (to run on the workstation).

**Absence, answered 2026-10-24 (the station-coverage study, `summary.txt` in that folder):** it cannot be used as a level. Only 8% of the
radar-only area (MESH of 1.00 in or more, no independent report) has any reporting station within 5 km, against 32% where a report exists;
only 3 to 5% of station-days under such radar are dry; and observers file hail on 16% of station-days even where radar and an independent
report agree (6% radar only). A wet station with no filing is weak, and a dry one is rare. Decision log 2026-10-24. **Revisit:** if the
Daily export is ever ingested for every station-day, or a second state with denser coverage is added.

**When:** before design phase 1 starts (CoCoRaHS ingest), and before any CoCoRaHS text reaches an email.

**Related:** items 205, 210, 215; decision log 2026-10-23.

## 254. The radar zones anchor a patch on any roof-relevant report, wind and snow included

**Status:** open, not urgent (filed 2026-10-24, found while writing `hailsys/likelihood/build.py`, Phase 5c step 1b)

`hailsys/radar/zones.py` `_REPORTS_SQL` has no `report_text` filter: a radar patch is "anchored" (core) by any report that is roof
relevant and at or above its type's minimum. That is 12 types (`report_types.roof_relevant`): HAIL, but also TSTM WND GST, TSTM WND DMG,
DOWNBURST, TORNADO, LANDSPOUT, HIGH SUST WINDS, NON-TSTM WND, HEAVY SNOW and the ice types. Checked 2026-10-24: 9,347 roof-relevant
non-hail reports since 2020-10-15. A damaging-wind report within 5 km of a patch of radar hail makes it core, and its margin follows. The
findings panel (slice 6) and the new likelihood build count HAIL reports only, so the radar layers and the likelihood levels can disagree on
a day with wind reports. `tests/test_likelihood_build.py` pins the difference (a wind report anchors the radar zones, not the likelihood build).

The radar layers are staff-only context and nothing sends from them, so this is a labelling question, not a harm. It matters when thresholds
are calibrated (slice 7) and if the likelihood map is read beside the radar layer.

**Options, none chosen:** filter `_REPORTS_SQL` to the picked report text (hail by default) and rebuild all days (2,194, the rebuild-all
action of slice 7); or keep it and say on the legend that "core" means "a roof-relevant report within 5 km".

**When:** before the radar thresholds are calibrated (slice 7), or if the likelihood layer looks wrong beside the radar layer.

**Related:** items 205, 252; decision log 2026-10-23 (design); `docs/phases.md` Phase 5b and 5c.

## 255. Backtest: the likelihood zones recover 63.5% of hidden IEM hail, under the 80% bar

**Status:** resolved 2026-10-25 (filed 2026-10-25, found by `docs/analysis/likelihood-backtest-2026-10/backtest.py`, Phase 5c step 1; closed by the developer's choice of the zip-overlap bar)

With IEM hidden from the anchors and each day rebuilt (rolled back), 1,027 of 1,617 IEM hail reports of 1 in or more inside the day's radar crop
box landed in Agree or One source: 63.5%, against the bar of about 80% (decision log 2026-10-23). The misses are mostly Context (527): a
hail report inside a warning area where radar shows less than 1 in. Only 63 fall in "none". By year 55% to 73%; Front Range 63%, east 67%. With
CoCoRaHS hidden the figure is 85.1% (143 of 168, 80 days), a pass. Reports outside the crop box (667 of 2,284 IEM points) are out of scope and are
not counted.

Not yet known: whether IEM's imprecise locations and sizes explain the gap, and what a wider radar tolerance would do.

**Next:** split the IEM recall by report size and by the nearest radar value within 5 km. The rules, bands and thresholds are not changed until that is
read.

**When:** before slice 7 proposes a parameter set, and before step 3 (matches with a level) is waived in, which needs the backtest passed.

**Related:** items 254, 243; decision log 2026-10-25 (Evidence: likelihood backtest); `docs/analysis/likelihood-backtest-2026-10/summary.txt`.

**Resolution (2026-10-25).** The developer chose the bar as the zip-overlap measure (the hidden report's zip overlaps Agree or One source,
any overlap, as step 2's pull would take it), because the 80% figure came from radar studies with a 5 km tolerance and the pull works by zip.
Result, points inside the crop box: IEM hidden **88.7%** (1,617 points), CoCoRaHS hidden **93.5%** (168). The other two measures sit beside it:
at the exact point 63.5% and 85.1%; within 5 km of the report 86.9% and 91.1%. By year every figure is 80% or more except IEM 2021 at 80.7%
(just over) and CoCoRaHS 2026 at 76.5% (17 points). The IEM misses at the exact point are radar hail of 1 in or more lying 3 to 8 km away
(`misses.txt`): location imprecision, not a hole in the rules.

**Still open, a caution only:** the zip measure is generous on large plains zips (IEM east 96.6%, against 84.8% on the Front Range). If step 2
bases its estimate on zip size, re-check by area. **When:** when step 2 (pulls from the zones) is waived in.


## 256. `likelihoodadmin.save` left the 20-rule check immediate for the rest of the transaction

**Status:** resolved 2026-10-26 (found by `tests/test_likelihood_admin.py` while the module was being written, slice 7; fixed before it was committed)

`save` ran `SET CONSTRAINTS ALL IMMEDIATE` to surface a short set at once. That setting lasts to the end of the transaction, so a second save in the same
transaction fired `likelihood_sets_complete` (`sql/056`) the moment the set row went in, before its rules, and failed with "has 0 rules". The page commits
after each save, so a user could not hit it, but any caller that saved twice in one transaction (the tests, and the try-on-a-day button's rolled-back
builds) would have.

**Fixed:** `_write_sets` says `SET CONSTRAINTS likelihood_sets_complete DEFERRED` before its writes, then IMMEDIATE to check, then DEFERRED again. Tests that
save twice in one transaction fail with the old behaviour.

**Related:** items 243, 255; `sql/056_likelihood_set_admin.sql`.


## 257. `estimate_pull` had no tests

**Status:** resolved 2026-10-26 (found when splitting `estimate_zips` out of it, Phase 5c step 2a)

`hailsys/rentcast/estimate.py` is behind the live pull page (the zip count and the request estimate a person confirms before spending RentCast money), and no test
imported it: the only file that matched a search for `estimate_pull` was `test_drip_plan.py`, which is about something else. So "the existing tests pass unchanged" proved nothing
when the function was split.

**Fixed:** `tests/test_estimate_zips.py` pins `estimate_zips` (sorted and de-duplicated, empty list, the default one call for a zip never pulled, a zip pulled before costs what it cost
last time and is flagged only when recent) and that `estimate_pull` equals `estimate_zips` over the zips it found and keeps its five keys. `tests/test_zone_estimate_web.py` also asserts the
estimate page's own numbers and pull form are unchanged.

**Not covered, accepted:** the `/pull/estimate` route's quota projection and the recent-pull table are still only exercised by hand. **When:** if that page changes again.

**Related:** items 238, 256; decision log 2026-10-26 (step 2a).


## 258. The matches CSV export broke when `storm_listing_matches` gained a `storm_date` column

**Status:** resolved 2026-10-26 (found by `tests/test_match_zone_rows.py` the same day `sql/057` was applied on `hail-dev`; fixed before any commit reached production, which has nothing deployed)

`hailsys/queries/exports.py` `MATCHES_SQL` selects `({LOCAL_TIME_EXPR})::date AS storm_date` and had `GROUP BY storm_date`. In Postgres a `GROUP BY` name that is both an input column and a
`SELECT` alias binds to the input column. `sql/057` added `storm_listing_matches.storm_date`, so the query grouped by `m.storm_date`, the selected expression was ungrouped, and
`fetch_matches` and `count_matches` (the CSV export, `/exports/matches.csv`) raised `GroupingError`. Nothing tested the export, and the 057 rehearsal checked the table and not the readers.
A sweep of every reader found only these two broken.

**Fixed:** `GROUP BY ({LOCAL_TIME_EXPR})::date, ...`. `ORDER BY storm_date` is unchanged (it prefers the alias). `test_match_zone_rows` calls the export and the other readers, and `test_match_readers`
pins each reader's inner join. The trap is in CLAUDE.md.

**Related:** items 257, 259; decision log 2026-10-26 (steps 2b and 3).


## 259. `slm_zone_facts` accepted a zone row with a NULL level or layer

**Status:** resolved 2026-10-26 (found by `test_wrong_zone_rows_are_refused`; fixed by `sql/058` before any zone row was written)

`sql/057`'s CHECK used `level IN ('agree', 'one_source')` and `layer IN (...)`. With a NULL level that is NULL, and a CHECK passes on NULL, so a zone row with no level or no layer was accepted. The
rehearsal tested a missing set but not a missing level.

**Fixed:** `sql/058` drops and re-adds the constraint with `level IS NOT NULL` and `layer IS NOT NULL`. The table held only `report` rows, so nothing could violate it. The trap is in CLAUDE.md.

**Related:** items 258; decision log 2026-10-26 (steps 2b and 3).


## 260. `/pull/estimate` returned a 500 after its template linked to a route the running process did not have

**Status:** resolved 2026-10-27 (found by the developer clicking "Pull Again"; fixed by restarting `web`)

`pull_estimate.html` gained `url_for('main.zone_estimate', ...)` (Phase 5c step 2a). Flask reloads templates from disk but Python once at startup, and the `web` container had started before
`zone_estimate` existed in `views.py`, so the page raised `BuildError: Could not build url for endpoint 'main.zone_estimate'` for every request, including "Pull Again". The tests passed because each run starts a
fresh process.

**Fixed:** `docker compose restart web`. CLAUDE.md Conventions now says templates reload but Python does not, so restart `web` after changing `.py` files before relying on a template that uses them.

**Not covered, accepted:** nothing checks that the running container is newer than the code. **When:** if it happens again, a status line in `scripts/status.sh` comparing the container's start time to the newest `.py`.

**Related:** items 257, 258; decision log 2026-10-26 (step 2a) and 2026-10-27 (piece c).


## 261. The pull path had no tests

**Status:** resolved 2026-10-27 (found while planning step 2b)

`start_pull`, `run_pull`, `create_pull` and the pull thread in `hailsys/web/jobs.py` and `hailsys/rentcast/pull.py` are the only code that calls RentCast, and no test exercised them: a search for `start_pull` found only
the drip step's own path, a static isolation test and a mock. A mistake there spends money.

**Fixed:** `tests/test_pull_jobs.py` runs the real functions on the real tables in a transaction that is always rolled back, with the RentCast client replaced by a recorder and the thread run inline. It pins that each zip is
requested exactly once for Active listings, the call log has one row per zip, a pull from reports is unchanged, a stale running pull does not block a new one and a fresh one does, an auth error aborts and marks the pull
failed, a server error on one zip leaves the rest to run, an unexpected error marks the pull failed and stops, the report matcher runs after a pull and the zone matcher never does, and every refusal requests nothing and
leaves no row. Eight planted bugs, including a non-atomic commit and a doubled request, were all caught. The first pass found a one-character typo (`%(estimated_api_call)s`) that would have failed the first real pull.

**Not covered, accepted:** the RentCast client's own retry and backoff (`hailsys/rentcast/client.py`) is replaced in these tests. **When:** before the client is changed.

**Related:** items 257, 262; decision log 2026-10-27 (step 2b).


## 262. The zones page showed no pull status, so a finished pull looked stuck

**Status:** resolved 2026-10-27 (found by the developer's first real zones pull)

After "Pull 1 zip" the page showed only the "Pull started" message from the redirect. The pull finished in 3.7 s, but nothing on the page said so until a refresh, and nothing said how it had gone.

**Fixed:** `/pull/zones` has a "Pulls for this day" table (`zonepull.recent_pulls`: the day's latest pulls with source, status, zips, requests, listings and finish time; a pull still running after `PULL_STALE_AFTER`
reads "Stopped?"), reloads itself every 5 s while a pull is running, and both write buttons show "Working..." and cannot be pressed twice. The web container logs each request with its server time and no query string, so a
click that feels slow can be measured (`tests/test_compose_web_log.py`).

**Not covered, accepted:** the slowness the developer felt on the Match click was not reproduced (server time 0.17 s; the page about 0.1 s) and may have been the Cloudflare tunnel or the browser. **When:** if it
happens again, read the new access-log time for that request first.

**Related:** items 260, 261; decision log 2026-10-27 (step 2b).



## 263. Guard tests scan `__pycache__`, so a stray `.py` there fails an unrelated test

**Status:** resolved 2026-10-28 (the stray file deleted); the helper change is open
**When:** next time a guard test is touched

A mis-keyed copy of `zonecontext.py` landed in `hailsys/email/__pycache__/`. Git ignores that directory, but `tests/test_radar_map_web.py` globs `hailsys/**/*.py` and flagged the copy as a second user of the zone send list. The failure was correct but its path (`hailsys/email/__pycache__/zonecontext.py`) was confusing.

**Fix when touched:** have the shared `files()` helper skip any path with `__pycache__` in it. **Related:** decision log 2026-10-28 (step 4 piece 3b).


## 264. `queue_batch` saves the built-in template under the default name

**Status:** resolved 2026-10-28 in step 4 piece 4a (found the same day while planning the zone send mode)
**When:** with piece 4a, before any zone send is queued

`hailsys/email/send.py` (`queue_batch`, the `ensure_current(conn, created_by=user_id, subject_src=..., body_src=...)` call) passes no `name=`, so it always saves under `hail_alert`. That is correct today only because the one built-in a send can reach is `hail_alert`. For the built-in zone template (`hail_alert_zone`, `template_id` None until its first send) it would try to save zone text under the report name and be refused by the report-kind validation, or save the wrong thing. Not hit: no zone send exists yet.

**Fix:** pass `name=template_name` and `match_kind` (piece 4a), with a test that the first zone send saves `hail_alert_zone` as kind zone. **Related:** items 263, 265; decision log 2026-10-28.


## 265. The report send's template list does not filter by kind

**Status:** open, to be fixed in step 4 piece 4b (verified 2026-10-28)
**When:** with piece 4b, before the zone template has a saved row

`hailsys/queries/templatelist.py` (`_SELECT`, `_COUNT_SQL`) filters on `is_active` only. Once `hail_alert_zone` has a saved row (its first send, or a test send) it appears in the report `/send` picker, in `_campaign_templates()` and in the `/templates` list. A report send that picked it would render every email as unrenderable (the engine refuses the wrong kind, and the database trigger refuses it again), so nothing wrong could be sent, but it is confusing and a follow-up could name it.

**Fix:** `fetch_page`, `fetch_one` and `fetch_all` take a `kind`; the report picker and the follow-up list ask for `report`, the zone mode for `zone`. **Related:** items 263, 264; decision log 2026-10-28.
