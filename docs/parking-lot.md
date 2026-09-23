# Hail-System — parking lot, carried into Phase 2

Open items deferred rather than dropped. Each carries the reasoning, because
several were argued through at length and the conclusion alone does not survive
the argument being forgotten.

Written 2026-09-11, at the close of Phase 1. Repo is at commit `9d48760` plus
whatever Phase 1's final commits added. Committed to the repository 2026-09-14;
before that it was carried between working sessions by hand, which is the
problem this file now exists to remove.

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

## 1. Which role sees operational views

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

## 2. `nws_issuer` is NOT NULL and unguarded

`iem_data.nws_issuer` is `NOT NULL`, but `iem_parse.parse_row` returns
`_clean(row["WFO"])`, which is `None` for an empty WFO field. No reject reason
covers it, so such a row would raise `IntegrityError` mid-batch and end the run.

Never observed in 22 years of archive. Left unpatched **deliberately** rather
than guessed at — adding a sixth reject reason for a case that has never
occurred would weaken the closed-enumeration argument that keeps
skip-and-continue from drifting into swallowing whatever goes wrong.

**When:** if a run ever fails naming `nws_issuer`.

## 3. The base image is on an EOL operating system

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

## 4. `county` has case variants and no normalized column

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

## 6. Aggregated one-row-per-zip export

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

## 8. `WALL CLOUD` and `ICE STORM` are not in `report_types`

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

## 9. The absence query has no schedule and nowhere to alert

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

## 10. Do IEM reports arrive after their event date?

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

## 11. Hail size names for email templates

StormerSite renders hail as *"Golf Ball"*, *"Half Dollar"*, *"Ping Pong"*
alongside the inch measurement. An agent reads 1.75″ fine; a homeowner says
"golf ball sized."

`report_types.mag_unit` already distinguishes inches from mph, so the mapping
has somewhere to live. The email wording rule stands: the message claims a
**report**, never damage.

**When:** Phase 5, when templates are written.

## 12. Is a "storm" a first-class entity, or just a query?

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

## 13. Buffer radius default — value, storage, and the per-type asymmetry

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

## 15. Does the frequency cap have a hard floor?

Decided in principle: a short window nobody can click past, plus a soft
warning above it. The actual numbers are unset, and the hard floor needs
enforcing in the database rather than the application, or it is not really a
floor.

**Depends on item 14** — the settings table is where the window value would
live.

**When:** Phase 5, when sending is built. *(`database-schema.md`, open
question 5)*

## 16. Merge field vocabulary — where is it stored?

Established that the merge-field list for email templates should be reference
data, not a hardcoded list, but it is not yet designed. Likely a small table:
placeholder name, source expression, whether it's required.

**When:** Phase 5, when templates are written — same phase as item 11 (hail
size names), which needs the same table. *(`database-schema.md`, open
question 6)*

## 17. What happens to a listing that goes inactive after a match?

A match points at a listing that may since have sold. Does the browser still
show it? Does it still get emailed? Probably worth surfacing `list_status` at
send time and letting the sender decide, but the rule is unstated today.

**When:** Phase 3, when matches start getting made against real listings.
*(`database-schema.md`, open question 7)*

## 18. Retention policy for `raw_payload`

The `JSONB` of every RentCast response is cheap at current volume but grows
without bound. No policy set — probably fine indefinitely, worth revisiting if
`listings` gets large.

**When:** revisit only if `listings` size or storage becomes a real cost. A
trigger item, not a deadline. *(`database-schema.md`, open question 8)*

## 19. Does outreach ever fall back to the office email when an agent has none?

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

## 20. Should append-only be enforced by the database, not just convention?

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

## 21. Should ingest widen past `state=CO`?

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

## 23. Address lookup — "did this address get hit?"

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

## 24. Replace the "Last N days" dropdown with a date picker

Explicit start and end dates, rather than a fixed set of ranges (30/90/365).

**When:** with item 10, which needs range parsing anyway — a territory browse
grouped by zip or city is naturally scoped to an explicit date range, not one
of three preset day counts.

## 25. The match view can only show listings already pulled

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

## 26. PDF export with table and map

Depends on how the map got built; the three options are a headless-browser
print, a server-side static map render, or a `@media print` stylesheet.

**When:** after the map.

## 27. CSV header labels

`"Closest Report (mi)"` / `"Farthest Report (mi)"` (0 = inside the zip),
`mag_unit` folded into the magnitude values, timestamps as
`MM-DD-YYYY HH:MM:SS`. Applies to both the CLI and web exports, since they
share `ZIPS_COLUMNS`.

