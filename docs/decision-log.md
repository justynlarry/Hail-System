# Decision Log

Running record of design decisions and the reasoning behind them. Append new
entries at the bottom with a date. Do not rewrite old entries — if a decision is
reversed, add a new entry that supersedes it.

The point of this file is that in six months these choices will look arbitrary
without the reasoning attached.

---

## 2026-09-01 — Generalize beyond hail from day one

Event type is a column value, not a table name. The schema handles any NWS
report type; only hail is loaded for targeting initially.

**Why:** costs nothing now. Adding wind or wildfire later becomes an `UPDATE` to
`report_types.roof_relevant` rather than a schema migration and a deploy.
Building a hail-only system would be the same work with a ceiling on it.

---

## 2026-09-01 — Three-stage design: free browse, paid pull, human send

Storm browsing queries our own database and costs nothing. RentCast listing
pulls cost money and are user-initiated. Email sends risk sending reputation and
require a human click.

**Why:** the exploratory step — where someone is figuring out what they even
want — happens entirely on free data. Cost is only incurred after a person has
narrowed the scope deliberately. Each stage is narrower than the last.

**Supersedes:** an earlier plan for a nightly automatic RentCast pull.

---

## 2026-09-01 — RentCast pulls are user-initiated, not scheduled

**Why:** RentCast bills against a monthly lookup allowance. A nightly pull buys
listings nobody reads on days when nobody acts, and the listings will have
changed by the time anyone looks anyway. Automatic fetching is only worth it
when data is consumed on the same cadence it is collected. This is not that.

**Cost:** loses a natural "new since last night" watermark. Recovered by storing
`first_seen_at` on the listing row.

---

## 2026-09-01 — Nothing sends email automatically, ever

There is no code path from the nightly ingest to an outbound message.

**Why:** storm data is sometimes wrong or duplicated, and a person glancing at a
list catches things software will not. And if something goes wrong, it cannot go
wrong four hundred times before anyone notices.

---

## 2026-09-01 — Storm reports stored at full lat/lon fidelity, zips derived on read

`iem_data` keeps exact coordinates. Affected zip codes are computed by spatial
query, not stored at write time.

**Why:** the buffer radius is a tuning parameter that will change. Storing
derived zips would mean re-ingesting history every time it does. Keep the finest
granularity received; derive everything coarser on read.

---

## 2026-09-01 — Storm report history is never trimmed

**Why:** ten years of Colorado is 135,856 rows, which is small for Postgres.
Slowness would be a missing index, not row count. Deleting costs the ability to
answer "when did this zip last get hit" and to replay a period at a different
radius.

---

## 2026-09-01 — Properties and Listings are separate tables

`properties` keyed on `rentcast_id`; `listings` keyed on a surrogate
`listing_id` with a natural key of `(rentcast_id, list_date)`.

**Why:** RentCast's `id` is a *property* identifier. A house listed in 2024 and
again in 2026 reuses it. One combined table means a relist silently overwrites
the listing an agent was contacted about — including which agent.

---

## 2026-09-01 — Listing agent fields are duplicated on `listings` alongside `realtor_id`

**Why:** two different facts. `list_agent_*` is a snapshot of what RentCast
reported at listing time. `realtors` is the resolved, current understanding of
that person. An agent changes name, brokerage, and email over time; the listing
must preserve who listed it under what name.

**Do not normalize this away.**

---

## 2026-09-01 — No realtor deduplication beyond exact normalized email

Jen Watson may exist five times under five email addresses. That is acceptable.

**Why:** merging on name similarity risks linking a live agent to a suppressed
one. Over-emailing is a recoverable annoyance; wrongly silencing a working agent
is invisible and permanent. Asymmetric risk, so err toward contact.

---

## 2026-09-01 — Suppression is a separate table keyed on email, not a flag on realtors

**Why:** if Jen exists five times under five emails, marking one realtor row
leaves four sendable. She asked not to be contacted at an *address*. Email
keying also allows suppressing addresses never seen as a realtor — a bounce, a
forwarded complaint, a phone call.

**Enforcement runs at send time against `dnc_list`, in the same transaction as
the send.** Never from the UI, never from a cached list.

---

## 2026-09-01 — Confidence is a tiered label computed at query time, not a stored percentage

Display format: "Moderate — 3 reports, up to 1.25″, 2 spotters".

**Why:** a percentage implies a probability the data does not support. What
would actually be computed is a weighted average of reporter categories. Once a
number is on screen, people treat it as more meaningful than it is. Showing the
inputs lets a human override with judgment.

Query-time because the weighting will change; a stored score goes stale
silently and leaves rows scored under different formulas with no way to tell.

---

## 2026-09-01 — Email wording claims a report, not damage

"Hail of X size was reported in your area" — never "your listing was damaged."

**Why:** accurate description of a public record. Defensible, survives scrutiny,
and requires no certainty score to hold up. The system reports; the agent decides.

---

## 2026-09-01 — Templates are append-only and versioned

To change a template: insert a new row, set `supersedes_id`, deactivate the old.

**Why:** editing in place would make historical sends claim to have used text
that did not exist at the time. The audit trail becomes fiction. Side benefit:
walking `supersedes_id` backward gives the edit history for free.

---

## 2026-09-01 — `send_log` snapshots the recipient email address

**Why:** if an agent changes address and the realtor row is updated, every
historical send would appear to have gone somewhere it did not. General
principle: anything describing a past event stores its own copy of the facts.

---

## 2026-09-01 — `send_log.realtor_id` is deliberately denormalized

Reachable via `match_id → listing_id → realtor_id`, stored directly anyway.

**Why:** the frequency-cap check runs on every send. A two-hop join for a query
that constant is not worth the normalization purity. The write path is
responsible for setting it correctly.

---

## 2026-09-01 — Sending goes through a queue; `queued` is a real status

**Why:** slow warmup requires throttled sending, and a crash mid-batch must not
leave uncertainty about what went out. The row exists before the attempt.

**Consequence:** the frequency cap counts `queued` rows, not just `sent`. A
message in the queue will arrive; if the check ignores it, a large batch
double-sends before the first clears.

---

## 2026-09-01 — Three user roles: viewer, sender, admin

`admin` manages users and nothing else — cannot touch templates, suppression, or
sending.

**Why:** the roles map to the cost stages. Someone who just wants to know where
hail hit does not need authority to spend API calls or sending reputation.
The narrow admin needs enforcing explicitly in code, because "admin"
conventionally means "can do everything."

---

## 2026-09-01 — Existing DNC lists imported before Phase 5

Marked `source = 'legacy import'`, `added_by = 'system'`.

**Why:** a suppression list that arrives after the first send arrived too late.
Distinguishable source keeps imported rows from drowning the bounce and
complaint signal from new suppressions.

---

## 2026-09-01 — No "currently being viewed" state tracking

Double-*sending* is prevented by `send_log` and the frequency cap. Duplicated
*effort* is not tracked.

**Why:** with four users in one office, someone saying "I've got the August 24
hail" out loud works better than software locks, which bring staleness problems.
Showing "last contacted" per row covers the case that actually matters.

---

## 2026-09-01 — Whole-country boundary data, not Colorado-only

**Why:** the spatial index makes national scope free to query. If RBI ever
chases a storm into Wyoming or Nebraska, that is not a data-loading emergency
mid-event.

---

## 2026-09-01 — IP: code retained, RBI licensed

Justyn owns the code. RBI receives the running system deployed on their
hardware, not source. Non-compete limited to roofers in RBI's service area,
defined by named counties with a term limit.

**Why:** the rate is discounted in exchange for reuse rights. Generic components
(storm ingest, buffer-to-zip, send log, suppression) live in a separate repo
from RBI-specific configuration so the legal boundary follows a file boundary.

**Open:** source escrow so RBI is not stranded if Justyn becomes unavailable.

---

## 2026-09-03 — Territory is a table: `coverage_zips`

RBI's service area is 183 ZCTAs in a table with `area_name`, `reason`, and
add/remove audit columns, not a constant in code or a filter in the UI.

**Why a table:** territory is edited by people, on business grounds, and the
reason a zip is in scope is the thing that goes missing first. A constant makes
every change a deploy and records no author. The add/remove pairs make "we
stopped working Greeley in March" answerable.

**Why the FK to `zcta_boundaries`:** the first hand-built list was 193 entries,
of which **10 had no ZCTA polygon** — PO-box-only zips (`80502`, `80522`,
`80539`, `80632`, `80638`, `80901`), institutional zips (`80225` Federal Center,
`80523` CSU, `80639` UNC), and `80213`, which is not an assigned zip at all. A
zip with no polygon cannot be reached through the spatial join and cannot be
checked against territory, so it is a silent hole rather than an error. Finding
those 10 took a purpose-written script against the raw TIGER `.dbf`. The FK makes
such a row uninsertable instead of periodically re-detected, and it survives
whoever expands the territory later without having read this entry. Dropping the
10 lost no geographic coverage — each sits inside a city already covered by its
residential ZCTAs.

**Cost:** TIGER must load before coverage, and a decennial revision retiring a
ZCTA blocks the reload until reconciled by hand. That is a loud, attended event
about once a decade, traded against a silent hazard that is otherwise always on.

**Enforcement point is the RentCast pull**, because that is the only place money
is spent. **Ingest stays unfiltered** — same argument as storing full lat/lon
rather than derived zips. If ingest dropped out-of-area reports, taking on Pueblo
next spring would leave a permanent hole in history.

The table is keyed on `zcta5` rather than a surrogate, breaking the convention
deliberately: the whole point of the row is to name a Census polygon, and a
surrogate would add a join without adding stability. It is named `zcta5` and not
`zip_code` because the name has to carry the constraint — `zip_code` invites
someone to insert a plausible USPS zip that matches nothing.

---

## 2026-09-03 — A `system` user account, with a real `emp_id`

`users.role` gains a fourth value, `system`, bootstrapped by an `INSERT` at the
end of `sql/002_users.sql`.

**Why an account rather than the free-text string `'system'`:** `added_by` and
`created_by` are `BIGINT REFERENCES users(emp_id)`. Machine-initiated rows —
automatic suppressions from bounces and complaints, the legacy DNC import — need
an author. Without a real row the alternatives are a nullable FK, which makes
"who did this" unanswerable for exactly the rows nobody remembers creating, or a
magic string in a column that is otherwise a key, which is not a foreign key at
all.

**It is not a login, and the database enforces that** rather than trusting the
application:

```sql
CONSTRAINT system_account_cannot_log_in
    CHECK (role <> 'system' OR (is_active = FALSE AND password_hash = '!'))
```

A machine identity that can be activated and given a password is a backdoor with
a name on it. `'!'` is not a valid hash for any algorithm we would use, so no
password can produce it.

**It is not a cost stage.** The other three roles describe what a person is
permitted to spend — free browsing, API calls, sending reputation. This one
describes rows no person created. Anyone reading the role list should not go
looking for the fourth tier of authority; there isn't one.

---

## 2026-09-03 — Naming drift resolved: `_api_calls`, `send_status`, `provider_message_id`

Three places where the schema doc and the DDL had drifted apart. Settled so the
review pass checks correctness rather than re-deciding names.

**`api_pulls` takes the DDL spelling:** `estimated_api_calls`,
`actual_api_calls`, `api_status`. The docs were updated to match.

**`send_log.send_status` wins, DDL side.** Prefixed status columns are the
convention across this schema — `list_status`, `api_status` — and a bare `status`
on one table out of three is the kind of inconsistency that gets typed wrong from
memory.

**`send_log.provider_message_id` wins, doc side; the DDL column was renamed from
`provider_message`.** Not a preference. The column holds an opaque identifier
from the email provider, used to match a bounce or complaint notification back to
the send it belongs to. `provider_message` implies it holds the message, which it
does not, and a column whose name misdescribes its contents will eventually be
used as though the name were true.

**General rule this establishes:** when a doc and the DDL disagree on a name,
pick by which name describes the thing, not by which file was written first.

---

## 2026-09-03 — Three CHECK constraints kept, with their reasoning recorded

They were in the DDL but in no document, which meant the next reader would have
had to guess whether each was load-bearing or leftover.

**`storm_listing_matches.distance_within_radius`** — `distance_miles <=
radius_used`. A match further away than the radius that produced it is
arithmetically impossible; such a row is a matcher bug, not a finding. Catching
it at write time keeps a bad distance calculation from quietly widening
targeting, which is the failure that would look like success.

**`send_log.sent_has_timestamp`** — a row cannot claim `sent`, `bounced`, or
`complained` without a `sent_at`. Those three statuses assert that a message left
the building. An audit trail that records that something happened but not when is
not an audit trail, and `send_log` exists to answer questions under scrutiny.

**`email_templates.report_type_pair_complete`** — `report_type` and `report_text`
are both null or both set. This is **not** redundant with the composite foreign
key. Postgres FKs default to `MATCH SIMPLE`, which **skips the check entirely
when any column in the key is null**. Without this constraint a half-set pair
passes the FK unverified and points at nothing — a template that appears scoped
to an event type but is scoped to no row that exists.

The third one is worth remembering as a general hazard: a composite FK with any
nullable column needs a completeness CHECK beside it, or it is not enforcing what
it appears to enforce.

---

## 2026-09-03 — `listings.list_office_website` retained

**Supersedes** the 2026-09-01 non-goal "no builder or agent-website fields,"
in part.

The office website is kept as a brokerage snapshot on `listings`, alongside the
other `list_office_*` fields. It costs one nullable text column and travels with
the rest of the office block RentCast already returns.

**Agent websites remain out of scope**, and the builder half of the original
non-goal stands unchanged — new construction is not accessible to RBI, so builder
fields buy nothing. The original reasoning against agent websites still holds:
they are trivially searchable, so storing one is a stale copy of something a
person can find in five seconds.

The distinction is that an office website is part of the identity snapshot of the
brokerage a listing came from, which is the same category as `list_office_name`
and `list_office_phone` — facts about who listed the house at the time, preserved
because the entity changes.

---

## 2026-09-03 — Email normalization is `lower(trim(...))` everywhere

Every normalized email column in the schema: `realtors.email_norm`,
`realtors.office_email_norm`, `listings.list_agent_email_norm`,
`listings.list_office_email_norm`, `dnc_list.email_norm`. Several had been
written with `upper()`.

**Why this is correctness and not style:** the suppression check compares
`dnc_list.email_norm` against the normalized address being sent to. If two tables
normalize with different case functions the comparison never matches — and it
never errors either. The failure mode is a suppressed agent receiving mail, with
nothing in any log indicating that a check ran and failed. A rule that must hold
in the database has to hold identically in every table it touches.

`lower` over `upper` because it matches how addresses are written and read, so a
normalized value is still recognizable in a query result.

**Also settled here:** `listings.list_agent_email_norm` is **not** unique. It had
been declared `UNIQUE`, which would have permitted each agent to list exactly one
house ever, with the second insert failing on a constraint violation that looks
nothing like its cause. Agent identity uniqueness belongs on `realtors.email_norm`.
`listings` holds a snapshot of who listed a house at a point in time, and many
listings sharing one agent email is the normal case, not a duplicate. The office
norm columns are not unique either, for a plainer reason: a brokerage address is
shared by every agent in the office by design.

---

## 2026-09-03 — Legacy DNC import: `source = 'legacy_import'`, `added_by` is the system account

**Supersedes** the value spellings given in the 2026-09-01 entry "Existing DNC
lists imported before Phase 5." That entry's reasoning is unchanged and still
stands; only the two literals are corrected.

`dnc_list.source` had a CHECK list of `('reply', 'unsubscribe', 'hard_bounce',
'complaint', 'manual')`. The documented import value was `'legacy import'`, which
is not in that list — **the import would have failed on its first row**, before
any send, which is precisely the moment the list is supposed to be in place.
`'legacy_import'` is now in the CHECK, snake_case to match `hard_bounce`.

`added_by` was free text when the original entry was written and is now
`BIGINT REFERENCES users(emp_id)`. The value is **the system account's `emp_id`**,
not the string `'system'`.

The reason the source value must stay distinguishable is unchanged: imported rows
would otherwise drown the bounce and complaint signal from new suppressions, and
those two are the numbers that say whether targeting and copy are working.

---

## 2026-09-03 — Office-email fallback deferred, with the hazard recorded

`realtors.office_email_norm` and `listings.list_office_email_norm` exist so a
batch can be pre-flighted against `dnc_list`. Whether outreach ever *sends* to an
office address when the agent has none is **not decided**, and nothing should be
built toward it yet.

Recorded now because the hazard is not obvious and would be found late.

**Suppression already handles the fallback correctly** — the check runs against
the address actually used, not against a person, so a suppressed `info@` inbox is
safe by construction.

**The frequency cap does not.** Fifteen agents at one brokerage with no email of
their own all resolve to a single `info@` inbox. Each is a distinct `realtor_id`,
so a per-realtor cap counts fifteen separate sends and the shared inbox receives
fifteen emails from one batch. Shared inboxes are also the least tolerant
recipients on any list, and a complaint is exactly the signal that means bad
targeting.

If this is ever built, two things have to change with it: the cap needs a
**per-address window** alongside the per-realtor one, and `send_log` likely needs
a column recording whether the recipient was a person or an office. Without that
second column `realtor_id` quietly stops meaning "who we emailed" and starts
meaning "who this was about" — a different fact under the same name, which is the
failure this schema otherwise works hard to avoid.

---

## 2026-09-03 — `iem_data` natural key uses `UNIQUE NULLS NOT DISTINCT`

`uq_iem_natural_key` on
`(utc_datetime, latitude, longitude, report_text, magnitude)` is declared
`UNIQUE NULLS NOT DISTINCT`. PostgreSQL 15+; we are on 16.

**Why:** SQL treats two nulls as distinct, so by default two rows identical in
every column, both with null `magnitude`, are not duplicates to a unique
constraint. Null magnitude is exactly the documented IEM `None` trap — 3,353 of
135,856 rows. The nightly job re-pulls a 30-hour overlapping window and relies on
`ON CONFLICT DO NOTHING`, so without this the ingest duplicates those rows
**every night**, and a duplicated `iem_data` row multiplies into duplicate matches
and duplicate sends.

**The tradeoff, stated plainly:** two genuinely distinct reports at the same
minute, at the same ~1 km coordinate, of the same type, both with no magnitude,
now collapse into one row. That is rare. Daily duplication is certain. Losing one
co-located report is cheap; a duplicated send is not — it reaches a real person
twice and is the kind of error that costs sending reputation.

**Alternative rejected:** a unique index on `COALESCE(magnitude, -1)`. It works on
any version but needs a sentinel that can never collide with a real magnitude,
and it hides the intent inside an expression.

---

## 2026-09-03 — `report_types.mag_unit` is nullable; NULL is not `'none'`

`NOT NULL` dropped. The `CHECK (mag_unit IN ('inches','mph','none'))` stays, so a
non-null value must still be one of the three.

**Why:** the two states mean different things. `'none'` means *this report type
has no magnitude* — a flash flood does not have a size. NULL means *we do not
know what the magnitude is measured in*. Seven of the 37 types are in the second
state, and they are exactly the seven with `unit_confidence = 'unknown'`:
DENSE FOG, EXCESSIVE HEAT, EXTREME COLD, EXTREME HEAT, EXTR WIND CHILL, FOG, and
TORNADO.

TORNADO is the one that shows why this matters. It has magnitudes — EF numbers —
but they are not inches and not mph. Storing `'none'` would assert it has no
magnitude, which is false. Storing `'inches'` is the documented trap that reads
EF 0–2 as hail sizes. NULL is the only honest value, and `unit_confidence`
exists to carry precisely that distinction. Collapsing the two states destroys
the column that was built to record it.

The old DDL also made this unloadable: `NOT NULL` rejected 7 of 37 rows, so the
reference load could not complete.

---

## 2026-09-03 — Index naming: `{table}_{column}_idx`, `_gix` for spatial

Full table and column names, no abbreviations. Multi-column indexes list the
columns in order. GiST indexes keep the `_gix` suffix.

So `slm_listing_idx` became `storm_listing_matches_listing_id_idx`,
`listings_realtor_idx` became `listings_realtor_id_idx`, and
`send_log_realtor_sent_idx` became `send_log_realtor_id_sent_at_idx`.
`iem_data_geom_gix` and `zcta_boundaries_geom_gix` are unchanged.

**Why this shape:** it matches what Postgres generates on its own for implicit
indexes, so hand-written and automatic names look alike and neither stands out as
special. Abbreviations like `slm_` save eight characters and cost the ability to
find an index by guessing its name. `_gix` is the PostGIS idiom and signals at a
glance that an index is spatial, which changes how you reason about whether a
query will use it.

Foreign keys are named the same way for the same reason: `fk_{table}_{target}`,
explicitly, on every FK. **Not** because duplicate constraint names collide —
they do not; CHECK and FK names only need to be unique per table, and it is
index-backed constraints that share a namespace with tables database-wide. The
reason is narrower: a constraint you may need to drop should have a name you can
predict from reading the file, rather than an auto-generated one you have to look
up first.

---

## 2026-09-03 — Generated seed SQL removed from `reference/`

`reference/seed_report_types.sql` and `reference/seed_sources.sql` deleted.

**Why:** both began with `DROP TABLE IF EXISTS` against a canonical table name
and then recreated it with a different schema — `report_types` keyed
`(typecode, typetext)` with eleven columns, rather than the five-column
`(report_type, report_text)` table in `sql/003`. Run in the wrong order they
either fail loudly against the foreign keys from `iem_data` and
`email_templates`, or, on an empty database, silently install the wrong schema
and let everything downstream fail later for reasons that point nowhere near the
cause.

They were build artifacts of `scripts/build_reference_tables.py`, not a
deployment path. The real loading path is the CSVs plus `load_reference.sh`,
which stages and merges rather than dropping.

**Consequence to watch:** `build_reference_tables.py` still writes both files on
its next run. Either it stops emitting them or they are written somewhere that
cannot be mistaken for a load step. Not fixed here because the script was not in
scope for this pass.

---

## 2026-09-03 — Append-only is application-layer; database enforcement deferred

`send_log` and `email_templates` are append-only by convention and in code. There
is no trigger, no rule, and no `REVOKE` — nothing in the DDL prevents an `UPDATE`
or a `DELETE`.

**Why record this rather than fix it:** the schema doc states as a principle that
"rules that must hold live in the database, not the interface," and this rule
does not. That gap should be visible rather than discovered later by someone who
assumed the guarantee was real.

**Why defer rather than implement now:** the obvious enforcement,
`REVOKE UPDATE, DELETE`, also blocks the legitimate provider-status update on
`send_log` — the bounce and complaint write-back that the table is designed
around. Getting it right means either a trigger that permits only the status
columns to change, or splitting status updates into a separate table so the log
itself is genuinely insert-only. Both are judgments about a write path that does
not exist yet. Recorded as open question 10, to be settled in Phase 5 when
sending is built and the real update pattern is known.

---

## 2026-09-03 — `report_sources` is a lookup, with no foreign key from `iem_data`

A 36-row table keyed on the normalized source string, carrying
`confidence_tier`, `is_automated`, and a display name. `iem_data` does **not**
reference it.

**Why no FK:** `report_source` is free text typed by individual NWS offices. The
ten-year archive contains `DEPARTMENT OF HIG` and `DEPT OF` — each one report,
each truncated mid-word by a person at a keyboard. A foreign key would have
failed the nightly ingest on those rows and on every future variant of them, and
failing the ingest is the one thing that must not happen: the storm data is the
free, automatic half of the system and it has to keep working unattended.

`report_types` can carry an FK because 37 NWS report types are a closed,
documented set. Sources are an open set and always will be. The same word —
"reference data" — covers two different guarantees, and the difference decides
whether an FK is safe.

Modelled on `zcta_boundaries`: joined when needed, never a constraint on a write.
An unrecognized source yields a NULL tier and the UI shows "unrated" rather than
dropping the report.

**`CHECK (source = upper(trim(source)))` is the load-bearing part.** The join is
`iem_data.report_source_norm = report_sources.source`, and it only works because
both sides are `upper(trim(...))`. Without the CHECK, one row inserted as
`Trained Spotter` matches nothing, raises no error, and silently drops that
source's confidence signal — the identical failure mode as an email
normalization mismatch, which this project has already been bitten by once.

**What is deliberately not in the table:** the report counts, per-qualifier
breakdowns, and hail measured-rates from `reference/sources.csv`. Those describe
one 2016–2026 extract and go stale the moment the nightly job runs. They are
evidence for the judgment, not the judgment. The evidence stays in `reference/`
and `data_quality_notes.md`; the table stores only what stays true.

**This is also what makes the confidence label computable.** "Moderate — 3
reports, up to 1.25″, 2 spotters" requires knowing that a trained spotter
outranks a member of the public, and until now there was nowhere for that fact
to live.

---

## 2026-09-03 — A `qualifiers` table is deferred, not rejected

`iem_data.report_qualifier` keeps its `CHECK (report_qualifier IN ('M','E','U'))`
and a column comment. No table.

**Why not now:** there are three codes. A three-row table earns its place only if
it carries text worth displaying, and the text available is actively wrong.
`reference/qualifiers.csv` glosses `M` as "Measured - magnitude was measured with
an instrument", which is precisely the claim the documented trap disproves — `M`
tracks reporter training, and 97.8% of `M` and 94.9% of `E` hail values land on
the same coin-and-ball catalog. Loading that text would take a trap already paid
for and promote it to a UI label telling the sender the opposite of what is true.

The CSV also is not shaped like a table: five rows for three codes, one being an
empty-string row for "no qualifier supplied" (already handled by the column being
nullable) and one a totals row that any positional load would ingest as a bogus
qualifier.

**The table also has no remaining job.** `database-schema.md` already steers away
from qualifier as a confidence signal in favour of `SOURCE`, and
`report_sources` now supplies that. Qualifier is metadata about a report, not a
basis for deciding whom to contact.

**Reversal condition:** if the UI ever displays qualifier to a user, the caveat
needs somewhere to live, and a three-row table is a reasonable home for it. In
that case the text is **written here, from the trap as documented**, and not
imported from that CSV.

---

## 2026-09-03 — `report_types.min_magnitude` added; thresholds left NULL

`NUMERIC(6,2)`, nullable, same scale as `iem_data.magnitude` so comparisons need
no cast. `CHECK (min_magnitude IS NULL OR mag_unit IN ('inches','mph'))`.

**Why the CHECK:** a floor on a type with no magnitude to compare against is
meaningless. `TSTM WND DMG` has `mag_unit = 'none'`; a threshold on it would
produce a filter that silently excludes every row of that type. Caught at write
time instead.

**The semantic that the DDL cannot express:** when `min_magnitude` is set and a
report's `magnitude` is NULL, `magnitude >= min_magnitude` evaluates to UNKNOWN,
not TRUE, so the report is excluded. For a wind gust with no recorded speed that
is correct — it cannot be assessed. The consequence is that **a type cannot have
both a floor and an include-the-unmeasured behaviour**, and choosing a floor is
also choosing to drop that type's unmeasured reports. Recorded in the column
comment, because it is invisible in the declaration and will surprise someone.

**All values left NULL.** Thresholds are a business decision and are being made
against the measured distributions rather than in the abstract. What the archive
shows, for the record:

- `NON-TSTM WND GST`, 17,368 reports, none null: median 57 mph, 51.0% below the
  58 mph NWS severe criterion. A floor at 58 halves the type — to 8,505, still
  larger than all hail.
- `HAIL`, 6,304 reports, none null: median 1.00″, 28.4% below 1.00″. But the
  distribution is not continuous — **96.7% of values sit on the NWS coin-and-ball
  chart, and `1.00″` alone is 2,011 reports, 31.9% of all hail.** Only 47
  distinct values appear. A floor of `>= 1.00` keeps that spike; any floor
  between 1.01 and 1.25 drops a third of all hail in one step. The cliff is an
  artifact of how sizes are reported, not of how hail falls.

---

## 2026-09-03 — Loader runs in its own image; the DB service stays stock

`docker/loader.Dockerfile`: `FROM postgis/postgis:16-3.4` plus the `postgis`
client package, pinned to `3.5.2+dfsg-1.pgdg110+1`.

**Why a second image:** `postgis/postgis:16-3.4` ships only the server-side
extension. `shp2pgsql` lives in the separate `postgis` client package, and the
TIGER load cannot run without it.

**Why the base image made this look impossible:** it clears
`/var/lib/apt/lists`, so `apt-cache policy postgis` reports
`Candidate: (none)` and a bare `apt-get install` fails with "unable to locate
package" — indistinguishable from the package not existing. It does exist, in
the PGDG repo the base image already has configured. `apt-get update` first is
the entire fix. Worth remembering as a general shape: on a slimmed image, "not
found" usually means "not indexed", not "not available".

**Why not put it in the DB image:** the container holding the data should not be
rebuilt to add a one-shot utility. Client 3.5.x against a 3.4 server is safe —
`shp2pgsql` is a standalone converter that emits SQL text and never links against
the server — but the version is pinned anyway so a PGDG refresh cannot change
the loader underneath us.

**Also fixed while verifying:** the script used `shp2pgsql -d`, which emits a
`DropGeometryColumn` for a stage table that does not exist on a first run. Under
`ON_ERROR_STOP=1` that killed the load before it began — a script advertised as
safe to re-run could not run once. Now an explicit `DROP TABLE IF EXISTS`
followed by `-c`.

---

## 2026-09-03 — The buffer query needs a geography index, not the geometry one

`zcta_boundaries` carries two GiST indexes: `zcta_boundaries_geom_gix` on `geom`,
and `zcta_boundaries_geog_gix` on `(geom::geography)`.

**Why:** the buffer query is written in metres, so it casts —
`ST_DWithin(geom::geography, point::geography, 8046.72)`. That cast is evaluated
per row and therefore cannot use an index on `geom`. Measured against all 33,791
ZCTAs: **parallel sequential scan, 17.9 seconds**. With the functional index on
the cast expression, the same query becomes a bitmap index scan at **22 ms**.
855×, for an index that builds in 10 seconds.

This matters beyond speed. `database-schema.md` said the GiST index was "the
entire performance story for the buffer query" — and that was true of the
intent but false of the schema as written, because the index did not match the
predicate the query actually uses. An index only helps the expression it is
built on.

**Why not avoid the cast instead:** `ST_DWithin` on raw geometry with a radius in
degrees does use `geom_gix`, but a degree of longitude is 85.6 km at the Front
Range and 111 km at the equator, so a fixed degree radius silently changes real
size with latitude. Correct-but-slow beats fast-but-quietly-wrong; indexing the
correct expression gets both.

Both indexes are kept: `geom_gix` serves geometry predicates like
`ST_Intersects`, `geog_gix` serves distance in metres.

---

## 2026-09-03 — Initial `roof_relevant` set and magnitude floors

Twelve of 37 types are roof-relevant. Five carry a floor.

| Type | Floor |
|---|---|
| HAIL | 1.00 in |
| TSTM WND GST | 58 mph |
| NON-TSTM WND GST | 58 mph |
| HIGH SUST WINDS | 40 mph |
| HEAVY SNOW | 6 in |

Unfloored, because the damage or the event *is* the report: TSTM WND DMG,
NON-TSTM WND DMG, DOWNBURST, TORNADO, LANDSPOUT, SNOW/ICE DMG, FREEZING RAIN.

**The premise that decided this.** `roof_relevant` was initially being treated as
gating what is *queryable*. It is not — every report is in `iem_data` regardless,
and a viewer can browse all of it. What the flag gates is **outreach**: which
types put a listing in front of someone with a send button. So the test is not
"might someone want to look at this," it is "would I email an agent about it."
That reframing is what moved four types.

**`SNOW` is N, `HEAVY SNOW` is Y.** SNOW is 85,051 reports, 63% of the entire
archive, and most of it is an ordinary February. The pitch also does not survive
contact: "12 inches of snow was reported near this listing" is a weather report,
not a public record implying damage. Colorado snow is dry and roofs here are
pitched for it; the real failure modes are ice dams and wet spring loading, and
HEAVY SNOW captures the second because a forecaster applied judgment when
choosing that label. That is a free human filter rather than a number we would
have to invent.

The archive confirms the label is doing real work: SNOW's median is 3.0 in with
78.6% under six inches, against HEAVY SNOW's median of 9.1 in with only 7.5%
under six. Approximating the label with a threshold on SNOW would need a floor
around 8 in, and would still be a number we picked over one the NWS picked with
more context.

**`HEAVY SNOW` still gets a 6 in floor.** Even after the label filter it is 34.5%
of the outreach set unfloored — more than hail. 643 of its reports are under six
inches, which contradicts the label's own implication. Cheap cut, same logic as
hail.

**`FUNNEL CLOUD` is N.** By definition it has not touched the ground. Nothing
happened on the roof. Having it Y while LANDSPOUT was N had it exactly backwards.

**`WILDFIRE` is N**, for two reasons. `CLAUDE.md` uses wildfire as the worked
example of a deferred addition — "adding wildfire later is an `UPDATE`, not a
deploy" — and turning it on now spends the example. Substantively, a
fire-affected house is usually either unlisted or a total loss, and neither is a
roof-repair conversation. **`DEBRIS FLOW` is N** for the same substantive reason:
a post-fire mudslide is not a roof event.

**What the numbers say about which decision mattered.** Measured against the
135,856-row archive:

| Set | Reports | Share |
|---|---|---|
| The 15-type list in `data-sources.md` | 37,042 | 27.3% |
| This 12-type set, unfloored | 36,799 | 27.1% |
| **This 12-type set with floors** | **24,124** | **17.8%** |

The type-list question is worth 243 reports — 0.2 points. The floors are worth
12,675 — 9.3 points. And excluding SNOW, decided before either, was worth 56
points on its own. The ordering is worth remembering: one type decided the scale
of this system, the floors tune it, and the rest of the list barely moves it.

Composition of the final outreach set: NON-TSTM WND GST 8,505 (35.3%),
HEAVY SNOW 7,905 (32.8%), HAIL 4,515 (18.7%), TSTM WND GST 1,729 (7.2%),
everything else 1,470 (6.1%). **Hail is under a fifth of it**, which is worth
knowing for a company whose pitch is hail.

**The floors sit on modal values, not in gaps — the sensitivity is on the
record.** `1.00 in` is 2,011 reports on its own, 32% of all hail, and 96.7% of
hail values land on the NWS coin-and-ball chart with only 47 distinct values in
6,304 reports. `50-60 mph` is 39.0% of NON-TSTM WND GST, and the 58 mph line runs
straight through that bucket. So a future "what if we tried 1.25?" is a **cliff,
not a slope**: it would drop a third of all hail in one step.

The defense is that these are published NWS severe criteria rather than numbers
we invented — 1.00 in and 58 mph are the thresholds the Weather Service itself
uses, and 1.00 in is also roughly where the roofing industry draws the
asphalt-shingle damage line. But anyone re-tuning them should know they are
balanced on a peak, and should look at the distribution before moving them.

Values live in `planning/report_types.csv` and load via `load_reference.sh`.
Because that load is `ON CONFLICT DO NOTHING`, changing them after the first load
is an `UPDATE`, not a re-run.

---

## 2026-09-03 — Data quality: the `SNOW` magnitude tail is not single reports

`SNOW` has a maximum magnitude of **175 inches**, with a handful of values above
60 in (60-66, 66-72, 72-78, 84-90, and the 175 outlier — 5 reports total out of
85,049).

Colorado does not get 175 inches in one storm report. These are almost certainly
**seasonal or storm-total accumulations** entered against a single LSR, not the
snowfall of one event.

**Inert today**, because `SNOW` is `roof_relevant = FALSE` and nothing queries it
for outreach. Recorded because it would stop being inert the moment anyone
flipped that flag: a magnitude floor on SNOW would admit these rows first, and
they are the least trustworthy in the type.

**Needs a look before SNOW is ever turned on.** The likely shape of a fix is a
sanity ceiling in the ingest that flags rather than drops — consistent with
storing what IEM sends at full fidelity and deriving judgments on read, the same
principle that keeps zips out of `iem_data`.

---

## 2026-09-04 — Backfill and nightly are two scripts over one parser module

The one-time historical load and the recurring nightly job are separate entry
points. They share a single parser module; neither has its own copy.

**Why:** the backfill is exercised over five years of data by a person watching
the output. The nightly job sees roughly forty rows and nobody watches it at
all. Separate parsers would mean the code that got tested and the code that runs
unattended are different code, and the difference would surface at 4am on a row
nobody has ever looked at.

The entry points differ in what they legitimately differ in — window
computation, how much they log, `run_mode` — and in nothing else.

---

