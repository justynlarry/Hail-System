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