**When:** before the PDF.

## 28. Vendor Leaflet into `static/` instead of the CDN

**When:** before prod deployment.

**Resolved 2026-09-17.** See `docs/decision-log.md`, "Vendor Leaflet into
`static/`, off the `unpkg.com` CDN" — `leaflet.css`/`leaflet.js` plus the two
marker images the CSS references are committed under
`hailsys/web/static/`, and `base.html` loads them locally. Done ahead of the
"before prod deployment" window stated here rather than waiting for it; kept
here per this file's convention rather than deleted.

## 29. Extract the repeated filter parsing

Five routes now duplicate the `days`/`type`/`actionable` parsing block.

**When:** next time a route needs it.

## 30. Bind-mount `hailsys/` into the `app` service

So one-off checks reflect the working tree rather than the last build. Cost
us time twice.

**When:** soon; dev-only.

## 31. Derive column lists from `cur.description`

Rather than maintaining them alongside the SQL. `ZIPS_COLUMNS` and
`ZIPS_SQL` have drifted twice, and the symptom is a blank cell, not an
error.

**When:** when a third projection drifts.

## 32. `StrictUndefined` in the Jinja environment

Missing template variables currently render blank rather than raising,
which has produced two silently-wrong pages.

**When:** soon.

## 33. `REPORT_POINTS_SQL`'s `LIMIT` keeps the lowest `iem_id`, not the most recent reports

Because `DISTINCT ON` pins the `ORDER BY`.

**When:** if the map ever hits the 2000 cap.

## 34. A favicon

To stop the 404 on every page load.

**When:** whenever.

## 35. Reverse direction of the radar analysis — signatures with no report

Needs event clustering; five overlapping radars re-detecting every ~5
minutes make raw counts meaningless.

**When:** only if the forward result raises a question it can answer.

## 36. `CLAUDE.md` is stale

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

If so, there's no silent-match problem.

**When:** next docs pass.

## 38. Per-event-type radius — needs wind analysis first

Split out of item 13, which item 13's resolution note didn't settle. Hail
cores are narrow, straight-line wind is broad, and a downburst is very
local, so one radius for every event type is a simplification. When there
is evidence, this belongs as a column on `report_types` next to
`roof_relevant` and `min_magnitude` — same kind of per-type judgment,
already version-controlled, queryable from SQL.

**When:** when there's wind analysis to base a number on. *(`tuning.py`'s own
comment already points here.)*

## 39. Admin page — settings table, users and roles, role_required

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

## 41. Index on `report_zip_distances (zcta5)` — for address lookup

The table's only index today is the `(iem_id, zcta5)` primary key, which
serves the `d.iem_id = i.iem_id` join `storms.py` runs. A `zcta5`-first
index would serve a different access pattern — "every report near this one
zip" — which nothing queries yet but item 23's address-lookup tool would.

**When:** when item 23 is built.

## 42. A complete pull where every zip failed still counts as pulled

`run_pull` marks `api_status = 'complete'` once it's iterated every zip,
regardless of how many individual zips returned a non-200 and zero
listings. A pull that technically finished but got nothing back reads the
same as one that worked.

**When:** edge case; revisit if it's observed for real rather than reasoned
about.

## 43. Test scripts attribute to `emp_id 1` (system) by accident

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

## 44. A pull job produces two feed lines

`hailsys/web/jobs.py`'s `_pull_and_match` runs `match_storm` right after
`run_pull`, so one click surfaces as a pull line and a separate match-run
line in the activity feed. Accurate — both things happened — but reads as
more activity than one decision produced.

**When:** acceptable for now; revisit if the feed gets noisy.

## 45. Rebuild step belongs in the verification loop

Companion to the already-filed "Nothing rebuilds automatically" entry
(2026-09-18): that entry names the failure mode, this item is the standing
todo to make a build-and-recreate step a checklist item — or a script —
rather than something that has to be remembered fresh each audit.

**When:** process improvement, no deadline.

## 46. No "pull again" affordance — Pull link only shows on Not pulled

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

`hailsys/web/jobs.py`'s own docstring already names the gap: `daemon=True`
means a thread dies with its process, leaving `api_pulls` stuck at
`'running'` after a restart mid-pull. `api_call_log` shows how far it got,
but nothing marks the run dead — and workstate.py's `'running'` still
counts as pulled, so the Pull link stays hidden for that storm
indefinitely.