## 2026-09-04 — The nightly ingest gets a run-log table, `ingest_runs`

`api_pulls` was considered and rejected as the place to record this.

**Why not `api_pulls`:** it records *spend*. Every column on it — `emp_id`,
`estimated_api_calls`, `actual_api_calls` — exists to attribute money to the
person who chose to spend it. IEM is free and nobody chooses. Widening that
table to cover a second, unrelated kind of run would make every column on it
conditionally meaningful, which is how a table stops being readable.

**Why a table rather than journald:** the alert that matters most is the
*absence* of a run. A script that never fires cannot report that it never fired
— there is no process to write the log line. In journald, "ran and found
nothing" and "never ran" both look like silence, and distinguishing them means
parsing text and reasoning about gaps. In a table it is a query: is there a
`complete` row whose window covers last night? Everything else the table records
is secondary to that one question.

Consequence: `ingest_runs` carries **no `emp_id`**, unlike every other
operational table. The runs are system-initiated, and pointing them at the
`system` account would imply an actor where there is none.

---

## 2026-09-04 — Malformed rows are rejected, logged, and skipped — not repaired

A row the parser cannot read is written to `iem_ingest_rejects` and the run
continues. It is never guessed at, patched, or silently dropped.

**Why this is not lossy:** `raw_row` holds the input line verbatim. Nothing is
destroyed; a rejected row can be read, replayed, or entered by hand. Without
that column this would be a record that something was thrown away, which is
worse than no record at all.

**Why it is bounded:** skip-and-continue applies only to an enumerated list of
reasons, enforced by a CHECK — `field_count_mismatch`, `unknown_report_type`,
`unparseable_timestamp`, `unparseable_coordinate`, `unparseable_magnitude`. Any
other exception must terminate the run.

The enumeration is the whole safeguard. A free-text `reason` column would let
the parser grow a new tolerated failure every time it met something it did not
understand, and a skip-and-continue loop with an open-ended tolerance is how
silent data loss happens. Adding a reason takes a migration and a human
decision, deliberately.

`raw_row` is `TEXT`, not `JSONB`: a row is in the table precisely because it did
not parse, and the malformation that rejected it is often the same thing that
would make it invalid JSON. Contrast `listings.raw_payload`, which is `JSONB`
because that data arrives well-formed.

---

## 2026-09-04 — The 76 unquoted-comma `CITY` rows are rejected, not realigned

76 rows in the archive carry an unquoted comma inside `CITY`, which shifts every
field after it. They are rejected as `field_count_mismatch`.

**Realignment is possible.** `CITY` is field 9 and is not stored, so the tail of
the row reads correctly counting from the right, and the extra field could be
absorbed. This was not a question of feasibility.

**Why not:** all 76 are from 2018, all from GJT, all `MESONET`, all outside
`coverage_zips`, and all below the magnitude floors. Not one of them would ever
reach an outreach query. Realigning them means writing a special case into the
parser — the code that runs unattended every night — that would fire once during
the backfill and never again, and would sit there afterwards as a branch nobody
can test and nobody dares remove.

Rejecting them costs 76 rows that were never going to be used, and keeps the
parser a parser.

**Reverses if** the pattern appears in any row dated after 2018. A recurring
malformation is a parser problem; a dead one from a single office in a single
year is a historical artifact.

Note that 2018 is the test, not 2021. The archive floor is `2021-01-01`, so the
nightly job will never see these 76 rows at all — only a backfill reaching
further back than the floor does. Those are separate numbers and conflating them
would set the tripwire three years too late: a 2019 or 2020 occurrence would
prove the malformation outlived 2018 while sitting below a 2021 threshold and
raising nothing.

---

## 2026-09-04 — A run that skipped rows still exits 0

`rows_skipped > 0` is an alert condition, raised off the table. It is not a
non-zero exit status.

**Why:** systemd's job is to answer whether the process ran. That is a different
question from whether the input was clean, and collapsing the two costs the
first one. A unit parked in `failed` because the NWS invented a report type
trains everyone to ignore `systemctl --failed`, and the next time it means
something real — the host is down, the timer never fired — nobody looks.

So the split is: systemd tracks whether the process ran, and Irin alerts on
`rows_skipped > 0`. The process succeeded; some of its input did not.

**The Irin half is not Phase 1.** Phase 1 delivers the column and the
condition — `rows_skipped` is populated and `SELECT ... WHERE rows_skipped > 0`
answers the question. Nothing is wired to anything, and no alert fires. Until
that integration exists the check is a query someone runs, which is worth
stating plainly: an alert nobody has built is not an alert, and this entry
describes where the signal *will* be read from, not a monitor that is watching.

Expect the first backfill window covering 2018 to report a non-zero
`rows_skipped`. That is the mechanism working, not a failure.

---

## 2026-09-04 — No partial unique index preventing concurrent runs

A `UNIQUE ... WHERE run_status = 'running'` index was considered and declined.
Nothing in the database prevents two ingest runs at once.

**Why:** single operator, single host, one 4am timer. The concurrency it guards
against does not currently have a way to occur.

**And the failure mode is worse than the thing it prevents.** A run that dies
without updating its status leaves a `running` row behind forever, and that row
would then block every subsequent night until someone noticed and cleared it by
hand. That converts a soft problem — two runs overlapping, which the `iem_data`
natural key already makes harmless — into a hard one: an ingest that has
silently stopped. The lock outlasts the crash that created it.

**Reverses if** the ingest ever runs on more than one host, or if anyone other
than the operator can trigger a run. Both change the premise.

---

## 2026-09-04 — The archive floor is a fixed `2021-01-01`, not a rolling five years

The backfill starts at a hard date. It is not "five years back from today."

**Why:** a rolling window does not survive a one-time load. The backfill runs
once; a window computed relative to `now()` means the boundary of the data
depends on the day the load happened to be run, which is not a fact anyone will
remember or be able to reconstruct. A fixed date says what it means — this is
where ingest started — and matches the existing decision that storm history is
never trimmed. Nothing walks the floor forward and nothing deletes behind it.

Widening it later is one script run rather than a migration, because the
`iem_data` natural key makes re-ingest idempotent: a backfill from an earlier
floor re-reads the overlap and inserts nothing new.

**Related:** *Storm report history is never trimmed* (2026-09-01).

---

## 2026-09-04 — `sql/` files are named for a domain, never a vendor or a phase

Each numbered file is named for the part of the model it defines. Not for the
external service the data comes from, and not for the phase that happens to
introduce it.

`009_ingest.sql` is consistent with this. **Ingest is a domain** — the run log
and the reject log describe the act of loading data, and would still be named
that if they had been written in Phase 0 or arrived in Phase 4. The name
survives the schedule that produced it.

**Why not vendor names:** a `010_rentcast.sql` would put a supplier's name on
our data model. Vendors get replaced; `properties` and `listings` describe
houses and sales regardless of who sells us the rows, and renaming a file after
a supplier change is the least of the work but the most visible reminder that
the name was wrong. RentCast tables belong in a file named for what they hold.

**Why not phase names:** phases are a plan, and plans get reordered. A file
called `phase3.sql` tells a reader when it was written, which is what `git log`
is for, and hides what is in it, which is what the name is for. It also makes
the numbering silently chronological rather than structural — at which point the
sequence stops grouping anything and is just an ordering.

The numeric prefix already carries load order. The name should carry meaning,
and the two should be independent enough that a file could be renumbered without
the name becoming a lie.

---

## 2026-09-04 — `sql/` is a build directory until the backfill runs; additive after

Until the historical backfill has loaded, `sql/001`–`009` are a **build**: the
database is dropped and recreated from them, and any of them may be edited in
place. There are no migrations, because there is nothing to migrate.

**Why this is safe right now:** nothing in the database is irreplaceable. Every
row is either reference data reloadable from `planning/report_types.csv` and the
TIGER shapefiles, or it is test data. Editing `004` to fix a column is one
`DROP DATABASE` away from being verified, and pretending otherwise would mean
carrying migration files for a schema no data has ever touched.

**The line is the backfill, not the first deploy or the first table.** Once five
years of `iem_data` are loaded, the rows stop being reproducible on demand — the
IEM query would have to be re-run, the reject decisions re-made, and anything
downstream that referenced an `iem_id` would be pointing at a different row. At
that moment `001`–`009` become history rather than source, and every change
after it is a new file: `010`, `011`, additive, never an edit to what came
before.

**Practical consequence, worth being blunt about:** the freedom to edit
`001`–`009` expires on a specific day, and it expires quietly. Nothing in the
tooling will start refusing edits. The check is "has the backfill run" — and
after it has, an edit to an early file is a change that the built database will
not have and no rebuild will reveal, because there will be no rebuild.

**Related:** *Storm report history is never trimmed* (2026-09-01), which is what
makes the backfill the point of no return rather than one snapshot among many.

---

## 2026-09-04 — CSV, not GeoJSON, for both ingest paths

`fmt=geojson` on the LSR endpoint returns **422**. GeoJSON exists only as a
static nationwide 24-hour file, which cannot serve the 30-hour overlap the
nightly job needs and cannot backfill at all.

CSV takes both a window and a date range, so one format covers nightly and
backfill. Live and archive CSV headers **verified identical**, which is what
makes a single parser module honest rather than hopeful.

Consequence: the CSV/GeoJSON key-name table in `docs/data-sources.md` is
reference material, not something the parser needs.

---

## 2026-09-04 — `state=CO`, not a WFO list

The query filters on state. This retires the `wfos=BOU,PUB` trap recorded in
`docs/data-sources.md` — Colorado is covered by five offices, not two, and
GLD and CYS carry the northeast corner.

State is one stable parameter instead of a list that is wrong by omission.

**Open consequence, not settled scope:** reports just over the state line are
excluded permanently, and **no buffer radius recovers them** — the radius widens
the search around a stored report, and these are never stored. A hailstorm three
miles into Wyoming that crosses into a covered zip is invisible to this system.

Same shape as the archive floor: quiet, permanent, and cheap to widen later,
since the `iem_data` natural key makes re-ingest idempotent. Carried as open
question 12 in `docs/database-schema.md`.

---

## 2026-09-06 — An out-of-domain `QUALIFIER` ends the run; it is not a reject

`iem_data.report_qualifier` keeps its `CHECK (report_qualifier IN ('M','E','U'))`
— settled 2026-09-03. The parser now validates against the same three codes and
raises `QualifierDomainError` on anything else.

**Not a sixth reject reason.** A reject discards the whole storm report, and
`QUALIFIER` tracks reporter training rather than instrument measurement — losing
a hail report over it would be the wrong trade.

**Not silently nulled either.** A fourth code is not a bad row, it is a changed
upstream domain, and nulling would turn that into no signal at all.

So it ends the run, which is what the skip-and-continue rule already says about
anything outside the five enumerated reasons. The raise adds nothing but
legibility: the run stops on a sentence naming the field, the value, and the
report's timestamp, WFO and type code, rather than on an `IntegrityError` thrown
from the middle of a batch `INSERT` that does not say which row caused it.

Fixing an occurrence means confirming the new code with IEM and migrating the
CHECK — a human decision, which is the same reasoning that keeps the reject
enumeration closed.

**Related:** *A `qualifiers` table is deferred, not rejected* (2026-09-03), which
is why the domain is three codes and not a lookup table.


---

## 2026-09-08 — `set -euo pipefail` is the standard for shell in this repo

Every `.sh` under `scripts/` begins with `set -euo pipefail`. Currently that is
one file, `load_reference.sh`, which already had it; this entry is written so
the second script does not have to rediscover the reasoning.

`pipefail` is the part that earned the entry. A pipeline's exit status is the
status of its *last* command, so `set -e` alone does not see a failure anywhere
upstream of the final `|`. This is not theoretical here — it already happened.
The precondition check was:

    psql ... "SELECT 1 FROM information_schema.tables WHERE ..." \
        | grep -q 1 || fail "table $t missing -- run sql/001..003 first"

A wrong `PGPASSWORD` made `psql` fail, `grep` saw no input and exited non-zero,
and the script reported a **missing table**. That sends you to `sql/001..003` to
debug a problem that is in `.env`. The rule the project already states —
failures should be loud, silent partial success is worse than an error — is
violated just as badly by a loud error naming the wrong cause.

The load itself depends on this directly: `shp2pgsql | psql` will happily leave
`psql` exiting 0 on empty input when `shp2pgsql` could not read the shapefile.
Verified both ways in the loader container: with `pipefail` the script stops at
the pipeline; with `set -eu` alone it prints the shapefile error and *continues
to the next line*.

Two corollaries, both of which cost more than they look:

- **Never infer a command's success from its output.** Capture the status
  separately, then test the output. `found=$(psql ...) || fail ...` and then
  `[[ "$found" == 1 ]]` are two different questions and need two checks.
- **`-u` is a real constraint, not decoration.** It turns a typo'd variable into
  an error instead of an empty string. Every expansion in a script carrying `-u`
  needs a default (`${VAR:-fallback}`) or a guaranteed assignment above it.

**Related:** *Malformed rows are rejected, logged, and skipped — not repaired*
(2026-09-04), which is the same principle one layer up: the failure is recorded
as what it actually was, not converted into something more convenient.

---

## 2026-09-08 — A sixth reject reason for `QUALIFIER` was reconsidered and declined

Revisited today as `invalid_qualifier`, with the argument that one bad character
should not cost an entire run. Declined. The 2026-09-06 entry stands unchanged
and no code was written.

Two corrections to the case for it, recorded because they are what settled it:

**The harm it described does not occur.** The proposal assumed an out-of-domain
qualifier reaches the `INSERT` and takes the transaction down. It does not.
`scripts/iem_parse.py` validates against `QUALIFIER_DOMAIN` and raises
`QualifierDomainError` *before* the record is built — which is exactly the work
done on 2026-09-06, so that the run ends on a sentence naming the field, the
value, `VALID`, `TYPECODE` and `WFO`, rather than on an `IntegrityError` from
the middle of a batch.

**The run ends either way.** This is the part worth keeping in mind if it comes
up again. A reject does not rescue the run; it discards the report and keeps
going, and a changed upstream domain will be on many rows, not one. So the trade
is not "lose a run" versus "lose a row" — it is "stop and look at it" versus
"quietly discard storm reports until someone reads a count." The first is what
the project already asks for: failures should be loud.

The reject enumeration stays closed at five. The friction of a migration is the
mechanism that keeps skip-and-continue from drifting into swallowing whatever
goes wrong, and that friction only works if it is actually felt.

**Reversal condition** is unchanged from 2026-09-06: confirm the new code with
IEM, then migrate the CHECK on `iem_data.report_qualifier`. A fourth code is a
changed contract and a human decision, not a row to skip.

**Related:** *An out-of-domain `QUALIFIER` ends the run; it is not a reject*
(2026-09-06) and *A `qualifiers` table is deferred, not rejected* (2026-09-03).

---

## 2026-09-08 — Error tracking is two layers, and the split is forced

Domain events get database rows: `ingest_runs` for what a run did,
`iem_ingest_rejects` for which lines it refused. Process events go to stdout and
are captured by journald.

This is not a preference for belt and braces. Two constraints force it:

- **A database failure cannot be written to the database.** If the connection is
  refused, the disk is full, or a constraint rejects the write, the layer meant
  to record the problem is the layer that failed. Anything that must survive
  that has to leave the process by another route.
- **A process killed before its `except` block writes nothing anywhere.** OOM
  kill, `SIGKILL`, power loss — no handler runs. The only record is what was
  already emitted, which is why the start line is emitted before anything can
  fail (see the logfmt entry).

So the division is by *what can still be true when the thing fails*, not by
severity. Detail lives in the database because it is queryable; the fact that
the process existed at all lives in the log because it survives the database.

---

## 2026-09-08 — No generic error table

Considered and declined.

The narrow tables are queryable **because** their constraints are narrow. A
five-value CHECK on `iem_ingest_rejects.reason` is what makes
`WHERE reason = 'field_count_mismatch'` mean something and what makes a new
value a deliberate migration. A table accepting arbitrary errors from arbitrary
sources cannot carry that constraint — its `reason` column is free text by
definition — and a table nobody can write a meaningful `WHERE` against is
write-only. It accumulates, it looks like diligence, and it is never read.

It would also be a second write path for failures, which reintroduces the
problem the two-layer split exists to solve: the generic table lives in the same
database that may be the thing that failed.

**If a single operational read is wanted later, it is a view** unioning the
failure conditions that already exist — a stale `ingest_runs`, rejects attached
to a run, an `api_pulls` row stuck in `running`. A view adds no write path and
cannot drift from the tables it reads.

**Related:** *Malformed rows are rejected, logged, and skipped* (2026-09-04) and
*A sixth reject reason was reconsidered and declined* (2026-09-08) — same
reasoning about closed enumerations, one layer down.

---

## 2026-09-08 — The `ingest_runs` row is written before the fetch, not after

The row is inserted with `run_status = 'running'` before the HTTP request is
made, then updated on completion.

Writing it afterward would mean **the failure most worth recording is the one
that leaves no trace**: a fetch that hangs, times out, or dies mid-parse never
reaches the code that would have written the row, so the run is
indistinguishable from a run that never fired. That is the exact question the
table exists to answer.

Same shape as `api_pulls`, and the reason `finished_has_timestamp` binds
`failed` as well as `complete` — a failed run stopped at a time, and the error
handler must set `finished_at` in the same `UPDATE` that sets the status.

---

## 2026-09-08 — Ingest health is an absence query, not a status query

The alert condition is:

```sql
SELECT max(finished_at) FROM ingest_runs
 WHERE run_mode = 'nightly' AND run_status = 'complete';
```

older than roughly 30 hours.

**A status column cannot express this.** Asking "is the latest run's status
`failed`?" answers nothing when the process was killed before it could write
one — a crashed run leaves `running` forever, which reads as healthy-in-progress
to any status check. And a run that never fired leaves no row at all, so there
is no status to inspect.

Phrasing it as "when did a nightly run last *succeed*" is the only form that
holds across all three: failed, crashed, and never started. It is also why the
table exists rather than log output — you cannot query a log for the absence of
a line without knowing to look for it.

---

## 2026-09-08 — logfmt to stdout, never to a file

`key=value` pairs, one event per line, `run_id` on every line so a run's lines
can be recovered from an interleaved journal.

```
event=ingest_start run_id=41 run_mode=nightly window_start=... window_end=...
event=ingest_done  run_id=41 rows_seen=118 rows_inserted=12 rows_skipped=0
```

**Stdout, not a file.** The container writes to stdout, systemd captures it into
journald, and rotation, retention, and `journalctl -u` filtering come for free.
A log file inside a container needs a volume, its own rotation, and is invisible
to `systemctl status`.

**logfmt, not JSON.** It is readable in `journalctl` by eye during development
and parseable by a shipper later without regex. JSON is neither of those at a
terminal.

**The start line is emitted before anything can fail** — before the HTTP
request, before the database write. It is the only evidence that survives a
`SIGKILL`.

**Detail stays in the database.** The log says *how many* rows were skipped; the
table says *which* ones and why. Duplicating reject detail into the log would
create a second copy that drifts and is harder to query than the first.

**`PYTHONUNBUFFERED=1` is required in the image.** Without it Python buffers
stdout when it is not a TTY — which is exactly the case under systemd — and a
process killed before the buffer flushes produces **no logs at all**, defeating
the one property this layer exists for. It is already set in
`docker/ingest.Dockerfile`; it is load-bearing, not tidiness.

---

## 2026-09-08 — Explicit grants, not `ALTER DEFAULT PRIVILEGES`

Every SQL file that creates a table ends with the grants for that table.
`sql/010_roles.sql` holds the roles and the current full set.

`ALTER DEFAULT PRIVILEGES` was considered. Two problems:

**It is easy to aim wrong and it fails silently.** The mechanism grants on
future tables created by a *named role*. Omitting `FOR ROLE` defaults to the
executing role, so a statement written expecting one creator and run by another
**succeeds and does nothing** — no error, no warning, and the gap only appears
later as a permission denial in an unrelated place.

**The failure modes are asymmetric, and that decides it.** A forgotten explicit
grant is a loud permission error, in development, at the moment the code first
touches the table. A default privilege quietly extending access is a role
holding permissions nobody decided to give it, discovered — if ever — during an
audit. One failure costs minutes and announces itself; the other is invisible
and is exactly the kind of thing `sql/010` exists to prevent.

This is the same asymmetry that governs realtor deduplication: choose the
failure that is recoverable and visible over the one that is silent and
permanent.

---

## 2026-09-08 — The ingest runs in a container, for a different reason than the loader

Both run in containers. The justifications are **not** the same, and conflating
them would lose one of them.

**The loader has no choice.** `shp2pgsql` is not on the Rocky host and should
not be — it ships in the `postgis` client package, and installing a database
client suite on the host to run a one-shot import is how hosts accumulate.

**The ingest has a choice, and takes the container for different reasons:**
keeping Python dependencies off the host, and pinning the runtime so the version
that runs tonight is the version that ran last night.

**systemd schedules and supervises; the container is only the runtime.** A
timer unit invokes `docker compose run`, and the unit is where `OnFailure=`,
`Persistent=true` (so a missed run fires after downtime rather than being
skipped), and `systemctl --failed` live.

**Cron inside the container was considered and declined.** It is a second
scheduler on a box that already has systemd, and it forfeits the things the
first one provides: journald capture of stdout, `systemctl --failed` as a single
place to see a broken job, `OnFailure=` hooks, and `Persistent=true`. It also
puts the schedule inside an image, so changing when the job runs means a
rebuild.

---

## 2026-09-09 — Three IEM endpoint behaviours, verified against the live endpoint

All three checked against `cgi-bin/request/gis/lsr.py` today. Recorded together
because they share a lesson: this endpoint's contract cannot be read off its
documentation or its status codes, only off its responses.

**IEM validates `fmt` but silently ignores unknown filter parameters.**
`fmt=geojson` returns **422**. `stat=CO` — one transposed character — returns
**HTTP 200 and every LSR in the country**: 27 New Jersey rows, 13 Kansas, 11
Texas, with Colorado fourth on the list. The failure is not merely quiet, it is
*shaped like success*, and a backfill would have loaded a national dataset into
`iem_data` while every log line said the run completed.

Filter correctness must therefore be **verified in the response, never inferred
from a status code**. The enforcement is the STATE assertion in
`scripts/iem_backfill.py`, which runs per month-window after the fetch and
before the row loop, and ends the run naming the states it found. It excludes
overflow rows on purpose: an unquoted comma in `CITY` shifts `COUNTY` into
`STATE`, so asserting on those would abort the backfill on each of the 76 known
malformed archive rows — the outcome the field-count check runs first to
prevent. An ignored filter produces thousands of *well-formed* out-of-state
rows, so the exclusion costs the check nothing.

This generalizes past `state`. Any parameter this endpoint accepts is a
parameter it may also ignore, so anything that matters has to be observable in
the data that comes back.

**`ets` is exclusive.** `sts=2021-05-08&ets=2021-05-09` returns 05-08 reports
only. Month chaining therefore needs **no gap and produces no overlap**: each
window's `ets` is the next window's `sts`. This is what makes `month_windows()`
in `scripts/iem_backfill.py` half-open, and why `--end` is documented as
exclusive. Off-by-one here would either lose a day per month across the whole
backfill or double-fetch one — the second being survivable, since the
`iem_data` natural key makes re-ingest idempotent, and the first being silent.

**`state=CO` is confirmed correct for this endpoint — not `states`.** Verified
directly rather than from documentation. This settles a question raised during
the 2026-09-09 review of `iem_backfill.py` and **confirms, but does not
supersede, the 2026-09-04 decision**; the open consequence recorded there —
reports just over the state line are excluded permanently, carried as open
question 12 — is untouched by this entry.

---

## 2026-09-09 — Natural key verified against the archive; duplicate reports confirmed benign

The first month-scale backfill (January 2021) was cross-checked against
`data/lsr_201601010000_202608312359.csv`, downloaded independently months
earlier.

**Row count matched exactly.** `awk -F',' '$1 ~ /^202101/' | wc -l` returned
1400; the run reported `seen=1400`. Endpoint, parameters, parser, and loop
agree with a file the script never touched.

**Ten rows conflicted on the natural key** despite January being empty before
the run, so the duplicates are inside IEM's own data rather than an artifact of
re-ingest. Confirmed with `sort | uniq -d` on the five key columns.

Inspecting one — `202101140304`, 2 NW MASONVILLE — the two rows are identical
in every field except `REMARK`: one empty, one containing a single period. Same
spotter, same station, same minute. A double submission with a stray keystroke,
not two observations.

**Conclusion: the dedup is correct and `REMARK` is rightly outside the natural
key.** Including it would have preserved both rows, and a period is not a
distinguishing fact. No change required.

**Consequence to carry forward:** the tiered confidence label ("Moderate — 3
reports, 2 spotters") counts stored rows, not reports IEM received. The
monthly average is 0.71% (10 of 1400), but the rate is not uniform — all ten
pairs fall on 2021-01-14 and are almost entirely NON-TSTM WND GST, consistent
with one office re-transmitting a product for a single wind event. So a label
computed for that day's storm could be off by considerably more than one
percent, while a label for a quiet week is off by zero. The direction stays
conservative — it under-counts rather than over-claims — but the label's
wording should say "reports" and must not imply distinct observers.

---

## 2026-09-09 — Reversal: the unquoted-comma `CITY` malformation is ongoing, not a 2018 artifact

Supersedes the factual premise of *The 76 unquoted-comma `CITY` rows are
rejected, not realigned* (2026-09-04). That entry set the tripwire as "reverses
if the pattern appears in any row dated after 2018." The condition is met.

`run_id = 4` (backfill 2021-01-01 → 2026-09-09, 84,268 seen, 1 skipped):

```
reason:  field_count_mismatch
detail:  17 fields, expected 16; overflow ['']
raw_row: 202608312045,2026/08/31 20:45,40.05,-108.15,None,GJT,F,FLASH FLOOD,
         CO Highway 64, at mile,Rio Blanco,CO,Department of Hig,Mud slide
         covering westbound lanes on CO Highway 64 at mile point 61.5 due to
         heavy rainfall.,COC103,Rio Blanco,
```

Dated **2026-08-31**, nine days before this entry. Same mechanism: an unquoted
comma inside `CITY` (`CO Highway 64, at mile`). Same office, GJT. The pattern
outlived 2018 by eight years and was never fixed upstream.

**Correction to the original entry's count, and it matters more than the
reversal.** The original said "all 76 are from 2018." Re-counted against
`data/lsr_201601010000_202608312359.csv`: **75 are from 2018 and one is from
2026** — 76 in total, so the headline number was right and the attribution was
not. That 2026 row was **already in the archive when the 2026-09-04 entry was
written**, since the archive runs through 2026-08-31. The hypothesis was not
disproven by new data; it was never true, and the disproving row was sitting in
the file the whole time. A total that matched the expected figure is what
stopped anyone looking at the distribution behind it.

**The decision does not change.** Rejecting rather than realigning is still
correct, and `raw_row` keeps it lossless. What changes is the expected
frequency: roughly one row in 84,000 over five years, so `rows_skipped > 0` is a
real recurring condition rather than a theoretical one. **Whatever eventually
watches `ingest_runs` must not treat a non-zero skip count as an emergency** —
it is the documented normal state of this feed, and an alert that fires on it
will be muted, which is worse than not having it.

**The overflow field is `['']`, not a value.** The row ends `Rio Blanco,` with
`QUALIFIER` empty, so the shift pushed a real value off the end rather than
merely displacing everything by one. Worth recording because it means the tail
of a shifted row is not reliably recoverable by counting from the right — the
realignment that the original entry called feasible would have silently
discarded `QUALIFIER` here.

**This one cost nothing, and that is luck.** A Western Slope flash flood outside
`coverage_zips`, below every magnitude floor, on a type that is not
`roof_relevant`. Nothing about the reject mechanism arranged that. A hail report
inside the territory would be lost the same way, which is the argument for
`rows_skipped` being visible rather than merely logged.

**Related:** the same row is the evidence for the `SOURCE` truncation entry
below, and for why `report_sources` has no foreign key from `iem_data`.

---

## 2026-09-09 — `coverage_zips` is loaded; the 10 unmatched zips get a second, independent explanation

`coverage_zips` was empty until today. `config/coverage_zips.txt` (formerly
`planning/rbi-zip-code-coverage-area-list.txt`) held 193 zips and nothing loaded
them, so the coverage-filtered `ST_DWithin` query returned **0 rows with no
error** — the inner join eliminated everything. Same silent-empty-join shape as
the SRID trap and as an unseeded `report_sources`: the query runs, the answer is
empty, and nothing anywhere says why.

`scripts/load_coverage.sh` now loads it: 183 inserted, 10 reported, exit 0.

### Correction to the 2026-09-03 entry: `80638` is institutional, not PO-box-only

*Territory is a table: `coverage_zips`* (2026-09-03) classified the 10 zips with
no ZCTA polygon as "PO-box-only zips (`80502`, `80522`, `80539`, `80632`,
`80638`, `80901`), institutional zips (`80225` Federal Center, `80523` CSU,
`80639` UNC), and `80213`, which is not an assigned zip at all."

**`80638` is in the wrong group.** It is University of Northern Colorado, the
same institution as `80639`, and the USPS delivery file has no record of it at
all — which is the signature of the institutional group, not the PO-box group.
The count and the conclusion are unaffected; only the label on one zip is wrong.
That entry stands as written and is not edited.

### The signal that produced the correction

The USPS PostalPro `ZIP_Locale_Detail` file, loaded today for `area_name`, turns
out to answer a question the original entry could only answer by inspection. It
lists **delivery** zips. Cross-tabulating membership in it against the presence
of a ZCTA polygon splits the 10 cleanly, five and five:

| | in USPS delivery file | has ZCTA polygon | what it is |
|---|---|---|---|
| `80502` `80522` `80539` `80632` `80901` | yes | no | **PO-box-only.** USPS delivers mail; Census draws no polygon because nobody lives in a PO box |
| `80213` `80225` `80523` `80638` `80639` | no | no | **Not a delivery zip at all.** Unassigned, or institutional mail handled internally |

Two independent sources agreeing on the same partition is worth more than either
alone. Finding the original 10 took a purpose-written script against the raw
TIGER `.dbf`; this reproduces the answer from a different direction and explains
*why* each is missing rather than only *that* it is.

**A third case the original entry could not have seen:** `80913` (Fort Carson)
is **absent from the USPS delivery file but has a ZCTA polygon** — the inverse
of the PO-box pattern. It loads fine, because the FK only cares about the
polygon, but it has no USPS city and so no `area_name`. It is the single row in
183 that falls back to the literal `ZIP 80913`. A military installation the
Census maps and USPS does not deliver to by ordinary route.

### What this does not change

**The FK stays.** Its job was never to explain the 10 — it was to make them
uninsertable rather than periodically re-detected, and that is still what it
does. This entry means the next person to expand the territory can be told
*which kind* of hole they have hit, not merely that they have hit one.

**The loader does not fail on them.** They are a stable fact about how USPS and
Census disagree, not an error. But each is printed on every run, because
silently dropping them means nobody learns which zips the customer believes they
cover that the system cannot represent.

**Related:** *Territory is a table* (2026-09-03), which this corrects and
extends, and *A `system` user account, with a real `emp_id`* (2026-09-03) — the
loader resolves `added_by` by querying that account rather than hardcoding an
integer.

---

## 2026-09-09 — Generic and specific are split by directory: `planning/` and `config/`

`load_coverage.sh` is deliberately **not** part of `load_reference.sh`.

`load_reference.sh` loads national static data that is identical for every
installation: 37 NWS report types, 33,791 ZCTAs, and now 37,104 USPS zip/city
names. `load_coverage.sh` loads one customer's territory. Different lifecycle,
different rerun cadence, different owner — sharing a script would tie a
territory edit to a reload of 33,791 polygons.

**The zip list is an argument, not a hardcoded path.** A roofing company in
Dallas points the script at their own file with no code change. That is the
whole design goal, and it only holds if the customer's list is configuration
rather than source:

```
load_coverage.sh /repo/config/their_zips.txt
```

So the split is made visible in the tree rather than living in a comment:

| | `planning/` | `config/` |
|---|---|---|
| Holds | generic national seeds — `report_types.csv`, `zip_city_names.csv` | this customer's `coverage_zips.txt` |
| Loaded by | `load_reference.sh` | `load_coverage.sh` |
| Replaced for a second customer | never | entirely |

`planning/rbi-zip-code-coverage-area-list.txt` moved to
`config/coverage_zips.txt`. The name loses "rbi" on purpose: a per-customer file
in a per-customer directory does not need the customer's name in it, and the old
name would have to be edited by every installation that copied it.

**`reference/` was considered and is wrong for both.** It is gitignored
wholesale — "entirely regenerable by `build_reference_tables.py` … and is also
where the DNC lists live. Never track it." `zip_city_names.csv` is neither
regenerable by that script nor safe to lose, and a loader depends on it, so it
belongs with the other tracked seed. `.gitignore` already documents this exact
distinction for `planning/report_types.csv`.

### `zip_city_names.csv`, and why it is a file rather than a job

Extracted 2026-09-09 from the USPS PostalPro `ZIP_Locale_Detail` release dated
2026-09-08. Free, no registration. 37,104 zips, all states.

**All states, not Colorado**, consistent with the existing decision to load
whole-country ZCTA boundaries rather than a subset — and practical, since the
file has no state column to filter on cleanly.

**It is a facility file**, one row per post office, so a zip with several
facilities appears several times and 10.0% of zips carry more than one distinct
`PHYSICAL CITY`. The dedup rule is **first occurrence in USPS file order**,
which is the preferred city name.

A modal rule was tried first and does not work: **3,686 of the 3,719 multi-city
zips are exact ties** at one row each, so frequency decides almost nothing.
Alphabetical was then tested against the six multi-city zips in RBI's own
territory and got **two wrong** — `GOLDEN` over `MORRISON` for `80465`, and
`FORT COLLINS` over `TIMNATH` for `80547`. First-occurrence gets all six right.
Order carries information here and alphabetical discards it.

**No fetch-and-parse pipeline.** `area_name` is decoration for humans in the
loop; nothing queries it. A job maintained forever to keep a cosmetic label
fresh is not worth it. The file header records the extraction date and the USPS
release so staleness is at least visible, and regenerating by hand is a
five-minute job on the rare occasion it matters.

**`reason` is left NULL by the loader, on purpose.** `database-schema.md` calls
it "the field that will be empty in six months if it is not filled in now."
Writing `'bulk import'` into all 183 rows would fill it with something worse than
empty — text that looks like an answer and tells nobody why the territory is in
scope. NULL is honestly unanswered; a placeholder is a lie that survives.

---

## 2026-09-10 — The archive floor moves to `2004-01-01`

**Supersedes the 2026-09-04 entry "The archive floor is a fixed `2021-01-01`,
not a rolling five years."** The *fixed date, not a rolling window* half of that
decision still holds and is the reason this is a one-line change; only the date
moves.

The 2021 floor was a round number with no data reason behind it — "about five
years back" at the time it was written. The backfill has since been run to the
practical bottom of the IEM LSR archive for Colorado: `iem_data` now holds
176,957 rows from **2004-01-26** (the earliest report that exists) to present.
The load walked down through overlapping windows — runs 4–8 — and each earlier
window inserted only its new rows, because the `iem_data` natural key makes
re-ingest idempotent. Runs below the old floor emitted the `below_archive_floor`
warning and proceeded; the constant was never a hard block.

**Why keep the extra ~17 years rather than trim back to 2021:**

- It is already loaded and verified. Removing it would be a deliberate delete of
  storm history, which every other decision here forbids.
- The cost is nil. 177k rows is small, the spatial indexes make date range
  irrelevant to query cost, and nothing downstream filters on a floor.
- It may be useful. A house hit in 2008 and again in 2023 is a stronger outreach
  story than the 2023 hit alone, and that pattern is invisible with a 2021 floor.

**What this changes:** `ARCHIVE_FLOOR` in `scripts/iem_backfill.py` is now
`2004-01-01`, so `below_archive_floor` fires only for a genuinely
pre-archive `--start`. The example range in `data-sources.md` is updated to
match. Two rows that sit below the old floor are now in scope and were noted as
downstream effects at the time: the `DEPT OF` truncated `SOURCE` value from
2019-03-09 (`report_sources` seed, open question 13 in `hail-consolidated.md`),
and the pre-2016 `unknown_report_type` rejects `('5', 'ICE STORM')` and
`('X', 'WALL CLOUD')` — one-off historical type/text pairs, not a seed gap.

