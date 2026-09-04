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