**When:** before background pulls are relied on for real operations.

## 48. Badge CSS classes derive from `workstate.py` label strings

`storms.html` builds `badge-{{ row.work_state.state | lower | replace(' ',
'-') | replace(',', '') }}` — the CSS class is computed from the label text
itself, not a stable key. Renaming a label in `workstate.py` (`NOT_PULLED`,
`PULLED`, etc.) silently breaks styling with no error anywhere.

**When:** if a label ever needs to change wording.

## 49. No cap on export date-range width

`/export.csv` and the underlying `storms.py` queries accept any start/end
range with no upper bound. Fine today; the 2019 wide-range timing finding
("Performance: the spatial join was the cost, not the hardware") shows what
an unbounded range can cost, and `report_zip_distances` fixes the specific
cause found, not the general absence of a limit.

**When:** if a wide range gets slow again.

## 50. Monthly RentCast quota tracker

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

## 51. Concurrent pulls by two users on one storm — duplicate spend

Nothing stops two people clicking Pull on the same storm day within
seconds of each other; both would spend real RentCast calls for the same
zips. Low risk at five known users, but a real gap if headcount grows.

**When:** low priority at current headcount.

## 52. Re-check for NULL property coordinates as more zips are pulled

`properties.geom` is generated from `list_latitude`/`list_longitude`; a row
missing either produces a NULL `geom`, silently dropping that property from
any spatial join. Worth a periodic check as more zips get pulled and the
`properties` table grows past its Phase-3 size.

**When:** periodic, as pull volume grows.

## 53. Zero test coverage under `hailsys/web/`

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

## 54. Missing agent email — watch it as a rate

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

## 55. Duplicate properties from RentCast address variants — one lot, two emails

RentCast's `id` is derived from the address string, so a formatting change
upstream mints a new id for the same building (`docs/data-sources.md`). Two
`properties` rows for one lot can each carry a listing and an agent, produce
two matches, and lead to two emails about one property, possibly to two
different people. Not observed yet: an exact check on lower-cased `address_1`,
`address_2` and zip finds no duplicates in the 508 properties, but exact match
cannot see variants like "St" versus "Street", so absence there is not
evidence. Needs a dedupe rule — likely address normalization or coordinate
proximity plus unit — decided before the first send.

**When:** Phase 5, before any email goes out.

## 56. Backfill progress and ETA should count reports, not ID range

`scripts/backfill_zip_distances.py` batches by `iem_id` range and reports
progress and ETA as a fraction of that range. `iem_id` runs 1 to 398,134 for
177,515 reports — the range is 2.2 times the row count (presumably ids
consumed by inserts that did not land), and nothing guarantees the ids are
evenly spread over time. So the logged percent complete and ETA are measured
against ids that do not exist. Count the reports still needing rows up front
and report progress against that.

**When:** only if the script runs again — a ceiling change or a TIGER reload
(`scripts/verify_zip_distances.py` covers the check afterward).

## 57. A "Pulling…" work state for running pulls

`workstate.py` treats any pull that is not `failed` as pulled, so a pull that
is still `running` reads "Pulled, not matched" until its match run lands. The
2026-09-21 pull ran under 2 seconds (22:52:49.18 to 22:52:51.13 UTC) for 4
zips, so the wrong label lasts about that long. Related to item 47: a pull
stuck at `running` after a restart would also read "Pulled, not matched"
indefinitely.

**When:** low priority while pulls take seconds; revisit if they get longer,
which scales with zip count.

## 58. Viewer gating not yet tested with a real viewer account

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

## 61. Force a password change on next login after an admin reset

An admin reset sets a password the admin now knows. Nothing makes the user
replace it. A `must_change_password` flag, set by the reset and cleared by
`/account/password`, with the login redirecting there while it's set, would
close that.

**When:** Phase 4, if wanted.

**Declined for now, 2026-09-23.** See `docs/decision-log.md`, "No forced
password change after an admin reset, for now". Reconsider before staff
accounts exist (Phase 7).

## 62. `scripts/*.py` still default `--radius` to `tuning.py` constants

The web app reads radii from `settings` per request (2026-09-22), but
`export_storm_zips.py`, `test_estimate.py`, `test_match.py` and
`verify_zip_distances.py` still import `DEFAULT_*_RADIUS_MILES` from
`tuning.py`. Once an admin changes a radius, the CLI and the web app disagree
by default — the same two-sources shape the `settings` table exists to remove.