**What this does not change:** the nightly job. It fetches a rolling `recent=`
window in seconds and never reads `ARCHIVE_FLOOR`; the floor is a backfill and
replay concept only.

**Related:** *Storm report history is never trimmed* (2026-09-01); *`sql/` is a
build directory until the backfill runs; additive after* (2026-09-04).

---

## 2026-09-11 — Read-only reporting scripts get their own Compose service, `app`

`scripts/export_storm_zips.py` needs `SELECT` on `report_sources`,
`zcta_boundaries`, and `coverage_zips`. None of those are in `hail_ingest`'s
grants (`sql/010_roles.sql`) — running the script under the existing `ingest`
service failed with `permission denied for table report_sources`. Added a
fourth Compose service, `app`, plus `docker/app.Dockerfile` (same shape as
`ingest.Dockerfile`), connecting as `hail_app` instead.

**Why a new service instead of adding grants to `hail_ingest` or reusing
`ingest`:** `hail_ingest` is deliberately scoped tight to the nightly write
path — the `ingest` service comment in `docker-compose.yml` already states the
intent: "a bug here cannot reach `send_log` even by trying." Widening its
grants to cover a reporting script's read needs would erode that boundary for
every future ingest change, not just this one. `hail_app` already had exactly
the grants this kind of script needs, because it is meant to be the eventual
web UI's role — a read-only export script is the same shape of consumer, just
without a browser in front of it yet.

**Why a bind mount for `./output` rather than a build-time `COPY`:** the script
writes its CSV to a relative `output/` path, which resolves inside the
container's own filesystem without a mount. `docker compose run --rm` deletes
that filesystem on exit, so the file would never reach the host at all — this
is not a permissions question, it is a "where does the byte actually end up"
question. `./output:/app/output` fixes both: the file survives `--rm`, and
because the Dockerfile's `useradd --uid 1000 app` matches the host account's
`uid 1000`, no `chown` is needed on either side.

**Related:** *Nothing sends email automatically, ever* (2026-09-01) — the same
shape of reasoning (least privilege per service/role) applied here to reads
instead of sends.

---

## 2026-09-10 — Shared ingest machinery extracted into `iem_common.py`

`iem_backfill.py` and `iem_ingest.py` differ only in how they decide which
window(s) to request: the backfill takes an explicit date range and chops it
into monthly chunks; the nightly computes a rolling window from the clock.
Everything after that — the request, the run-lifecycle bookkeeping, the row
loop, the counters — is identical, and now lives once, in `iem_common.py`.

**The seam is `perform_run(mode, window_start, window_end, windows)`.**
`windows` is an iterable of `(chunk_start, chunk_end, url)` — that tuple is the
*only* thing the two scripts supply differently. `window_start`/`window_end`
are recorded in `ingest_runs` as what was asked for, not how it was chopped up.

**Why:** the same argument as the shared row parser (`iem_parse.py`, already
unit-tested): the exercised path (backfill, run by hand, watched) and the
unattended path (nightly, run by a timer, unwatched) must not diverge. A bug
fixed in one and not the other is exactly the failure mode a shared module
rules out by construction.

---

## 2026-09-10 — `tuning.py`: both the zip radius and the match radius are `5.0` miles, and there is no settings table yet

Answers open questions 2 and 3 (`hail-consolidated.md`) for the radius knobs
specifically — the broader "is there a settings table at all" question stays
open for the other candidates (frequency-cap window, monthly API ceiling,
warmup limit).

**Evidence for `5.0`:** same-day report-pair distances look flat in raw
counts, but pair counts grow with ring area — normalizing by radius shows
report density halving between 1 and 3 miles, and halving again by 10 miles.
The flat histogram was geometry, not weather; the normalized signal supports a
tight number. Cost, measured against `coverage_zips` from a 200-report hail
sample: 5.1 / 9.4 / 15.1 / 18.2 ZCTAs per report at 3 / 5 / 8 / 10 miles —
roughly linear, not quadratic, because a report near the territory edge only
picks up zips on one side.

**Why two constants at the same value instead of one:** `DEFAULT_ZIP_RADIUS_MILES`
bounds what we *look at* (and therefore how many RentCast lookups a pull
costs); `DEFAULT_MATCH_RADIUS_MILES` bounds what we *claim* in an email a
homeowner might question. They start equal, but only the match radius has to
survive that conversation, so collapsing them into one name would hide that
they can diverge later.

**Why a module, not a settings table, for now:** a handful of values, each
with a single consumer, changing rarely, worth version-controlling with the
reasoning attached. A table adds operational overhead and drops the git
history. **What forces the move:** a non-developer needing to change a value,
a second consumer needing the same number, or the frequency cap specifically —
that one *must* be enforced in the database and cannot live in a Python
constant.

---

## 2026-09-10 — `report_sources` seeded with 47 rows, built from what ingest actually produced

Closes the data half of open question 13. `planning/report_sources.csv` is one
row per distinct `report_source_norm` value observed in `iem_data` as of
2026-09-10 — not a raw scan of every value IEM could theoretically send —
verified as an exact 47/47 match against `SELECT DISTINCT report_source_norm
FROM iem_data`, zero unmatched either direction.

**Verified against the live archive (176,966 rows):** `high`-tier sources cover
**84.7%** of it by volume, because `COCORAHS` and `TRAINED SPOTTER` alone are
**53.0%** (28.5% + 24.5%). **The tier is a filter, not a headline** — a UI
should surface source names, not just tiers, since two sources carry most of
the archive's weight on their own.

**Why the tier is not a universal confidence signal:** what a source is good
at depends on the event type. An automated station (`MESONET`, 15.2% of the
archive) measures wind and precipitation well and does not size hail at all —
`report_qualifier = 'M'` on a hail report tracks *reporter training*, not
instrument measurement (`hail-consolidated.md` §7). Tier and qualifier answer
different questions and neither substitutes for the other.

---

## 2026-09-11 — `export_storm_zips.py`: one row per report-zip pair, local-day window, no magnitude floor

1. **One row per report-zip pair, not per zip, for now.** A busy storm day
   produces many report-zip pairs without a correspondingly large number of
   distinct zips. Aggregating to one row per zip is the natural next step once
   there is a consumer that wants it that way; this script exists to show the
   shape of the data (magnitude, source, distance, time) individually first.
2. **`--date` is a local Denver calendar date, converted to a UTC range at
   query time**, not a UTC calendar date. A Front Range storm at 8pm MDT is
   02:00 UTC the following day; filtering on UTC calendar date would split one
   storm's reports across two exports. `denver_day_bounds()` builds the range
   from two independently-resolved local-midnight instants — see the DST
   subtraction trap in `hail-consolidated.md` §7, which does not affect this
   window's correctness but would affect any *duration* computed from it later.
3. **No magnitude floor.** Every report of the requested type is exported,
   `NULL` magnitude included, so what should trigger outreach can still be
   decided later from real exported data rather than guessed at in the query.
4. **Coverage zips only, no override flag, retired zips excluded explicitly**
   (`c.removed_at IS NULL`) — consistent with storm reports never being
   filtered at ingest and coverage being edited by marking rows, not deleting
   them.

---

## 2026-09-11 — systemd units: copied not symlinked, `TimeoutStartSec` sized to the retry budget, no `OnFailure=` yet

**Copied with `install -m 644`, not symlinked**, into `/etc/systemd/system/`.
A symlink from `/home/hail-user/hail-system/systemd/` was refused by SELinux —
`init_t` (systemd) cannot read a target labelled `user_home_t`. A file created
directly at the destination path picks up that path's default context
(`systemd_unit_file_t`) automatically; a symlink keeps the label of wherever it
actually lives. Relabeling the repo directory with `semanage fcontext` was
rejected as the fix, because that would be a fact about one machine's SELinux
policy, not something a fresh clone carries with it — copying is portable,
relabeling is not. **Cost:** the repo and the installed copies can now drift
silently; there is no enforced link, only the habit of re-copying after an
edit.

**`TimeoutStartSec` must exceed the ingest script's own worst-case retry
time, not just its steady-state run time.** `HTTP_TIMEOUT=120`,
`HTTP_ATTEMPTS=3`, backoff `HTTP_BACKOFF ** attempt` slept between attempts
but not after the last one — 2s, then 4s. Worst case is 3 × 120s + 2s + 4s ≈
366s, roughly 6 minutes, dominated by the timeout budget rather than the
backoff. The systemd default (90s) would SIGTERM a run that was still
correctly retrying and record a timeout instead of the real upstream cause.
Set to 900 on `iem_ingest.service` and 1800 on `iem_weekly_replay.service`,
which can make several such fetches — the replay chunks its 30-day window by
month.

**`OnFailure=` deliberately omitted from both services.** There is no
notification path built yet for it to trigger — adding the directive now would
be automation with nowhere to go, not a safety net. Revisit once there is
somewhere for a failure to be sent.

**Related:** *`set -euo pipefail` is the standard for shell in this repo*
(2026-09-08) — same instinct (fail loud, don't let a wrapper mask the real
cause) applied to the process-supervision layer instead of shell scripts.

---

## 2026-09-11 — Weekly replay closes a gap the nightly window structurally cannot

The nightly window filters on `VALID` (IEM's event time — when the storm
happened), not on when IEM received or published the report. A report entered
into IEM's system days after its storm falls outside every nightly window that
already passed, and the natural-key overlap that makes re-ingest safe cannot
retroactively catch it — there is no "re-check yesterday" without deliberately
looking again. It is not late; **it is permanently missed** unless something
re-queries the recent past.

**The weekly replay (`iem_backfill.py --mode replay` over the last 30 days,
Sundays at 11:00 UTC, an hour after the nightly) is both the fix and the
measurement.** A replay run that inserts zero new rows says late entry did not
happen that week; one that inserts rows *is* the evidence that it does — first
observed on 2026-09-11's manual run (`run_id=18`, 413 seen, 6 inserted against
data the nightly had already passed over).

**Related:** *Backfill and nightly are two scripts over one parser module*
(2026-09-04); *A run that skipped rows still exits 0* (2026-09-04) — same
reasoning extended from malformed rows to structurally-late ones.

---

## 2026-09-14 — Phase 2 UI is reached over Tailscale, not a Cloudflare tunnel

The web container publishes to `127.0.0.1:8000` only. Access during Phase 2 is
`tailscale serve --bg 8000` on `hail-dev`, giving
`https://hail-dev.<tailnet>.ts.net` with a certificate from Tailscale's CA.
Nothing listens on the LAN or the WAN.

**Why:** the entire user base during Phase 2 is one person who is already on the
tailnet, so this costs no setup and nothing to install. The loopback binding
matters independently of Tailscale: Docker writes iptables rules ahead of
firewalld's zones, so `-p 8000:8000` would open the port on every interface the
host has regardless of firewalld being closed. Binding to loopback removes the
interface a wrong rule could apply to, rather than relying on a firewall that
Docker bypasses.

**Supersedes:** `server-setup.md`, which states that firewalld stays closed
because the UI arrives through a Cloudflare tunnel. There is no tunnel in Phase
2.

**Cost:** if office LAN access (`192.168.1.x`, plain HTTP) is ever used as a
stopgap for staff, passwords cross the wire in clear text. Acceptable against
this threat model, unacceptable as a permanent arrangement, and recorded here so
the interim does not become the default by inattention.

**Revisit:** Phase 6, when staff use the system. Cloudflare Access is *less*
client-side work for them than Tailscale — a browser and an email code, nothing
installed — and it is not blocked on RBI controlling DNS, since a tunnel runs on
any domain in our own Cloudflare account. That reverses the natural assumption
that the tunnel is the heavier option.

**Unchanged by this:** the application still needs its own login. Tailscale and
Access both authenticate a device or a person to the network; neither tells the
application which `emp_id` to write into `api_pulls`.

---

## 2026-09-14 — "City" in the UI means the USPS city of an affected zip

Grouping by city uses `coverage_zips.area_name`. It is a property of the zip in
range, not of the report.

**Why:** `iem_data` has no city column, and IEM's `CITY` field would not serve as
one anyway — it is a position relative to a landmark (`2 SW Great Divide`), which
is why it was never stored. `area_name` is the only place name in the system.

The distinction is invisible on screen: a column headed "City" reads as "where
the hail was reported," and it actually means "one of our cities had a zip within
the radius." For outreach that is the better question, but it is a different
claim than the label implies.

**Cost:** a single report within range of zips in two cities appears under both.
Correct — property in both was affected — but it means reports-by-city sums to
more than the report count, the same one-to-many shape as the export's
report-zip pairs.

---

## 2026-09-14 — County comes from TIGER county polygons

`tl_2025_us_county` is loaded into a new `county_boundaries` table, by the same
path as the ZCTA load, and county is derived spatially.

**Why:** all three existing sources fail, each differently. `iem_data.county` is
free text with 64 county groups differing only by case (`EL PASO` 10,299 rows,
`El Paso` 2,549), so browse-by-county silently halves counts unless every query
remembers to `upper()` both sides. `nws_geo_code` (UGC) is unambiguous but null
before mid-2022, which rules it out for a 22-year archive. `properties.county_fips`
describes a property, not a report, and has no rows yet. A polygon lookup is
authoritative across all 22 years and produces a zip→county crosswalk as a
by-product.

**Supersedes:** parking-lot item 4, which proposed a `county_norm` generated
column. That fixes case collisions and nothing else — not the pre-2022 gap, not
the report-location-versus-affected-zip mismatch.

**Traps that apply, both already documented:** TIGER ships NAD83, so the load
needs `-s 4269:4326` or the geometry lands in the wrong SRID and every spatial
predicate silently returns nothing; and `-c` versus `-d` on a re-run, which
determines whether the table is recreated or appended to.

**Cost:** a new table, a loader change, and an additive migration. `004_weather.sql`
is frozen post-backfill and is not edited.

---

## 2026-09-14 — Flask with server-rendered Jinja templates

**Why:** FastAPI's advantages are async I/O concurrency, pydantic request
validation, and generated OpenAPI docs. None of the three applies here. There is
no async workload — Phase 3's RentCast pull is one person waiting on one
foreground request. Form handling in a server-rendered app is a template
concern rather than a schema concern. And there is no third-party consumer to
publish an API contract to. Server rendering also means no build step, no npm,
and no second language in the repository, which matters more for a system one
person maintains than any framework feature under discussion.

**Note for when the map is built** (nice-to-have, post-completion): it is a
`<script>` tag over an ordinary route emitting `ST_AsGeoJSON`, not a reason to
revisit this. ZCTA polygons carry thousands of coordinate pairs each, so the
route must `ST_Simplify` at query time or the payload becomes the bottleneck.
That simplification is display-only and is never applied to geometry the
matching uses.

---

## 2026-09-14 — Password hashing via `hashlib.scrypt`; `SECRET_KEY` joins `.env`

**Why:** scrypt is a memory-hard KDF in the Python standard library, so Phase 2
adds no dependency for authentication. argon2id is the stronger current
recommendation but is a C extension, and the standing rule is that dependencies
are proposed before they are installed.

**Supersedes:** `database-schema.md`, which names bcrypt or argon2 for
`users.password_hash`.

**Storage format:** the hash column stores the parameters alongside the digest
(n, r, p, salt). Storing a bare digest makes existing passwords unverifiable the
first time a parameter is raised, which is a thing that should be possible to do
without a password reset for every user.

**Cost:** `SECRET_KEY` becomes the fourth value in `.env`, under the same rule as
the others — secrets only, never configuration. Rotating it invalidates every
session at once.

---

## 2026-09-14 — Repository becomes a package; `scripts/` keeps its entrypoint names

`hailsys/` holds importable code (`tuning.py`, `db.py`, `iem/`, `queries/`,
`web/`). `scripts/` keeps every existing filename and becomes thin entrypoints.

**Why the filenames are preserved:** the systemd units invoke
`scripts/iem_ingest.py` by path. A move that does not touch them cannot break the
nightly, which is mid-clock on Phase 1's unattended week.

**Why `tuning.py` keeps its name:** it describes what the file holds — values
that were reasoned about and may need re-tuning — better than `config.py` would,
and it avoids collision with Flask's own `config`.

**Trap this introduces:** running `python3 scripts/iem_ingest.py` puts `scripts/`
on `sys.path`, not the repository root, so `import hailsys` fails with a
module-not-found error that reads as though the package is absent while it sits
one directory over. `ENV PYTHONPATH=/app` in all three Dockerfiles fixes it
without changing how anything is invoked.

**Verification, in this order:** capture a baseline export CSV for a fixed date
and radius *before* the move; move; add `PYTHONPATH`; rebuild all three images
(the build-context snapshot trap otherwise makes a correctly-moved file look
missing); run the 44 parser tests; re-run the export and `diff` against the
baseline; `sudo systemctl start iem_ingest.service` and confirm a new `run_id`.

---

## 2026-09-14 — One connection seam in `hailsys/db.py`; `dict_row` now, no pool

Every query acquires its connection through a single context manager. Rows come
back as dicts. No connection pool in Phase 2.

**Why `dict_row` now:** default psycopg rows are tuples, so call sites index by
position. The day a column is added to the middle of a `SELECT`, every consumer
keeps running and returns the wrong field — a quiet wrong answer rather than a
loud error. Setting the row factory once, before there are call sites, costs
nothing; changing it after there are is a sweep through every one of them.

**Why no pool:** at three to five users the saving is a few milliseconds per
request, and a pool is not free. It holds connections open, so it hands out dead
sockets after the `postgis` container restarts unless a `check=` callback is
configured — a pool without one is less reliable than no pool. And a pool created
before gunicorn forks gives every worker copies of the same sockets; it works
today only because `preload_app` defaults to false, and would break silently the
day someone sets it true to save memory.

**Triggers that would force one**, recorded instead of a phase number: a route
holding a connection open across slow non-database work, sustained concurrency
above the gunicorn worker count, or connection setup measurably showing up in a
real timing. Note that the sizing variable is in-flight requests, not headcount.

**When a pool does arrive, the ingest scripts keep a plain connect.** A one-shot
process that opens one connection and exits gains nothing from pooling. `db.py`
ends with two entry points, and that is correct rather than a wart.

---

## 2026-09-14 — The storm query lives in `hailsys/queries/storms.py`

The joins, coverage rule, distance expression and local-day boundary are written
once. Two projections sit over them: `pairs`, one row per report-zip pair, and
`zips`, the same query grouped by coverage zip. `export_storm_zips.py` keeps
argument parsing, logging and CSV writing, and contains no SQL.

**Why now rather than when RentCast needs it:** the browse UI is a second
consumer of this exact question, and a CSV that disagrees with the screen it was
downloaded from is a failure with no good diagnosis. Extracting it while there is
one consumer costs an afternoon; extracting it after two have diverged costs the
reconciliation as well. Same argument that produced `iem_common.py`.

**Aggregate projection** (supersedes the sketch in PL-06, which proposed
`min(distance_miles)` alone): `report_count`, `nearest_miles`, `farthest_miles`,
`first_report`, `last_report`, `max_magnitude`, `min_magnitude`, `sources`.

**Why the time span rather than the distance span.** PL-06 asked what "nearest
distance" means for a zip touched by two cells 30 miles apart. `max(distance_miles)`
is a weak discriminator: inside a 5-mile radius the spread is bounded, and a
large rural zip produces a wide spread from a single cell anyway. Two separate
cells almost always differ by hours, while one cell produces reports minutes
apart — so `first_report`/`last_report` is what actually exposes a second event.
`report_count` and `sources` are carried because the confidence display needs
them regardless.

**Known property of `report_count`:** it counts stored rows, so it inherits the
~0.7% natural-key deduplication. A zip showing 3 reports is showing three stored
rows, not necessarily three the IEM received. Conservative direction, and the
right one for a number a homeowner may eventually read.

**Filenames are now suffixed** — `storm_zips_<date>_<type>_pairs.csv` and
`..._zips.csv`. A filename that does not name its format is ambiguous the moment
a second format exists. Note this renames the existing `pairs` output; nothing
outside `output/` referenced the old name.

**Verified:** `pairs` byte-identical to the pre-move baseline for 2026-06-24
HAIL at radius 5.0; `zips` returns 45 rows whose `report_count` sums to 64; 88
tests pass.

**Pulled forward with this:** `hailsys/db.py`, since this created the project's
first call site for a connection. Its design is the 2026-09-14 seam entry.

---

## 2026-09-15 — Correction: `loader.Dockerfile` needed neither `COPY hailsys` nor `PYTHONPATH`

*Repository becomes a package* (2026-09-14) says "`ENV PYTHONPATH=/app` in all
three Dockerfiles fixes it." That overstates it. Checked, not assumed:
`docker/loader.Dockerfile` has neither line. Only `app.Dockerfile` and
`ingest.Dockerfile` do.

**Why the loader is exempt rather than merely forgotten:** it runs no
`hailsys` Python at all — the image is `psql` and `shp2pgsql` against a
bind-mounted repository (`volumes: - .:/repo:ro,Z` in `docker-compose.yml`),
not a build-time `COPY`. A bind mount means there is nothing copied to go
stale and nothing to `import`, so neither the trap this entry describes nor
its fix applies to that image. Mounting rather than copying is also why the
loader was never touched by the build-context-snapshot trap the verification
steps call out — there is no snapshot for a bind mount to disagree with.

Left the original entry as written, per this file's own rule. Recorded here
instead, so the next person copying the "all three" line does not have to
re-derive which two it actually means.

---

## 2026-09-15 — Zip detail loads lazily, into the row, as a fetched HTML fragment

The recent-storm-days list is one row per (day, type). Seeing which zips a
row touched had three candidate shapes: render every row's zip breakdown
eagerly on page load, link out to a separate detail page per row, or fetch
the breakdown into the row itself only when asked.

**Lazy fetch, not eager render.** Eager means running `fetch_zips` — a real
`ZIPS_SQL` query — once per row in the list regardless of whether anyone
ever looks at it. A busy stretch can put a dozen-plus rows on screen at
once; paying for all of them to answer a question almost none of them will
be asked is the wrong default.

**Lazy fetch, not a detail page.** The point of this list is to glance
across several days and drill into a few. Chasing each one through a full
page load and a back button is worse friction than the list not expanding
at all — it breaks exactly the "browse, then look closer" flow the page
exists for.

**Mechanics:** each row carries a hidden sibling `<tr>`. The `+` button
toggles it and, on first expand only, fetches
`GET /storms/zips?date=...&type=...` and drops the response straight into
the cell with `innerHTML`. `cell.dataset.loaded` guards the fetch so
collapsing and re-expanding afterward is free — no second request.

**The fragment-template convention: a leading underscore means "not a
page."** `_zips.html` has no `{% extends %}` and no `<html>` — it is the
`<table>` fragment and nothing else, meant to be dropped into an existing
DOM, not requested on its own by a person. `storm_zips()` is the one route
under `/storms/...` that does not return a full page, and the filename
says so before the route body does. The convention: a template named with a
leading underscore is includable, not routable-as-a-destination — the same
signal a leading underscore carries in other languages for "not part of the
public surface."

**Why this does not reopen *Flask with server-rendered Jinja templates*
(2026-09-14).** That entry's own "Note for when the map is built" already
named this shape as compatible: "a `<script>` tag over an ordinary route,"
not a reason to revisit. The zip fragment is the same idea at a smaller
scale — a route that returns HTML instead of JSON, fetched by 42 lines of
vanilla JavaScript in `static/storms.js`, no framework, no bundler, no
second build step. With JavaScript disabled the list still renders and
still reads correctly; only the expand-in-place behavior is lost.

**Related:** *Flask with server-rendered Jinja templates* (2026-09-14).

---

## 2026-09-15 — `_ACTIONABLE` pulled into the shared query core, after the day list and its own zip detail disagreed

*The storm query lives in `hailsys/queries/storms.py`* (2026-09-14) built
`_FROM_WHERE` specifically so every projection agrees on the join, the
coverage rule, and the window. It does not, by itself, cover a filter that
only some projections apply — and one such filter was written outside it,
which reopened exactly the disagreement the shared core exists to close.

**What happened.** The actionable-only rule —
`t.roof_relevant AND (t.min_magnitude IS NULL OR magnitude >= min_magnitude)`
— was appended directly onto `RECENT_DAYS_SQL`, after `_FROM_WHERE`, rather
than folded into the shared core. `ZIPS_SQL`, the query behind a row's zip
drill-down, had no equivalent clause at all.

**The consequence, concretely:** a day can appear on an "actionable only"
list because it has one actionable report. Expanding that row ran `ZIPS_SQL`
unfiltered, so the drill-down's `report_count`, `nearest_miles`,
`first_report`/`last_report` were computed over every report at that
location that day, actionable or not — a wider set than the one the list
used to decide the day belonged on screen at all. The list and the
drill-down underneath it were silently answering two different questions
about the same day.

**Why this is the seam lesson, not just a bug:** the shared core was built
to stop two consumers of "which storm days/zips matter" from diverging, and
they diverged anyway, because the rule that mattered here lived outside the
one place both projections were guaranteed to read from. The generalization
worth keeping: the seam has to include every rule a projection *might*
independently apply, not only the join/filter core that was obviously
shared when `_FROM_WHERE` was written.

**Fix:** `_ACTIONABLE` is now its own string beside `_FROM_WHERE`, and both
`RECENT_DAYS_SQL` and `ZIPS_SQL` interpolate it. `fetch_zips()` picked up a
required, keyword-only `actionable_only` argument to match. Its one caller
outside the web view — `scripts/export_storm_zips.py --format zips` — broke
until it gained a corresponding `--actionable-only` flag; `--format pairs`
needed no change, because `PAIRS_SQL` does not interpolate `_ACTIONABLE`.

**`PAIRS_SQL` still omits `_ACTIONABLE`, deliberately — this is not the same
gap recurring.** The pairs export exists to show a storm day's raw shape,
unfiltered, and it has no second consumer yet to disagree with. Give it the
rule only when the export itself grows an `--actionable` flag, and fold it
into `_ACTIONABLE` at that point rather than writing a third copy.

**Related:** *The storm query lives in `hailsys/queries/storms.py`*
(2026-09-14).

---

## 2026-09-15 — County-by-polygon lookup verified: index scan, sub-millisecond warm

Closes the verification *County comes from TIGER county polygons*
(2026-09-14) asserted but did not measure. The entire argument against
storing county on `iem_data` rests on the query-time polygon lookup staying
cheap, so it needed a number, not just a plan.

**Note on the number itself:** an earlier session is recorded as having
measured this at ~11ms, but that EXPLAIN output was never committed to this
log and could not be found to verify against. Rather than copy a figure
that cannot be checked, the numbers below are freshly measured against the
live database today. The conclusion is the same either way.

`EXPLAIN (ANALYZE, BUFFERS)`, live archive (176,973 `iem_data` rows, 3,235
counties nationwide in `county_boundaries`), joining
`iem_data.geom` to `county_boundaries.geom` via `ST_Contains` — no query
module writes this join yet; this verifies the lookup is cheap before one is
built, not after:

| Case | Cold | Warm |
|---|---|---|
| One report (`iem_id = 1`) | 6.7 ms | 0.55 ms |
| One full storm day, 9 reports (2026-08-27) | 27.1 ms | 1.97 ms |

**Every plan uses `Index Scan using county_boundaries_geom_gix`, never a
sequential scan** — the GiST index built alongside the table (2026-09-14
entry) is what the planner actually reaches for. Same shape of proof as *The
buffer query needs a geography index, not the geometry one* (2026-09-03):
an index only helps if the query it is verified against actually uses it,
and only measuring confirms that rather than assuming it.

**Related:** *County comes from TIGER county polygons* (2026-09-14); *The
buffer query needs a geography index, not the geometry one* (2026-09-03).

---

## 2026-09-15 — The storm browser and the RentCast match view are separate pages

`/` (the storm browser) answers "what has hit, and where" — date and type
filters, expandable per-zip detail, and eventually a map. It is the page a
user lands on after login. The RentCast match view, built in Phase 3, is a
separate page answering a different question: "which listings does a storm
touch, and who has been contacted."

**Why separate pages, not one page with a mode switch:** the two have
different units. A storm-browser row is a query over `iem_data` and
`coverage_zips` — a storm day, a zip, a city. A match-view row is a query
over `storm_listing_matches`, `listings`, and `realtors` — a listing. Folding
both into one page behind a toggle means one URL answers two unrelated
questions depending on hidden state, and neither filtered view could be
bookmarked on its own — a link to "hail, last 90 days, type HAIL" and a link
to "uncontacted listings for the 08-26 storm" need to be two addresses, not
one address in two modes.

**Consequence for parking-lot item 10** (a date-range territory browse
grouped by zip or city): it is now the answer to "which of our zips got hit
this season," a question asked with no listings in mind — squarely the storm
browser's question, not the match view's. **Whether it needs its own page
under the storm browser, or lives as a view within the existing
recent-storm-days page, is unsettled** and left for when it is built.

**Related:** *Flask with server-rendered Jinja templates* (2026-09-14);
parking-lot item 10 (territory browse) and item 12 (is a storm a first-class
entity).

---

## 2026-09-15 — Contact state is shown as history, not a boolean

The match view (Phase 3) displays prior sends inline next to the agent —
"contacted 08-28 re: 08-26 hail" — rather than greying out or marking a
listing "done."

**Why:** `send_log` records a send to an agent, about a listing, referencing
a report. "Already contacted" is ambiguous about which of those three the
question is scoped to. Scoped to the listing, a second storm's legitimate
outreach reads as suppressed even though the report is new. Scoped to the
storm, the same agent getting a second letter about the same address two
weeks later — a plausible legitimate resend — looks identical to a
duplicate.

**The right rule depends on the frequency cap, which is undecided**
(parking-lot item 15; `database-schema.md` open question 5). Encoding a
grey-out now would choose that policy by accident, inside a display
decision, before the cap itself is designed. History over a boolean lets a
person read the actual sequence of prior contact and judge for themselves,
rather than trusting a status field that bakes in a rule nobody has chosen
yet.

**Related:** *Sending goes through a queue; `queued` is a real status*
(2026-09-01); *No "currently being viewed" state tracking* (2026-09-01) —
same instinct, showing a fact rather than encoding a lock, applied here to
contact state instead of concurrent editing.

---

## 2026-09-16 — `confidence_tier` stays out of the UI, and radar size is not a severity signal

Two decisions from one study. Evidence:
**`docs/analysis/radar-verification-2026-09.md`**, which checked 2,538
coverage-area hail reports against ten years of NEXRAD Level-III hail
detections from NCEI SWDI — radar-derived and fully independent of the LSR
network.

**`report_sources.confidence_tier` is not surfaced in the browse or match
views.** The column stays, the seed stays, the `LEFT JOIN` stays. Nothing
displays it.

**Why:** it would be displaying a distinction that is not there. Public
reports corroborate against radar at 93.5%, trained spotter reports at 92.4% —
a 1.09-point gap with p = 0.32, and the two converge to 94.6% and 94.7% once
four days of missing radar archive are excluded. The premise behind showing a
tier was that `PUBLIC` — roughly half our hail reports — is the weak input. It
is not, and the sign is the other way round.

Showing a tier anyway would be worse than useless. A sender who sees
"moderate" next to a public report will discount it, and would be discounting
a report the evidence says is as good as the trained one. That is a real cost
paid for a distinction that does not exist.

**This is a display decision, not a data decision.** The tier remains useful
for exactly what it was built for — an internal handle for spotting whether
some *specific* source is degenerate, the way `mPING`-as-`PUBLIC`
(parking-lot item 7) would need to be found. It is not a per-report quality
score and must not become one by appearing next to reports.

**Reversal condition, stated so this is not permanent by default:** a
per-source rate that separates by more than its confidence interval, on a
sample that supports the claim. COCORAHS is the live candidate — nominally
lower, but n = 119 gives a ±5-point band and that is not a finding. Rerun
against a longer archive before ever quoting a COCORAHS deficit.

**Radar-estimated hail size is not adopted as a severity signal.** Where a
report and a radar signature coincide, radar `MAXSIZE` correlates with the
reported magnitude at only r = 0.24–0.38, and exceeds the report on ~44% of
pairs. Medians agree exactly; individual pairs are close to a coin flip.

**Why this matters beyond this study:** it removes the cheap version of the
MRMS/MESH idea in `parking-lot.md` ("Not on the roadmap"). That entry defers
MESH on the grounds that a radar estimate is a different *claim* than a filed
report, and rightly treats the volume as the lesser objection. This adds a
second, independent reason: at the resolution we would want it — how big was
the hail at *this* address — the radar number does not carry the information.
So MESH is not a shortcut to per-listing severity, and if that trigger ever
fires it must not be justified on severity grounds.

**What this does not decide.** Whether radar could corroborate a report's
*existence* at the storm-day level is untouched and looks more promising —
~95% of our reports have a signature nearby. But that number is a forward-only
rate, inflated by five overlapping radars re-detecting the same cell every
volume scan, and the reverse direction was deliberately not computed because
it needs an event-clustering rule first. Do not quote 95% as symmetric
agreement; the write-up says why at length.

**Related:** *`report_sources` is a lookup, with no foreign key from
`iem_data`* (2026-09-03) — unchanged; this decides what is *shown*, not what
is stored or joined. `parking-lot.md` item 7 (mPING arrives as `PUBLIC`) and
the "Radar-derived hail size (NOAA MRMS / MESH)" entry under *Not on the
roadmap*. `hail-consolidated.md` §7 notes that the thin per-day report counts
shape "how the confidence tier should be framed" — this is that framing:
not framed, because there is nothing to frame.

---

## 2026-09-16 — `ZIPS_SQL` groups by report type

Previously one row per zip regardless of report type. That produced the same
mixed-unit magnitude problem the city view would later hit: `max(magnitude)`
across a HAIL row and a NON-TSTM WND GST row on the same zip reports one
number against whichever unit the planner happened to return, which is
meaningless when the two types use different scales.

**Fix:** `ZIPS_SQL`'s `GROUP BY` now includes `i.report_text` (`t.mag_unit`
alongside it, so the aggregate and its unit travel together), so a zip that
saw both hail and wind in the window returns two rows instead of one row
with an ambiguous max.

**Consequence for the CLI export.** `scripts/export_storm_zips.py --format
zips` changes shape: the unfiltered case now emits one row per zip per
report type rather than one row per zip. The filtered case (a single
`--type`) is unaffected by construction, since only one report type can
appear in that result set — verified at 46 lines for 2026-06-24 HAIL,
unchanged from before the grouping change.

**Related:** *The storm query lives in `hailsys/queries/storms.py`*
(2026-09-14); *`_ACTIONABLE` pulled into the shared query core*
(2026-09-15) — same family of bug, a rule applied in only one projection.

---

## 2026-09-16 — Territory browse is its own page, grouped by city-and-type

`/territory` answers "which places got hit" — a different question from the
storm list's "what happened when." The storm browser groups by day;
territory groups by place.

**Why city grouping carries `report_text`, not just `area_name`:** the same
mixed-unit problem `ZIPS_SQL` was just fixed for shows up again one level up.
A city with 1.5″ hail and a 70 mph gust in the same window would report
`max(magnitude) = 70` against whichever unit happened to come back first — a
number that means nothing without knowing which type produced it. Grouping
by `(area_name, report_text, mag_unit)` keeps the magnitude and its unit
attached to the type that produced it, the same fix as `ZIPS_SQL`.

