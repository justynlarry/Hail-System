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

## 14. Is there a settings table at all?

Radius default, frequency-cap window, monthly API ceiling, warmup send limit —
none of these has a home today. A settings table means tuning them without a
deploy, the same argument that already justified putting `roof_relevant` in
`report_types` instead of in code.

**When:** Phase 2 — items 13 and 15 (frequency-cap floor) are both blocked on
this being decided first. *(`database-schema.md`, open question 3)*

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

## 22. Map — side-by-side with the storm list

Leaflet from a CDN, no build step, consistent with the server-rendered
decision. Three layers: the 183 coverage zips as a static pre-generated
GeoJSON fixture in `static/`, simplified once by a script and cached by the
browser; report points for the selected day, colored by `report_source`; and
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

**When:** after items 10 and 12, at the close of Phase 2.

## 23. Address lookup — "did this address get hit?"

A one-off tool: paste an address, get the reports near it. Different unit of
analysis from everything else built — the coverage-zip join drops out
entirely, since the question is "did a report fall within X miles of this
point" rather than "which of our zips were in range."

The blocker is geocoding. Nothing in the system converts a street address to
coordinates. RentCast returns coordinates for properties it knows, which
covers listings but not an arbitrary address a coworker types in, so a
standalone version needs a geocoder — a new external dependency, an API key,
and a rate limit to respect.

**When:** Phase 3, alongside RentCast, when addresses have coordinates
attached. A manual lat/long entry form would work sooner if someone needs it
before then.

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
