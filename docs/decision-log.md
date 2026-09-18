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