**Zip mode needed no new SQL.** `ZIPS_SQL` already accepted any window, not
only a single day — the single-day assumption lived in the caller
(`storm_zips()`, which always supplied one day's bounds), not in the query.
Territory's zip-grouped view calls `fetch_zips` with the page's own
date-range window, and the query itself needed no change.

**The city→zip fan-out is disclosed, not hidden.** A report in range of
coverage zips that sit in two different cities counts under both, so a
page's sum of `report_count` across cities can exceed the actual report
total for that window. The page carries a footnote saying so, rather than
the number quietly failing to add up.

**Related:** *`ZIPS_SQL` groups by report type* (2026-09-16); *The storm
browser and the RentCast match view are separate pages* (2026-09-15) — same
reasoning against folding a second grouping into a mode switch, applied one
level down. Answers parking-lot item 10.

---

## 2026-09-16 — CSV export from the UI returns zip-level detail

`/export.csv` reuses `fetch_zips` — the same query behind territory's
zip-grouped view — with the page's current filters passed through via
`request.query_string`, not re-derived from the form fields.

**Why zip-level, not the day list:** the day list is something you read — a
summary to decide where to look. The zip export is what you act on — the
actual list a planner takes to RentCast. Exporting the day list would hand
someone a table they'd still have to turn into zips by hand; exporting zip
detail hands them the thing the next step actually consumes.

**Why the query string, not a re-read of the form:** the export link sits
next to Apply and points at whatever the page is currently showing.
Reusing `request.query_string` means the download matches what's on screen
without a second source of truth for "which filters are active" to drift
out of sync with the first.

**Related:** *`ZIPS_SQL` groups by report type* (2026-09-16); *`_ACTIONABLE`
pulled into the shared query core* (2026-09-15) — the `actionable_only` flag
this route reads has already caused one cross-projection disagreement, which
is why it's read with the same `"submitted"`-gated default as every other
route rather than a route-specific rule.

---

## 2026-09-16 — Radar verification follow-up: lone reports don't corroborate worse, and 2 miles is below the datasets' joint resolution

Follow-up on the same matched data behind *`confidence_tier` stays out of
the UI, and radar size is not a severity signal* (2026-09-16 above), no new
tolerances. Full detail in `docs/analysis/radar-verification-2026-09.md`.

**The concern:** `hail-consolidated.md` §7 records 382 of 1,468 Denver-local
hail days carrying exactly one report — if single-report days corroborate
worse against radar, a large share of the targeting data is weaker than the
headline rate suggests, and a lone `PUBLIC` report is the sharpest version
of that case.

**Result: no deficit.** Grouped by local Denver day: 1 report/day matches at
97.1% (n=69), 2–3 at 97.0%, 4–10 at 94.3%, 11+ at 94.7%. Lone reports run
slightly *higher*, every confidence interval overlaps, and the 2.4-point
spread isn't even monotonic — 11+ sits above 4–10 — which is what noise
looks like, not a gradient.

**A correction that mattered along the way.** The archive-gap exclusion
(days with zero SWDI rows anywhere in the box) has to key on the UTC day,
not the local day. A UTC day with no radar rows spans two local days, each
of which usually *does* have rows from the adjacent UTC day — keying on
local day alone retained 46 of the 50 gap reports while still scoring them
0. In this data that leak landed mostly in the busy-day bucket (one storm,
UTC 2016-07-08, splitting into two local days), *flattering* the lone-report
bucket by comparison — the opposite of the failure being guarded against.
Both exclusion keys land on the same headline number, but only excluding on
both gives an honest per-bucket breakdown, and the direction of the error
would not have been caught by checking only the bucket one was worried
about.

**The lone-`PUBLIC` cell specifically is too small to carry a claim.** n=20,
a 23-point confidence band — one report either way moves the cell 5 points.
Lone `PUBLIC` (95.0%) versus `PUBLIC` on a multi-report day (94.6%) is
p = 0.94. Honest form: *this data cannot detect a difference at n=20*, not
*there is no difference*. Needs more years of archive before this specific
case can be leaned on for anything.

**Separately, from the tolerance sweep: 2 miles is a resolution floor, not a
finding.** Widening 2→5 miles at fixed 30 minutes adds 15.6 points;
widening 15→60 minutes at fixed 5 miles adds only 2.9 — distance does the
work, time does almost none, except at the 2-mile row, which is unstable.
LSR positions are geocoded to town centroids and offsets like "2 NW
Durango," exported coordinates are quantized to ~0.4 mi on their own, and a
radar signature is a storm-cell centroid *aloft*, displaced by advection and
storm tilt from where the hail actually lands. Two miles is below the joint
resolution of the two datasets — which is why the existing 5-mile buffer
radius (`tuning.py`, 2026-09-10) is the right scale to verify against in the
first place, not a compromise from a tighter one.

**Related:** *`confidence_tier` stays out of the UI, and radar size is not a
severity signal* (2026-09-16); *`tuning.py`: both the zip radius and the
match radius are `5.0` miles* (2026-09-10).

---

## 2026-09-16 — Map: Leaflet, no tile layer

Leaflet, loaded from a CDN with no build step, consistent with the
server-rendered-Jinja decision (2026-09-14). No basemap tile layer: a tile
layer means a third-party request on every pan, with its own terms of use,
and the coverage polygons already serve as the basemap.

**Coverage polygons are a static, pre-generated GeoJSON fixture, not a
query.** `scripts/build_coverage_geojson.py` runs
`ST_SimplifyPreserveTopology` at 0.0005° (~55 m at this latitude) once, by
eye. Measured against the live database: the same 183 zips as unsimplified
GeoJSON run 4.18 MB; simplified, the fixture is 418 kB — a 10× reduction,
written to `static/coverage.geojson` for the browser to cache. **Why a fixture and
not per-request simplification:** the service area is stable — 183 zips,
edited rarely — so simplifying on every request would recompute an answer
that doesn't change between edits. **The simplified geometry is
display-only and never touches matching** — `ST_DWithin` and every other
spatial predicate in `storms.py` runs against `zcta_boundaries.geom`
directly, never against the simplified fixture.

**Report points are colored by `report_source_norm`, not raw
`report_source`.** The raw field is free text typed at individual NWS
offices with inconsistent case (`Public`, `PUBLIC`, `public`) — matching
color keys against it silently miscolors anything not cased exactly like the
key, the same class of bug the `report_sources` join has always guarded
against by matching on the normalized column instead. The tooltip still
shows the raw `report_source`, since the human-readable form — not the
normalized join key — is what should be displayed.

**5-mile radius circles use `L.circle`, not `L.circleMarker`.** `L.circle`
takes a radius in metres and draws a true circle on the ground;
`L.circleMarker` takes pixels and would grow or shrink with zoom, which
would misrepresent how far 5 miles actually is at whatever zoom level
someone is looking at. Circles are drawn `interactive: false` so they don't
intercept hover events meant for the report markers layered on top of them.

**Known caveat, already on record:** color-by-source is honest about what's
stored, not how a report was collected — parking-lot item 7 (mPING taps and
phone calls both arrive as `PUBLIC`).

**Related:** *Flask with server-rendered Jinja templates* (2026-09-14);
*`confidence_tier` stays out of the UI, and radar size is not a severity
signal* (2026-09-16) — same instinct, not asserting a distinction the data
doesn't support, applied to marker color instead of a tier badge.
Parking-lot item 22 (map).

---

## 2026-09-16 — Access for the demo: Tailscale Funnel, temporarily

`tailscale serve`, the mechanism chosen for Phase 2 (2026-09-14 entry), is
tailnet-only — a non-technical viewer would need to install a Tailscale
client to reach it, which isn't realistic to ask of someone previewing a
demo.

**Funnel makes the same hostname publicly reachable over TLS**, for the
duration of the demo. **Consequence:** with Funnel on, network-level access
control is gone — the application's own login becomes the *only* gate
between the public internet and the system, which raises the priority of
CSRF protection on every state-changing route, not only the send path Phase
5 will eventually add.

**Turn it off after.** This is not a reversal of the Phase 6 tunnel timing
(2026-09-14 entry's question stands as originally decided) — it's a
temporary widening for one demo, reverted once the demo ends.

**Related:** *Phase 2 UI is reached over Tailscale, not a Cloudflare
tunnel* (2026-09-14).

---

## 2026-09-17 — Vendor Leaflet into `static/`, off the `unpkg.com` CDN

*Map: Leaflet, no tile layer* (2026-09-16) loaded Leaflet itself from
`unpkg.com` — reasonable for getting the map built, but the CDN request runs
on every page load, not just the map ones, since `base.html` includes it
unconditionally for every route. Parking-lot item 28 named this as a
before-prod item; nothing about the app changed enough to make it more
pressing than described there, it was just next.

**`leaflet.css`, `leaflet.js`, and the two marker image assets it references
(`marker-icon.png`, `marker-shadow.png`) are now committed under
`hailsys/web/static/`**, and `base.html` points at them via `url_for`
instead of `unpkg.com`. No version change — still Leaflet 1.9.4, verified
against the vendored file's own `/* @preserve */` header rather than assumed.

**Why now rather than at actual prod deployment (item 28's original "when"):**
no reason to wait once the map was stable — a CDN dependency is the same risk
whether it's flagged for later or removed today, and removing it needed no
other change to land first. **Resolves parking-lot item 28.**

**Known gap, not a regression:** `leaflet.css` also references
`marker-icon-2x.png`, `layers.png`, and `layers-2x.png`, none of which are
vendored. This app never calls `L.marker` or adds a layers control — the map
draws report points as `L.circleMarker` and 5-mile rings as `L.circle`
(2026-09-16 entry) — so nothing requests those three images today. If a
future feature adds either, vendor the missing assets from the same
`leaflet@1.9.4` release at that point rather than assuming they're already
covered.

**Related:** *Map: Leaflet, no tile layer* (2026-09-16). Resolves
`parking-lot.md` item 28.

---

## 2026-09-17 — Ingest stays state=CO-only; no widening

RBI is licensed only in Colorado, so out-of-state hail reports have no
business use regardless of geographic proximity. This is a licensing
constraint, not a coverage gap to eventually close. Resolves parking-lot
item 21 / database-schema.md open question 12 — closed, not deferred. The
`state=CO` filter (2026-09-04 entry) stands as originally chosen.

---

## 2026-09-17 — RentCast client: stdlib urllib, Active-only, daysOld server-side

`hailsys/rentcast/client.py`. No new dependency — `urllib.error` already
splits cleanly into "RentCast responded with a status" vs. "never got a
response," which is the same split the UI error-popup design needs, so
`requests` wasn't buying anything on top of that for one GET endpoint.
`status=Active` scoping, learned from the prior system's 25k-record incident
(its `rentcast.py:69`). Pagination stops on `len(page) < 500` rather than
inspecting listing dates client-side — the prior system's early-exit assumed
page order tracked `listedDate`, but RentCast sorts by `lastSeenDate`; using
`daysOld` as a server-side filter avoids the assumption entirely. Throttled
to 20 req/sec (RentCast's hard per-key limit); retries with exponential
backoff on 429/500/503/504; RentCast's 404 is treated as zero results, not a
failure. `RentCastError` and subclasses carry `.attempts`/`.status` so cost
bookkeeping survives a failed call, not just a successful one — every
physical request is counted toward `api_pulls.actual_api_calls`, retries
included, since RentCast doesn't document whether a failed request is
excluded from billing and this errs toward not understating spend.

---

## 2026-09-17 — Storm identity: api_pulls links to a (date, report_text) window, not one iem_data row

`sql/013_pull_storm_link.sql` (applied). A storm has never been a single row
anywhere in this codebase — `hailsys/queries/storms.py`'s `fetch_zips` takes
a date window and a `report_text`, never an `iem_id`, because one hail day is
usually several scattered reports. `api_pulls.iem_id` stays NULL for a
storm-browser pull; `storm_date`/`report_text` (nullable, paired by a CHECK
constraint) record what was actually clicked instead. No change to
`iem_data` or the storm-grouping query — this only extends how a pull is
traced back to its origin.

---

## 2026-09-17 — Pull orchestration: continue past a bad zip, abort past a bad key

`hailsys/rentcast/pull.py`. A single zip's RentCast failure (bad param,
transient 5xx) logs that zip's real `http_status` to `api_call_log` and
moves on to the next zip — money already spent on prior zips isn't
discarded, and the gap is diagnosable rather than silent. An auth failure
(401/403) aborts the whole pull instead — a bad key fails identically on
every remaining zip, so there's nothing to gain by trying them.
`api_pulls.api_status = 'complete'` means the run finished, not that every
zip succeeded — per-zip truth lives in `api_call_log.http_status`; the
column is deliberately not overloaded to mean both. An upsert failure (bad
data from RentCast, e.g. `NotNullViolation`) aborts the whole pull the same
way an auth failure does, for the same reason — our own code will fail
identically on the next zip too.

---

## 2026-09-17 — Learning: a failed statement poisons the whole transaction until rolled back

Found during testing, not designed for in advance: once any statement in a
psycopg transaction raises, every subsequent statement on that connection
raises `InFailedSqlTransaction` — including the one meant to record the
failure — until an explicit `conn.rollback()`. `pull.py`'s upsert-failure
branch now rolls back before calling `_finish_pull`, or the `api_pulls` row
was left stuck at `'running'` forever with the real error swallowed by a
second, unrelated one. General pattern worth carrying forward to any future
code that catches a DB exception and tries to write anything afterward on
the same connection.

---

## 2026-09-18 — "Nothing is deleted" governs production data, not pre-launch test artifacts

Manual DELETEs cleared dry-run fixture rows (DRY-A/DRY-B) and their associated
`api_pulls`/`api_call_log` test rows (pull_ids 1–6) ahead of real RentCast
testing.

**Why:** CLAUDE.md's "nothing is deleted" rule governs production data —
captured storm reports, real listings, agent/realtor records, send/suppression
history — not test fixtures generated before the system has gone live.
Recording this once, deliberately, so it reads as a stated exception rather
than a quiet violation of the rule.

---

## 2026-09-18 — Work state is derived, never stored

`hailsys/queries/workstate.py`, its own module rather than a seventh
projection in `storms.py`: that file's `_FROM_WHERE` core answers "which
reports touched which coverage zips" (`iem_data`, `report_types`,
`zcta_boundaries`, `coverage_zips`); this one answers "what work has been
done" (`api_pulls`, `storm_listing_matches`, `send_log`). Different tables,
different question — and the 2026-09-16 `_ACTIONABLE` bug is the standing
lesson about bolting a rule onto a shared core it wasn't built for.

A storm day's state (Not pulled / Pulled, not matched / Matched, not sent /
Sent) is computed at read time. Looking at a storm never changes its state;
only doing the work does — a per-user "seen" flag would let the first person
to glance at the page absorb a storm that still needs attention, which is
the exact failure mode multi-user makes likely.

Storm days with no activity are absent from the result and default to Not
pulled via `state_for()`, keeping this module independent of the
storm-browser query core.

**Staleness is returned separately from state:** state is a fact about the
data, staleness a fact about the calendar.

---

## 2026-09-18 — One year is the work-queue aging threshold

`CLAIM_WINDOW_DAYS = 365`. Colorado insurance claims must generally be filed
within a year of the date of loss, so a storm older than that is not worth
chasing. Past the threshold, Not pulled reads as "No action taken" —
history rather than a task.

**Why anchored to the claim deadline rather than a round number:** a work
queue that only grows is one people stop reading.

---

## 2026-09-18 — Multi-user model: derived queue, separate activity feed

Up to five users; realistically one operator using the RentCast features and
four viewer-role users checking hail dates and property distances. The work
queue is derived from data state and is the same for everyone.

The activity feed ("since your last login") is layered on top, sourced from
`api_pulls.emp_id`/`started_at` and `users.last_login_at`, and is purely
informational — it never consumes the queue. Storm days never disappear from
the browser; a date-range selector means any window can be revisited
regardless of what's been seen or done.

---

## 2026-09-18 — `properties.geom` is generated, matching `iem_data`

`sql/014_properties_geom.sql`. `GEOMETRY(Point, 4326) GENERATED ALWAYS AS
ST_SetSRID(ST_MakePoint(list_longitude, list_latitude), 4326) STORED`, with
both a geometry GiST index and a `(geom::geography)` GiST index — the same
two-index split `zcta_boundaries` carries, for the same reason (`ST_DWithin`
in metres needs the geography one).

Generated rather than Python-populated so it cannot drift from its source
columns, which is why `iem_data.geom` is generated too.

**Added while `properties` was at 277 rows:** the same `ALTER` at 50k rows is
a table rewrite under lock.

**Verified:** 277 of 277 rows have coordinates, and `ST_AsText` confirms
longitude-first ordering (the `ST_MakePoint(x, y)` trap).

---

## 2026-09-18 — Matching: eligibility rules and an explicit trigger

`hailsys/matching/matcher.py`. Active listings only; New Construction
excluded at match time (a new roof is not a hail claim — the only play there
is representing a buyer after purchase); `actionable_only` reports, matching
how the storm browser already filters.

**Storing everything and filtering at match time is deliberate:**
`upsert.py` keeps New Construction so the catalog stays complete, and
targeting decisions live in the matcher.

`list_type IS DISTINCT FROM 'New Construction'` rather than `!=`, because
`NULL != 'x'` is `NULL` and would silently drop listings with no type.

Report type is not a matcher parameter — a match row carries its `iem_id`
and therefore already knows whether it came from hail or wind, so which
template to send is a Phase 5 question read off the row.

One `INSERT ... SELECT` with `ON CONFLICT DO NOTHING`; `RETURNING` gives a
free count of new rows.

**Verified:** 2024-05-30 HAIL produced 3390 rows / 277 distinct listings / 22
distinct reports; a second run produced 0.

---

## 2026-09-18 — Match counts must use `COUNT(DISTINCT listing_id)`

The many-to-many is intentional (one report matches many listings; one
listing matches several reports), which means raw row counts badly overstate
opportunity: 3390 match rows for 2024-05-30 is at most 277 actual leads.

Anything counting outreach opportunities — the match detail view, Phase 5
sending — counts distinct listings, not rows.

---

## 2026-09-18 — Pulls run in a background thread; matching follows automatically

`hailsys/web/jobs.py`. A thread, not a job queue: one operator pulling a
handful of storms a week doesn't justify a worker container. Status lives in
`api_pulls.api_status`, never in process memory — Gunicorn may run several
workers, and a status poll can land on one that never held the thread.

A pull runs `match_storm` at the end, because matching costs no API calls,
is idempotent, and there's no case where you pull a storm's listings and
don't want to know which are near the reports; the standalone Match button
remains for re-matching and for storms whose zips were pulled for a
different storm.

**Known gap:** `daemon=True` means a thread dies with its process, leaving
`api_pulls` stuck at `'running'` after a restart — `api_call_log` shows how
far it got, but nothing marks the run dead. Needs a stale-pull sweep before
this is load-bearing.

---

## 2026-09-18 — The pull POST recomputes zips and verifies a count

`/pull` recomputes the zip list rather than trusting hidden form fields, and
the form carries only the expected zip count. If the recomputed count
differs, nothing is pulled and the estimate is re-shown.

**Chosen for simplicity rather than security** — one integer instead of 45
hidden inputs — with the count check still catching data landing between
the two clicks. Both failure modes it guards against (new IEM data
mid-session, an authenticated user editing hidden fields) are remote at five
known users.

---

## 2026-09-18 — Date range replaces the fixed 30/90/365 selector

`_window_from_args()` in `views.py`, shared by all six filtered routes.
`?start=`/`?end=` is the interface; `?days=` is still honored so existing
links and bookmarks keep working.

Half-open windows throughout (`>= start`, `< end`), matching `_FROM_WHERE`
and `denver_day_bounds()` — consecutive half-open ranges tile with no gap
and no overlap, which is why the convention exists and why mixing a `<=`
into one branch of `workstate.py` was a real off-by-one.

`/export.csv` had to change in the same pass: `storms.html` forwards
`request.query_string` verbatim, so an export route understanding only
`days=` would have silently returned a different window than the page
showed.

---

## 2026-09-18 — Nothing rebuilds automatically; verify against a rebuilt image or you're testing yesterday's code

An end-of-day audit found the running `web` container 29 hours stale — none
of Phase 3's code was live in it. `docker/*.Dockerfile` uses `COPY`, and only
`./output` and `hailsys/web/static` are bind-mounted, so Python changes
require `docker compose build` plus a container recreate.

**Import checks against a stale image produce misleading passes, not
obvious failures:** modules that existed yesterday import fine while today's
don't exist at all. Any verification of Python changes must run against a
freshly built image, and a build step belongs in the loop before any claim
that something works.

---

## 2026-09-18 — Filters must travel across every handoff

Third instance of the same bug shape: `/export.csv` reconstructing its own
day range, `map.js`/`storms.js` forwarding a stale `data-days`, and
`/pull/estimate` hardcoding `actionable_only=True`. In each case the
receiving route rebuilt a default instead of receiving the caller's filter,
and silently answered a different question than the page the user was
looking at.

**The rule:** any link or form that hands off from a filtered view carries
every filter that view applied. The tell is a route computing a default for
something the caller already knows.

---

## 2026-09-21 — Match detail: one row per listing, grouped by agent

`hailsys/queries/matches.py`, `/storms/matches`, `matches.html`. One row per
listing, collapsing that listing's many match rows into nearest distance and
worst magnitude — "the closest, worst thing that hit it" is the pitch.
Grouped by agent, then listing, because outreach goes to agents and one
agent often holds several listings. Listings with no agent on record are
shown greyed, not hidden: they are real matches, and omitting them would
make this page's count disagree with the storm row's. Filtered to
`DEFAULT_MATCH_RADIUS_MILES`. No `actionable_only` parameter — the matcher
already applied it at match time, and re-filtering here would put the same
rule in two places. Counts are `COUNT(DISTINCT listing_id)`: 2024-05-30 HAIL
is 3,390 match rows but 277 listings across 234 agents.

---

## 2026-09-21 — Learning: Jinja's groupby sorts; itertools.groupby doesn't

Jinja's `groupby` filter sorts by the key before grouping, which (a) raises
`TypeError` on a nullable key mixing `None` with integers, and (b) discards
any ordering the SQL established. `itertools.groupby` never sorts — it
groups adjacent rows with equal keys, and `None == None` is fine. Grouping
now happens in the view with `itertools.groupby` over SQL already sorted
`agent_name NULLS LAST`. General point: equality and ordering are different
operations, and `None` only breaks ordering.

---

## 2026-09-21 — The match page states its own coverage gaps

A storm can read Matched while some of its zips were never pulled — a zip's
listings can arrive via a pull for a different storm. The match page lists
unpulled zips by number, links to a pull estimate for them, and shows the
date of the oldest listing data. Coverage uses the last successful pull
(`http_status = 200`): a failed call spent a request but returned no
listings. Measured against `actionable_only=True` zips, matching what the
matcher uses.

---

## 2026-09-21 — Activity feed: previous login captured before overwrite

`hailsys/queries/activity.py`; panel on `/` and full page at `/activity`.
`login()` stores the prior `last_login_at` in the session before updating
it — reading after the update makes the feed permanently empty. Because the
marker lives in the session, the feed is stable for a whole visit.
First-ever login shows a welcome note. Three sections: new storm days,
pulls, match runs.

New storm days use `iem_data.ingested_at` (when a report arrived, not when
the storm happened), indexed by 016, and bounded to the claim window so a
backfill doesn't announce years-old storms as news. Coverage and
actionability are not re-implemented: the feed finds which storm days got
new reports, then keeps only those `storms.fetch_recent_days` returns.
Match runs collapse to one line each by grouping on `matched_at`. `now()`
returns the transaction's start time, so every row from one `match_storm`
call carries an identical timestamp. Verified: 2024-05-30 shows as one run
of 277 listings.

---

## 2026-09-21 — Work state: failed pulls don't count; sent means sent_at IS NOT NULL

The `api_pulls` branch of `workstate.py` excludes `api_status = 'failed'`: a
failed pull brought nothing back, so the storm still needs one. `'running'`
still counts, so the Pull link doesn't reappear mid-pull and invite a
duplicate spend. The `send_log` branch requires `sent_at IS NOT NULL` — the
same test as the `sent_has_timestamp` CHECK, so query and constraint agree
on what "sent" means. Because state is derived, fixing the query
retroactively corrected pull 11's badge with no data cleanup.

---

## 2026-09-21 — RENTCAST_KEY belongs to web

Moving pulls into a background thread moved them into the web process,
which didn't have the key; the audit's live pull failed with
`RentCastAuthError` before any HTTP request (zero cost, correct abort). The
key is now in web's environment: block and kept on app for the CLI scripts.
General point: moving work between processes moves its secrets with it.

---

## 2026-09-21 — Header and flash messages live in base.html

One header bar: app name and nav (Storm Days, Territory, Activity) left;
"Signed in as" and Sign Out right; active page marked. Sign Out stays a POST
form — `/logout` is POST-only so a prefetch or link preview can't sign
anyone out. Flash messages render in `base.html`; previously only
`login.html` displayed them, so every other `flash()` sat invisibly in the
session.

---

## 2026-09-21 — Detail views are keyed by their row, not the page

`/territory`'s city expand forwarded the page's type filter instead of the
row's `report_text`, so expanding a HAIL row under Type = All returned hail
and wind days. Now `data-type="{{ row.report_text }}"`, matching
`storms.html`. Companion to the filters-travel rule: a handoff must carry
the right level's value, not just any value.

---

## 2026-09-21 — Performance: the spatial join was the cost, not the hardware

`/` took ~141 s on a 2019 range and ~3 s on a recent 60-day range. `htop`:
one core pinned, memory flat, swap idle — CPU-bound. `EXPLAIN (ANALYZE,
BUFFERS)` put 3.07 of 3.08 s in the `zcta_boundaries_geog_gix` index scans:
~8 ms per report of spherical `ST_DWithin` against ~24 KB (~1,500-vertex)
polygons, all buffers shared hit, and the whole join run twice (CTE plus
main query). Learning: actual time inside a loop is *per loop* — multiply by
loops. And Postgres runs a query on one core, so more cores wouldn't have
helped.

---

## 2026-09-21 — report_zip_distances: compute once, store

`sql/017_report_zip_distances.sql`. Every zip within the ceiling of every
report, with nearest-edge distance in metres, primary key `(iem_id, zcta5)`.
Same reasoning that made `storm_listing_matches` a table: reports never
change, TIGER polygons change yearly. `storms.py`'s `_FROM_WHERE` now joins
it on `iem_id` with `distance_m <= radius_m`, replacing the spatial join for
all six projections from one edit.

Ceiling lives in the database: `hail_pair_ceiling_m()`, 10 miles. A trigger
can't read `tuning.py`, and one number in two places drifts. Raising it
means a migration and a full recompute; `tuning.py` carries a comment
pointing here.
Guard: `hail_assert_radius_within_ceiling()` in the shared WHERE raises if a
query's radius exceeds the ceiling, rather than returning a silently
truncated answer.
Maintained by an `AFTER INSERT` trigger on `iem_data`, so no report can
exist without its distances and an absent row means "no zip in range,"
never "not computed." `AFTER` because `geom` is generated and not yet
computed in `BEFORE` triggers. Tradeoff accepted: a trigger bug stops
ingest — loud over silent.
Covers all 33,791 US ZCTAs, so adding a coverage zip later needs no
recompute. No FK on `zcta5`: it would block a TIGER reload.
Grants in the same transaction as the trigger: `hail_ingest` gets `SELECT`
on `zcta_boundaries` (without it the next nightly ingest fails, since the
trigger runs as the inserting role) and `SELECT, INSERT` on the new table —
`SELECT` too, not just `INSERT`, because the backfill script's own
resumability check reads this table as well as writing it. `hail_app` gets
`SELECT`.
Backfill: `scripts/backfill_zip_distances.py`, batched and resumable, run as
`hail_ingest` — the same role the trigger's own `INSERT` runs as, and the
one the grants above were written for. ~5 hours for 177,515 reports.
`storms.py` deploys only after the backfill completes and old-vs-new
`PAIRS_SQL` output is verified identical.
Entirely derived: truncating and rebuilding it is not deletion under
"nothing is deleted."

---

## 2026-09-21 — Admin settings page: Phase 4, and not every number is a setting

Moving tuning values from code into a single-row, typed settings table lets
an admin page change them without a deploy — `tuning.py`'s own header
anticipated this. But the three candidates differ. `DEFAULT_ZIP_RADIUS_MILES`
is a true setting (must stay ≤ ceiling). `DEFAULT_MATCH_RADIUS_MILES` is a
setting with a consequence: changing it hides every existing match until
storms are re-matched, and it's the number that appears in emails; it also
shouldn't exceed the zip radius. `hail_pair_ceiling_m()` is a rebuild, not a
setting — shown read-only. Settings read per request (correct across
Gunicorn workers without cache invalidation), with a change history. Users
and roles share the page; requires a `role_required` decorator, since routes
currently check only login.

---

## 2026-09-21 — With `./hailsys` bind-mounted into web, editing is deploying

**Supersedes the deploy-order note in the 2026-09-21 "report_zip_distances:
compute once, store" entry** ("`storms.py` deploys only after the backfill
completes…"), which treated deploying as a step that comes after the commit.

`docker-compose.yml` mounts `./hailsys:/app/hailsys:ro` into `web`. There is
no build-and-ship step between the working tree and the running app: the
files the container serves are the files on disk. `docker compose build`
matters only for `requirements.txt` and the Dockerfile.

Two consequences, both already true:

**A "verify before deploy" gate has to sit before the change reaches the
working tree, or it gates nothing.** The `report_zip_distances` plan wrote
"`storms.py` deploys only after the backfill completes and old-vs-new
`PAIRS_SQL` is verified identical" as though deploying were a later act. It
wasn't: `93c7f85` put the new `storms.py` on disk, and the next `web`
restart made it live — at the latest, the recreate before the Phase 3
audit's real pull. The
gate was a sentence in a document, not a mechanism. To gate a change for real,
develop and verify where `web` does not read it: a **`git worktree` in another
directory**, or a scratch copy, then merge or copy in once verified. Note a
branch checked out *in the mounted directory* is not a gate — `git checkout`
rewrites the same files `web` reads.

**Uncommitted edits are live after the next `web` restart.** Committed or
not, whatever is on disk when the workers start is what runs. Python is
imported at worker start; templates and static files are read from disk, so a
working tree can be half-live between edits and a restart — a template that
calls a filter the running workers never registered fails when it is first
loaded. Practical rule: after editing anything under `hailsys/`, either
restart `web` promptly or don't leave the tree in a state that can't run.

---

## 2026-09-21 — report_zip_distances verified: old and new `PAIRS_SQL` identical

**Supersedes the deploy-order note in the 2026-09-21 "report_zip_distances:
compute once, store" entry** ("`storms.py` deploys only after the backfill
completes and old-vs-new `PAIRS_SQL` output is verified identical"): the
backfill is complete and the output is verified identical, below. See also
"With `./hailsys` bind-mounted into web, editing is deploying" for why that
note could not have gated anything.

`scripts/verify_zip_distances.py` runs the whole `PAIRS_SQL` two ways over the
same window — the live `ST_DWithin` join against `zcta_boundaries`, embedded
in the script as a frozen reference (storms.py at `93c7f85^`), and the deployed
`storms.PAIRS_SQL` reading `report_zip_distances` — and compares every column
of every row as a multiset. Exact match required, not approximate: both sides
call the same spheroidal `ST_Distance` on the same inputs and round the same
way, so any difference is a finding. Ran as `hail_app` in a read-only
transaction, default 5-mile radius (8046.72 m).

| Window | Rows (old / new) | Old | New | Pairs within 1 m of radius | Result |
|---|---|---|---|---|---|
| 2024-05-30, HAIL | 1,420 / 1,420 | 2.53 s | 0.03 s | 1 | identical |
| Last 60 days (2026-07-23 → 2026-09-22 local) | 875 / 875 | 12.31 s | 0.02 s | 0 | identical |
| 2019-01-01 → 2019-03-01 (half-open) | 11,876 / 11,876 | 48.51 s | 0.50 s | 5 | identical |

No pair present on one side and missing on the other, no `distance_miles`
difference. Two of the three windows contain pairs within 1 m of the radius —
the only place a disagreement could plausibly hide — and both passed.

**The check can fail.** Negative control: with the new side's `distance_m`
test tightened by 1 m, the 2024-05-30 window fails with one row "only in
OLD" — zip 80229, `iem_id` 59966, `distance_miles` 5.00, exactly on the
boundary — and exits 1.

The 2019 window's old query took 48.5 s here, against the ~141 s recorded in
the performance entry above for the same dates. Not like for like: that
figure was for `/` (`RECENT_DAYS_SQL`, which ran the join twice — CTE plus
main query), and this run is a single `PAIRS_SQL`. The old `RECENT_DAYS_SQL`
was not re-timed on this window, so the gap between 48.5 s and 141 s is
unexplained beyond that; compare 48.5 s to 0.50 s for this pair, not to 141 s.

`storms.py` timings on the full calendar year 2019 after the switch, via the
same calls `/` and `/export.csv` make: `/` recent days 0.06–0.09 s
(actionable) and 0.45 s (all types); zips 0.05–0.06 s; export pairs 0.91 s
(49,592 rows). Was ~141 s.

Why the script stays: any recompute of `report_zip_distances` — raising
`hail_pair_ceiling_m()`, or a TIGER reload of `zcta_boundaries` — needs this
same check. It takes `--only NAME` and `--radius-miles N`; run it against
the ceiling radius too after a ceiling change. The old join is slow on wide
ranges, which is the reason the table exists, not a hang.

---

## 2026-09-22 — Land matches removed at the moment it was free to

Vacant land has no roof and no hail claim, so `matcher.py` now excludes
`property_type = 'Land'` at match time, alongside New Construction. It uses
`IS DISTINCT FROM`, so a property with no type recorded is still matched —
the exclusion drops known land, not unknowns.

The exclusion only governs future matching. The 54 Land matches already in
`storm_listing_matches` (3,430 → 3,376 rows) were deleted by hand while
`send_log` was empty. Matches are derived, and nothing had been sent against
them, so removing them cost nothing and rewrote no history. Once a match has
a send against it, it is history, and removing one becomes a real decision
rather than a cleanup — this was free only because it was done before the
first send.

Verified afterward: 93 Land properties exist in `properties`, 0 are matched.
The Land properties and listings themselves were not touched — only their
match rows.

---

## 2026-09-22 — Admin is a full superset of sender

**Supersedes** the 2026-09-01 entry "Three user roles: viewer, sender, admin"
on one point: `admin` no longer "manages users and nothing else." An admin can
do everything a sender can, plus user management and settings.

**Why the reversal:** the narrow admin was reasoned from the cost stages, and
the reasoning held for a larger office. It doesn't hold for this one. The
person who administers the system is the same person who runs pulls and will
send, so a user-management-only admin means that person needs two accounts
and has to switch between them to do a normal day's work. Two logins for one
human is friction with no security payoff: whoever holds the admin password
can already create a sender account for themselves. The separation would be a
ceremony, not a control.

`sql/002_users.sql`'s column comment said the old rule. `sql/018_admin_failsafe.sql`
replaces it with `COMMENT ON COLUMN users.role`, per the additive-after-backfill
rule, so `002` is not edited. `docs/database-schema.md`'s `users` section carries
a superseded note pointing here.

---

## 2026-09-22 — Three roles, enforced server-side first

`viewer` browses storms, territory, matches and activity, and exports CSV.
`sender` adds everything that spends or acts: the pull estimate, the pull
itself, and match. As send, re-pull and quota warnings are built, they go to
`sender` too. `admin` is everything (entry above).

**The pull estimate is sender and admin, not viewer.** It costs nothing to
render, but it is the confirmation step for a paid action. A viewer has no use
for it, and leaving it open would mean the Pull link leads to a page whose
submit button 403s.

**Enforcement is `role_required` on the route; the UI follows it.**
`@role_required("sender", "admin")` sits on `/pull/estimate`, `/pull` and
`/match`, and a viewer who types the URL gets a 403. The UI gating is
`can_pull`, injected into every template by a context processor in
`create_app()`, which swaps the Pull link and Match button on `storms.html`
for greyed text with a tooltip. The rule holds without the UI part.
Hiding a button without checking the route is the mistake this order avoids.
Shown greyed rather than hidden, so a viewer can see the action exists and
why they can't take it.

---

## 2026-09-22 — Admin routes are a blueprint with one before_request check

`hailsys/web/admin.py` registers `admin_bp` at `/admin`, with a blueprint-level
`before_request` that returns `require_role("admin")`. A `before_request`
hook that returns a response ends the request before the view runs, and
`require_role` returns `None` when the user is allowed.

**Why a blueprint hook and not a decorator per route:** a decorator has to be
remembered on every new route, and the one it's forgotten on is an open admin
action. The hook makes that impossible: any route added to `admin_bp` is
enforced by being on it. It also gives Phase 6 a single URL prefix to put
extra protection in front of (Cloudflare Access, an IP rule), without auditing
routes one by one.

---

## 2026-09-22 — Role is cached in the session; account state is checked every request

`session["role"]` is set at login and read from there. `is_active`,
`users.sessions_invalidated_at` and `settings.global_sessions_invalidated_at`
are re-read on every request by `auth.load_current_user`, registered as
`app.before_request`.

**Why the split:** Flask's sessions are signed cookies. The server holds no
session list, so there is nothing to delete to sign someone out remotely.
Forced logout therefore has to be a comparison: at login the session records
`issued_at`, and every request compares it against the two invalidation
timestamps. If either is later, the session is cleared. Setting the per-user
column boots one person, and setting the `settings` column boots everyone,
the admin included. A deactivated user fails the `is_active` check on their
next request, not at their next login.

Role is the one thing left cached, because a role change already forces a
re-login (entry below), so the cache can't go stale in practice. The cost of
the per-request query is one indexed single-row lookup plus a singleton join.

---

## 2026-09-22 — Changing a role signs the user out

`change_role` sets `sessions_invalidated_at = now()` in the same `UPDATE` as
the new role. Because role is cached in the session, without this a demoted
sender would keep sender rights until they chose to log out, which might be
days. Forcing the re-login makes the change take effect on their next request.
The admin reset-password route does the same, for the same reason: a reset
password should end every session that was opened with the old one.

---

## 2026-09-22 — Changing your own password keeps the current session

`/account/password` sets `sessions_invalidated_at = now() RETURNING` that
value, then writes the exact returned timestamp into `session["issued_at"]`.
The hook boots a session only when `invalidated_at > issued_at`, strictly
greater, so the current session, now stamped equal, survives, and every other
session, issued earlier, is signed out.

**Why the exact returned value and not `datetime.now()`:** the Python clock
and the database clock are two clocks. A Python timestamp taken a moment
before the database's `now()` would read as earlier, and the user would be
signed out by their own password change. Taking the value from `RETURNING`
removes the second clock entirely.

---

## 2026-09-22 — Last-admin protection is a deferred constraint trigger

`sql/019_last_admin_protection.sql`: `trg_last_admin`, an `AFTER UPDATE OR
DELETE` constraint trigger on `users`, `DEFERRABLE INITIALLY DEFERRED`,
calling `enforce_last_admin()`, which raises if no active admin remains.

**Why not a CHECK:** a CHECK sees one row. "At least one active admin exists"
is a fact about the whole table, and no single-row constraint can count.

**Why deferred:** a non-deferred trigger checks after each statement. A
legitimate swap (demote A, promote B, one transaction) passes through a
moment with zero admins between the two statements, and a per-statement
check would reject it on that transient state. Deferred, it runs at `COMMIT`,
against the state the transaction actually leaves behind.

It lives in the database because it is a rule that must hold. The routes
report it (`_user_action` catches `RaiseException` and flashes its message),
but the protection doesn't depend on them.

---

## 2026-09-22 — Settings changes are attributed through a transaction-local setting

`update_settings` runs `SELECT set_config('app.current_emp_id', %s, true)`
before its `UPDATE`, and the `log_settings_change()` trigger reads it with
`current_setting('app.current_emp_id')` to fill `settings_history.changed_by`.
`set_config(..., true)` is the parameterised form of `SET LOCAL`: `SET` can't
take a bind parameter, and the `true` makes it last only until the transaction
ends, so it can't leak onto the next request's use of the connection.

**Why this route at all:** the trigger runs inside Postgres, and the database
connects as `hail_app` for every user. Nothing in the database knows which
employee is behind a request unless the application tells it.

**Fails loudly if unset, by design.** `current_setting` is called without
`missing_ok`, so a settings `UPDATE` that didn't set attribution raises
rather than writing a history row with no author. A missing attribution is a
bug in the calling route, and a silent NULL would hide it.

---

## 2026-09-22 — `trg_log_settings_change` is scoped to the radius columns

The trigger is `AFTER UPDATE OF default_zip_radius_miles,
default_match_radius_miles`, with a `WHEN` clause requiring one of them to
actually differ (`IS DISTINCT FROM`).

**Why:** `settings` is a singleton row that holds more than the radii. The
boot-everyone route updates `global_sessions_invalidated_at` on the same row.
Unscoped, the trigger fired on that update too, and it demanded attribution the
boot route has no reason to set. **Verified, not assumed:** an `UPDATE` with
no `app.current_emp_id` set raises `UndefinedObject`, so the sign-out-everyone
action would have failed outright. That is the more serious of the two
consequences. The other is that, had attribution been set, it would have logged
a radius "change" that never happened. `UPDATE OF` limits it
to statements naming the radius columns. `WHEN` then drops a save that
re-submits the same values, so the history records changes, not form
submissions.

---

## 2026-09-22 — CSRF via Flask-WTF, no token time limit

`CSRFProtect(app)` in `create_app()`, with `WTF_CSRF_TIME_LIMIT = None`. Every
POST form carries `csrf_token()`.

**Why a library and not a hand-rolled token:** `CSRFProtect` fails closed.
It rejects any POST without a valid token, including POST routes written
later by someone who never thought about CSRF. A hand-rolled check protects
only the routes it was added to. The Tailscale Funnel entry (2026-09-16)
already named CSRF as the gap once the login is the only gate.

**Why no time limit:** the default is 3600 seconds, after which a form left
open for an hour fails with a 400 on submit. The token is still tied to the
session, so it dies when the session does, which is the lifetime that
matters. A `CSRFError` handler flashes a "form expired" message and
redirects back, not a bare 400 page.

---

## 2026-09-22 — Radii are read from `settings` per request

**Supersedes** the 2026-09-10 entry "`tuning.py`: both the zip radius and the
match radius are `5.0` miles, and there is no settings table yet" on storage
only. The values and the reason for two separate radii are unchanged.
`sql/020_settings_radii.sql` moves them into `settings`, seeded at 5.0 each.

`hailsys/settings.py`'s `fetch_settings()` returns them, and
`load_current_user` puts them on `g.settings` for every request. It casts to
`float`: the columns are `NUMERIC`, psycopg returns `Decimal`, and
`miles_to_metres` multiplies by a float, and `Decimal * float` raises
`TypeError`. Casting once in `fetch_settings` means no call site can forget.

**No caching.** Gunicorn runs several workers, and a cached value would need
invalidating in every one of them when an admin saves. Reading per request
costs one singleton-row query and has no invalidation to get wrong.

`matcher.py`'s `match_storm` takes `radius_miles=None` and reads settings when
it is `None`. A default argument like `radius_miles=fetch_settings(...)` would
be evaluated once at import, freezing whatever the radius was when the worker
started.

---

## 2026-09-22 — Correction: identity columns need no sequence grant

An earlier note in this project's working notes said a
`GENERATED ALWAYS AS IDENTITY` column needs a separate
`GRANT USAGE ON SEQUENCE` for the role inserting into it. That was wrong.
Verified: table-level `INSERT` is sufficient, and the identity's sequence is
advanced without a separate grant. `sql/010_roles.sql`'s `hail_ingest` comment
("No sequence grants, each primary key is GENERATED ALWAYS AS IDENTITY, which
is reachable through INSERT") was right.

`sql/020_settings_radii.sql` includes
`GRANT USAGE ON SEQUENCE settings_history_history_id_seq TO hail_app`, written
under the wrong belief. It is redundant, not harmful, and is left in place
per the additive-migration rule.

---

## 2026-09-23 — Municipal boundaries from DOLA's dissolved layer; permit sources scoped for the top three jurisdictions

**A deliberate exception to "do not build ahead of the current phase."**
Building permits are not in the phase list. `municipal_boundaries`
(`sql/021`, `scripts/load_municipal.sh`) was authorised during Phase 4 as the
groundwork for scoping permits as a source. The permit half stays research
only: no permits schema and no adapter. The exception covers this one table
and sets no precedent for the rest.

### Boundary source: DOLA, dissolved layer

`DOLA_Municipalities_(Boundaries_Dissolved)/FeatureServer/0` on
`services3.arcgis.com/DgjqnJA1rgO92Soi`, listed on geodata.colorado.gov as
`public_authoritative` and owned by OIT for DOLA. There are 274 features,
native SRID 3857, fetched with `outSR=4326`.

**Why DOLA and not the CU GeoLibrary copy:** the GeoLibrary copy is a 2017
snapshot, and a municipal boundary moves every time a town annexes land.
DOLA is the state agency that records annexations, and it republishes nightly.

**Why the dissolved layer and not the 1,911-row `Municipal_Boundary` layer:**
the dissolved layer has one row per municipality, which is what
point-in-polygon needs. Its description says it "does not show annexations,"
which could mean the geometry is missing annexed land. It isn't. Every
city's area in the dissolved layer was compared against `ST_Union` of the
base and `type='A'` polygons in the annexation layer, and none differs by
more than 1%. Denver differs by 0.0006% and Aurora by 0.0010%. The largest
gap, Commerce City at +0.46%, is exactly its one `type='S'` polygon
(0.440 km²). The dissolved layer includes it, and against a union of all
rows the gap is 0.002 km². The phrase means the annexation *attributes*
(ordinance number, recording date) are dropped. That history stays in the
1,911-row layer if it is ever needed.

**Traps recorded:**
- `city` is the 5-digit Census place code (Denver `20000`), not a name. The
  name is `first_city`. The column is named `place_fips` so it cannot be
  mistaken for a name.
- The annexation layer writes missing values as the **literal string
  `'null'`**, the same shape as IEM's `None`. The loader converts these to
  JSON null.
- `dataLastEditDate` changes every night at about 07:00 UTC. It dates the
  publish, not a boundary change.
- **Hudson appears twice.** `37820` is the town (17.6 km²). `03782` is one
  0.036 km² 2024 annexation ("Long Annexation No. 8") filed under a mistyped
  code, the same digits shifted one place. This is the whole reason for 274
  rows against 273 base polygons. **This is a known source error,
  `03782` → `37820`, and it is not fixed in the raw data or at load.** The
  raw file and `municipal_boundaries` both hold what DOLA published. The fix
  belongs in a correction table applied on read, to be built later, so the
  correction is visible and attributable rather than buried in a loader,
  and it survives the next reload. Until then, anything that counts or ranks
  municipalities must treat `03782` as Hudson. Worth reporting to DOLA.
- 65 of 274 geometries were invalid as received (nested shells, ring
  self-intersections). `ST_MakeValid` can return a GeometryCollection, so the
  loader applies `ST_CollectionExtract(..., 3)` before `ST_Multi`.

**Fetch is `scripts/fetch_municipal.py`** (stdlib, runs on the host). It
takes the total from `returnCountOnly`, pages by `resultOffset` ordered on
the OID, fails if the pages don't add up to the total or an OID repeats, and
writes `<name>_<date>.geojson` with `<name>_<date>.layer.json` beside it.
The loader reads the source edit date from the `.layer.json`. It sends its
own User-Agent because `ags.auroragov.org` returns 403 to urllib's default.

**Reload replaces the whole set: DELETE + INSERT in one transaction.** This
departs from the ZCTA and county loads, which use `ON CONFLICT DO NOTHING`.
On this table, `DO NOTHING` would keep a pre-annexation boundary forever. The
table is derived entirely from DOLA, so replacing it is not deletion under
"nothing is deleted". Verified: a second run deleted 274 rows and inserted
274, with no duplicates.

**Verified:** 274 rows, all SRID 4326, zero invalid geometries.
- Known-answer points: Civic Center Park is in Denver, and the Aurora
  Municipal Center is in Aurora. Highlands Ranch Town Center is in no
  municipality, and `county_boundaries` puts it in Douglas.
- Jurisdiction inventory over the 183 coverage zips: area fractions per zip
  sum to 1.00000–1.00004.

### Permit sources: the top three jurisdictions by stored property count

The ranking counts the 508 rows in `properties`, which come from 5 pulled
zips (80014, 80103, 80105, 80135, 80136). **It reflects pull history, not
the territory.** By area, unincorporated El Paso, Weld and Adams lead.
Rerank before building anything.

| | Aurora (277 properties) | Unincorporated Adams (75) | Unincorporated Douglas (64) |
|---|---|---|---|
| Issuer | City of Aurora Building Division | Adams County Community & Economic Development (unincorporated only) | Douglas County Building Division (unincorporated only) |
| Coverage check (point-in-polygon on roofing permits) | 99.95% in Aurora | 100% in unincorporated Adams | 100% in unincorporated Douglas |
| Endpoint | `ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44` | `services3.arcgis.com/4PNQOtAivErR7nbT/.../Building_Permits_Eye_On_Adams/FeatureServer/0` | `services.arcgis.com/seTexOicoRXDvRsJ/.../All_Permits_View/FeatureServer/0` |
| Records | 162,233 | 72,249 | 285,635 |
| History | 2021-09-24 → (looks like a rolling 5 years) | 2011-01-03 → | 1990 → (roofing type used from ~2000) |
| Cadence | not stated; latest record yesterday | not stated; edited today | "Nightly" (item description); full rebuild |
| Roofing identified by | distinct `SubDesc`: `Roofing-RT2`, `Roofing Commercial-NT2` | `TypeOfWork = 'Re Roof'` through 2016; after that mostly keywords in `Description` with a blank type | distinct `PERMIT_JOB_TYPE = 'Roofing'` |
| Coordinates | point geometry, native 2232, served in 4326 | `X`/`Y` in degrees; datum not stated | `LOCATION` text `(lat, lon)`; **51% of roofing rows have none** |
| Terms | disclaimer + indemnity, no licence grant | none published | none published |
| Page size | 2000 | 2000 | 1000 |

**Known answer.** Denver is not in the top three, so the 2017 RESCON and
ROOFSIDE check does not apply. Tested instead against our own `iem_data`,
using hail ≥ 1.00″ inside each jurisdiction and comparing roofing permits in
the 90 days after a storm with the same 90 days a year earlier:
- 2023-05-10: Aurora 4,294 vs 803, Adams 382 vs 86.
- 2023-06-22: Douglas 3,926 vs 500.
- 2012-06-06: Douglas 5,543 vs 624.
- Adams, May 2017 storm: June–August 2017 is 2.3× the same months of 2016.

Every source shows the surge. A miss (Aurora 2024-05-30) is explained by the
comparison year being the 2023 surge itself.

**Raw snapshots taken 2026-09-23, with no schema and no loader.** Aurora's
history starts 2021-09-24, five years less a day before the fetch, which
looks like a rolling window. If so, every day that passes drops a day of
history for good. So all three sources were captured with
`fetch_municipal.py --allow-null-geometry` to
`data/raw/permits/<source>/`, gitignored, with each layer's metadata beside
it:

| Source | Records | Size | Null geometry |
|---|---|---|---|
| Aurora | 162,233 | 149 MB | 0 |
| Adams | 72,249 | 66 MB | 856 |
| Douglas | 285,635 | 226 MB | all of them (it is a table; coordinates are in `LOCATION` text) |

Whether Aurora's window really rolls is **not yet confirmed**: the only two
readings of its start date were both taken on 2026-09-23. Re-check the
earliest `InDate` on a later day. If it has moved past 2021-09-24, the window
rolls and snapshots need to recur.

**What this does not settle:** licence terms for any commercial use, Adams's
keyword precision (untested), Douglas's missing coordinates (geocode or
address-match, which is a design question), and whether "permit filed" is
ever a claim the product makes. The last one is the same kind of question
the MESH entry in the parking lot raises.

---

## 2026-09-23 — Permits are parked; the claim rule; jurisdiction is a polygon, never a mailing city

Three decisions that follow from the permit and jurisdiction research above
(*Municipal boundaries from DOLA's dissolved layer…*, same date). Recorded
now so the research does not read as an implied commitment to build.

**Permits are parked until the system is running.** The research showed that
permit data exists and corroborates hail. It did not show that RBI needs it
before Phase 5 sends a first real batch. Jurisdiction and address-search work
is parked too, near the end of the project, and **depends on parking-lot
item 23 (geocoding) being resolved first**: a jurisdiction can only be stated
for an address once the address is a point. The parked work is filed as
parking-lot items 70–84.

**The claim rule: permit data never becomes "your roof is X years old."**
The most it can support is "no roof permit on record since <date>," and only
for a jurisdiction whose records are known to go back that far. Aurora's open
data starts 2021-09-24, so a missing Aurora permit says nothing about 2019.
The absence of a record is only evidence where the record would exist.
Corroboration stays internal: ranking, confidence and what the UI shows a
sender. **Outreach still cites the filed hail report only**, which is the
same line *Email wording claims a report, not damage* (2026-09-01) draws and
the MESH entry in `parking-lot.md` defends. A permit is a record of what an
owner filed with a building department, not of the roof's condition, and
presenting one as a condition changes the product's claim in the same way.

**Jurisdiction comes from point-in-polygon on `municipal_boundaries`, never
from the mailing-address city.** A USPS city name is a post office's service
area, not a municipality. "Aurora" addresses include unincorporated Arapahoe
(reported, not re-measured here). Douglas County's permit table shows the
same shape from the other side. It covers unincorporated Douglas only: all
36,762 of its roofing permits that carry coordinates fall there. Yet its
roofing permits give the mailing city as `PARKER` 18,484 times and
`CASTLE ROCK` 2,378 times, and both are separate municipalities.
`HIGHLANDS RANCH` (40,332) is not a municipality at all. Asking which
building department covers an address means asking which polygon contains
its point. A point in no municipality is unincorporated, and its county
comes from `county_boundaries`. `coverage_zips.area_name` is a USPS city
too (*"City" in the UI means the USPS city of an affected zip*, 2026-09-14),
so it must not be read as a jurisdiction either.

**Related:** *Municipal boundaries from DOLA's dissolved layer…*
(2026-09-23); *Email wording claims a report, not damage* (2026-09-01);
parking-lot items 23 and 70–84.

---

## 2026-09-23 — Match runs are recorded, so an empty match is visible

`sql/022_matched_runs.sql` adds `match_runs`, one row per match attempt.
Before it, `storm_listing_matches` was the only trace of matching, and a run
that found nothing in range wrote nothing. So "ran, nothing in range" and
"never ran" were the same absence, and the badge could not tell them apart
(parking-lot item 40). A run is now a record in its own right.

**The row is written before the work, as `'running'`, and finished as
`'complete'` or `'failed'`.** Same shape as `api_pulls` and `ingest_runs`,
for the same reason: a process that dies mid-match leaves a row to reconcile
against instead of nothing. `match_storm` commits the start row on its own,
and on failure it rolls back, records `'failed'` with the error, and commits
that before re-raising. Without that last commit the caller's
`with get_connection()` rolls the failure back on the way out, and the run
sits at `'running'` for good.

**`matches_created` counts new rows only.** `_MATCH_SQL` is
`ON CONFLICT DO NOTHING`, so re-running an already-matched storm records 0
even though the storm has matches. The badge must not read that number.
Whether a storm has matches is still a question for `storm_listing_matches`.

**`match_storm` now requires `storm_date` and `report_text`.** Both are
keyword-only with no default. A `match_runs` row has to name one storm day
and one type, or the work-state query can't tell which badge to change, so
the old all-types call path is gone. Leaving `report_text=None` as a default
would have meant the only thing preventing a `NOT NULL` violation was
callers happening to pass it. Python now raises at the call site instead.
`/match` returns 400 for a blank type.

**"Matched, none in range" needs both a completed run and a pull.** A run
alone is not enough. A match against a storm whose zips were never pulled
searched an empty set of listings, and finding nothing there says nothing
about the storm. That storm is still "Not pulled", and its Pull link must
stay. The rule is `match_ran and pulled` in `workstate._label`. Verified:
2026-08-22 HAIL, never pulled, reverted to "Not pulled" with Pull after two
empty runs. `/match` also now distinguishes "No listings within range" from
"No new matches: already matched" by checking `storm_listing_matches` in the
same connection. Match stays offered on "Matched, none in range", since a
wider radius may find something.

**Numbering:** `match_runs` is `sql/022`, not `021`. `sql/021` is
`municipal_boundaries`, from the permits and jurisdiction research on the
same day.

**Related:** *The `ingest_runs` row is written before the fetch, not after*
(2026-09-08); *Learning: a failed statement poisons the whole transaction
until rolled back* (2026-09-17); *Work state is derived, never stored*
(2026-09-18).

---

## 2026-09-23 — Re-pull goes through the estimate page; stale storms are greyed, not blocked

**The re-pull link reuses `/pull/estimate` rather than adding a route.**
That page already shows the cost and lists every zip pulled in the last
seven days with its last pull time. The guard a re-pull needs already
exists, and a second route would be a second place for it to drift. The link
appears on every state past "Not pulled" (parking-lot item 46). Its label is
"Pull again" when a pull was recorded against this storm, and "Pull" when the
storm reached "Matched" through a pull made for a different storm. That
case is a first pull of its own, and "again" would be wrong.
`workstate.last_pulled_at` drives both the label and the "last pulled" date,
which is shown in Denver time.

**Past the 365-day claim window, the control is greyed with a "past claim
window" note, for every role.** The stale check runs before the role check,
because the claim window is a property of the storm, not of who is looking.
Spending on listings whose owners can no longer file a claim is the waste
the one-year threshold exists to prevent.

**`/pull/estimate` is deliberately not gated on staleness.** The greying is
guidance, not a guard. A sender who goes to the estimate URL directly still
sees the cost and the recent-pull warning before anything is spent, and a
two-year-old storm may still be worth one look at what's listed there now.
Recorded so the missing server-side check is not later "fixed" as an
oversight. If it should become a guard, that is a new decision.

**Related:** *One year is the work-queue aging threshold* (2026-09-18);
*The pull POST recomputes zips and verifies a count* (2026-09-18).

---

## 2026-09-23 — RentCast quota: settings, warn and allow, usage from `api_call_log`

`sql/023_rentcast_quota.sql` adds `settings.rentcast_billing_day` (default 9)
and `settings.rentcast_monthly_quota` (default 1000), for parking-lot item 50.
They live in `settings` rather than in code so a plan change needs no deploy,
the same reasoning that moved the radii there.

**The billing day is capped at 28.** A billing day of the 29th to the 31st
has no equivalent in February, and nobody has decided what rule to fall back
on. The CHECK keeps the question from arising rather than answering it in
code. `quota.period_bounds` relies on the cap: it can always set the billing
day within any month.

**Warn and allow, not a hard block.** Overage is billed, not refused, so
going over costs money but breaks nothing. The figure being checked is an
estimate, so a hard block would be enforcing a guess. And a block that fired
mid-pull would stop after some zips had already been paid for, leaving a
half-pulled storm: money spent for incomplete data. The pull estimate shows
this period's usage and, when used plus this estimate would pass the quota,
an amber note naming the projected overage. The pull still runs.

**Usage is summed from `api_call_log`, not `api_pulls.actual_api_calls`.**
The log is written per zip as a pull runs, so it counts spend by a pull
still in flight and by one that died partway. `actual_api_calls` is only set
when a pull finishes. It is NULL for a running pull, and stays NULL forever
for one whose thread died, so both would read as zero. Known gap: calls made
by a pull that aborts on a bad API key are added to `actual_api_calls` but
get no `api_call_log` row, so they aren't in the usage figure. RentCast
probably doesn't bill rejected-key requests, but that is unconfirmed.

**The period rolls over at midnight Denver time.** `fetch_usage` converts the
period's dates with `denver_day_bounds`, like every other "what day is it"
question in the app. Whether RentCast itself rolls over on UTC or Denver time
is **not confirmed**. Near a boundary the two differ by up to seven hours of
calls counted in the wrong month.

**`settings_history` records the two new columns.** "Who raised the
ceiling, and when" is exactly the question that table exists to answer.
`trg_log_settings_change` now fires on all four settings columns, and still
not on `global_sessions_invalidated_at`. Rows from before `023` are NULL in
both new columns, not backfilled: a default would assert a value nobody
recorded at the time.

**Usage shows on the storm list for senders and admins only.** Viewers can't
spend, so the query doesn't run for them.

**Related:** *Radii are read from `settings` per request* (2026-09-22);
*`trg_log_settings_change` is scoped to the radius columns* (2026-09-22),
extended here to four columns on the same scoping rule; *RentCast client:
stdlib urllib…* (2026-09-17), which counts every physical request.

---

## 2026-09-23 — An admin cannot deactivate, demote or sign out their own account

`deactivate_user`, `change_role` and `boot_user` refuse the acting admin's
own row, as `reset_password` already did. The admin page shows "your
account" in place of that row's controls (parking-lot item 60).

**Why last-admin protection wasn't enough:** `trg_last_admin` only fires when
a change would leave no active admin. With a second admin present, all three
actions succeed on your own row, and each ends your session. Deactivation
fails the `is_active` check, and a role change or sign-out sets
`sessions_invalidated_at`, so you are signed out on the next click. One
mis-click locks you out of the account you are using.

**Enforced on the server, hidden in the UI.** Hiding the buttons alone would
leave the routes open to a crafted POST. Same order as the role rules:
the route decides, and the page follows.

**Consequence, accepted at current headcount:** no admin can change their
own account through the UI. Another admin has to do it. With a single admin,
that means creating a second admin first. Recovery from a mistake that the
UI can't reach, such as a locked-out sole admin, is a manual `UPDATE` at
psql.

**Related:** *Last-admin protection is a deferred constraint trigger*
(2026-09-22); *Changing a role signs the user out* (2026-09-22).

---

## 2026-09-23 — CSRF failures return 400 with a rendered page

**Supersedes** one point of *CSRF via Flask-WTF, no token time limit*
(2026-09-22): that entry's handler "flashes a 'form expired' message and
redirects back". The handler now renders `csrf_error.html` with status 400.

**Why:** the 302 looked identical to a successful POST, which also returns
a 302. A person saw the flash, but anything checking status codes (curl, a
script, any future test) saw success, so a CSRF failure was untestable
(parking-lot item 59). A 400 is a failure by any reading. The rest of the
earlier entry stands: `CSRFProtect` still fails closed on every POST, with
no token time limit.

---

## 2026-09-23 — No forced password change after an admin reset, for now

Parking-lot item 61 proposed a `must_change_password` flag, set by an admin
reset and cleared by `/account/password`, so a user can't keep a password
the admin knows. **Declined for now.** With a handful of known users in one
office, the admin can tell the person to change it, and the reset already
signs them out everywhere. Recorded so its absence reads as a decision, not
an oversight. It should be reconsidered before staff accounts exist (Phase 7),
or if the office grows past the point where "tell them" works.

---

## 2026-09-23 — `role_required` alone where a route needs a role

`@login_required` was removed from `/pull/estimate`, `/pull` and `/match`,
the three routes that also carry `@role_required("sender", "admin")`.
`require_role` already redirects to `/login` when there is no signed-in
user, so the second decorator did nothing. Routes with no role requirement
keep `@login_required`: on those it is the only check. The per-request
`load_current_user` hook sets `g.user` but never redirects.

Verified: signed out, all three return 302 → `/login`. An app-wide
`before_request` login check, which would make `@login_required` redundant
everywhere and close the forgotten-decorator gap the admin blueprint already
closes, was considered and deferred.

**Related:** *Three roles, enforced server-side first* (2026-09-22); *Admin
routes are a blueprint with one before_request check* (2026-09-22).

---

## 2026-09-23 — Phase 4's done condition verified

**Done when:** a viewer account can browse and export but cannot trigger a
pull. Checked through the real routes against the real `testview` account
(role `viewer`), with **CSRF protection left on and a valid token** taken
from a page the viewer can load.

The viewer session was set up in the Flask test client for `testview`'s row
rather than by a password login. The login form itself is covered by
parking-lot item 58.

- 403 on `POST /pull`, `POST /match`, `GET /pull/estimate` and `GET /admin/`.
- 200 on `GET /`, and on `GET /export.csv` with a `text/csv` body.

**The valid token is what makes the 403s mean something.** Without one, a
POST fails CSRF before it reaches the role check. The control run proved
it: the same POSTs with no token returned 400. A 403 on a tokenless request
would have tested nothing about roles.

Also checked in the same audit, every one through the app: every route
other than `/login` and `/logout` redirects a signed-out visitor to
`/login`; last-admin protection refuses demoting the only admin but allows
a same-transaction swap (rolled back); a session issued before
`sessions_invalidated_at` is cleared on its next request; and all 13 POST
forms across the templates carry a CSRF token.

---

## 2026-09-23 — Three Phase 2 outline items, settled in scoping and recorded late

Phase 2's outline listed "group results by city, county, or zip," "filter by
magnitude," and "confidence label with its inputs shown." All three were
settled while the storm browser was being scoped, 2026-09-14 to 09-16, as
consequences of other answers rather than as stated choices, so none was
written down at the time. Recorded now so the gaps between outline and build
read as decisions.

**County grouping: deferred, not built.** The browse was scoped to four
questions: which zips got hit, which cities, did this address get hit, and
which areas got hit regardless of size. County isn't among them, so
`GROUP_BYS` is `("zip", "city")`. Nothing is lost by waiting.
`county_boundaries` is loaded, and the county-by-polygon lookup is verified
cheap (2026-09-15), so adding a county grouping later is a new projection,
not new data. Trigger: someone needing an answer by county.

**Magnitude filter: satisfied in a different shape.** "Which areas got hit
regardless of size" is the storm list's "Actionable only" checkbox, turned
off. Turned on, it applies each type's own floor from
`report_types.min_magnitude` instead of one threshold the user types in.
That is the better rule, because a single number means different things
across types: 1.00″ is a hail size, 58 mph is a wind speed, and a threshold
entered against one is meaningless for the other. The floors themselves are
the 2026-09-03 decision.

**Confidence label: dissolved into columns, not rejected.** The 2026-09-01
format, "Moderate — 3 reports, up to 1.25″, 2 spotters," was never built as
one string. Two of its questions were settled in scoping: sources are shown
on the day list, and single-report days are shown like any other. The third,
whether the tier appears at all, was settled as no by the radar study
(*`confidence_tier` stays out of the UI*, 2026-09-16). What remained of the
label became separate table columns: report count, maximum magnitude and
source names. Separate columns let each figure be sorted and read on its own,
and they carry no tier word implying a distinction the data doesn't support.

**Related:** *County comes from TIGER county polygons* (2026-09-14);
*Territory browse is its own page, grouped by city-and-type* (2026-09-16);
*Initial `roof_relevant` set and magnitude floors* (2026-09-03);
*Confidence is a tiered label computed at query time* (2026-09-01).

---

## 2026-09-24 — Scheduled ingest runs a built artifact, not the working tree

The nightly `iem_ingest.timer` and weekly `iem_weekly_replay.timer` run
`docker compose run --rm ingest` without building first. The `ingest` image
bakes in `hailsys/` and `scripts/` at build time and mounts nothing from the
repo.

**Why:** an unattended job that picks up whatever is half-finished in the
working tree is worse than one that needs a deliberate rebuild. A known
artifact makes a failed night traceable to a specific build.

**Tradeoff:** ingest code changes, including changes anywhere under
`hailsys/` that ingest imports, do not take effect until someone runs
`docker compose build ingest`. `web` and `loader` bind-mount the repo and see
edits immediately; `app` is baked like `ingest`. See `docs/command-ref.md`,
*Which services see your edits*.

---

## 2026-09-24 — The activity feed shows who did what, to everyone

/activity and the storm-list feed join users to show first and last names
next to pulls and match runs, and viewers see them. That is deliberate.

The feed exists so people know what has already been worked -- "someone
pulled this yesterday" doesn't tell you who to ask, and the whole point is
to stop two people spending money on the same storm. Anonymizing it for
viewers would keep the surface tidy at the cost of the thing it's for.

RBI is an office of five who work together and already know each other's
names. Every account is created by an admin; there are no self-service or
external accounts. This is a shared work log, not a surveillance surface.

The permission matrix (2026-09-22) is unchanged by this: the feed is read-
only and behind login_required, and nothing in it exposes a user's
credentials, contact details, or anything beyond the fact that they did a
piece of work in this system.

Revisit if the account model changes -- external or customer-facing
accounts, or a headcount where people don't all know each other.

---

## 2026-09-24 — RentCast's billing boundary timezone is unknown; our dashboard is the check

Our billing period runs from settings.rentcast_billing_day at Denver
midnight. RentCast's own documentation never states which timezone its
period boundary uses -- it covers the reset, the no-carryover rule, the
overage fees and the 85%/100% notification emails, but not the boundary.
Searched 2026-09-24; not answerable from their docs.

Left as-is rather than guessed at. Denver midnight is 06:00 UTC under MDT
and 07:00 UTC under MST, so a UTC-based vendor boundary would put our period
start six or seven hours late relative to theirs, depending on DST. Only
calls made inside that window -- the evening before our billing day, Denver
time -- land on the wrong side of the line, and on a 1,000-request plan that
is unlikely to change a decision.

Two free checks rather than more investigation: compare what /admin reports
against RentCast's own API dashboard for the current period, and watch for
their automated 85% email -- its arrival date against our period start and
our count against 850 pins the boundary from both ends.

Worth naming the failure this avoids: a public project hit exactly this,
counting RentCast spend in UTC calendar months against a vendor billing
11th-to-11th. The drift made their counter read ~2,333 against a real
vendor total of 315, producing repeated false "quota exhausted" reports.
Our period is at least anchored to the right day; only the hour is in
question.

---

## 2026-09-24 — Unreadable RentCast responses fail loudly, with an attempt count

Parking-lot item 88. `_get` parsed the body with `json.loads(response.read())`
inside its `try`, but only `HTTPError` and `URLError` were caught. A truncated
body (`JSONDecodeError`), a short read (`IncompleteRead`) or a timeout during
the read escaped `_get`. They then escaped `search_sale_listings`' attempts
adjustment, escaped both of `run_pull`'s handlers, and landed in the
catch-all in `jobs.py`. By then there was no `api_call_log` row for the zip,
`_finish_pull` never ran, and the pull sat at `'running'` for good. Yet the
request had been made, and presumably billed.

**Fixed with `RentCastResponseError`**, a `RentCastError` that carries
`.attempts` like every other one. `run_pull`'s existing `RentCastError`
handler now catches it, so that zip fails on its own and the pull goes on.
It is not retried: whether a partial read means the data could be recovered
is unknowable, and the request has already been spent.

**The invariant this establishes: the client never raises anything without
an attempt count.** Cost bookkeeping depends on every failure saying how many
requests it made, which is the reasoning of the 2026-09-17 client entry
applied to one more failure shape.

**`IncompleteRead` inherits from `http.client.HTTPException`, not from
`OSError` or `ValueError`.** A first cut of the fix caught
`(JSONDecodeError, ValueError, OSError)` and missed it for exactly that
reason. It was caught by testing against a fake network layer, which is the
kind of check this needs, because a real short read is hard to provoke on
demand.

**Related:** *RentCast client: stdlib urllib, Active-only, daysOld
server-side* (2026-09-17); *Pull orchestration: continue past a bad zip,
abort past a bad key* (2026-09-17).

---

## 2026-09-24 — Every aborted zip's calls reach `api_call_log`

**Supersedes** one point of *RentCast quota: settings, warn and allow, usage
from `api_call_log`* (2026-09-23): that entry's "known gap", that calls from
a pull aborting on a bad API key never reach `api_call_log` and so are
missing from the usage figure. They now do.

`pull.py`'s new `_log_zip` writes the zip's row before `_finish_pull` on both
abort paths:

- **Bad-key abort:** it records `exc.attempts`. The rejected request itself
  probably isn't billed, but any pages that zip had already fetched were, and
  they used to vanish from usage. The first version also counted those
  attempts twice in `api_pulls.actual_api_calls`; that is fixed.
- **Unclassified catch-all** (anything the client didn't turn into a
  `RentCastError`): it records **1 call**. The true count is unknowable
  there, and overstating spend is the safe direction, as in the client entry.

**A JSON object where a list was expected now raises** `RentCastResponseError`
instead of returning an empty list. Returning `[]` logged the zip as a clean
200 with no listings, which reads the same as a zip that genuinely has none.
That is silent partial success, which CLAUDE.md forbids.

**What `api_call_log.http_status` records for these:**
- For a body that couldn't be read or parsed it is **NULL**: no usable answer
  was ever received, and NULL says exactly that.
- For the non-list case it is the **real status of the complete response**,
  normally 200, because there the status was real and only the body's shape
  was wrong.

Either way, the match page's coverage check (`http_status = 200` means
pulled) counts the NULL case as not pulled. The non-list case, with its real
200, still counts as pulled even though its listings were rejected. That's
rare enough to leave; recorded here so it isn't a surprise.

---

## 2026-09-24 — `testview` stays an active viewer account

The `testview` viewer account stays active rather than being deactivated
after Phase 4's done-when check. Being able to sign in as a viewer to check
any change to role gating is worth more than the risk, while the app is
reachable only over Tailscale by people already on the tailnet. **Revisit
before Phase 6**, when the app becomes reachable by staff and possibly
beyond the tailnet (parking-lot item 103). Deleting it was never an option:
`sql/002_users.sql` says users are never deleted, because audit columns
point at them. If it goes, it's deactivated.

---

## 2026-09-24 — Scripts keep the `tuning.py` radius constants; the app never reads them

Parking-lot item 62. The web app reads both radii from `settings` on every
request (2026-09-22). `DEFAULT_ZIP_RADIUS_MILES` and
`DEFAULT_MATCH_RADIUS_MILES` stay in `tuning.py` only as `--radius` defaults
for `scripts/`, and `tuning.py`'s comment now says so.

Why the scripts don't read `settings` too:
- `test_estimate.py` and `test_match.py` are manual test scripts.
- `export_storm_zips.py` is run by hand, with `--radius` available whenever
  the setting matters.
- `verify_zip_distances.py` *wants* a fixed radius. A verification run must
  not change behaviour because someone edited a setting that morning.

**What a user downloads always matches what they saw.** The CSV export the UI
offers is the `/export.csv` route, which reads `g.settings`. It is not
`export_storm_zips.py`. The cost is that a script run with its default can
disagree with the UI after an admin changes a radius, so pass `--radius` to
match.

This closes parking-lot item 62, which treated the CLI and the web app
disagreeing by default as a problem to fix. It's accepted instead, for the
reasons above.

---

## 2026-09-24 — The storm list pages at 50 storm days

Parking-lot item 86. `index()` asked for `limit=50` with nothing saying more
existed, so a wide date range silently dropped days. A 2024–2026 range never
reached 2024-05-30. **The 50-day limit stays, with page links**, rather than
raising the limit or adding a "showing 50 of N" notice. A notice would say
what's missing without offering a way to reach it. Each page shows "Page X
of Y — N storm days".

**OFFSET is safe here.** The days CTE groups by `storm_date` and orders by
`storm_date DESC`, so every row has a unique sort key and a page's contents
are the same on every request. OFFSET over a sort with ties can return rows
in a different order each time, repeating some rows across pages and
skipping others. That doesn't arise here.

A page is 50 storm *days*, not 50 table rows: one day with hail and wind
shows as two rows. `fetch_day_count` runs first, so a `?page=` past the end
**clamps to the last page** instead of showing an empty table. The page links
carry every active filter (the "filters must travel" rule, 2026-09-18), so
paging never changes the question being asked.

---

## 2026-09-24 — Web logging: shared logconfig module, level and logger in the line, rotated json-file

Parking-lot item 99. Nothing configured logging in the web app, so every
`logger.info` from the web app, the pull thread and the matcher was dropped at
Python's default WARNING threshold. A 12-zip pull and its match run left no
`info` lines at all.

**The config lives in `hailsys/logconfig.py`, and `create_app()` calls it
first.** `configure_logging()` is one `logging.basicConfig` to stdout at INFO.
Gunicorn runs without `--preload`, so every worker runs `create_app()` and
configures itself. `basicConfig` does nothing if the root logger already has
a handler. That works here, because gunicorn configures only its own
`gunicorn.*` loggers, and those don't propagate to root, so their lines aren't
doubled. Checked: calling `create_app()` twice still leaves one root handler.

**The line carries the level and the logger name:**
`level=INFO logger=hailsys.web.jobs event=pull_job_complete …`. Web's stdout
goes to Docker's `json-file` driver, not journald. Docker records the time,
but nothing records which logger wrote a line or at what level, so the format
carries both. The ingest scripts keep a bare `%(message)s`, because journald
already supplies the unit and the time. This **extends** *logfmt to stdout,
never to a file* (2026-09-08) and doesn't supersede it: still one event per
line, `key=value`, to stdout.

**Rotation:** `web`'s log is capped at 20 MB × 5 files, about 100 MB. **Log
options apply only when a container is created**, so a change takes
`docker compose up -d web`, which recreates the container. `restart` keeps the
old settings.

**Quoting:** a value that can contain spaces is formatted with `%r`, so
`report_text='TSTM WND GST'` stays one field. A datetime is formatted with
`.isoformat()`, because `%r` on a datetime prints its Python repr
(`datetime.datetime(…)`). Keys never contain spaces: `new_matches`, not the
old `new matches`.

**Verified 2026-09-24:** `match_complete` and `pull_job_complete` lines appear
in `docker compose logs web`, for example
`level=INFO logger=hailsys.matching.matcher event=match_complete emp_id=2
window_start=2026-08-26T00:00:00-06:00 report_text='TSTM WND GST'
radius_miles=5.0 new_matches=949`. `docker inspect` confirms the container's
log config: `json-file`, `max-size 20m`, `max-file 5`.

## 2026-09-24 — CSV exports: three projections, suppression optional, snapshot

Parking-lot item 92 (the matched-listings half; `/activity` is now item 107).
`28a0195`. The SQL lives in `hailsys/queries/exports.py`
(`MATCHES_SQL`, `REALTORS_SQL`, and a count for each); the routes and helpers
(`_dnc_from_args`, `_csv_response`, `_stamp`) are in `views.py`.

**Three exports:** the storm on the match page (`/storms/matches.csv`, linked
from `matches.html`), a bulk export over a date range (`/exports/matches.csv`)
and the realtor list (`/exports/realtors.csv`). `/exports` is the page for the
last two, linked from the nav.

**The bulk grain is one row per listing per local storm day and type.** The
match page's own query groups by listing alone, which is right for a page
scoped to one day. Over a range it would collapse a house matched on two
storms into one row that can't say which storm. `LOCAL_TIME_EXPR` is
**imported from `storms.py`, not copied**, so a download's `storm_date` is the
same local day the browser shows, and a change to the DST handling can't drift
between the two.

**Actionability is not re-applied at export time.** `matcher.py` already
applies `roof_relevant` and the magnitude floor when it creates a match
(`_MATCH_SQL`), so a row in `storm_listing_matches` has passed both.
Re-filtering on `actionable_only` would drop legitimately matched rows if
`min_magnitude` were later raised. This **supersedes the assumption** that the
export should mirror the storm browser's `actionable_only` filter.

**Suppression: excluded by default, included with flags on request**
(`?dnc=include`, the Suppressed dropdown on `/exports`).
- It is keyed on the **agent's** address. The office flag is reported as
  `office_dnc` but never filters, pending item 19 (whether outreach ever falls
  back to an office address).
- The joins put `removed_at IS NULL` **in the `ON` clause**. In `WHERE` it would
  turn the `LEFT JOIN` into an inner join and silently drop every agent who
  isn't suppressed.
- `hail_app` already had `SELECT` on `dnc_list` (`sql/010_roles.sql`), so no
  migration was needed. The exports read it with a plain `LEFT JOIN`; there is
  still no sending code.

**A CSV is a snapshot.** Anyone suppressed after a download is still in the
file, and a list worked outside this system never passes the send-time check.
So every filename carries the export date (`_stamp()`, Denver) and `/exports`
says so. **The send-time check against `dnc_list`, in the send's transaction,
remains the only real protection**; excluding suppressed agents from a
download is a convenience, and it is not that check.

**Who can download what:** the realtor export is `role_required("sender",
"admin")`. Every agent's email and phone in one file is the most sensitive
download here, and marketing works outside this system. The match exports stay
`login_required`, viewers included, matching Phase 4's done-when (a viewer can
browse and export).