**When:** Phase 4, when convenient.

## 63. A reachable path to `hail-dev` for anyone but the developer

`web` publishes to `127.0.0.1:8000` only (decision-log 2026-09-14), so nothing
off the host reaches it without `tailscale serve` or a tunnel in front of it.
Needed before anyone else logs in — including the viewer test in item 58 if
it's run from another machine.

**When:** Phase 6, or sooner.

## 64. Emailed password-reset link

Today a forgotten password means an admin resets it by hand from `/admin`. A
self-service emailed link needs a sending path.

**When:** Phase 5, once sending exists.

## 65. `scripts/create_user.py` — bootstrap-only, or retire?

It predates `/admin` and never sets `created_by`, so every account it creates
has no author. The admin page now does the same job with attribution. Either
keep it strictly for bootstrapping the first admin (and say so in its
docstring) or remove it.

**When:** before deployment.

## 66. Username convention and its security implications

Usernames are free-form at creation (stripped and lower-cased, nothing else).
Settle the convention — and what it gives away, e.g. whether a username is
guessable from a name or email — before real staff accounts exist, since
changing it afterward means renaming live logins.

**When:** before real accounts get created.

## 67. Header bar doesn't wrap on phones

`.site-header-right` is `white-space: nowrap` and the nav links can't shrink,
so at phone widths the header alone is wider than the viewport and every page
scrolls sideways — the matched-listings overflow fix (2026-09-22) can't stop
that on its own. Lives in `base.html`/`style.css`, shared by every page.

**When:** Phase 4, cosmetic.

**Resolved 2026-09-23** (`d8d91cc`). `.site-header` and `.site-header-left`
wrap, with a 640px breakpoint that tightens padding and lets the nav wrap.
Checked by rendered markup only, not in a browser at phone width.

## 68. Admin users table overflows narrow screens

Eight columns plus the fixed 15rem Actions group. Same fix as the
matched-listings tables: wrap it in `.table-scroll` so it scrolls in its own
box instead of pushing the page.

**When:** Phase 4, cosmetic — same pass as the matched-listings overflow fix.

**Resolved 2026-09-23** (`d8d91cc`). The Users table is wrapped in
`.table-scroll`. Checked by rendered markup only, not in a browser.

## 69. `.action-disabled` has no spacing next to the badge

The greyed Pull/Match text sits flush against the status badge. The link and
button it replaces get `margin-left: 0.5rem`, `font-size: 0.8125rem` and
`white-space: nowrap` from `.pull-link`/`.inline-action`; giving
`.action-disabled` the same three lines makes the two states line up.

**When:** Phase 4, cosmetic.

**Resolved 2026-09-23** (`d8d91cc`). `.action-disabled` has the same margin,
size and `nowrap` as `.pull-link`/`.inline-action`. Checked by rendered
markup only.

## 70. Permits as a source — corroboration first, roof age later

Open-data roofing permits exist for Aurora, unincorporated Adams and
unincorporated Douglas, and each surges after a known hail day
(`docs/data-sources.md` §5). First use is internal corroboration: ranking,
confidence and what the UI shows a sender. "No roof permit on record since
<date>" comes later, if ever, and only where the jurisdiction's records go
back that far. Never "your roof is X years old" (decision log 2026-09-23,
the claim rule). Items 71–84 are the prerequisites and follow-ons.

**When:** parked until the system is running.

## 71. Records (CORA) request to Aurora for full roofing-permit history

Aurora's open-data permits start 2021-09-24, which looks like a rolling five
years. A raw snapshot was taken 2026-09-23 (`data/raw/permits/aurora/`) so
nothing more drops off unseen, but anything older than the window needs a
Colorado Open Records Act request. Whether the window really rolls is still
unconfirmed: re-check the earliest `InDate` on a later day.