**The realtor list is not deduplicated.** `realtors` holds one row per address
by design, and DNC is keyed on the address too, so an agent reachable at two
addresses appears twice. `/exports` says this beside the count.

**Known cost:** the count on `/exports` runs the full projection, and the file
is built in memory (parking-lot item 49). The range was uncapped when this was
written; it is capped at 400 days as of 2026-09-25 (see below).

## 2026-09-24 — Match page and activity panel: aligned columns, condensed, two columns

`d1b08c5` (match page), `110bd8a` (activity panel).

**Aligned columns.** The match page draws one table per agent, so the browser
sized each table to its own content, and a long address pushed that group's
columns out of line with every other group. **Measured:** the Zip column
started at 650, 731 and 674px in three adjacent groups.
`table-layout: fixed` plus an explicit width per column makes every group
agree, and it stops the browser measuring content on a 656-row page.

**Trade-off:** fixed layout wraps or clips content wider than its column, so
Address is sized for a realistic worst case (22rem, where the longest address,
66 characters, wraps to two lines). `td.addr`'s `min-width` no longer applies
on this page. The widths are `nth-child` rules, positional, so they must be
kept in step with the columns in `matches.html` (parking-lot item 108). The
nine columns total 71rem, so on a phone each group scrolls sideways
(item 109).

**Condensed.** Padding and margins are tighter: the gap between an agent's last
listing and the next agent's name was doing more separating than it needed.

**Activity panel.** Pulls and match runs now sit **side by side in a grid**
and collapse with `<details>`/`<summary>`, so the storm table, which is the
page's actual subject, starts higher up. It is the browser's own disclosure
widget: no JavaScript, and keyboard-accessible. Each line carries a
Denver-time stamp. **New storm days stays full width above them.** The feed's
since-last-login window is unchanged. Under 48rem the two columns collapse to
one.

## 2026-09-24 — CSS responsiveness pass

`6ae56af` and `2060328`, committed on the `responsive-css` branch on
2026-09-25 UTC. `style.css` and templates only: no Python, no JavaScript, no
framework, and no page's information or columns changed.

**The finding:** `h1 ~ table { margin: 0 1.5rem }` on a `width: 100%` table
ended the table **1.5rem past the viewport at every width**, on every page with
a bare table (storm days, the territory zip view), because CSS ignores the
over-constrained `margin-right`. A desktop bug found by a mobile audit. Fixed
with `width: calc(100% - 3rem)`; both tables are now wrapped in
`.table-scroll`, and the corrected rule is kept as a guard (item 113).

**What changed:**
- **Territory:** `.split-table` scrolls sideways in its own box, and the split
  stacks at `48rem`.
- **Storm-days and territory zip tables** are in `.table-scroll`, and so is the
  admin settings history table.
- **Admin settings** stack (label above value) at `48rem`.
- **Filter bars:** each label and its control is wrapped in
  `<span class="field">`, so a label can't end one line with its input on the
  next. Storm days, territory and exports.
- **`40rem`, not `640px`:** the two existing `640px` queries (header, pagination)
  are now `40rem`, the same value at the default font size, so a user's
  font-size setting moves the breakpoint too. That is one scale with the
  existing `48rem` `.feed-columns` query.
- **A right-edge shadow on `.table-scroll`** (CSS only) as a scroll hint. It
  is not a fade, and it doesn't show behind a table's header row (item 113).
- **Gutters:** `.flashes` and `.activity-panel` get the side margin, `/activity`
  is wrapped in `.page`, and `.note` no longer zeroes its side margins with a
  shorthand that beat the `h1 ~ p` gutter.
- **Phones (`40rem` and under):** form controls are 1rem, since iOS Safari
  zooms the page when a focused control's font is under 16px; the storm-days
  Status cell's actions (`.pull-link`, the Match button, `.action-disabled`)
  are about 44px tall.
- `body.login-page` uses `100dvh` with a `100vh` fallback.

The viewport `<meta>` was already in `base.html`.

**Decided against:** stacked cards for the match page on a phone (item 109).
That page is desktop scan-and-compare work, and the scroll shadow is enough to
make it reachable.

**Verification, plainly:** no browser was available in the agent's
environment, so every claim above was **reasoned from the CSS**, not observed.
The one runtime check was that the templates still parse, and it stopped
short: the templates wouldn't compile outside the app because the app's own
`magnitude` filter wasn't registered, so a real page load is what confirms
them. The developer reported checking the result in a browser afterwards, but
which pages, widths and devices, and what was seen, were not reported into this
record. **No specific check is recorded as passed.** Item 114 lists what
remains unconfirmed, including iOS focus-zoom and `100dvh`, which only show on
a real phone. Estimated sizes (tap targets, breakpoints) came from the CSS
and were not measured.

## 2026-09-25 — Explicit date ranges are capped at 400 days

Parking-lot item 49. `_window_from_args` in `views.py` validated only the
`days` shortcut against `DAY_RANGES`; an explicit `start`/`end` came straight
from the query string. That never mattered for the storm list, but the bulk
match export (`/exports/matches.csv`, 2026-09-24) made an unbounded range
expensive, and the date pickers can ask for any span as easily as a hand-typed
URL can.

**`MAX_RANGE_DAYS = 400`.** It covers the 365-day claim window with room to
spare, so it can't block legitimate work. A wider range keeps its end date and
moves its start to 400 days earlier.

**The clamp runs last, after the fallback and the swap.** A first version sat
right after the dates were parsed, where a missing or unparseable date is
still `None`, so `end_day - start_day` raised `TypeError` on any page loaded
without both dates (the default landing page among them). A reversed pair
also slipped past it, since its width was negative until the later swap.
Caught in review before it was committed. Checked in the web container across
eight cases (no arguments, `?days=90`, a bad date, only one date, a normal
range, exactly 400 days, 2000 to today, and the same reversed): the four
default and bad-input cases give the 30-day default, and both wide ranges come
out at exactly 400 days.