**When:** only if roof age (item 70's later half) is pursued.

## 72. Commercial-use terms for permit data

Aurora publishes a disclaimer with an indemnity clause and no licence grant.
Adams and Douglas publish no terms at all. Whether RBI may use permit data
commercially is a question for RBI's attorney, or for each jurisdiction.

**When:** before permit data appears in anything agents see.

## 73. RBI's business map, to set the permit-adapter order

The top-three ranking used in the 2026-09-23 research counted stored
`properties`, which reflects which 5 zips had been pulled, not where RBI
works. By area, unincorporated El Paso, Weld and Adams lead. The build order
should follow where RBI actually wins work.

**When:** before any permit adapter is built.

## 74. Real jurisdiction count from the inventory

`output/jurisdiction_inventory_2026-09-23.csv` (680 rows, 183 zips) includes
slivers where TIGER and DOLA edges disagree. Excluding rows under 0.5% of a
zip, the 2026-09-23 run gave 71 municipalities and 13 unincorporated
counties. That is a starting figure only: Hudson is counted under two codes
(item 75), and the CSV is regenerable output, not tracked.

**When:** with the permits work (item 70).

## 75. Correction and override table for jurisdiction

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

DOLA republishes nightly, but that only dates the publish. How far behind
the real annexations it runs is measurable from the newest `cl_re_date` in
the 1,911-row `Municipal_Boundary` layer. Settle how often
`fetch_municipal.py` + `load_municipal.sh` should run.

**When:** before address search states jurisdictions.

## 77. Near-boundary confidence flag

Flag an address whose point sits close to a municipal boundary:
`ST_Distance` on geography to the nearest boundary, starting at a ~30 m
threshold and tuned against real misses (item 78). Geocoded points and
boundaries each carry error, and an answer 10 m from a line should say so.

**When:** with address search. **Depends on item 23** (geocoding).

## 78. Jurisdiction accuracy test against the permit datasets

The downloaded permit datasets say which department issued each permit, so
they serve as ground truth. Run our address → point → jurisdiction path
over their addresses and count disagreements. This is also what tunes item
77's threshold.

**When:** with address search. **Depends on item 23** (geocoding).

## 79. County assessor parcels as a geocoding-free upgrade

A parcel polygon places an address in a jurisdiction without an
interpolated point. Whether the counties publish parcels openly has not been
checked. `Colorado_Public_Parcels` on gis.colorado.gov exists but was not
examined.

**When:** only if near-boundary misses (items 77, 78) prove common.

## 80. Adams keyword precision; Douglas missing coordinates

**Adams:** after 2016, roofing is mostly identified by keywords in
`Description` with a blank `TypeOfWork`. That filter (16,368 hits) has not
been checked for false matches. **Douglas:** 51% of roofing permits have no
`LOCATION` and need address matching before any spatial use.

**When:** with those adapters.

## 81. Denver: RESCON vs. ROOFSIDE, and the 2017 known answer

Not examined 2026-09-23 because Denver was not in the top three. Open
question: are reroofs in the RESCON layer, or under a separate ROOFSIDE
type? The known-answer test is 18,475 roof permits in 2017, 54.6% above
2016 (`docs/data-sources.md` §5).

**When:** if Denver enters the build order (item 73).

## 82. The annexation layer as boundary-change history

DOLA's 1,911-row `Municipal_Boundary` layer keeps each annexation with its
ordinance number and `cl_re_date`. That answers "when did this land join the
city," which matters if a permit predates an annexation and was issued by
the county.

**When:** only if permit history needs that question answered.

## 83. In-house realtor and contacts database

A searchable store of known realtors, beyond what `realtors` holds from
RentCast pulls, with a different email template for agents RBI already
knows. **Open question:** what system holds these contacts now.

**When:** Phase 5.

## 84. Historical hail aggregation — which areas were hit hardest over n years

A query over existing `iem_data` and `report_zip_distances`, not a new
ingest. Overlaps `/territory` (decision log 2026-09-16, *Territory browse is
its own page*), which already groups a date range by city or zip with
report counts. What is new is ranking over many years, for example by
distinct storm days rather than raw report count, which the ~0.7%
natural-key dedup and busy single days would otherwise skew. Probably a
view of `/territory` or a map layer rather than its own page.

**When:** after Phase 4, alongside the map (item 22).

## 85. Pull estimate shows "last pulled" times in UTC

`pull_estimate.html`'s "Pulled in the last 7 days" table formats
`z.last_pulled.strftime('%Y-%m-%d %H:%M')` with no conversion. `CLAUDE.md`
says to convert to `America/Denver` at display, and the storm list's
"last pulled" date already does (`.astimezone(display_tz)`). Needs the same
conversion, plus `display_tz` passed from `pull_estimate()`.

**When:** small; next pass over the pull estimate.

## 86. The storm list silently drops days past 50 rows

`index()` passes `limit=50` to `storms.fetch_recent_days`, so a wide date range
returns the 50 rows the query orders first, and nothing on the page says more
exist. Found in the Phase 4 audit: a 2024–2026 range didn't reach 2024-05-30.
Same silent-truncation shape as item 33. Either say "showing 50 of N" or
page.

**When:** before anyone relies on a long date range.

## 87. Confirm RentCast's billing-period timezone

`quota.fetch_usage` rolls the period over at midnight Denver time. Whether
RentCast rolls over on UTC or Denver time is unconfirmed. Near a boundary,
up to seven hours of calls could land in the wrong month's count.

**When:** before the quota figure is compared against a real invoice.

## 88. Calls from a key-rejected pull don't reach `api_call_log`

On `RentCastAuthError`, `pull.py` adds the attempts to
`api_pulls.actual_api_calls` but writes no `api_call_log` row, so the usage
figure (which reads the log) leaves them out. Today both totals agree. It only
matters if RentCast bills rejected-key requests, which is unconfirmed.

**When:** if a real invoice disagrees with the usage figure.

## 89. "Matched, none in range" on a pulled storm has never been seen

The rule (`match_ran and pulled`) is verified only on its other half: a
never-pulled storm with empty runs stays "Not pulled". Showing the badge
itself needs a storm that was pulled but has no listings within the match
radius.

**When:** the first time a pull comes back with nothing in range, or with a
deliberate test.

## 90. An app-wide login check instead of per-route `@login_required`

A `before_request` that redirects any request without `g.user` to `/login`,
except `/login`, `/logout` and static files, would make `@login_required`
redundant everywhere and close the forgotten-decorator gap, as the admin
blueprint's hook already does for `/admin`. Considered and deferred
2026-09-23 (decision log, "`role_required` alone where a route needs a
role").

**When:** the next time a route is added outside the admin blueprint, or
Phase 6, before staff use the system.

## 91. `_MATCH_SQL`'s all-types branch is dead

`match_storm` now requires `report_text`, so the
`%(report_text)s::text IS NULL OR …` branch in `_MATCH_SQL` can't run. It's
harmless, but it suggests an all-types path that no longer exists.

**When:** cleanup.

## 92. CSV export missing on the matched-listings and activity pages

Phase 2's outline says "CSV export on every list." The storm list and
territory have one (`/export.csv`); `/storms/matches` and `/activity` don't.
The matched listings are the list Phase 5 acts on, and the likeliest one
someone will want in a spreadsheet.

**When:** Phase 5, before the first real batch is worked from the match page.

## 93. The activity feed shows other users' names to viewers

`hailsys/queries/activity.py` joins `users` for first and last name, so
`/activity` and the storm list's feed show every signed-in user, viewers
included, who ran each pull and match run. Intended for a five-person office,
where "who pulled this" is useful. But it is another user's data behind
`login_required` alone, and nothing records it as a choice. Worth a
decision-log line: keep as is, or show names to senders and admins only.

**When:** next decision-log pass; before staff accounts exist (Phase 7) at
the latest.

## 94. The TIGER geocoder extension is installed, and `tiger` is on the search path

`postgis_tiger_geocoder` and `postgis_topology` came with the
`postgis/postgis:16-3.4` image; nothing decided to add them. The `tiger`
schema is on the default `search_path` (`"$user", public, topology, tiger`),
and it holds empty tables named `county`, `place`, `zcta5`, `edges` and
others, all SRID 4269. A typo'd or unqualified table name could silently
query an empty NAD83 table instead of failing, the same silent-SRID shape
CLAUDE.md warns about. Options: drop the two unused extensions, or take
`tiger` off the search path for `hail_app` and `hail_ingest`.

**When:** before production deployment, alongside item 3 (the base image).

## 95. No test for the `R` = RAIN / HEAVY RAIN composite key

CLAUDE.md lists "IEM `TYPECODE` is not unique" as a known trap, and the parser
does key on `(report_type, report_text)` tuples. But no test in `tests/`
exercises two rows sharing a `TYPECODE` with different `TYPETEXT`, so a
regression back to keying on `TYPECODE` alone would pass the suite. Every
other trap in that list has a test.

**When:** next time the parser is touched.

## 96. Has RBI answered the DNS ask?

`phases.md` runs "the domain and email-sending setup" alongside every phase
from day one, because DNS changes at a small company can sit in an inbox for
weeks, and asks for "DNS access and existing subscription status" in
Phase 0. Nothing in the repo records the answer. Phase 5's first send needs a
sending subdomain with SPF, DKIM and DMARC.

**When:** now: Phase 5 is current, and this is the item most likely to be
waiting on someone else.

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