**Clamped, not rejected.** A 400 would break a bookmarked URL. The user is told
instead: the storm list, territory and `/exports` flash "Range limited to 400
days." **The flash is raised by those three routes, not by the helper.**
`_window_from_args` also serves `/map/points.geojson`, `/storms/zips`,
`/territory/days` and the CSV downloads, and a flash from one of those would
sit in the session and appear on whichever page was loaded next (the map's
background fetch would have queued a duplicate of the page's own message). The
helper only sets `g.range_clamped`. A clamped CSV download shows its dates in
the filename.

**The remaining cost was measured and accepted, not fixed.** The count beside
Apply on `/exports` runs the whole projection, any signed-in account can ask
for it, and the file is built in memory. Timed on 2026-09-25 (read-only, dev
VM, single user): 30, 90 and 400 days returned 4,765, 5,671 and 5,671 rows,
counted in 0.2 to 0.3s and fetched in 0.35 to 0.44s, a 1.4 MB file and about
12 MB of Python memory at most. That is a small dataset (11,575 matches in all,
all recent), so 400 days was no bigger than 90 and this is **not a stress
test**. Considered and deliberately not built: a hard row ceiling (about
50,000) that refuses with a message, and a streamed response. **Item 49 is
closed on that basis and reopens** if a count or fetch takes over about 2
seconds or a range returns over about 10,000 rows. Rate limiting is left to the
Cloudflare tunnel work (item 110).

## 2026-09-25 — Stale pulls are swept at startup and marked cancelled

Parking-lot item 47. `54cc7f2`, with the age rule below in the commit after.

**The problem.** A pull runs in a `daemon=True` thread, which dies with its
process. A restart mid-pull left `api_pulls` at `'running'` forever, and
`workstate.py` read that as pulled, so the Pull link stayed hidden and the
storm looked done when it wasn't.

**The fix.** `sweep_stale_pulls()` in `jobs.py`, called from `create_app()`
right after `configure_logging()`, sets `api_status = 'cancelled'` and
`finished_at = now()` on any pull still `running` and started over 10 minutes
ago, and logs `event=stale_pull_swept` at WARNING for each.

**Startup is the signal, not an age threshold alone:** nothing a process
started is still running when it starts.

**The 10-minute floor exists because gunicorn runs 2 workers**
(`docker-compose.yml`, `--workers 2`) and can replace one crashed worker while
the other's pull is genuinely in flight. Both call `create_app()`, so a bare
"every `running` row" sweep would kill the live pull. Pulls have taken seconds,
so real work doesn't reach the floor.

**`'cancelled'`, not `'failed'`:** the pull didn't fail, its process went away.
The value was already in the `api_pulls` CHECK constraint (`sql/008`) and
nothing used it, so no migration was needed. `workstate.py` excludes both, so
the Pull link returns either way, but pull history can tell a RentCast error
from a lost process.

**`workstate.py`'s `pulled` CTE had to change too.** It excluded only
`'failed'`, so a swept row would still have read as pulled: the exact bug the
sweep exists to fix. It now excludes `('failed', 'cancelled')`.

**The sweep's own failure is caught and logged** (`event=sweep_failed`), and
doesn't stop the app starting. `api_call_log` still records what was actually
spent; the sweep only closes the bookkeeping row. A swept pull reads "Not
pulled" again, so a re-pull spends again.

**A pull orphaned early is covered by an age rule, added the same day.** The
floor means a pull orphaned in its first 10 minutes isn't swept at that
restart. `workstate.py` now counts a `running` pull as "Pulling..." only while
`running_since` is under `PULL_STALE_AFTER` (10 minutes); past that the storm
reads "Pulled, not matched" again, with Match and re-pull offered, without
waiting for a sweep. `fetch_work_state` takes a timezone-aware `now`, passed in
like `today`, and both callers in `views.py` pass it. **The sweep takes its
age from the same constant** as a query parameter, so the label and the sweep
can't disagree about when a running pull is dead.

**Verified 2026-09-25, as reported by the developer:** with a backdated
`'running'` row, a restart logged `event=stale_pull_swept` and the row read
`'cancelled'` with `finished_at` set; the test row was deleted afterwards. For
the age rule: the label logic was checked with synthetic rows (1 minute and
9m59s old read "Pulling...", exactly 10 minutes, 3 days and a missing start
time read "Pulled, not matched", and Matched still outranks running), the real
query returns states, and the sweep's age parameter binds, checked with a
read-only `SELECT`.

**Known gaps, not fixed:** `create_app()` runs the sweep, so anything that
builds the app (tests, a script) runs an `UPDATE`, and so does each worker;
`finished_at` is the sweep time, not when the pull died.

**Match runs are swept too (`6d6ba63`).** `sweep_stale_pulls()` also sets
`match_runs` rows still `running` after `PULL_STALE_AFTER` to `'failed'`, with
`finished_at` and an `error_detail`, and logs `event=stale_match_run_swept`.
`match_runs` has no `'cancelled'` status, and its CHECK requires `finished_at`
unless a run is running. It matters because `workstate.py` counts a running
match like a running pull, so an orphaned one would otherwise hold its storm on
"Pulling...".

**A dependency worth noting:** this was diagnosable because item 99 (logging)
landed first. Until then the pull thread's `info` lines were dropped.

## 2026-09-25 — A running pull reads "Pulling..." and the storm list polls it

Parking-lot item 57, built with item 47: once a running pull has its own
label, an orphan would read "Pulling..." forever instead of "Pulled, not
matched", which is why the age rule above exists. `54cc7f2`.

**The label.** `workstate.py` selects `running` as its own activity kind, kept
separate from `pulled` and not excluded from it, so a pull that never finishes
still can't read "Not pulled". `_label` checks Sent, Matched and Matched-none
first, then `running`, then `pulled`. **Consequence:** a re-pull of a storm
that is already matched keeps its old label and never reads "Pulling...", so it
isn't polled either. *(Changed later the same day: `running` now comes first.
See "Pull and Match return to the filtered list; a running pull or match
outranks the other labels".)*

**One template for the cell.** The storm-days Status cell is now
`_status_cell.html`, included by `storms.html` for every row and rendered by
`/storms/state` for the polling, so how the cell looks and what it offers is
decided in one place and not rebuilt in JavaScript. While a pull runs it
carries `data-poll="1"` and the storm's date, type and actionable flag. The
browser never decides that a pull has finished: the reply is the same cell,
and when it has no `data-poll`, polling for that row stops.

**Polling (`storms.js`):** every 3 seconds, at most 40 times per row (about 2
minutes, then it stops and a refresh is needed). The count lives in a JS `Map`,
because replacing the cell discards its attributes and a counter kept there
would restart every time. One request per row at a time; nothing is sent while
the tab is hidden; a failed poll is logged with `console.warn` and retried;
a redirect (signed out) stops polling for the row and isn't inserted into the
table. `submitted=1` is always sent, so an unticked "Actionable Only" isn't
read as the default.

**"Pull again" isn't offered while a pull runs**, since a second click would
start a second paid pull of the same zips. The server doesn't refuse one
either (item 51). The badge is `.badge-pulling`, purple, a hue no other state
uses.

**Verification, plainly.** The Python compiles and the app builds. The cell
template was rendered for six states through the app's own Jinja setup:
"Pulling..." carried `data-poll` with the right values and no action links, and
the other states kept theirs. **Not done:** the JavaScript has not been run (no
`node` on `hail-dev`). **Later the same day the developer reported, from the
browser, that the Status cell's "Pulling..." display works.** That is the only
check of the polling, and what exactly was watched wasn't recorded.

## 2026-09-25 — Pull and Match return to the filtered list; a running pull or match outranks the other labels

`6d6ba63`. The "Pulling..." row wasn't being seen. The first theory, that a
small pull finishes before the page renders, was wrong on the numbers: real
pulls take 1 to 15 seconds (13 zips took 8.8 s), and the storm list renders in
60 to 90 ms, with the work state read about 50 ms in. Two other things were
hiding it.

**The redirect dropped the filters.** `pull_start` and `match_start`
redirected to a bare `main.index`, so after clicking you landed on the default
30 days, and the storms being pulled (Aug 14 to 22 that day) were outside it.
`_list_url()` in `views.py` accepts only `/` with its query string, from the
same host, so a crafted value can't send anyone to another page or site. The
estimate page carries it as a hidden `back` field, taken from `?back=` or else
the `Referer` (the list when the estimate is opened from it, but the estimate
page itself after the mismatch redirect, hence `?back=`). `pull_start`
redirects to it and falls back to the default list. The zip-count mismatch
redirect now keeps `submitted`, `actionable` and `back`, which it used to drop.
`match_start` posts from the list itself and uses the `Referer` directly, and
Cancel on the estimate page returns to the filtered list too.

**`running` outranks the other labels.** `_label` checked Sent and Matched
first, so a re-pull of an already matched storm read "Matched, not sent"
throughout, and 14 of 20 rows in a wide window were matched. `running` is now
checked first. A running `match_runs` row counts the same way as a running pull
(same kind, same 10-minute age limit), so the cell stays on "Pulling..." through
the pull and its automatic match, and doesn't drop to "Pulled, not matched" and
stop polling in between. The startup sweep closes stale match runs too
(decision log, "Stale pulls are swept at startup and marked cancelled").

**Verified** in rolled-back transactions against the real database: `_list_url`
against valid, foreign-host, `//host`, other-path and `javascript:` inputs; the
estimate page's `back` field from the `Referer`, from `?back=`, and from a
hostile `Referer`; the mismatch redirect, with and without a hostile `back`; a
matched storm with a running pull, with a running match run, and with a
3-day-old running match run (the last falls back to "Matched, not sent"); and
the match-run sweep with one or several stale pulls and match runs mixed. **The
developer confirmed in the browser that the date range now stays.**

**Known gap:** a few milliseconds separate a pull finishing from its match run
being inserted, and a poll landing exactly there would stop early.

## 2026-09-25 — A live banner under the heading replaces the "Pull started" flash

`ea8762d`. What the developer had been trying to fix all along was not the
Status cell but the line right under "Recent storm days": "Pull started for …
(n zips)" was a flash message, rendered once, and it sat there after the pull
finished.

**The banner is read from the database.** `pull_start` puts `{date, type,
zips, since}` in the session as `pull_watch`, and the storm list pops it once
(so a reload clears it, as it cleared the flash) and renders
`_pull_banner.html` from `workstate.fetch_pull_banner()`, which reads the
latest `api_pulls` and `match_runs` rows for that storm since the click. The
marker means it says "Pulling 2026-08-13 HAIL (22 zips)…" from the first
paint, even before the pull's own row exists. While the pull or match runs it
carries `data-poll="1"`, and `storms.js` refreshes it every 3 seconds (at most
200 times) from `/storms/banner`, which returns the same fragment, until the
reply has no `data-poll`.

**Phases:** pulling, matching (pull finished, match running), done ("Pull
finished for …: 22 zips, 3,512 listings, 6,024 new matches."), match_failed
(with a pointer to the row's Match button), failed (failed or swept), and lost.
"Lost" means no row appeared within `BANNER_GRACE` (15 s) or a running row is
older than `PULL_STALE_AFTER`. A `since` five seconds early absorbs the gap
between the web process's clock and the database's. The banner reuses
`.flashes`, so it looks as the flash did.

**Choices:** it shows only the pull the user just started (other users' pulls
are in the Status cells and the Activity page); the finished message stays
until the next reload; the mismatch and Match flashes stay flashes. (Parking-lot
item 118.)

**Verified:** the phase logic against synthetic `api_pulls` and `match_runs`
rows (13 cases), the template for every phase, and the route (200, and 400 for
a bad date, a bad zip count, a missing or a timezone-less `since`; a redirect
when signed out). Then the whole flow was replayed with a fake 1.2-second
pull, first on copies of the files with my edits applied and then on the
developer's files: landing at about 55 ms showed "Pulling…" with `data-poll`
and no old flash, a poll at +0.7 s still said "Pulling…", and one at +2 s said
"Pull finished … 6,024 new matches." with no `data-poll`. A reload cleared it.
**Not done:** the JavaScript has not been run (no `node`), and no browser check
of the banner was recorded at the time.

**Confirmed working end to end, 2026-09-27.** The developer watched a real
pull with the banner up: it read "Pulling…", followed the pull through to the
finished message, with no refresh. This was the display they had been trying
to fix from the start of this work — a separate thing from the Status cell's
own "Pulling..." display (parking-lot item 118 confirmed; see also the
"Pulling..." entry above, confirmed 2026-09-25).
of the banner is recorded.

## 2026-09-25 — RentCast values a column can't hold are stored as NULL and logged

`5933ebc`. Two pulls failed with `NumericValueOutOfRange`: numeric field
overflow, precision 3, scale 1. RentCast had returned **615 Remington St, Fort
Collins, 80524 with `bathrooms` 150**, and later **5331 S Delaware St,
Littleton, 80120 with 912**, and `properties.bathrooms` is `NUMERIC(3,1)`. One
bad listing rolled back the whole zip and ended the pull after its calls were
spent, and a re-pull failed identically. Pulls 41 and 78 (2026-08-14 HAIL, 4
calls each) and 79 (2026-07-06 NON-TSTM WND GST, 13 calls) spent 21 calls for
nothing. The banner reported both failures correctly; this was a data problem.

**The fix:** `_fits()` in `upsert.py` checks `bathrooms` (`NUMERIC(3,1)`),
`bedrooms` and `yearBuilt` (`SMALLINT`) against what the column can hold, and
returns `None` with an `event=field_out_of_range` warning (id, field, value)
when the value doesn't fit or isn't a finite number. `isfinite` comes first, per
the `Decimal('NaN')` trap in CLAUDE.md, since `float()` accepts `"nan"` and
`"inf"`. Numeric strings such as `"2.5"` still pass, as they did before. The
listing is kept and `raw_payload` holds the original value. No migration.

**Chosen over the alternatives:** widening the column would have stored
nonsense as if it were real and needed the admin role; a savepoint per listing
would let a pull succeed with rows missing, so it would have to count and report
them, and wasn't built. **The limits are what the column can hold, not what is
plausible.** (Parking-lot item 116.)

**Verified:** against fake listings in a rolled-back transaction, the old code
reproduced the error with 250 bathrooms, and the new code stored `NULL` with a
warning, kept 99.9, nulled 100 and -1, and handled `bedrooms` and `yearBuilt`
of 99999, `'nan'`, `'abc'`, a missing value, and `'2.5'`. A first keyed version
had a typo that made every listing raise `KeyError`; it was caught in review
before any pull ran. Then real re-pulls: 2026-07-06 NON-TSTM WND GST (17 zips,
3,040 listings, 930 new matches) and 2026-08-14 HAIL (26 zips, 3,323 listings)
completed, logging one warning each for the two listings above.

**Found on the way:** `api_pulls.storm_link_paired` means a pull with a storm
date must have a report type, so a `POST /pull` without one can't be recorded
(item 117).

## 2026-09-28 — RBI's domain, its live mail setup, and the sending-subdomain plan

Parking-lot item 96. WHOIS plus public DNS lookups (`dig`), confirmed against
`roofbrokersinc.com` directly — no access to RBI's DNS was needed for any of
this, it's all public record.

**The domain.** `roofbrokersinc.com`, registered 2006, registrar GoDaddy.com,
LLC, registrant privacy-shielded via Domains By Proxy. **DNS is hosted
directly at GoDaddy**, not a separate provider: the nameservers
(`ns1-4.domaincontrol.com`) are GoDaddy's own defaults. The "client transfer/
update/delete prohibited" WHOIS statuses are anti-hijacking locks on domain
*transfer* only — they don't block DNS record edits.

**RBI's live business email is Microsoft 365 / Exchange Online.** MX is
`roofbrokersinc-com.mail.protection.outlook.com`; SPF is `v=spf1
include:spf.protection.outlook.com -all` — a hard fail for anything not
Microsoft's servers. **This is the load-bearing fact for everything else
here: nothing about this project may ever touch the root domain's MX or
SPF.** A mistake there breaks RBI's actual company email, not just this
system's outreach.

**A DMARC record already exists at the root, and it's permissive.**
`_dmarc.roofbrokersinc.com` → `v=DMARC1; p=none;` — monitor-only, no `sp=`
subdomain override, no `rua=` reporting address. Default alignment is
therefore relaxed, so it won't block a new sending subdomain, and RBI
currently gets zero visibility into anyone spoofing their domain (worth
offering a `rua=` address later, not required).

**Direct, current evidence of the prior Mailchimp use the docs already
flagged as an open question.** `k2._domainkey.roofbrokersinc.com` still
resolves to a live Mailchimp DKIM key (`dkim2.mcsv.net`), full public key
still published. This confirms Mailchimp was configured to send **from the
root domain itself**, and the record was never cleaned up after — matching
`hail-consolidated.md`'s "a contractor-built predecessor used Mailchimp and
led to blacklisting." It doesn't confirm damage occurred, but it turns a
vague worry into a specific, informed question for RBI's contact. (`k1`,
`google._domainkey`, `selector1/2._domainkey` are all empty — no other ESP's
DKIM found.) One other TXT record on the root, an unidentified verification
token (`7gv5oc0n8qoj2q79a5k29kacsu`), couldn't be attributed to anything and
is also worth asking about directly.

**Candidate subdomain names, checked, not guessed:** `send.roofbrokersinc.com`
has no `A`, `CNAME`, or `TXT` record at all — free. `mail.roofbrokersinc.com`
already has a live `A` record (`64.29.145.40`) — something's there; don't
propose that name.

**The one-shot ask, narrowed to something concrete.** Rather than asking
RBI's DNS admin for individual SPF/DKIM/DMARC records piecemeal (which would
mean going back every time the provider changes or a record needs updating),
DNS supports delegating just one subdomain's zone to a different nameserver
without moving the rest of the domain. The plan: sign up for a free
Cloudflare account, add `send.roofbrokersinc.com` **itself** as its own
Cloudflare zone (not the root domain), and Cloudflare hands back two
nameservers. **The entire ask becomes: "please add these two NS records for
`send.roofbrokersinc.com`."** Nothing else, ever, needs to go back to RBI —
every future record (SPF, DKIM for whatever provider is chosen, a DMARC
override for the subdomain, MX for receiving) becomes self-service in that
Cloudflare account. This also confirmed Cloudflare's actual role here: DNS
hosting and Email Routing (receiving/forwarding), **not** an outbound sending
platform — that's a separate, still-open decision (see below).

**`justyn@roofbrokersinc.com` does not exist as a mailbox.** The person who
controls GoDaddy is believed to be the same person who'd handle a Microsoft
365 mailbox request, i.e. one admin covers both — worth confirming, not
assuming. Since Outlook/M365 involvement isn't wanted, the plan is to send
and receive on the subdomain entirely: use `justyn@send.roofbrokersinc.com`
as both the From and Reply-To address, with Cloudflare Email Routing (free,
part of the same delegation) forwarding it straight to a personal Gmail
account. This needs no Microsoft 365 mailbox, no Outlook, and no change to
the root domain's mail flow at all. Trade-off, not yet decided: the visible
sender reads `@send.roofbrokersinc.com` rather than the plain company
domain — normal for this category of email, and arguably better reputation
isolation, but a judgment call on how it reads to recipients.

## 2026-09-28 — Mainstream email-sending providers all prohibit this use case; a different category might not

Parking-lot item 96 / the "Email provider" row, `hail-consolidated.md`
External sources. Fetched the actual current Acceptable Use Policy or Terms
of Service from each provider directly — not blog summaries, which
disagreed with each other and with the primary sources on details.

**Every mainstream transactional/API provider checked prohibits sending to
someone who hasn't opted in, with no exception for B2B or professional
contact:**

| Provider | What its own policy says |
|---|---|
| SendGrid | Prohibits "sending unsolicited or unwanted emails in bulk"; bans emailing addresses "obtained from the internet or social media... without obtaining prior affirmative consent"; bans purchased/rented lists |
| Postmark | "All email lists... must be permission-based subscriptions"; purchased/rented lists prohibited; unsolicited email "will receive abuse complaints... reflected on your account" |
| Mailgun | Requires "confirmed single or double opt-in"; "acquiring or sending to a third-party mailing list is prohibited" |
| Resend | "Prohibited from sending unsolicited messages of any kind, including cold outreach"; complaint rate must stay under 0.08%, bounce under 4% |
| Amazon SES | Prohibits "unsolicited mass email... or solicitations (spam)"; enforcement is complaint/bounce-rate based rather than an explicit upfront opt-in check, but the same prohibition applies |

Read plainly: a listing agent who never asked to hear from anyone is exactly
what all five prohibit, regardless of it being professional, individualized,
or about information the agent themselves made public (their own listing).
**This is the same wall the prior Mailchimp attempt hit, just under a
different name** — not a case of "some are stricter than others."

**A different category exists for exactly this gap, with a real caveat.**
Cold-outreach/sales-engagement platforms (Instantly, Smartlead, lemlist,
Apollo) are built around CAN-SPAM's opt-out model rather than requiring prior
consent. Checked Instantly's actual sending policy: it requires an
unsubscribe link, honored promptly, and non-deceptive headers — **no prior
opt-in requirement**, a genuine structural difference from the five above.
**Not yet confirmed:** whether these platforms offer a plain API to send one
individualized email per call from this project's own backend, or whether
everything routes through their own campaign-builder UI and a pool of
rotated, individually warmed-up mailboxes (their marketing language —
"unlimited sending accounts," "email warmup" — points toward the latter,
which would be a materially heavier architecture than the single-subdomain,
single-API plan sketched so far). This needs a direct answer from their
support before it's a real candidate, not just a policy read.

**Recommended next steps, unresolved:** rule out SendGrid/Postmark/Mailgun/
Resend/SES for this use case as currently policied (this isn't a "convince
support to bend the rule" situation — the rule is the point); investigate the
cold-outreach category specifically on the API-architecture question; and
before building against whichever is chosen, get the exact use case
("emailing real-estate listing agents about specific storm-damaged
properties they have listed, not a purchased list, not a newsletter") in
writing from that provider's own compliance team.

## 2026-09-28 — Is this a "commercial" email under CAN-SPAM? Yes, and here's what that requires

Prompted by drafting an actual line of outreach copy: *"It looks like hail
hit within 5 miles of this listed property, it might be worth it to have a
licensed roofer (Roof Brokers) come look to see if there's any damage. Our
inspections are free, and we'll tell you what kind of shape the roof is
in."* Checked against the primary sources — `15 U.S.C. § 7702` and its
implementing regulation `16 CFR § 316.3` — not a summary.

**The statutory test (§ 7702(2)(A)):** a "commercial electronic mail message"
is one "the primary purpose of which is the commercial advertisement or
promotion of a commercial product or service." The only exemption
(§ 7702(17)(A)) is a "transactional or relationship message" — five
categories: facilitating a transaction the recipient already agreed to;
warranty/recall/safety notices; a change to an existing subscription/
account; employment/benefit information; or delivering something the
recipient is already owed. **CAN-SPAM draws no distinction for B2B or
professional recipients** — only for what the message is *for*.

**The mixed-content test (16 CFR § 316.3):** a message combining commercial
and non-transactional content is deemed commercial if a recipient would
reasonably conclude, from the subject line or from how the content is
placed and proportioned, that its primary purpose is commercial promotion.

**"Free" doesn't exempt it — if anything it's the textbook case.** Nothing
in either the statute or the regulation ties "commercial" to a price tag in
the message; "free inspection," "free estimate," "free consultation" are
themselves forms of advertising, which is exactly why the FTC has a separate
rule specifically governing "free" claims in advertising (16 CFR § 251.1) —
that rule wouldn't need to exist if "free" took a message outside commercial
speech.

**Applied to the draft:** it doesn't fit any of the five transactional/
relationship categories at all (no prior transaction, no existing account,
nothing owed), so there's no exemption route to check in the first place.
Roughly half the message is a direct recommendation of RBI's own named
service ("have a licensed roofer (Roof Brokers)... our inspections are
free"). Under the mixed-content test this reads as commercial — not a close
call.

**What that requires, in practice — and this is the useful part, not bad
news.** CAN-SPAM is an **opt-out** law, not opt-in: being "commercial" under
it doesn't make cold email illegal, it just triggers four requirements —
non-deceptive headers and subject line; a clear notice it's an ad if that
isn't obvious from context; a valid physical postal address for RBI in the
message; and a working, conspicuous opt-out mechanism, honored within 10
business days. None of this exists in the schema or template plan yet
(`email_templates` is still empty; item 16's merge-field vocabulary is still
undesigned) — this is now a concrete requirement for that design, not an
abstract legal note.

**The distinction worth keeping straight:** the *law* only requires opt-out.
It's the individual providers (SendGrid, Postmark, Mailgun, Resend) that
impose a stricter opt-in requirement, voluntarily, through their own
policies, to protect their own infrastructure's reputation. Legal is not the
same question as usable-with-a-given-provider — both entries above stand
independently.

## 2026-09-28 — How much HAIL-storm listing/agent overlap exists, and what consolidating sends would save

Prompted by the volume finding above. Queried `storm_listing_matches`, HAIL
only, 2026-08-13 through 2026-09-22 (the real range in the database at the
time):

| | |
|---|---|
| Total match rows (one per listing per HAIL storm day it was near) | 13,974 |
| Distinct listings across the whole window | 5,086 |
| Listings that recurred across more than one HAIL storm day | 1,594 |
| Extra rows from that recurrence | 8,888 |
| Most HAIL storm days any single listing was near | 4 |
| Sum of per-storm agent counts (what per-storm sending totals) | 5,628 |
| Distinct agents if consolidated into one send for the period | 3,259 |
| Agents who'd get more than one email if sent per-storm instead | 2,369 |

**This is not duplicate or bad data.** All matches used the same 5-mile
radius; about 31% of listings genuinely fell within range of more than one
distinct hail report in this six-week stretch, which was an active one for
the Front Range. Consolidating sends across a period rather than sending once
per storm would cut the required email count by about 42% for this window —
real, but nowhere near enough on its own to make a personal account viable
(see the entry below).

**Not built.** The matcher deliberately writes one row per listing per storm
(`storm_listing_matches`), so the page and exports can say which report a
listing matched — this is item 106's still-open point, "no row says which
report it matched." Nothing consolidates recipients across storms before
sending; `send_log` and the frequency-cap items (15, 19) are schema and open
questions only.

**A constraint on any consolidation design, not just an implementation
detail.** `CLAUDE.md`'s rule that the message claims a *report*, never
damage, means a consolidated send has to still name which specific storm
day(s) it's referring to. Folding four separate hail events into "storms
have hit your area lately" blurs the report-backed claim that makes the
message defensible in the first place.

## 2026-09-28 — Throttled sending from a personal account: considered and rejected

Raised as an alternative to a dedicated provider, given how much harder that
search turned out to be (items 96, 119). Rejected, for reasons worth keeping
on record so it isn't re-proposed without re-deriving them.

**The published daily caps (500/day personal Gmail, ~300/day personal
Outlook.com) are not the flagging threshold — they're where the server
outright refuses more mail.** Spam/abuse detection runs on behavioral
signals — an account suddenly emailing strangers with no prior
correspondence, at a steady cadence, with commercial-reading content and a
link — and that pattern is caught well under any published cap. Providers
deliberately don't publish a "safe" threshold, because a fixed number is
exactly what a sender trying to evade detection would throttle to; the real
system is adaptive.

**A personal account gives no visibility into whether it's working.** No
bounce webhook, no complaint feedback, no Postmaster Tools — those require a
verified sending domain. The only signal on a personal account is the
account itself being restricted, a catastrophic and lagging indicator, and
it's the developer's own daily-use account at risk, not a disposable
identity.

**The backlog math fails even absent any detection risk.** 3,259 distinct
agents for the six weeks already in the database (previous entry), with new
storms arriving roughly weekly. At a conservative 40/day, clearing just that
already-past window takes about 82 days, during which several more storms'
worth of agents queue up behind it. The backlog only grows, and the
project's 365-day claim window means a growing backlog actively burns real
claim time.

**Slower doesn't change what the activity is.** Every provider AUP checked
(item 119), and ordinary consumer Gmail/Outlook terms, prohibit unsolicited
commercial bulk email as a category, not "more than N per day." Throttling
doesn't cure a category prohibition.

**What's still correct in the instinct:** gradual ramp-up is exactly how
sending reputation gets built — that's `phases.md`'s "Warmup schedule:
deliberately low volume, ramping." It has to run on infrastructure built to
carry it (a verified subdomain, real bounce/complaint feedback), not a
personal account. The more promising lever is the consolidation finding
above: fewer required sends to begin with, on top of a real warmup, not
instead of one.

## 2026-09-28 — Two provider categories, not one: correcting an over-generalization, and three next steps

Follow-up to items 119/121, after checking the cold-outreach platforms
(Instantly, Smartlead, lemlist, Apollo) directly: they do require building
campaigns through their own system, much like Mailchimp — confirming the
architecture caveat item 119 flagged as unconfirmed. Worth being precise
about what that does and doesn't close off.

**Two separate axes were getting collapsed into one "everything requires
someone else's system" conclusion.** SendGrid, Postmark, Mailgun, SES, and
Resend (checked in item 119) are plain APIs — a backend calls `POST /send`
with a From, To, subject and body, exactly like this app already calls
RentCast. No campaign builder, no UI. They were ruled out on **policy**
grounds, not architecture. Instantly and its category are ruled out (or at
least made harder) on **architecture** grounds, not policy — their AUP is
opt-out-model-compatible, but the product wants to own the sending
mechanism. No single option checked so far satisfies both axes at once,
which is a real bind, but narrower than "no way around it."

**Three threads not yet exhausted, in order of effort:**
1. **Ask a Category-A provider's compliance/sales team directly**, describing
   the specific use case — individualized outreach to named listing agents
   about a specific property they're publicly advertising, low volume, not a
   purchased list. AUP text is a default a human reviewer can grant an
   exception to; this costs only the conversation.
2. **Amazon SES's policy is structurally different from the other four.**
   SendGrid, Postmark, Mailgun and Resend all state an explicit up-front
   proof-of-opt-in requirement. SES's AUP prohibits "unsolicited mass email"
   without that same explicit prior-proof language, and its actual gate is a
   "production access" request where the use case is described to AWS
   directly, with complaint/bounce-rate monitoring afterward — outcome-based,
   not input-gated. Under-weighted the first time through; worth a direct
   production-access conversation.
3. **Self-hosting is real and fully code-controlled** — a mail server on the
   sending subdomain, SPF/DKIM/DMARC, sending via plain SMTP from Python, no
   one else's UI, no monthly bill. It does not remove the reputation-building
   burden, it relocates it: building sending reputation from zero, handling
   every bounce and complaint directly, and registering for the free
   feedback-loop/reputation programs the major mailbox providers offer to any
   sender with a verified domain (Google Postmaster Tools, Microsoft SNDS,
   Yahoo's JMRP) rather than getting them bundled with a paid provider.

**The one thing that removes the conflict rather than working around it:** a
real opt-in mechanism over time (an agent proactively signs up to be
notified about storms near their listings). That would make every Category-A
provider usable outright, no exception needed — a product change, not a
near-term technical one, but worth being on the roadmap as the actual exit
rather than a permanent workaround.

## 2026-09-28 — Comparing RBI's existing contacts, the hail system's realtors, and the legacy DNC list

Three files: RBI's DNC export (`rbi-dnc-list-09-28-2026.csv`, 719 unique
normalized emails, every row "Unsubscribed" / "Added by you" — a Mailchimp
audience-export format, another piece of evidence alongside the live
`k2._domainkey` DKIM record (item 96) that Mailchimp was RBI's prior
sender); RBI's own existing realtor/contact list
(`Final Realtor Database-09-25.csv`, 4,187 unique emails, 3 duplicate rows);
and the hail system's `realtors` table (7,082 unique emails, sourced from
RentCast pulls). Relates to items 4 (DNC import, blocking any send) and 83
(in-house realtor database, open question of what system holds it — this
file is apparently the answer).

| Comparison | Result |
|---|---|
| RBI's client list ∩ hail-system realtors | 675 (16.1% of the client list) |
| RBI's client list ∩ DNC list | 0 |
| Hail-system realtors ∩ DNC list | 83 (1.2% of hail-system realtors) |
| All three | 0 |

**The zero overlap between RBI's own client list and RBI's own DNC list was
checked, not assumed.** Raw-string inspection found no whitespace/encoding
issue, and the two lists share 52 domains (`kw.com`, `remax.net`,
`coloradohomes.com`, and others) despite zero exact-email matches — so it
isn't a data or normalization bug. The DNC list's `Created At` values span
2017-10-26 to 2025-09-16, with two large clusters (213 rows on 2018-07-19,
103 on 2018-11-14) suggesting bulk unsubscribe events years apart from the
current client list's roster. Plausible explanation: several years of
realtor turnover (brokerage changes, new emails, agents leaving the
industry) between the DNC list's older entries and the client list's current
one, and/or the DNC list drawing from a broader audience than realtors
specifically. Not confirmed either way — worth asking RBI if the history
matters.

**The concrete, load-bearing number:** 83 of the hail system's current 7,082
realtor emails are already on the legacy DNC list. `dnc_list` is still
empty. Sending today, before that import lands, would improperly contact
83 people who already asked not to be — direct, specific evidence for
CLAUDE.md's rule that the DNC import comes before any send, not just a
general precaution.

**The 675 is a useful subset, not yet acted on.** These are RBI's own known
contacts who also already appear in the hail-matched realtor pool — a
meaningfully stronger footing for the provider conversations above (item
119's next steps) than "every listing agent within 5 miles of a hail
event," since there's a plausible prior-relationship signal on RBI's side
for this subset specifically. Not yet decided whether or how to use this
distinction.

**Not done:** no import into `dnc_list`, and this was read-only analysis
only. The full email-level breakdown (which specific addresses fall in each
overlap) was saved to a scratchpad file, not committed to the repo or
printed in full here.

## 2026-09-28 — Constant Contact cross-reference: the client list and CC are different relationships, and the CC copy is two years old

Follow-up to the previous entry, after the developer clarified what each
file actually is:
- `rbi-dnc-list-09-28-2026.csv` — the full **current** DNC list, dated
  today. Individual entries carry old `Created At` dates (2017–2025)
  because that's when each suppression was *added*, not because the
  export itself is stale. The earlier "83 of 7,082 hail-system realtors
  already on DNC" finding stands as current and actionable.
- `Final Realtor Database-09-25.csv` — **not** an email-marketing list. It's
  RBI's own record of clients who signed up for RBI's roofing service at
  some point over the last 12+ years. An updated copy is coming. This
  reframes the near-zero overlap with Constant Contact from the previous
  entry: it was never expected to overlap, because it's a different kind of
  relationship (service/referral) from a newsletter subscription.
- `alyx-constant-contact-full-list-10-24.csv` — a copy of the full Constant
  Contact list, but **from two years ago**, not current.

**The four-way comparison, run against these files and the hail-system
`realtors` table:**

| | |
|---|---|
| Client list ∩ Constant Contact, at all | 1 of 4,187 |
| CC Active (as of the 2-years-ago snapshot) | 2,432, all "Implied" permission, zero "Confirmed" |
| CC Unsubscribed (same snapshot) | 613 |
| DNC file ∩ CC Unsubscribed | 613 of 719 (85%) — the rest came from elsewhere |
| Hail-system realtors ∩ CC Active | 339 (4.8%) |
| Hail-system realtors ∩ CC Unsubscribed | 66 (0.9%) — must stay excluded |
| The 675-set (client ∩ hail, prior entry) ∩ CC or DNC | 0 — unknown status to both |

**The 339 needs a caveat the original framing didn't have: it's "were
active two years ago," not "are currently active."** Real subscription
status can move in either direction over two years. It's still the
best-corroborated warm-start subset available today — meaningfully better
than the 675, which has zero corroboration from either CC or the DNC file —
but it should be re-verified against a current CC export before being
treated as safe to email, and a fresh export is worth requesting since it's
RBI's own vendor account, not blocked on anything external.

**A real open question, not yet resolved:** what "signed up for our
service" means for the Final Realtor Database specifically — a personal
roofing job on the recipient's own property, versus a referral-partner or
preferred-vendor program membership. Either could plausibly fit
§ 7702(17)(A)'s "transactional or relationship message" exemption (item
120) for at least a subset of that list, which would be stronger footing
than "existing relationship, better than cold" — it could mean CAN-SPAM's
opt-out framework doesn't even need to be invoked for those contacts. Not
confirmed; worth asking RBI directly what the signup actually represents.

**Also surfaced: Constant Contact itself, for the CC-active subset
specifically, wasn't previously considered as an option.** The earlier
objection to Mailchimp/Constant-Contact-style tools (item 119) was scoped to
cold outreach to thousands of never-contacted agents; it doesn't apply the
same way to continuing to send, through a channel RBI already owns and
already has standing (if "Implied," not "Confirmed") permission on, to
people already on it. The one caveat: that permission was presumably given
for whatever RBI has historically sent through CC (list names read as
broker-relationship/networking content — "DMAR Industry Partners,"
"Diamond Circle Club Members"), and storm-alert content is a real change in
kind, worth being thoughtful about even with an audience that has some
standing relationship.

## 2026-09-28 — What "signed up for our service" means, and always including the CAN-SPAM footer regardless

Closes the open question from the previous two entries and item 83. Per the
developer: it means the realtor **called RBI directly and requested an
inspection** — of a property on a buyer or seller side of a transaction
they're representing, or their own roof. A voluntary, recipient-initiated
service request, not a name collected in passing.

**This is a clean fit for Constant Contact's own stated "implied consent"
standard** ("an existing business relationship, including making a purchase
from you") — stronger than "used our service" in the abstract, since it's a
specific, direct engagement the recipient started. It's also the strongest
available framing for a Category-A provider's compliance team (item 119),
if that route is still pursued for any part of this. It confirms the
relationship is with the realtor as a professional, which makes a future
storm alert about a *different* property they now represent a natural
continuation, not a fresh cold contact.

**What it doesn't do:** the storm-alert message is still a new solicitation,
not a continuation of that specific past inspection, so it's still
"commercial" under the CAN-SPAM analysis (item 120) regardless of how strong
the relationship is.

**Decided: the CAN-SPAM footer (physical address, working opt-out, no
deceptive subject/headers) goes in every send regardless of which consent
theory covers a given recipient** — including the two now well-grounded
pools (the 339 CC-active, the 675 service-request clients). Not because
either pool is legally required to have it under every reading, but because
it's cheap, and a policy that depends on correctly classifying every
recipient's consent basis before deciding whether to include it is a policy
that will eventually get it wrong for someone. This supersedes any
per-recipient judgment call about whether the footer is "needed" for that
specific person.

## 2026-09-28 — Self-hosting reassessed against the smaller, warmer audience (no decision made)

**Not a decision — analysis to inform one, still being weighed alongside
Constant Contact and the provider options above.** Prompted by the shift to
the ~1,014-person combined pool (item 122; 339 CC-active-two-years-ago +
675 service-request clients) and a stated preference for staying
self-contained rather than adding vendor dependencies.

**What the smaller, warmer audience actually changes:** not the fixed cost
of building the infrastructure — the risk profile of using it. A brand-new
sending domain's biggest exposure is high bounce and complaint rates during
the window before it has any track record. A batch of people who either
called RBI directly and requested an inspection, or were actively receiving
RBI mail within the last two years, has materially lower expected bounces
(real, validated addresses, not scraped) and complaints (a real reason to
recognize the sender), and higher expected engagement — close to the
textbook profile for how a new domain's warmup is supposed to go, rather
than the cold, thousands-large, ongoing case examined earlier (see "Throttled
sending from a personal account: considered and rejected" — that entry was
about *personal* Gmail/Outlook accounts specifically, not a self-hosted
domain-owned MTA; this is a different, more capable option than that one).

**The build, concretely, if pursued:**
- One-time: a VPS with a dedicated IP (checked against blacklists first), a
  PTR record matching the sending domain, Postfix with TLS, DKIM signing
  (`opendkim`) plus SPF and DMARC — all on `send.roofbrokersinc.com`,
  self-service through the Cloudflare delegation already planned (item 96),
  and the actual send call in hailsys via `smtplib` (stdlib, no new
  dependency).
- Ongoing: bounce classification from Postfix's own delivery logs (more
  fiddly than the send path itself), registering for and processing
  Google Postmaster Tools, Microsoft SNDS/JMRP, and Yahoo's feedback loop
  (partly bottlenecked on provider approval timelines, not build time),
  manual deliverability monitoring (no bundled dashboard the way a paid
  provider gives), and ongoing patching/maintenance of an internet-facing
  service — not a one-time cost.
- Estimated, calibrated to the existing Postgres/PostGIS/Flask/Docker
  skillset and the DNS work already done: roughly a few days to a week for
  the initial build, another few days to a week for bounce/complaint
  handling, then indefinite light ongoing operational attention.

**A genuine advantage over Constant Contact specifically:** the DNC
suppression check can run in the literal same database transaction as the
send — no approximation needed, since hailsys would control every step.
That satisfies `CLAUDE.md`'s rule exactly, rather than the narrowed-window
compromise a third-party campaign tool requires.

**Where this leaves it:** self-hosting is the most genuinely self-contained
option on the table — no vendor policy to satisfy, only the law itself
(item 120 still applies in full) — and now a better-suited case for it than
the original cold-audience problem ever was. Still weighed against Constant
Contact (faster to a first send, leans on their already-built reputation
and feedback loops) and the provider options (item 119). No decision made.

## 2026-09-26 — Address identity: parsed components, not string cleaning

`sql/024_address_key.sql` (`905895b`, grant `7555d71`), `sql/025_address_
key_generated.sql` (`7872aa3`). Parking-lot item 55.

**The bug.** RentCast's `rentcast_id` is a slug of the address as typed, so
a formatting difference mints a second id for the same house.
`4518 Wordsworth Cir N` and `4518 N Wordsworth Cir` are two `properties`
rows for one building — confirmed live, one of the 21 duplicate groups
found once the key was built. String cleaning (trim, case-fold, strip
punctuation) cannot fix a directional that *moved position* in the string;
only parsing the address into components can.

**`address_standardizer` was available but not installed; the TIGER
geocoder alone does not provide it.** `postgis_tiger_geocoder` came
pre-installed with the base image (item 94) and is unrelated —
`tiger.normalize_address()` exists but is weaker, a simpler string-level
normalizer, not a full lexer/gazetteer/rules parse. `address_standardizer`
and `address_standardizer_data_us` needed installing explicitly.

**The key:** `house_num | coalesce(predir, sufdir) | name | suftype | unit
| postcode`. The directional's position is made irrelevant while its value
is kept, so `1677 Rosemary` and `1677 S Rosemary` stay distinct — a
directional present at all is a different street segment from one absent,
regardless of which side of the name RentCast happened to put it on.

**Checked, not assumed: this collapsing has a real, if currently dormant,
gap.** The original assumption was that no address carries both a prefix
and a suffix directional, so `coalesce` would never have to choose between
them. Checked directly against all 16,407 properties: **31 do carry
both** — real streets, not data errors, concentrated in Centennial,
Littleton and Englewood's compound-directional naming (`1280 W Oxford Ave
S`, `7314 S Downing Cir W`, `5995 W Hampden Ave E`). For these, `coalesce`
keeps the prefix and silently drops the suffix, so two *genuinely
different* streets sharing a prefix but differing only in suffix
directional would collide under one key. Checked whether this has already
happened: **none of the 31 currently share a key with a different
address** — the gap is real and verified, not theoretical, but it has not
yet produced an incorrect merge. Parking-lot item 55 carries this forward.

**City is deliberately not in the key.** RentCast reports different city
names for the same house depending on which municipal boundary layer it
resolved against — `3690 Gray St` reads `Wheat Ridge` on one record and
`Denver` on the other, same zip. Zip is stable and is in the key.

**The column is `GENERATED`, not written by the upsert — this was the
correction that mattered, not a footnote.** The first attempt was a plain
column with a one-time backfill (`sql/024`'s original form). That was
wrong on inspection: `upsert.py` never sets `address_key`, and its
`ON CONFLICT` clause updates `property_address` on every re-pull, so a key
written only at insert time would silently go stale the moment an address
got corrected upstream. `GENERATED ALWAYS AS (address_key(property_address))
STORED` (`sql/025`) recomputes on every write and cannot be forgotten by a
future write path — the same reasoning as `realtors.email_norm`.

**Measured cost:** about 7ms per call. A full-table recompute (16,407 rows)
is a couple of minutes; a large pull paying it per inserted row is
unnoticeable at pull scale (the largest pull recorded so far was 26 zips).

**Verified:** zero mismatches between every stored `address_key` and a
freshly computed value, across all 16,407 rows.

## 2026-09-28 — The Airtable DNC list was populated from Constant Contact, and the two have since drifted apart

Two more suppression files found: `reference/Airtable-DNC-List.csv` (735
rows: `Email`, `Name`, `Name2`, `Name3`, `Time Removed`, `Unsubscribed`,
`Date`) and `reference/rbi-constant-contact-dnc-list-09-28-2026.csv` (719
rows — same shape and, by its early rows, the same underlying data as the
`rbi-dnc-list-09-28-2026.csv` already analyzed). Asked whether Airtable was
built independently or populated from Constant Contact's data.

**Populated from Constant Contact — confirmed, not inferred.**
- **691 of Airtable's 735 rows (94%) share one exact `Time Removed`
  timestamp: `10/21/2025 9:07pm`.** No one unsubscribes at the same minute
  691 times; this is a single bulk-import event.
- **For the 693 emails the two files share, Airtable's `Date` matches
  Constant Contact's `Created At` to the minute**, once converted through
  US Eastern time with daylight-saving handling (UTC-4 for summer
  timestamps, UTC-5 for winter ones): 481 matched under the summer offset,
  208 under the winter offset — 689 of 693, not a coincidental resemblance,
  the same recorded moment in two timezones. A naive same-calendar-day
  string comparison first flagged 242 as "mismatches"; all were this
  timezone artifact, not real differences — worth noting since the wrong
  comparison method would have understated the match badly.

**But they've diverged since the sync, in both directions.**
- **40 entries exist only in Airtable**, every one with a `Time Removed`
  date *after* 2025-10-21 — scattered across November and December 2025,
  one or two at a time (11/1, 11/8, 11/13, 11/22, 12/4, 12/8, 12/9…). This
  reads as Airtable becoming the active, ongoing DNC list after the sync,
  with nothing shown flowing back into Constant Contact.
- **26 entries exist only in Constant Contact's current DNC export, and
  every one predates the 2025-10-21 sync** (0 of 26 are newer). These were
  already suppressed in Constant Contact at sync time but didn't make it
  into that particular Airtable import — a gap in the sync, not newer
  Constant Contact activity.

**What this means for the DNC import (still blocking any send — no
dedicated parking-lot item, tracked in `CLAUDE.md` and `phases.md`'s Phase
5 checklist):** these aren't two lists to reconcile disagreements between —
one lineage, two different sets of drift since a shared origin. **The
correct import source is the union of all suppression files found so
far** — the original DNC export, this Constant Contact copy, and Airtable
together — not any single one. Using only one would under-suppress by
whichever of the 40 or 26 it's missing.

## 2026-09-28 — DNC import: admin upload, staged and previewed before commit

`sql/026_dnc_import_batches.sql`, `hailsys/queries/dncimport.py`,
`hailsys/web/admin.py` (`dnc_upload`, `dnc_preview`, `dnc_commit`,
`dnc_discard`), `hailsys/web/templates/dnc_preview.html` and the upload
section in `admin.html` (`3ebb5e7`, `f94d27f` for the `.gitignore` fix
below). There is still no dedicated parking-lot item for "the DNC
import blocks any send" -- it lives in `CLAUDE.md` and `phases.md`'s
Phase 5 checklist, per the correction two entries up. Related: items
96/119/122's DNC research.

**Suppression is the one table where getting an import wrong is expensive
in both directions.** Suppress someone who never asked, and RBI loses a
real contact silently, with no error to notice. Miss someone who did ask,
and they get mail they explicitly refused — the exact failure `CLAUDE.md`'s
non-negotiable rule exists to prevent. Neither failure announces itself.
Hence preview-then-confirm, not a straight import: an admin sees precisely
what would happen before anything is written.

**The parsed rows are staged in `dnc_import_batches`/`dnc_import_rows`, not
in the session and not re-read from a re-upload.** A signed cookie can't
hold 700-plus rows, and re-reading the file on confirm would let the
confirm silently apply to a *different* file if it changed between preview
and click. What an admin confirms is provably what they reviewed, because
it's the same staged rows, referenced by the same batch token.

**Admin only, not sender.** Considered and declined: the admin blueprint's
`before_request` already calls `require_role("admin")` for everything on
it, and widening this one feature to senders would mean either duplicating
that check or exposing user management alongside it. Suppression list
changes stay with the same role that already manages settings and users.

**The importer verifies rather than trusts the file.** It requires the
Constant Contact header set (`Email address`, `Email status`, `Created
At`) and rejects any row whose status isn't `Unsubscribed`, naming the
reason and line number in the preview rather than silently dropping it.
The export is filtered by hand upstream today; a future one assembled
differently — by someone who doesn't know that convention — must not
silently suppress people it shouldn't, or silently skip people it should
catch.

**`source` is `'legacy_import'`**, already present in `dnc_list`'s `source`
CHECK constraint before this work started — no migration needed for it.

**`added_at` is `NOT NULL`, so an unreadable source date gets `now()`, and
the reason records that explicitly** — "(source data unreadable)" appended,
never a silent claim that the person unsubscribed today when the real date
is simply unknown. The date matters if consent is ever questioned later.

**`ON CONFLICT (email_norm) DO NOTHING`:** an address already suppressed
keeps its first suppression and its original date — the date that actually
matters — rather than being overwritten by a later import's guess.

**Abandoned previews are swept at the start of the next upload** (any batch
uncommitted and older than a day), not on a timer — the same shape as the
stale-pull sweep (item 47).

**Re-posting a confirm is refused via `committed_at`**, so a page reload
after a successful import can't import the same batch twice.

**The Airtable/legacy format has no importer.** It isn't a live,
re-pullable source — see the entry below — so its roughly 40 rows not
already in a Constant Contact export were converted to the Constant Contact
shape by hand rather than building a second parser for a one-time need.

**Measured outcome — the actual justification for building this:**
759 suppressed addresses imported (719 from the current Constant Contact
export, ~40 legacy-only from Airtable), spanning 2017-10-26 to 2025-12-13.
**108 of the hail system's 7,082 realtors are now suppressed**, up from the
83 measured against the Constant Contact list alone (item 122) — the
legacy file caught roughly 25 more people who would otherwise have been
emailed. That gap is the concrete argument for having imported both
sources rather than just the newer one.

**A gap in my own earlier verification, worth recording plainly:** the
rolled-back-transaction test that confirmed this feature worked end to end
ran as `hail_admin`, not `hail_app` — so it never actually exercised the
grants `hail_app` needs. `sql/026`'s first version was missing `UPDATE` on
`dnc_import_batches` (`_MARK_COMMITTED_SQL` needs it), found only once the
feature was actually used on `hail-dev`. Fixed by appending the grant.
Testing SQL correctness under a privileged role and testing the actual
runtime role's permissions are two different checks; this was the second
time in this project a missing grant slipped past the first kind (the
`address_standardizer` lookup tables, item 55, was the first).

## 2026-09-28 — The `.gitignore` DNC pattern was too broad, and excluded code, not just data

`.gitignore` (`f94d27f`). Found while trying to commit the DNC import work
above: `sql/026_dnc_import_batches.sql` and `hailsys/queries/dncimport.py`
never showed up in `git status` at all.

**The rule was `*[Dd][Nn][Cc]*`**, meant to keep real suppression data —
CSV exports holding live email addresses — out of this public repo. It
matched on the *word* "dnc" appearing anywhere in a filename, which also,
silently, matched two legitimate source files that happen to have "dnc" in
their module and migration names. No error, no warning — `git status`
simply never mentioned them, exactly the kind of failure that's invisible
until someone goes looking for it.

**Fixed by excluding data by location and type, not name:** `*.csv`,
`*.xlsx`, `*.xls`, with `!planning/*.csv` to keep the three CSVs that are
genuinely meant to be tracked (`report_sources.csv`, `report_types.csv`,
`zip_city_names.csv`) from being caught by the same blanket rule.
`reference/` and `data/` (both already ignored, for unrelated reasons)
already cover where the real DNC/unsubscribe files actually live; the new
rule catches a stray copy dropped anywhere else in the tree.

**The general point, worth keeping:** a name-pattern ignore rule fails
silently and sweeps up code that happens to share a word with what it was
meant to catch. Excluding by location or file type doesn't have that
failure mode — a `.py` or `.sql` file is never going to match `*.csv`.
**Still open:** `*[Uu]nsubscribe*`, right next to the old rule, has the
identical problem and hasn't been fixed — a future `unsubscribe.py` route
(Phase 5 will need one, item 120's opt-out requirement) would hit the same
silent exclusion. Not fixed here; flagged for whenever that file exists.

## 2026-09-28 — Email copy and merge fields: first draft, not final

**Provisional.** This entry records a first draft that captures the
intent, not finished copy. Images and more content are expected before any
real send — recorded here so the draft doesn't get mistaken for something
settled just because it's written down. Parking-lot item 16.

**The old system's email (Mailchimp, a seasonal newsletter) is the
reference point for tone and offer, not for structure.** It was general
marketing with exactly one merge field (`FNAME`) and nothing about a
property or a storm — a genuinely different kind of message from a
storm-specific alert, so this draft's fields are new, not inherited.
**Worth keeping from it:** the free inspection / $35 five-year
certification offer, the agent testimonials, "700+ real estate agents in
the Denver Metro area since 1992," and the fallback pattern on a missing
first name (the old template fell back to an em dash).

**Draft copy, verbatim:**

```
Subject: Hail reported near your listing at {{property_address}}

{{agent_first_name}},

Hail was reported {{nearest_miles}} from your listing at
{{property_address}} on {{storm_date}} — the largest stones
reported nearby were {{hail_size}}.

Roof damage isn't always visible from the ground, and an
unresolved roof question can slow a closing down. We'll
inspect it free, and certify it for five years if it passes.

[TRY ROOF BROKERS]

Roof Brokers has served 700+ real estate agents in the
Denver Metro area since 1992.

Roof Brokers Inc. · 2222 S Fraser St · Aurora, CO 80014
(303) 750-1900 · RoofExperts@RoofBrokersInc.com

This is an advertisement. Unsubscribe | Preferences
```

**Rationale for the length:** a storm alert earns its open on the specific
fact in the subject line; the trust material (testimonials, the "since
1992" line) competes with that fact if placed above the fold, so it's kept
below the offer rather than opening with it. The footer's physical address
and unsubscribe link are the CAN-SPAM requirements already decided as
non-negotiable regardless of consent theory (the CAN-SPAM analysis
entry, "always including the CAN-SPAM footer regardless").

**Merge-field vocabulary:**

*Required — a missing value blocks that one send, which is reported, and
the batch continues:*
`property_address`, `storm_date`, `hail_size`, `nearest_miles`,
`rbi_address`, `unsubscribe_link`.

*Optional, with a fallback:*
`agent_first_name` (first token of `realtors.agent_name`),
`agent_office_name`, `mls_number` (omitted when absent).

*Defined but unused by the current draft:*
`agent_name`, `property_city`, `property_zip`.

**Fallbacks for cosmetic fields, blocking for fields the message cannot do
without.** Conditional blocks in the template itself were considered and
declined — that's designing a template language, not choosing
placeholders, and it's a much bigger scope than this phase needs.

**Required-field failures must be visible, not silent.** If 40 of 600
sends are skipped for a missing required field, the operator has to see
that 40 were skipped, or the missing 40 look sent when they weren't. The
check is meant to run at preview as well as at send — the same shape as
the DNC import's preview-then-confirm (the entry above).

**Measured 2026-09-28, checked against the live database, not assumed:**
across all 7,082 realtors — 0 missing `agent_name`, 0 missing
`agent_email`, 1 missing `agent_office_name`, 3 single-word names
(`Coloradohomesource`, `Nikkiowen`, `Heather` — the first two are
brand/team names concatenated without a space, not a person missing a
last name). Empty required-ish fields are nearly theoretical for RentCast
data today; the required/optional split and the visible-failure design
exist anyway because the in-house realtor import (item 83) is expected to
be messier than RentCast's own data.

## 2026-09-29 — `address_key`: coalescing every field, not just the directional

`sql/027_address_key_coalesce_fix.sql`. Parking-lot item 55.

**The bug:** `concat_ws` skips a `NULL` argument entirely rather than
leaving an empty slot — `025`'s function only wrapped `predir`/`sufdir` in
`coalesce(...)`, so a `NULL` street name, suffix, unit, or zip dropped a
field and shifted every field after it one position left. A key that
should read `house|dir|street|suffix|unit|zip` could come out
`house|dir|street|zip` — four fields, with the zip landing where the unit
would normally be. Position, not just value, is load-bearing for this key
(the function's own comment says so), so a shifted key isn't just
cosmetically wrong, it's a false match waiting to happen.

**Checked against the live data before writing anything down, not
assumed:** the file's own header comment claimed "245 of 24,932 keys built
with only five fields" and "3 groups collided as a result." The 24,932 and
the 3 collisions are both exactly right, verified independently. **The 245
figure is wrong — the real count is 425.** Every short key has exactly
five fields, none shorter. Worth fixing in the file's comment; doesn't
change what the migration does.

**One existing finding in item 55 needs re-checking, not just noting.**
`8557 Highway, 86` / `8557 State Hwy, 86` (Kiowa) was recorded there as a
genuine duplicate — "a name/suffix spelling difference the standardizer
resolves to the same parsed street." Its actual pre-fix key
(`8557||86||80117`) has empty slots exactly where `predir`/`suftype`
belong, which is this bug's signature, not necessarily a real semantic
match. It may still be a genuine duplicate once re-parsed correctly — it
just isn't confirmed as one by the evidence that was cited for it. Re-check
after `027` runs, before trusting that pair either way.

**Why this doesn't threaten a fresh production setup, even though it hit
`hail-dev` for months.** Migrations here have no automatic runner —
`docker-compose.yml`'s own comment shows the real mechanism, one file at a
time: `docker compose run --rm loader psql -v ON_ERROR_STOP=1 -f
/repo/sql/0NN_name.sql`. On `hail-dev`, real `properties` data already
existed — months of RentCast pulls — before `027` was written, so `025`'s
under-coalesced function sat live for that whole window, generating short
keys on every insert. **On a fresh production database, if `001` through
`027` are applied in numeric order before the first pull, that window
never opens** — `properties` starts empty, and by the time any row is ever
inserted, `address_key`'s generation expression is already `027`'s fixed
version. The bug requires a gap between "buggy function is live" and "real
data starts flowing through it" to produce anything; run straight through
in order, and that gap is zero. The manual backfill `UPDATE` run on
`hail-dev` is also unnecessary on production for the same reason — a
`GENERATED ALWAYS AS (...) STORED` column computes itself on every insert,
automatically, correctly, from the first pull onward.

**The one real requirement, precisely because there's no automatic
runner:** every file `001` through `027` (and beyond) has to be applied in
strict numeric order before the first real pull, or this exact class of
problem can resurface. Nothing in the tooling enforces that — it's a
manual discipline point for whoever stands up the production box.

## 2026-09-29 — `027` ran on hail-dev: verified, and the Kiowa re-check resolved

Checked against the live database after the run, not assumed. `properties`
is now 25,219 rows (up from 24,932 — ordinary growth from pulls since the
last check, unrelated to this migration). Of those, every one of the
24,932 non-`NULL` keys has exactly six fields — zero short keys remain, the
`concat_ws` bug's signature is gone. 287 rows are `NULL` (no house number),
proportionally in line with the 170-of-16,407 figure recorded when the key
was first built.

**The Kiowa pair, flagged above for re-checking, is resolved — but not the
way either prior note guessed.** Its pre-fix key, `8557||86||80117`, no
longer exists anywhere in the table (zero rows), confirming the fix
actually took effect for this pair specifically. But `8557 Highway, 86` and
`8557 State Hwy, 86` **still collide** under the corrected key,
`8557||86|||80117` (house_num `8557`, name `86`, every other field empty).
So this was never a bug artifact, which settles that question — but item
55's original characterization, "a name/suffix spelling difference the
standardizer resolves to the same parsed street," is also wrong. There is
no street name here for the standardizer to resolve two spellings of:
`address_standardizer` drops "Highway"/"State Hwy" entirely for a rural
route address, leaving only the house number and the bare route number
behind. Two more pairs in the current 50 groups show the identical
pattern — `10985 E Hwy, 24` / `10985 E Us Hwy, 24` and `21295 E Hwy, 24` /
`21295 E Us Hwy, 24`, both Peyton — so this is a real, recurring fourth
cause (rural highway/route addressing), not a one-off.

**Full re-audit of the current 50 duplicate `address_key` groups (100
properties), all inspected, not sampled:**
- **39 groups** — the already-documented cause: city-label disagreement
  within one zip (RentCast's municipal-boundary variance).
- **7 groups** — the already-documented cause: directional-position
  variance, same city both times (`1043 Nolte Dr W` / `1043 W Nolte Dr`,
  and five more of the same shape).
- **3 groups** — the new cause above: rural highway/route addressing
  collapsing to house number + route number.
- **1 group — not new, already on record.** `6637 E 149th Ave, Thornton, CO
  80602` (`rentcast_id` `:-6637-E-149th-Ave,-Thornton,-CO-80602`) has a
  stray leading `": "` in its stored `property_address`, against an
  otherwise identical clean row. Item 55 already documented this exact
  pair on 2026-09-26 as a fourth, minor cause. Re-checking it here only
  confirms it's still present, unaffected by `027` (it was never the
  coalesce bug) and still unexplained — whether it's how RentCast sent it
  or something in the pull path remains open.

**Not yet done:** the 50-group, 100-property count here isn't diffed
group-by-group against the 21-group, 42-property count from 2026-09-26 —
`properties` grew from 16,407 to 25,219 rows in between, so more
duplicates from more data is expected on its own, but whether every one of
the original 21 groups is still intact inside today's 50 hasn't been
checked directly.

## 2026-09-29 — Item 55: send-time dedup, scoped to what the data supports

Measured on 25,219 properties with corrected keys, checked against the
live database:
- 50 duplicate groups on exact `address_key`, all clean pairs — about
  0.2% of properties.
- On one real storm (2026-09-22, `HAIL`): 656 matched properties, 654
  distinct `address_key` — exact-key dedup removes 2, about 0.3%.
- The item's original "186 candidate pairs" figure was proximity-based (a
  25-metre `ST_DWithin` self-join) against 508 properties. It measured a
  different thing — closeness, not identity — and is superseded by the
  `address_key` approach, not contradicted by it.

**Decision: build Part 1 only — group by `address_key` at send time, one
email per key.** A `DISTINCT ON`, essentially free, and matches what the
measured rate actually justifies.

**Part 2 is designed and NOT built:** same house number, directional,
street, suffix and zip, one record carrying a unit and another not, AND a
shared listing agent. 126 groups qualify (see the entry below). Deferred
because the measured duplicate rate doesn't yet justify the added
complexity. Revisit if a duplicate email is ever reported.

**Also decided: no suffix merging.** Genuine same-zip suffix conflicts
exist (516 Superior Dr / St, 6765 Utica Ave / Cir, 1915 Canyonpoint Ln /
Pl) and could be either distinct streets or RentCast disagreeing with
itself. Sending two emails is better than silently dropping a real
address.

**Nothing is merged in the `properties` table.** Send-time only, per the
item's original reasoning: matches point at listings, not properties.

## 2026-09-29 — What the unit-mismatch groups actually are

Checked against the live database (`address_key` split into its
components, joined to `listings`/`realtors`), not assumed:
- 561 groups share the same house number, directional, street, suffix and
  zip, with some records carrying a unit and some not.
- 412 of them (73%) have **no agent at all** on the unit-less records, so
  they can never produce an email regardless of dedup — documentation,
  not logic. **Independently rechecked this figure exactly: 412.**
- Of the remaining 149, 126 share a listing agent across the unit
  boundary (Part 1's true duplicates) and 23 do not, and are treated as
  genuinely distinct units.
- The no-agent majority is **new construction**: a builder lists several
  floor plans at one address, with one lot-numbered record carrying a
  real agent alongside them. Confirmed example, 13796 Daffodil Pt: one
  "Lot 76" at $899,990 with agent Batey Mcgraw, three unnumbered at
  $724,990, $664,990 and $654,990 with none.
- This inverts the item's stated Lot rule in effect but not in outcome: a
  no-Lot record at an address with Lot records IS a duplicate, but
  because it's a builder plan listing, not a variant unit.

**Independent recheck, noted rather than smoothed over:** rebuilding this
from scratch landed at 566 mixed groups / 130 shared-agent / 24 distinct
— close to the 561/126/23 above but not identical, while the 412 no-agent
figure and the Daffodil Pt example both matched exactly. The gap is most
likely a handful of properties carrying multiple listings with different
agents, where "which agent counts" wasn't pinned down with an explicit
tie-break on this rebuild. Not chased further; the headline numbers and
the underlying pattern are solid either way.

## 2026-09-30 — `api_pulls` identity gaps are expected (moved from parking-lot item 128)

Moved out of `docs/parking-lot.md`, where it was informational with no action
attached; the parking-lot number 128 is left vacant.

27 rows, `pull_id` running 7 to 81 — the gap is from sequence values consumed
by `INSERT`s inside test transactions that were rolled back during this
project's verification work (the sequence itself doesn't roll back with the
transaction, by design). Normal, expected Postgres behavior, not a sign of
lost or failed pulls. Recorded so nobody spends time investigating it twice.

**When:** none — informational only.

Re-counted 2026-09-30: `api_pulls` now holds 38 rows, `pull_id` 7 to 92 (the
figures above, 27 rows and 7 to 81, were current when it was written).

## 2026-09-30 — The Airtable-only suppressions carry the wrong kind of date (moved from parking-lot item 131)

Moved out of `docs/parking-lot.md`, where it was note-only with no action
expected; the parking-lot number 131 is left vacant.

Converting Airtable's rows to the Constant Contact import shape used the
`Date` column (when the contact was created) for `added_at`, not the
`Time Removed` column (when they actually asked to stop) — that column
was dropped in the conversion since the importer only understands the
Constant Contact shape. Suppression itself is unaffected, since it's
enforced on `email_norm`, not on any date — but for these ~40 rows,
`dnc_list.added_at` is not evidence of when the person unsubscribed, if
that's ever asked.

**When:** note only, no action expected.

## 2026-09-30 — Append-only is now enforced in the database; owner TRUNCATE left open

Supersedes the 2026-09-03 entry "Append-only is application-layer; database
enforcement deferred" (parking-lot item 20). `sql/029` (`send_log`) and
`sql/030` (`email_templates`) add `BEFORE UPDATE` and `BEFORE DELETE` row
triggers. Applied to `hail-dev` 2026-09-30; `sql/guard_test.sql` (18 checks,
run in a transaction that rolls back) passed 18 of 18. Not checked on the
production OptiPlex.

**`send_log` — what may change after insert:** `send_status`,
`status_updated_at`, `provider_message_id`, `error_detail`, and `sent_at`
(write-once, NULL to a value). Frozen: `send_id`, `realtor_id`,
`recipient_email`, `match_id`, `template_id`, `queued_at`, `sent_by`.
`DELETE` is refused.

**`send_log` status only moves forward:** `queued` to `sent` or `failed`;
`sent` to `bounced` or `complained`; `bounced` to `complained`. `failed` is
terminal. A same-status update is allowed so a retried provider webhook is a
no-op. **Known edge:** `queued` cannot go straight to `bounced` or
`complained`. If the provider accepts a message but we never record `sent`, a
later bounce webhook hits the exception instead of being recorded. Left as is
until the send path is decided. Also, `queued` to `sent` must set `sent_at` in
the same `UPDATE` (the `sent_has_timestamp` CHECK requires it, and the
write-once rule then freezes it).

**`email_templates`:** every column frozen except `is_active`, which may go
true to false only. A retired template is never reactivated; write a new one.
`DELETE` is refused.

**Decision: `TRUNCATE` by the table owner is not blocked, and that is
acceptable for now.** Row triggers never fire on `TRUNCATE`. `DELETE` and
`TRUNCATE` are revoked from `hail_app` on both tables, which covers the
application role (it held only `INSERT, SELECT, UPDATE` to begin with, so the
revokes are belt-and-braces and make the intent explicit). `hail_admin` owns
the tables and can still `TRUNCATE`, and can also disable or drop the
triggers, so a statement-level `BEFORE TRUNCATE` trigger would narrow the gap
without closing it. Accepted because `hail_admin` is one person, the only one
with access. `sql/guard_test.sql` has a `KNOWN GAP` check that expects
`TRUNCATE` to succeed.

**Why:** the audit trail's guarantee is against the application, not against
someone with admin access. That is enough while it is one person. The
application must never connect as `hail_admin`, or the revokes do nothing.

**When:** revisit when a second person gets database access, or before anyone
needs to show the log is tamper-proof. Adding the trigger is a few lines: a
`no_truncate()` function and a `BEFORE TRUNCATE ... FOR EACH STATEMENT` trigger
on each table, plus flipping the test's expected result to `23001`.

## 2026-09-30 — hail-dev reachable through a Cloudflare Tunnel; items 66, 103, 110 and 127 closed

hail-dev is now reachable at `dev.roofbrokersinc-weather.com` through a
Cloudflare Tunnel with Access in front. It was Tailscale-only before. The
domain was bought for this and is separate from RBI's own zone, so none of it
waits on item 96 (the DNS ask, still open).

### Username convention (item 66)

`first.MILI`, lowercase: first name, a dot, then middle and last initials, so
John Fitzgerald Kennedy is `john.fk`. No middle name means last initial only
(`angela.l`). A collision appends a digit (`john.fk2`); `users_user_name_key`
rejects duplicates loudly, so a collision surfaces rather than corrupts. The
admin account was renamed from `justyn` (now `justyn.ml`). Nothing references
`user_name`: it is a column only on `users`, and the 14 foreign keys that point
at `users` (across 11 tables) all use `emp_id`, so the rename was one `UPDATE`.

**Reasoning:** usernames are identifiers, not secrets. The convention reduces
guessability; it does not remove it. **Item 66 closes as residual risk
accepted**, mitigated by Access, rate limiting and the item 127 fix. It is not
"guessability solved". Emails as usernames were considered and rejected:
`users.emp_email` already exists as its own unique column, so login identity and
contact address are deliberately distinct.

### Accounts (item 103)

`testview` is deactivated (`is_active = false`), not deleted: it may own rows in
`api_pulls`, `storm_listing_matches` and `match_runs`, and those foreign keys
have no `ON DELETE` behaviour. Angela was created as `sender` through `/admin`,
not `scripts/create_user.py`, so `created_by` is set; login was verified end to
end. `sender` rather than `admin` because she does not need user management.
`/admin` can promote her later and sets `sessions_invalidated_at`, so the change
applies at her next sign-in.

### Login timing enumeration (item 127)

**Do not cite the first timing run.** It read 18.2 ms for a real user, 2.1 ms
for an unknown one and 2.2 ms for a deactivated one, and it measured CSRF
rejections, not logins. Those requests returned 400 and never reached
`login()`, so the apparent 8x gap was not evidence of a timing leak.

The valid run used a CSRF token and its session cookie; all three returned 401:

| Account | Time |
|---|---|
| real user, wrong password | 106.6 ms |
| unknown user | 107.1 ms |
| deactivated user | 105.8 ms |

The spread is about 1%, consistent with scheduling noise.

The fix is wider than the item as written. The old `or` chain short-circuited,
so `verify_password` was skipped for unknown users **and** for deactivated ones,
and the `system` account's `'!'` hash returned without hashing. All three now
cost one real scrypt, against `DUMMY_PASSWORD_HASH` in `hailsys/web/auth.py`,
generated per process from random bytes. The rejection check runs after the
verify.

**Consequence:** every failed login now costs about 106 ms of CPU. That is
intended, and it is why rate limiting matters.

### Tunnel topology (item 110)

- **Two tunnels, two hostnames:** `dev` now, `app.roofbrokersinc-weather.com`
  when the production machine arrives, each with its own credential. Sharing
  one tunnel was rejected: multiple connectors on one tunnel are treated as HA
  replicas, so traffic would load-balance between dev and production.
- **Locally managed, not dashboard managed.** `cloudflared/config.yml` is in
  git; `cloudflared/*.json` is gitignored. Routing stays in git.
- `~/.cloudflared/cert.pem` is deliberately **not** mounted, so the container
  can serve traffic but cannot create or delete tunnels in the account.
- Compose service on `hailnet`, reaching the app at `http://web:8000`, with a
  `404` catch-all. No `ports:` block: the connector dials out, so nothing is
  exposed inbound.
- `user: "1000:1000"` is required. The credential is mode 600 owned by
  `hail-user`, bind mounts pass UIDs numerically, and the image's default user
  could not read it.
- The image is distroless: no shell, no `ls`. Debug from outside the container.
- `web` keeps `127.0.0.1:8000:8000` on purpose, for local testing. The item 127
  measurement depended on it.

### Access policy

Application `dev.roofbrokersinc-weather.com`, policy "Roof Brokers Access":
Action **Allow**, include rule **Emails** (two addresses), "Accept all available
identity providers" **off**, One-time PIN explicitly selected, session duration
raised off the default. Recorded as a shape, not a click path: the dashboard's
navigation moved during this session, and the docs describe two different
locations for the OTP identity provider.

The One-time PIN identity provider must exist at the account level before any
policy can send codes. Without it the policy looks correct and silently sends
nothing.

### Rate limiting: a thin second layer, not "done"

One rule: `/login`, 5 requests per 10 seconds per IP, 10-second block. The free
plan caps the counting period at 10 seconds and allows one rule, so this is
burst protection only. An attacker pacing at one request per second is
unaffected. Access remains the primary control. Revisit if the plan changes.

### Known gaps, noted and not fixed

- Per-IP counting means a shared office egress could rate-limit Angela and the
  developer together.
- The QUIC UDP receive buffer is below what quic-go prefers; cosmetic at this
  volume.
- **Open, unexplained:** during the invalid measurement a real handle took 8x
  longer than an unknown one on a 400 CSRF rejection, which should not touch the
  database at all. No explanation found. Not guessed at here.

## 2026-10-01 — Address search: Census Geocoder, keyed through `address_key()` (items 23 and 124)

A `/search` page: type an address, get the hail and wind reports near it.
Informational only; it does not touch listings, realtors, matching or the send
path. It reads `iem_data`, `settings` and `report_types`, and writes only
`geocode_cache` and `address_searches`.

New: `hailsys/geocode.py`, `hailsys/web/search.py`,
`hailsys/web/templates/search.html`, `sql/031_address_search.sql`, a blueprint
registration in `hailsys/web/__init__.py` and a nav entry in `base.html`.
Commits `d9cb846`, `5c74fde`, `4554b8d` (the page), `d6f41bc` (cache insert
fix, below), `64aa6f9` (report-type filter) and `c993a2d`. The report-type
filter was specified after the first working version and has landed: a Type
dropdown fed by `storms.fetch_report_types`, so it offers the same list as the
storm-days page, and an unknown value falls back to All. `sql/031` was applied
to `hail-dev` before this entry; not checked on the production box.

**Phase.** Items 23 and 124 were gated on Phase 6 (re-gated 2026-09-30, when no
geocoder or address route existed). The developer pulled them forward into
Phase 5 on purpose, so the web app has a more visible feature to show RBI's
management. It touches no send path.

### The geocoding decision (closes item 23)

Item 23's blocker was that nothing converts an address to coordinates:
RentCast returns coordinates only for properties it already knows.

**Chosen: the Census Geocoder API** (`geocoding.geo.census.gov`,
`/locations/onelineaddress`, benchmark `Public_AR_Current`). No API key, US-only,
street-level addresses only.

**Rejected for now: loading TIGER address data locally.** Evidence, checked
2026-10-01, because this will be reconsidered:

- `postgis_tiger_geocoder` is installed but its data is empty: `tiger.edges`,
  `tiger.addr` and `tiger.featnames` all have 0 rows.
- The Census API serves the same TIGER data, so this is not an accuracy
  tradeoff, only a question of where the lookup runs.
- `Loader_Generate_Script(ARRAY['CO'], 'sh')` downloads only state-level layers
  (`place`, `cousub`, `tract`, `tabblock20`). The per-county address ranges
  would need `Loader_Generate_Census_Script` as well (the function exists).
- The `postgis` container has none of `wget`, `unzip`, `shp2pgsql` or `curl`,
  and no `/gisdata`. The generated script also carries placeholder settings
  (`PGUSER=postgres`, `PGPASSWORD=yourpasswordhere`, `PGDATABASE=geocoder`) that
  would need rewriting.
- The load would be repeated on the production machine, which has not arrived.
- Disk is not the constraint: 33 GB free, database 1.35 GB.
- The loader's configured vintage is `rd22`, fetched from `TIGER_RD18`, older
  than the TIGER2025 boundary data already loaded. Those URLs still return
  HTTP 200 (checked 2026-10-01).

Local TIGER remains the better end state and is a swap behind `geocode.py`'s
interface if the API's limits or uptime become a problem (item 145).

### Accuracy

Census interpolates the point along a street edge from the house-number range,
so a result is accurate to roughly a block, not to the rooftop. That is well
inside a 5-mile radius, but the page must not imply it is the exact position of
the building; it says so under every matched result.

The response carries the TIGER edge ID (TLID) and the side of the edge, **not**
a census tract; a tract would need the separate `geographies` endpoint. The TLID
is stored in `geocode_cache.tiger_line_id` so a later jurisdiction join to TIGER
edge data is possible. (The first draft of the `sql/031` comment called it a
tract; corrected in `5c74fde`.)

### Normalization (closes item 124)

Typed input is normalized by calling `address_key()` **in SQL**, never
reimplemented in Python. `address_key()` reads `address_standardizer`'s
reference tables; any approximation diverges silently into cache misses and
duplicate rows rather than an error. `geocode_cache.address_key` is the unique
key, so two spellings of one house resolve to one cached point.

Cost: `address_key()` takes about 8 ms per call (measured 2026-10-01: 200 calls
in 1.67 s; a single call 8 ms). A search calls it in the cache lookup and, on a
miss, in a separate key-only query; the insert reuses the key already held. A
couple of calls per search is irrelevant interactively and would matter in bulk.

An address with no house number cannot be keyed: `address_key()` returns NULL,
the same reason 287 of 25,219 `properties` have no key. Those are logged as
`unparseable` and the Census call is skipped, since the geocoder needs a street
address anyway.

### Schema

`sql/031_address_search.sql` adds two tables:

- `geocode_cache`: one row per successfully geocoded address, keyed on
  `address_key`. Rows do not expire. `hail_app` has `INSERT` and `SELECT` only.
  A cache row is a record of what the geocoder returned.
- `address_searches`: every search including misses, with an `outcome` of
  `matched`, `no_match`, `unparseable` or `service_error`. Built so that real
  failures decide whether structured address fields or fuzzy suggestions are
  worth building (items 143, 144). It does not record the report-type filter.

The first draft of the migration ended with
`GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hail_app`. Removed in
review: `sql/010` records that identity columns need no sequence grant, and the
line would have widened `hail_app` across the whole schema.

**The cache insert is `ON CONFLICT (address_key) DO NOTHING`, not `DO UPDATE`.**
`DO UPDATE` needs `UPDATE` privilege on the table even when nothing conflicts,
so with insert-only grants the first search of any new address failed with
`permission denied for table geocode_cache`. When the insert returns no row (a
concurrent search cached the address first) the id is fetched with a `SELECT`.
This was found only by running the page: the earlier SQL checks had run as
`hail_admin`, a superuser. Test as `hail_app` (`d6f41bc`).

### Date range

Search uses its own window function, not `views._window_from_args`. It defaults
to 365 days and applies **no** 400-day clamp. That clamp (`MAX_RANGE_DAYS`,
item 49) exists for export sizing; this is a single point against a GiST index.
Recorded so the difference does not later look like an oversight.

### Rate limiting

`geocode.py` self-imposes 2 requests per second, with a lock so concurrent
Flask threads queue rather than burst. Census does not publish a documented
limit that we could verify, so this is a conservative guess, not their figure.
Only cache misses reach the network.

`BENCHMARK = "Public_AR_Current"` is a versioned name that Census retires over
time; a sudden rise in `service_error` or `no_match` is the signal to check it
(item 148).

### Review corrections worth recording

The same kind of error recurred: plausible code written without checking what
already existed. Caught in review before the first run:

1. A raw `SELECT default_match_radius_miles FROM settings`, where
   `fetch_settings()` exists precisely because NUMERIC comes back as Decimal and
   `Decimal * float` raises `TypeError`.
2. No `report_types` join, so the `magnitude` filter received `report_text`
   where it needs `mag_unit`.
3. An `unparseable` branch that could not run: `match` was None only after
   `no_match` or `service_error` had already been set. It also meant a no-house-
   number address still went to Census.
4. A `denver_day` Jinja filter that does not exist (only `magnitude` is
   registered); the established pattern is converting in SQL with
   `AT TIME ZONE 'America/Denver'`.
5. Plain mistakes: `"format": json` (the module, not the string) in
   `geocode.py`, a `remarkd` column name, `DO NOT UPDATE` (invalid SQL), and
   `$(report_text)s` for `%(report_text)s`.
6. Every non-retryable HTTP error told the user the service was busy and to
   retry; a 400 or 403 now raises `GeocodeRequestError` with its own message.

Caught only by running it: the `DO UPDATE` privilege failure above.

### Parking lot

Items 23 and 124 resolved. Items 77 and 78 now wait on item 70 only. Item 94
closed as dropped, on the strength of this decision (Census is the better
choice, so the TIGER geocoder is not wanted now); nothing was dropped or
reconfigured, so the extensions, the empty `tiger` tables and the search path
are unchanged. New: 143 to 145 (open, parked) and 146 to 148 (watch).

## 2026-10-01 — `create_user.py` stays, as a bootstrap path (item 65 closed)

`scripts/create_user.py` predates `/admin` and never set `created_by`, so every
account it made had no author. Decided: keep it, for bootstrapping the first
admin on a fresh database, and nothing else. `/admin` is the everyday path and
records who created each account.

Changes: a `--created-by` option, and a refusal when it is omitted and any
non-system user already exists (the `system` account always exists and has no
creator, so "fresh" means no other user). The check runs before the password
prompts. The password floor now comes from `MIN_PASSWORD_LENGTH` in
`hailsys/web/auth.py`, so the script and the web app cannot drift. The docstring
says bootstrap only.

Tested 2026-10-01 inside the `web` container: omitted `--created-by` is refused
with exit 1 and no prompt; with `--created-by 2` it reaches the password prompt.
No account was created. `users` still holds 4 rows. The `INSERT` itself with
`created_by` set was not run from the script; `/admin` uses the same column.

**Why:** the one thing this script can do that `/admin` cannot is create the
first admin, because `/admin` needs a signed-in admin. That is worth keeping; an
unattributed second account is not.

## 2026-10-01 — Ingest health on the Storm Days page (item 1)

A one-line verdict under the RentCast quota block on Storm Days, with the detail
behind a `<details>` element. `hailsys/queries/ingest.py`,
`templates/_ingest_health.html`, `INGEST_STALE_AFTER` in `tuning.py`. Commits
`0a2052d`, `a3d5f98` (alignment). Visible to every signed-in role: the route is
`login_required` only, which is item 1's 2026-09-30 decision ("everyone should
see ingest health").

**Health is an absence question.** A crashed run leaves its `ingest_runs` row at
`running` forever, and a missed run leaves no row at all, so "did a run fail"
cannot be answered from rows. "When did a nightly last complete" can: it covers
failed, crashed and never-started alike. So the verdict leads, and the run table
(last 5 runs), report count, newest report and recent reject reasons are
secondary.

**Quiet when healthy:** one green line ("Ingest OK, last nightly completed N
hours ago"); a red STALE line, or "no nightly run has ever completed", otherwise.
Colour reinforces the words OK and STALE and does not carry the meaning alone.
Finished times are shown in Denver (the first draft showed UTC).

**It mirrors `scripts/status.sh`; it does not reuse it.** `ingest.py` holds its
own copies of the four queries (runs, data, rejects, nightly). The verdict is
computed in Python (`age < stale_after`), where `status.sh` does it in SQL, and
`status.sh`'s data query also returns `newest_utc`, which the page leaves out.
If one changes, the other must, or the page and the operator script disagree.

**The 30-hour figure lives in three places, and nothing makes them agree:**
`INGEST_STALE_AFTER` (`tuning.py`), `STALE_HOURS` (`scripts/status.sh`) and
`DEFAULT_HOURS` in `scripts/iem_ingest.py` (a lookback: 30 hours, so each night
overlaps the previous run by 6).

Item 1's role question is answered for ingest health only. `api_pulls` and
`api_call_log` have the same unanswered question and are not covered.

## 2026-10-01 — Matched-export latency: two fixes, and two theories that were wrong (item 49)

`a2afa9c`. Item 49's reopen conditions were more than about 2 seconds, or more
than about 10,000 rows. Measured through `exports.count_matches` and
`fetch_matches` as `hail_app` (all types, 5.0 miles, DNC excluded, warm):

| Range | Rows | Count before | Count after | Fetch before | Fetch after |
|---|---|---|---|---|---|
| 30 days | 1,126 | 0.08 s | 0.045 s | 0.06 s | 0.05 s |
| 90 days | 11,868 | 0.57 s | 0.29 s | 0.62 s | 0.39–0.45 s |
| 400 days | 29,870 | 2.51 s | 0.72 s | 2.89 s | 1.37–1.43 s |

The "before" column is item 49's own 2026-09-30 table. One 400-day count run
read 1.88 s against 0.72 s for the others; not explained. The match page query
at 400 days went from 1.67 s to 0.69 s.

**The two changes:**

- `_DNC_FILTER` is now `NOT EXISTS (SELECT 1 FROM dnc_list ...)` instead of
  `da.dnc_id IS NULL`. `dnc_id` is a primary key, so the planner estimates
  `IS NULL` on it as matching almost nothing, which collapses the join estimate
  to one row. (That is an inference; the plan agrees with it, I did not prove
  the mechanism.) `da` and `do_` stay joined for the flag columns.
- `m.radius_used = %(radius_miles)s::numeric` in `exports.py` and `matches.py`.
  The app passes the radius as a Python float (`fetch_settings` casts it, to
  avoid `Decimal * float`), and `numeric = float8` makes Postgres cast the
  *column*, which discards its statistics: estimated 487 rows against 97,370
  actual, so nested loops.

**Neither is enough alone through the app.** New filter with a float radius
2.41–2.49 s (no gain); cast alone about 1.49 s; both 0.72–0.76 s. A float
round-trips exactly for every `NUMERIC(4,1)` radius from 0.1 to 10.0. In psql,
with a literal `5.0` (which is numeric), `NOT EXISTS` alone looked like the fix
(1.65 s to 0.84–0.94 s), because a literal hid the float problem. Test through
the app's own bound parameters.

**Two false starts, recorded because both were plausible:**

1. The `OR`-against-a-parameter theory (`NOT dnc_exclude OR ...`). Removing the
   OR changed nothing; 1,872 ms to 1,680 ms was cache warming.
2. Stale statistics. `pg_stat_user_tables` showed `dnc_list`, `realtors`,
   `listings` and `storm_listing_matches` all analyzed 2026-10-01 20:28 with
   `n_mod_since_analyze = 0`. `ANALYZE` was a no-op (1,629–1,652 ms against a
   1,680 ms baseline) and the estimate stayed at one row.

**Declined:**

- `work_mem`. With both fixes: 4 MB 710–790 ms (spills 6.7 MB), 32 MB 615–670 ms,
  64 MB 615–650 ms, 128 MB 630 ms then 1,776 and 1,836 ms on repeat runs,
  unexplained and reason enough not to go higher. About 70–100 ms (10%) for a
  per-sort, per-connection setting. `SET LOCAL work_mem = '32MB'` inside the
  export transaction remains available if that ever matters.
- A count-only variant (distinct on `storm_date`, `report_text`, `listing_id`):
  about 375 ms, same 29,870. `MATCHES_COUNT_SQL` derives the count from the
  projection precisely so the number beside Apply cannot disagree with the file,
  and the shortcut relies on every other column being determined by the listing.

**Equivalence checked:** old and new SQL returned identical rows for the 400-day
and 30-day windows, all types and HAIL, DNC excluded and included, plus the
count, the match page query and the realtors list.

Item 49: the time trigger is cleared. The row trigger is not (11,868 rows at 90
days, 29,870 at 400), so it resolves with residuals: the row ceiling (about
50,000) and server-side-cursor streaming remain the next options.

## 2026-10-01 — A dead branch removed from `_MATCH_SQL` (item 91)

`eacefc1`. `%(report_text)s::text IS NULL OR i.report_text = ...` is now
`i.report_text = %(report_text)s`. The NULL arm could not run:
`match_storm` requires `report_text`, it inserts a `match_runs` row first, and
`match_runs.report_text` is `NOT NULL` (`sql/022`), so a NULL would fail there
before `_MATCH_SQL` executed. All three callers pass a non-empty type (`/match`
returns 400 on a blank one, the pull thread, `scripts/test_match.py`). An
all-types pull dies earlier still, at the `api_pulls` `storm_link_paired` CHECK
(item 117).

The same construct is **correct and retained** where the filter is genuinely
optional: the storm browser, the exports and the match page, where "All" is a
real choice.

## 2026-10-01 — App-wide login hook, default-deny (item 90)

Deferred 2026-09-23 in favour of per-route decorators. Its trigger, a new route
outside the admin blueprint, fired on 2026-10-01 with `search.py`. `605f0b5`.

- `require_login` is registered after `load_current_user`, in `create_app`.
  **The order is load-bearing.** Reversed (tested), every protected route
  raises `AttributeError: user` (500) because `g.user` does not exist yet.
  `/login` and `/static` still work, since public endpoints return before
  reading `g.user`.
- The whitelist is **endpoint names**, `main.login` and `static`, not URL
  prefixes: a prefix silently exempts anything later added under it. The first
  version also listed `main.logout`; removed, because an anonymous logout is
  redirected to `/login` either way.
- `request.endpoint is None` passes through, so Flask's own 404 handles an
  unmatched URL.
- No live gap existed: every route already had a decorator. This closes a
  fragility (a forgotten decorator on a future route), not a hole.
- The decorators stay, as defence in depth.

**Verified** by requesting every rule in the URL map anonymously: all 16 GET
routes redirect to `/login` except `/login` and static; all 16 POST routes
redirect (with CSRF disabled for the test; with it on they return 400 first,
because `CSRFProtect` is registered earlier, so CSRF is the outer layer). A
signed-in admin gets 200 on `/`, `/search/` and `/admin/`. That sweep covers
every existing route, not a hypothetical new undecorated one.

## 2026-10-01 — "Matched, none in range": verified by reading, not by test (item 89)

`workstate._label()` checks in this order: pulling, sent, **matched**, then
`match_ran and pulled` ("Matched, none in range"), pulled, not pulled. `matched`
reflects rows in `storm_listing_matches`, not `matches_created`. So a re-run
that creates 0 new rows still reads "Matched, not sent".

Checked 2026-10-01: 5 of the 37 `match_runs` completed with
`matches_created = 0` (runs 1, 2, 3, 6 and 14), and every one is on a storm that
has rows in `storm_listing_matches`. None produced a wrong badge.

The "none in range" badge itself has never been seen on a live storm: it needs a
storm that was pulled and has no listings within the radius, which costs a
RentCast pull for a cosmetic check. Item 89 closes on the reading, not on a
sighting.

## 2026-10-01 — Match page: the CSV download link also at the top (item 149)

The "Download CSV" link was at the bottom of the matched-listings page
(`28a0195`, 2026-09-24); `958cd67` adds it beside the listing and agent counts at
the top. Both are `/storms/matches.csv?{{ request.query_string.decode() }}`: the
query string has to travel with the link wherever it appears, so the download
matches the filters on screen.

## 2026-10-01 — Header greeting by first name: declined (item 150)

A greeting like "Signed in as Justyn" would need `emp_fname` in the session.
Widening the session for a display string was judged not worth it. The header
keeps `session.user_name`. **Declined, not deferred.**

## 2026-10-01 — Export filenames and report-type validation; a gap in how review findings were filed (items 151 to 153)

**The problem.** `report_text` reached `Content-Disposition` from the query
string with only `/` and space replaced, so a double quote would close the
quoted filename early. Three routes built a label this way (`/export.csv`,
`/storms/matches.csv`, `/exports/matches.csv`).

**The fix, `a4376cc`.** `_filename_label()` keeps only `[A-Za-z0-9_-]`; and
`_export_report_type()` validates `?type=` against `storms.fetch_report_types`
and returns 400 for anything else, so an unknown type is refused instead of
producing an empty file that looks like "no storms". Effects: `SNOW/ICE DMG`
labels as `SNOW_ICE_DMG` (was `SNOW-ICE_DMG`); types the dropdowns do not offer
(`FLOOD`, `RAIN`) are refused on these three routes; `search.py` falls back to
"All" instead, being a form, not a download. Tested with valid, empty, unknown
and quote-injection values on all three routes.

**Not changed.** `scripts/export_storm_zips.py:116` still uses the old
replace-only label; its input is a command-line argument, not the web. The DNC
upload's filename is stored as given (`dnc_import_batches.filename`, the
`dnc_list` reason string, and a flash message); admin-only, and Jinja escapes
the flash.

**The process finding.** This and two other review findings (CSV formula
injection, and agent contacts visible to viewers) were identified in review and
never filed as parking-lot items. The pass-1 audit checked whether *filed* items
were still open, not whether *identified* problems had ever been filed. The two
others are now filed:

- **Item 152, CSV formula injection.** `_csv_response` uses `csv.writer`, which
  quotes but does not neutralise a cell starting with `=`, `+`, `-` or `@`. None
  of the text columns exported today begins with one (checked 2026-10-01:
  `agent_name`, `agent_office_name`, `city`, `property_address`, `agent_phone`,
  `agent_email`, `list_mls_number`, 0 rows each), so there is no live hit; the gap
  is in the code.
- **Item 153, agent contacts visible to viewers.** `/storms/matches` shows each
  agent's email and phone, and the two match CSVs include them; all are
  `login_required` only. That was deliberate (decision log 2026-09-24, "Who can
  download what", matching Phase 4's done-when that a viewer can browse and
  export); only the realtor CSV is sender/admin. Filed as an open question
  because it was never revisited.

## 2026-10-01 — CSV formula injection fixed (item 152), and a correction to the earlier finding

`8ae4b7c`. A spreadsheet may evaluate a cell that starts with `=`, `+`, `-` or
`@` as a formula. `csv_safe()` in `hailsys/formatting.py` prefixes an apostrophe
to such a *string*; numbers, dates and `None` pass through, so columns keep their
types. Excel does not display the apostrophe.

**One path, not two that agree.** `_csv_response` applies it, and `/export.csv`,
which had its own writer and its own `Response`, now goes through
`_csv_response` too (its rows are dicts like the others, so nothing blocked it;
its output is byte-identical to the old hand-built file). So all four web CSVs
share one path. `scripts/export_storm_zips.py` applies it as well.

**Correction.** The 2026-10-01 entry "Export filenames and report-type
validation" and item 152 said no exported text column begins with one of those
characters. That was true of the columns the web match exports write
(`agent_name`, `agent_office_name`, `city`, `property_address`, `agent_phone`,
`agent_email`, `list_mls_number`: 0 rows each) and wrong for the command-line
pairs export, which also writes `iem_data.remark`: 31 values start with `+`, `-` or
`@` (`+SN AT OBSERVATION.`, `@ NWS BOULDER OFFICE.`), and a spreadsheet would turn
`+SN AT OBSERVATION.` into a formula error.

**Limits, accepted:**

- Tab and carriage return, which OWASP also lists as leading characters, are not
  covered; adding them is one line in `_FORMULA_PREFIXES`.
- A phone written `+1 303 ...` would become `'+1 303 ...` in the file. None exist
  today (0 rows).
- Only for files a person opens. A CSV loaded back into the database would store
  the apostrophe. The script's own comment says its output was once `\copy`'d, so
  that comment now warns against loading it back.

**Process note.** `scripts/` is baked into the `app` and `web` images; `web`
mounts only `./hailsys`, so its `scripts/` copy was from Sep 17 and the first
script test ran the old code. The CLI change needs an image rebuild or a
`scripts/` mount to take effect in a container (`docs/command-ref.md`).

Tested: unit cases, all four routes (200, same content type and filenames), a
hostile row pushed through the real `/export.csv` route, and the script on
2019-11-22 (7 remarks prefixed, none left bare).
