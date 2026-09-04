# Database Schema

Storm outreach system — reference document for the data model.
Written during planning, before any tables exist. Update as decisions change.

---

## How the database works

The system has two halves that never touch each other directly.

**The weather half** is free and automatic. A nightly job pulls Local Storm
Reports from the Iowa Environmental Mesonet, stores them at full lat/lon
fidelity, and does nothing else. It runs whether or not anyone is watching.

**The property half** costs money and only runs when a person asks for it.
Someone browses the storm history, decides an area is worth working, and
triggers a RentCast pull for listings in the affected zip codes.

**The two halves meet in exactly one table:** `storm_listing_matches`. That
table records "this listing was within N miles of that storm report," and it is
what every outbound email hangs off.

The flow, end to end:

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

### Principles the schema follows

**Store the finest granularity you receive.** Storm reports keep their exact
coordinates rather than being collapsed to zip codes at write time. The buffer
radius is a tuning parameter that will change; storing derived zips would mean
re-ingesting history every time it does.

**Anything describing a past event stores its own copy of the facts.** The send
log records the email address actually used, not a pointer to the agent's
current address. The listing records the agent as RentCast reported them at the
time. Entities change; history must not.

**Rules that must hold live in the database, not the interface.** The
suppression check, the uniqueness constraints, and the frequency cap are
enforced where the data is. The UI makes rules visible; the database makes them
true.

**Nothing is deleted.** Suppressions are marked removed, users are deactivated,
templates are superseded. Every audit column points at a row that must still
exist.

**Nothing sends automatically.** The database supports a human clicking send.
There is no path from the nightly ingest to an outbound email.

---

## Key strategy — ours versus theirs

Three kinds of identifier appear in this schema, and mixing them up is the
easiest way to get in trouble.

### Third-party keys

| Key | Source | Stability |
|---|---|---|
| `rentcast_id` | RentCast | Derived from the address string. Stable in practice, but a formatting change upstream (`Hargis St` → `Hargis Street`, a unit number appearing) mints a *new* id for the same physical house. |
| `zcta5` | US Census | Very stable. Redrawn only after a decennial census. |
| `mls_number` | Regional MLS | Stable per listing, but not unique across different MLS organizations. |
| `provider_message_id` | Email provider | Opaque. Only meaningful for matching bounce notifications back to a send. |

We accept these as given. We do not attempt to correct or normalize them.

### Our own surrogate keys

Every table we control uses a system-generated `BIGINT` primary key:
`iem_id`, `listing_id`, `realtor_id`, `match_id`, `send_id`, `template_id`,
`emp_id`, `pull_id`, `api_log_id`, `dnc_id`.

Reasons: they are single-column, so foreign keys stay simple; they never change,
so history stays intact; and they do not depend on any third party's formatting
decisions.

### Natural keys, enforced as unique constraints

Surrogate keys do not prevent duplicates on their own. Each table that ingests
external data also carries a unique constraint on its natural key:

| Table | Natural key | Why |
|---|---|---|
| `iem_data` | (utc_datetime, latitude, longitude, report_text, magnitude) | Makes the overlapping nightly ingest window idempotent |
| `listings` | (rentcast_id, list_date) | One row per time a house was listed |
| `realtors` | email_norm | Email is the only identifier we have for a person |
| `dnc_list` | email_norm | Suppression is per address |
| `storm_listing_matches` | (iem_id, listing_id, radius_used) | Lets the same pairing exist at multiple radii |
| `report_types` | (report_type, report_text) | The code alone is not unique — `R` is both RAIN and HEAVY RAIN |

### The one deliberate denormalization

`send_log.realtor_id` is reachable by walking
`match_id → listing_id → realtor_id`. It is stored directly anyway, because the
frequency-cap check runs on every single send and a two-hop join for that query
is not worth the normalization purity. The write path is responsible for
setting it correctly.

---
# Tables

Seventeen tables. Grouped by which half of the system they belong to.

---

## Weather side

### `report_types`

Reference data. 37 rows, one per distinct storm report type. Effectively static —
changes only if the NWS introduces a new type.

Holds the *meaning* of a report, separate from the reports themselves: what a
magnitude value is measured in, and whether the type is worth acting on.

| Field | Purpose |
|---|---|
| `report_type` | One-character IEM code. **Not unique on its own** — part of the composite PK |
| `report_text` | Textual type, e.g. `HAIL`, `NON-TSTM WND GST`. Other half of the PK |
| `mag_unit` | `inches`, `mph`, or `none`. **Nullable** — NULL where `unit_confidence` is `unknown`, which is a different statement from `none`. `none` means the type has no magnitude; NULL means we do not know what the magnitude is measured in. 7 of 37 rows are NULL |
| `unit_confidence` | `certain` / `inferred` / `unknown`. Derived from the type name only — inferring units from value ranges produced wrong answers (tornado EF numbers read as inches, heat index as mph) |
| `roof_relevant` | Whether this type triggers outreach. **The reason this table exists** — adding wildfire later is an `UPDATE`, not a deploy |
| `min_magnitude` | `NUMERIC(6,2)`, nullable. Outreach floor for this type; NULL means no floor. Same scale as `iem_data.magnitude`, so comparison needs no cast. `CHECK` blocks a floor on a type whose `mag_unit` is not `inches` or `mph` — a threshold on `TSTM WND DMG` has nothing to compare against and would silently exclude everything |

**The NULL-magnitude semantic, which the DDL cannot show.** When
`min_magnitude` is set and a report's `magnitude` is NULL,
`magnitude >= min_magnitude` evaluates to UNKNOWN — not TRUE — so the report is
**excluded**. For a wind gust with no recorded speed that is the right answer: it
cannot be assessed against a threshold. But it has a consequence worth stating
plainly: **a type cannot have both a floor and an include-the-unmeasured
behaviour.** Choosing a floor is also choosing to drop that type's unmeasured
reports.

Referenced by `iem_data` and optionally by `email_templates`.

---

### `report_sources`

Reference data. 36 rows observed in ten years of Colorado. What a reporting
source is and how much its reports should be trusted.

| Field | Purpose |
|---|---|
| `source` | PK. The normalized source string, `upper(trim(...))`. `CHECK (source = upper(trim(source)))` |
| `display_name` | Human label for the UI, e.g. `Trained Spotter` |
| `confidence_tier` | `high` / `moderate` / `low` / `unrated`. `NOT NULL DEFAULT 'unrated'` so a newly discovered source can be inserted before anyone has judged it |
| `is_automated` | Whether the source is an instrument rather than a person. Nullable — unknown for a source nobody has classified |
| `notes` | Free text |
| `added_at`, `added_by`, `removed_at`, `removed_by` | Audit and retirement, same removal-pair CHECK as `coverage_zips` and `dnc_list` |

**There is deliberately no foreign key from `iem_data`.** `report_source` is free
text typed at individual NWS offices. The archive contains `DEPARTMENT OF HIG`
and `DEPT OF` — truncated mid-word, one report each. An FK would have failed the
nightly ingest on those rows and on every future variant, which is the one thing
the ingest must never do.

`report_types` can carry an FK because 37 NWS types are a closed set. Sources are
not, and never will be.

So this is modelled the way `zcta_boundaries` is: a lookup you `LEFT JOIN` when
you need it, never a constraint that can break a write. An unrecognized source
yields a NULL tier and the UI shows "unrated" rather than dropping the report.

**The join is `iem_data.report_source_norm = report_sources.source`, never the
raw `report_source`.** Both sides are `upper(trim(...))`, and the CHECK on
`source` is what guarantees the right-hand side stays that way. The failure this
prevents is the same class as an email-normalization mismatch: the join silently
returns nothing and no error is raised.

**This table is what supplies the confidence signal.** `report_qualifier` is
explicitly not that signal — `M` tracks reporter training, not instrumentation.
The 36 rows carry the distinction that actually matters: `ASOS` and `AWOS` are
instruments, `PUBLIC` is a stranger on a phone, `TRAINED SPOTTER` is someone who
took the class.

The statistics that informed the tiers — report counts, per-qualifier
breakdowns, hail measured-rates — live in `reference/sources.csv` and
`reference/data_quality_notes.md`, deliberately not here. They describe one
ten-year extract and go stale the moment the nightly job runs. The durable fact
is the judgment, not the evidence for it.

---

### `iem_data`

Storm reports. One row per NWS Local Storm Report. ~135k rows for ten years of
Colorado, growing by a handful daily. The only table fed by an automatic job.

| Field | Purpose |
|---|---|
| `iem_id` | Surrogate PK |
| `utc_datetime` | Report timestamp. **UTC, always.** Converted to `America/Denver` at display only — a Front Range evening storm crosses midnight UTC and will split across two calendar days if grouped naively |
| `latitude`, `longitude` | `NUMERIC(9,6)`. Two decimals of real precision from IEM (~1km) |
| `geom` | `GEOMETRY(Point, 4326)` generated from lat/lon. **GiST indexed** — this index is the entire performance story for the buffer query |
| `magnitude` | `NUMERIC(6,2)` NULL. Meaning depends on type — join `report_types` to interpret. **IEM sends the literal string `None` as its null marker**; coercing it to 0 produces 549 magnitude-zero tornadoes |
| `report_type`, `report_text` | Composite FK → `report_types` |
| `nws_issuer` | Forecast office. Colorado: `BOU`, `PUB`, `GJT`, plus `GLD` and `CYS` on the borders |
| `report_source` | Free text, entered by the reporting office. Inconsistent case |
| `report_source_norm` | Uppercased/trimmed. Match on this, never on the raw field |
| `report_qualifier` | `M`/`E`/`U`. **Does not mean what it says for hail** — tracks reporter training, not instrument measurement. Both M and E values land on the same coin/ball catalog |
| `county`, `state` | As reported |
| `nws_geo_code` | UGC, e.g. `COC081` = CO + county-type + FIPS 081. Free county crosswalk. **Null before mid-2022** |
| `nws_geo_name` | IEM's `UGCNAME` — the county name that goes with the UGC. Null on the same rows |
| `remark` | Free text. On damage reports with no magnitude, this is where the content is |
| `ingested_at` | When we pulled it |

Unique on `(utc_datetime, latitude, longitude, report_text, magnitude)`,
declared `UNIQUE NULLS NOT DISTINCT`. This is what makes the nightly job safe: it
pulls a 30-hour overlapping window and lets `ON CONFLICT DO NOTHING` discard
repeats, so a failed night self-heals rather than leaving a permanent hole.

**`NULLS NOT DISTINCT` is load-bearing.** By default SQL treats two nulls as
distinct, so two rows identical in every column but with null `magnitude` would
both insert — and null magnitude is exactly the IEM `None` trap, 3,353 of 135,856
rows. Without it the overlapping window duplicates those rows every single night,
and a duplicated `iem_data` row multiplies into duplicate matches and duplicate
sends. Requires PostgreSQL 15+.

Never trimmed. 135k rows is small for Postgres; deleting old data would cost the
ability to answer "when did this zip last get hit" and to replay history at a
different radius.

---

## Reference Data

### `zcta_boundaries`

Zip code shapes from Census TIGER/Line. ~33,000 rows nationwide, a few hundred MB.
Loaded once, never written to.

| Field | Purpose |
|---|---|
| `zcta5` | 5-digit ZCTA code, PK |
| `geom` | `GEOMETRY(MultiPolygon, 4326)`. The outline — often thousands of coordinate pairs per zip. **Two GiST indexes**: `zcta_boundaries_geom_gix` on `geom` for geometry predicates, and `zcta_boundaries_geog_gix` on `(geom::geography)` for distance-in-metres — see below |
| `centroid` | Generated: `ST_PointOnSurface(geom)`. Verified: 0 of 33,791 fall outside their own polygon. `ST_PointOnSurface` rather than `ST_Centroid` because the centroid of a C-shaped or donut-shaped ZCTA can land outside the polygon |
| `land_area` | From Census attributes |

**No foreign keys.** Joined spatially, not relationally. Nothing in the database
enforces a relationship between a storm report and a zip — that is computed on
every query.

Appears in exactly one operation: turning a storm report point into a list of
zips to hand to RentCast. Everything downstream works with zip strings.

**The buffer query needs the geography index, not the geometry one.** The query
is written in metres, so it casts:

```sql
ST_DWithin(geom::geography, point::geography, 8046.72)   -- 5 miles
```

That cast is evaluated per row, so it **cannot** use a plain index on `geom`.
Measured on the full 33,791-row national table: parallel sequential scan,
**17.9 seconds**. With a GiST index on the cast expression itself, the same query
plans as a bitmap index scan and runs in **22 ms** — an 855× difference, and the
index takes 10 seconds to build.

Both indexes are kept because they serve different predicates: `geom_gix` for
`ST_Intersects` / `ST_Contains`, `geog_gix` for distance in metres. The
alternative — a degrees-based `ST_DWithin` on raw geometry — does use `geom_gix`
but is wrong in a way that hides: a degree of longitude is 85.6 km at the Front
Range and 111 km at the equator, so a fixed degree radius silently changes size
with latitude.

Loaded in EPSG 4326 to match IEM and RentCast coordinates. Mixing coordinate
systems fails silently — the join runs, returns too few rows, and never errors.

ZCTAs are the Census approximation of USPS zip codes. They do not match exactly
at the edges, and some PO-box-only zips have no ZCTA. Fine for finding storm
areas; not authoritative for mail.


### `coverage_zips`

RBI's service territory. The list of ZCTAs worth spending a RentCast call on.
183 rows. Reference data, but unlike `zcta_boundaries` it is ours and it will
be edited.

Derived from a one-hour drive time from the Fraser Ave office, extended down
I-25 to Colorado Springs and north to the Fort Collins/Wellington line.

| Field | Purpose |
|---|---|
| `zcta5` | PK. FK → `zcta_boundaries`. **A ZCTA, not a USPS zip** — see below |
| `area_name` | Human label, e.g. `Peyton/Falcon`, `Windsor`. What the territory is called out loud |
| `reason` | Free text. Why this is in scope. **The field that will be empty in six months if it is not filled in now** |
| `added_at`, `added_by` | FK → `users` |
| `removed_at`, `removed_by` | **Nullable.** Populated only if territory is dropped |

`CHECK ((removed_at IS NULL) = (removed_by IS NULL))` — the two removal columns
move together. A removal date with no author is a half-written row, and there is
no reason the database should accept one. `dnc_list` wants the same constraint
on its own removal pair.

**This is not a link in the storm → match chain.** There is no relationship
between `coverage_zips` and `iem_data`, in either direction. A storm reaches
coverage only by passing through geometry, and only at read time:

```
iem_data ──spatial──► zcta_boundaries ──equality──► coverage_zips
```

The first hop is `ST_DWithin`; the second is an ordinary join on the zip string.
Coverage constrains which zips leave that query. It does not constrain which
storms enter the system.

**Ingest stays unfiltered.** The nightly IEM job does not consult this table.
Same argument as storing full lat/lon rather than derived zips: territory is a
tuning parameter. If ingest dropped out-of-area reports, taking on Pueblo next
spring would leave a permanent hole in history, fillable only by re-ingesting
from an archive that may not cooperate.

**The enforcement point is the RentCast pull.** That is the only place money is
spent, so that is where the rule has to hold — not in the UI having filtered the
zip list. Secondarily it is a browse default ("storms that touched our
territory"), but that one is a filter the user can switch off, not a gate.

**Why `zcta5` and not `zip_code`.** These have to be ZCTAs; a zip with no polygon
cannot be reached through the spatial join and cannot be checked against
territory. Naming the column `zip_code` invites someone to insert a plausible
USPS zip that matches nothing. The name carries the constraint.

**Why the FK to `zcta_boundaries`.** The first hand-built version of this list was
193 entries, of which 10 had no ZCTA polygon — PO-box-only zips (`80502`, `80522`,
`80539`, `80632`, `80638`, `80901`), institutional zips (`80225` Federal Center,
`80523` CSU, `80639` UNC), and one (`80213`) that is not an assigned zip at all.
5%, and it took a purpose-written script against the raw TIGER `.dbf` to find
them. The FK makes that row uninsertable rather than periodically re-detected,
and it means the rule survives whoever expands the territory later without
having read this paragraph.

Cost: TIGER must load before coverage, and a decennial boundary revision that
retires a ZCTA will block the reload until it is reconciled by hand. That is a
loud, attended event roughly once a decade, traded against a silent hazard that
is otherwise always on.

Note that dropping those 10 lost no geographic coverage — every one sits inside a
city already covered by its residential ZCTAs.

**This table breaks the surrogate-key convention deliberately.** Every other table
we control uses a generated `BIGINT`. This one is keyed on a third party's
identifier because the whole point of the row is to name a Census polygon, and a
surrogate would add a join without adding stability — `zcta5` is the most stable
third-party key in the schema, redrawn only after a decennial census.

**Nothing is deleted.** Dropping territory sets `removed_at` and `removed_by`.
Every query that reads this table filters on `removed_at IS NULL`. Retaining
removed rows is what makes "we stopped working Greeley in March" answerable.

**What this table does not answer:** which ZCTAs fall inside the drive-time area
but were never added. That is the question that finds missing territory rather
than dead entries, and it is a genuine spatial query — buffer the office point,
intersect `zcta_boundaries`, anti-join `coverage_zips`. Worth running once TIGER
is loaded, to catch anything eyeballing a map missed.


---

## Property side

### `properties`

One row per physical house.

The test for whether a field belongs here: **would it still be true in five
years, when the house is listed by a different agent at a different price?**
Year built yes, price no.

| Field | Purpose |
|---|---|
| `rentcast_id` | PK, from RentCast. Derived from the address string |
| `property_address` | Full formatted address |
| `address_1`, `address_2` | Street, unit |
| `city`, `state`, `zip_code`, `county` | Address components |
| `state_fips`, `county_fips` | Census codes. **Join key to Census data** — the same crosswalk `iem_data.nws_geo_code` gives you on the weather side |
| `list_latitude`, `list_longitude` | Property coordinates. Used for point-to-point distance in matching |
| `property_type` | Single Family, Condo, etc. Filter for excluding multifamily |
| `bedrooms`, `bathrooms`, `square_footage`, `lot_size`, `year_built`, `hoa_dues` | Structural attributes |
| `created_date` | When *RentCast* first saw the property |
| `first_seen_at` | When *we* ingested it. A different fact — since pulls are user-initiated, the gap can be weeks. The same distinction is drawn on `listings` |

**Known weakness:** because the key comes from the address string, an upstream
formatting change mints a new id for the same building. Would produce two rows
for one house. Not worth solving now; a periodic lat/lon proximity check would
catch it later.

Has no storm awareness at all. It is a catalog of buildings.

---

### `listings`

One row per *time a house was for sale*. Many-to-one with `properties`.

| Field | Purpose |
|---|---|
| `listing_id` | Surrogate PK. Exists so downstream FKs are one column instead of two |
| `rentcast_id` | FK → `properties` |
| `list_date` | Most recent list date. Second half of the natural key |
| `list_status` | `Active` or `Inactive` only |
| `list_price`, `list_type` | Price; Standard / New Construction / Foreclosure / Short Sale. **New Construction is not worth outreach** — a brand new roof is not a hail claim |
| `list_removed_date`, `list_last_seen`, `list_days_on_market` | Listing lifecycle |
| `list_mls_name`, `list_mls_number` | Identifies the *listing*, not the agent or office. Useful for looking a property up in REcolorado directly |
| `list_agent_name`, `list_agent_phone`, `list_agent_email` | **Snapshot** — who RentCast said listed it, at the time |
| `list_agent_email_norm` | `lower(trim(list_agent_email))`. **Not unique.** Many listings sharing one agent email is the normal case, not a duplicate — agent identity uniqueness lives on `realtors.email_norm`, not here |
| `list_office_name`, `list_office_phone`, `list_office_email`, `list_office_website` | Brokerage snapshot |
| `list_office_email_norm` | `lower(trim(list_office_email))`. Not unique, for the same reason as `realtors.office_email_norm` |
| `realtor_id` | FK → `realtors`. **Nullable** — some listings have no agent block |
| `raw_payload` | `JSONB` of the full RentCast response. Cheap insurance for fields not mapped today, including `history` |
| `first_seen_at` | When *we* ingested it |
| `created_date` | When *RentCast* first saw it. Different fact — since pulls are user-initiated, the gap can be weeks |

Unique on `(rentcast_id, list_date)`.

**The agent fields are duplicated here on purpose.** `list_agent_*` records what
was true at listing time; `realtors` is the resolved current understanding of
that person. Jen may be `jw@kw.com` today and `jennyw@gmail.com` next year — the
listing preserves who listed it under what name, the realtor row tracks her
going forward. Neither is redundant. Do not "normalize" this away.

---

### `realtors`

Resolved agent identities. Keyed on email.

Exists for exactly two reasons: contact frequency capping, and tying send
history to a person rather than to scattered listing rows.

| Field | Purpose |
|---|---|
| `realtor_id` | Surrogate PK |
| `agent_name` | As received. Single display string — MLS feeds do not split first/last |
| `agent_email` | **Nullable.** Some feeds give a name and phone but no email |
| `email_norm` | Lowercased/trimmed. UNIQUE where not null. Match on this |
| `agent_phone` | 10 digits, unformatted |
| `agent_office_name`, `agent_office_phone`, `agent_office_email` | Brokerage |
| `office_email_norm` | `lower(trim(agent_office_email))`. **Deliberately not unique** — one brokerage address is shared by every agent in the office. Exists to pre-flight a batch against `dnc_list`; see open question 9 |
| `first_seen_at`, `last_seen_at` | Most recent listing they appeared on |

**Rows are created during a pull:** if the incoming normalized email is not
present, insert; if it is, link and update `last_seen_at`. Email is the only
matching rule.

**Deduplication is deliberately not attempted.** Jen Watson may appear five
times under five addresses. That is acceptable. Merging on name similarity risks
linking a live agent to a suppressed one — over-emailing is a recoverable
annoyance, wrongly silencing a working agent is invisible and permanent.

**No suppression flag lives here.** See below.

---

### `dnc_list`

The suppression list. Answers one question: *may we send to this address?*

| Field | Purpose |
|---|---|
| `dnc_id` | Surrogate PK |
| `email_raw` | As received |
| `email_norm` | Lowercased/trimmed. **UNIQUE. This is the enforcement key** |
| `added_at`, `added_by` | When, and the `emp_id` of who suppressed it — the system account's `emp_id` for machine-initiated rows (bounces, complaints, the legacy import) |
| `source` | reply / unsubscribe / hard bounce / complaint / manual. **Bounces mean bad data; complaints mean bad targeting or copy.** Count them separately when watching deliverability |
| `reason` | Free text |
| `realtor_id` | FK, nullable. Convenience only, never a requirement |
| `name_at_add`, `phone_at_add` | For the best-effort second-address pass — **feeds human review, never automatic enforcement** |
| `removed_at`, `removed_by` | **Nullable.** Populated only if a suppression is reversed |

**Why this is a table and not a flag on `realtors`:** if Jen exists five times
under five emails, marking one row leaves four sendable. She asked not to be
contacted at an *address*. Keying on email also allows suppressing addresses
never seen as a realtor — a bounce, a forwarded complaint, a phone call to the
office.

**Every normalized email column in the schema is `lower(trim(...))`.** Not
`upper`, not `trim` alone, in any table: `realtors.email_norm`,
`realtors.office_email_norm`, `listings.list_agent_email_norm`,
`listings.list_office_email_norm`, and `dnc_list.email_norm`. This is a
correctness requirement, not a style preference. The suppression check compares
`dnc_list.email_norm` against the normalized address being sent to; if two
tables normalize with different case functions the comparison silently never
matches, and the failure mode is a suppressed agent receiving mail with no error
anywhere.

**The check runs at send time against this table. Always.** Not against a cached
flag, not against the UI having filtered the list. The dangerous version is
where the interface hides suppressed agents and everyone assumes that is the
protection — then a bug, a stale page, or a quick script bypasses it.

Nothing is ever deleted. Un-suppressing sets `removed_at` and `removed_by`.

---

## The hinge

### `storm_listing_matches`

The join between the two halves. Nothing else connects them.

Records the finding: *this listing was within N miles of that storm report.*

| Field | Purpose |
|---|---|
| `match_id` | Surrogate PK |
| `iem_id` | FK → `iem_data` |
| `listing_id` | FK → `listings`. **Points at the listing, not the property** |
| `distance_miles` | `NUMERIC(6,2)`. Point to point, report to property |
| `radius_used` | The radius setting that produced this match |
| `matched_at` | When computed |

Unique on `(iem_id, listing_id, radius_used)`.

`CHECK (distance_miles <= radius_used)` — constraint `distance_within_radius`. A
match further away than the radius that produced it is arithmetically impossible,
so a row like that is a matcher bug rather than a finding. Catching it at write
time keeps a bad radius calculation from quietly widening targeting.

**Why it points at `listing_id` rather than `rentcast_id`:** a house listed in
2024, matched to hail, sold, and relisted in 2026 would otherwise carry its old
match forward — and you would email the *current* agent about a storm that hit
while a *different* agent had the listing. The match belongs to the sale
attempt, not the building.

**Why it is a table and not a query.** It is expensive to compute (spatial work
plus a paid API call), it is what `send_log` points at, and it is a historical
fact — the distance never changes, but the radius setting will.

**Why `radius_used` is in the unique constraint:** re-running at 8 miles after
previously running at 5 neither fails nor overwrites. Both results coexist and
can be compared, which is what radius tuning requires.

**Why distance is stored rather than recomputed:** it is a display and sorting
field. "1.2 miles from a 1.75″ hail report" is a stronger pitch than "somewhere
in a zip that had hail," and it lets the sender prioritize.

**Zips get you the listings; distance decides the match.** Zip membership is how
RentCast is queried. The match itself is point-to-point — a listing at the far
edge of a qualifying zip may be 9 miles from the report. Filtering on actual
distance is what keeps targeting honest.

Many-to-many in effect: one report matches many listings; one listing can match
several reports. A house hit by hail in May and wind in July has two match rows.
That is correct — two outreach opportunities with different pitches.

---

## Sending

### `send_log`

One row per email to one agent. Append-only except for provider status updates.

| Field | Purpose |
|---|---|
| `send_id` | Surrogate PK |
| `realtor_id` | FK → `realtors`. **Denormalized** — reachable via match, stored directly because the frequency cap queries it constantly |
| `recipient_email` | **Snapshot of the address actually used.** If she changes her address later, history still shows where the message really went |
| `match_id` | FK → `storm_listing_matches`. Carries full provenance: which storm, which listing, which pitch |
| `template_id` | FK → `email_templates` |
| `queued_at` | Row created, before the send attempt |
| `sent_at` | Nullable until the provider accepts it |
| `sent_by` | FK → `users` |
| `send_status` | `queued` → `sent` → (`bounced` \| `complained`), or `queued` → `failed`. Prefixed to match `list_status` and `api_status` |
| `provider_message_id` | The ESP's id. **How a bounce finds its way home** |
| `status_updated_at` | When a bounce or complaint arrived |
| `error_detail` | Provider error text |

`CHECK (send_status = 'queued' OR send_status = 'failed' OR sent_at IS NOT NULL)`
— constraint `sent_has_timestamp`. A row cannot claim `sent`, `bounced`, or
`complained` without recording when it was sent. Those three statuses are
assertions that a message left the building; without a timestamp the audit trail
says something happened but not when, which is not an audit trail.

Index on `(realtor_id, sent_at)` — exists purely for the frequency-cap lookup.

**The cap counts `queued` rows too.** A message in the queue is a message that
will arrive; if the check only counts `sent`, a large batch double-sends before
the first clears.

**Why `queued` exists:** slow warmup means throttled sending, and a crash
mid-batch must not leave uncertainty about what went out. The row exists before
the attempt, so there is always something to reconcile against.

Hard bounces and complaints write to `dnc_list` automatically.

**Append-only because it is the audit trail.** If anyone asks whether a
suppressed agent was emailed, the answer must come from an immutable record.

**Append-only is enforced in application code only.** There is no trigger, no
rule, and no `REVOKE` on this table or on `email_templates` — nothing in the DDL
prevents an `UPDATE` or `DELETE`. The convention is documented and followed, not
enforced where the data is, which is a departure from the principle stated at the
top of this file. Whether to enforce it in the database is open question 10.

---

### `email_templates`

Message text, versioned. Never edited in place.

| Field | Purpose |
|---|---|
| `template_id` | Surrogate PK |
| `template_name` | Internal label |
| `subject`, `body` | Text, containing merge fields |
| `report_type` | FK → `report_types`, nullable. Null = applies to any event type |
| `is_active` | Available for sending |
| `created_at`, `created_by` | FK → `users` |
| `supersedes_id` | Self-FK to the prior version |

**Editing in place is forbidden.** Changing a body would make every historical
send claim to have used text that did not exist at the time — the audit trail
becomes fiction. To change a template: insert a new row, set `supersedes_id`,
set the old row's `is_active` to false. Walking `supersedes_id` backward gives
the edit history for free.

`CHECK ((report_type IS NULL) = (report_text IS NULL))` — constraint
`report_type_pair_complete`. The two columns are both null or both set. This is
not redundant with the composite foreign key: Postgres foreign keys default to
`MATCH SIMPLE`, which **skips the check entirely when any column in the key is
null**. Without this constraint a half-set pair — a `report_type` with no
`report_text` — passes the FK unverified and points at nothing.

**Merge fields are required.** Minimum vocabulary: agent name, property address,
storm date, event type and magnitude, distance from report to property.

Rules:

1. **A render with a missing required field fails loudly and does not send.**
   "Hail of  inches was reported near " is worse than sending nothing.
2. A failed render writes a `failed` row to `send_log` with the reason, so a bad
   template is a visible error rather than a silent gap in a batch.
3. The substitution vocabulary is reference data, not a hardcoded list.
4. Templates are validated against the vocabulary **when saved**, so an unknown
   placeholder is caught by the person writing copy, not at 6am in a batch.

---

## Operations

### `users`

Logins. Four or five rows, probably ever.

| Field | Purpose |
|---|---|
| `emp_id` | Surrogate PK |
| `user_name` | UNIQUE, login |
| `emp_fname`, `emp_lname`, `emp_email` | Identity |
| `password_hash` | bcrypt or argon2. **Never anything reversible** — people reuse passwords, so a weak store is a liability to the user personally, not just to the system |
| `role` | `admin` / `sender` / `viewer` / `system` |
| `is_active` | Disable, never delete |
| `created_at`, `created_by` | Self-FK, nullable for the first account |
| `last_login_at` | |

**The roles map to the cost stages.** `viewer` browses storms and exports CSV —
free. `sender` triggers pulls and sends email — spends money and reputation.
`admin` adds and removes users **and nothing else**.

That last constraint is deliberate and needs enforcing in code, because "admin"
conventionally means "can do everything" and anyone picking this up later will
assume that unless it is explicit. An admin cannot touch templates, suppression,
or sending.

**`system` is the fourth role and is not a cost stage.** The other three describe
what a person is allowed to spend; this one describes rows no person created. It
is **not a login**. It exists to own machine-initiated writes — automatic
suppressions from bounces and complaints, and the legacy DNC import — so that
`added_by` and `created_by` can stay `NOT NULL BIGINT` foreign keys instead of
going nullable or accepting the free-text string `'system'`.

The account is bootstrapped by an `INSERT` at the end of `sql/002_users.sql` and
is constrained in the database rather than by convention:

```sql
CONSTRAINT system_account_cannot_log_in
    CHECK (role <> 'system' OR (is_active = FALSE AND password_hash = '!'))
```

It cannot be activated and cannot be given a real password hash. A machine
identity that can be turned into a working login is a backdoor with a name.

**`is_active` rather than deletion:** every `sent_by` and `created_by` points
here. Deleting someone who left breaks the audit trail on everything they did.

---

### `api_pulls`

One row per user-initiated RentCast pull.

| Field | Purpose |
|---|---|
| `pull_id` | Surrogate PK |
| `emp_id` | FK → `users`. Who chose to spend |
| `iem_id` | FK → `iem_data`, nullable. What storm they were working |
| `started_at`, `finished_at` | Null while running or if it died |
| `zip_count` | Zips in scope |
| `estimated_api_calls` | **What the UI showed before the user confirmed** |
| `actual_api_calls` | What it really cost |
| `listings_returned` | |
| `api_status` | running / complete / failed / cancelled |

Every other table records what the system *knows*. This records what it *spent*
— a different category, and the one that got the previous system into trouble
when call volume blew up with no record explaining where it went.

**Estimated versus actual side by side is the feedback loop.** The pre-click
estimate is a number shown to someone before they spend money; it should get
better over time rather than staying a guess.

**A pull is one human decision covering many zips**, so cost attributes to the
decision rather than smearing across zip rows. When a monthly ceiling is
eventually enforced, it is enforced here, against the estimate, *before* the
calls go out.

---

### `api_call_log`

One row per zip within a pull.

| Field | Purpose |
|---|---|
| `api_log_id` | Surrogate PK |
| `pull_id` | FK → `api_pulls` |
| `zip_code` | |
| `called_at` | |
| `calls_made` | Pages fetched for this zip |
| `listings_returned` | |
| `http_status` | So partial failures are diagnosable |

Index on `(zip_code, called_at DESC)` — powers the "this zip was pulled two days
ago, re-pulling probably returns the same listings" warning.

Per-zip granularity also shows which zips are expensive. A dense metro zip may
take six pages; a rural one takes one.

---

### `ingest_runs`

One row per execution of an IEM ingest script. One row a night, plus a handful
of backfill and replay rows — a few thousand rows after a decade.

| Field | Purpose |
|---|---|
| `run_id` | Surrogate PK |
| `run_mode` | `nightly` / `backfill` / `replay` |
| `window_start`, `window_end` | The UTC range actually requested |
| `started_at` | Set on insert, before any work |
| `finished_at` | Null while running |
| `rows_seen` | Rows the parser read from IEM |
| `rows_inserted` | Rows that survived the natural key |
| `rows_skipped` | Rows written to `iem_ingest_rejects` |
| `run_status` | running / complete / failed |
| `error_detail` | Why a `failed` run failed |

**This exists because `api_pulls` records spend, not ingest.** The two look
alike deliberately — a row is written *before* the attempt, so a process that
dies mid-run leaves something to reconcile against instead of nothing. But they
answer different questions. `api_pulls` asks what a person chose to spend;
`ingest_runs` asks whether the machine did its job.

**The alert that matters most is the absence of a row.** A nightly job that
never fires cannot report its own failure — there is no process to write a log
line, and nothing in journald distinguishes "ran and found nothing" from "never
ran." A table makes that a query: *is there a `complete` row whose window covers
last night?* That is the whole reason this is a table rather than log output.

**No `emp_id`.** Every other operational table records which human is
responsible. This one is system-initiated, and inventing a `system` actor for it
would imply a decision nobody made.

**Counts are nullable, not `DEFAULT 0`.** "Not yet counted" and "counted zero"
are different facts. A crashed run with zero-defaulted counts reads as a clean
ingest that happened to find no storms, which is exactly the failure this table
is meant to catch.

The nullability is load-bearing for the constraints, and depends on a detail of
SQL that is worth stating outright: **a CHECK rejects a row only on definite
false, and passes on unknown.** While a run is in progress the counts are NULL,
so `rows_inserted + rows_skipped <= rows_seen` evaluates to NULL and the row is
accepted. The same constraint bites once the counts are filled in. One
constraint covers both states, with no `run_status` term in it.

Four named constraints:

| Constraint | Holds that |
|---|---|
| `window_ordered` | `window_end > window_start` |
| `finished_has_timestamp` | Anything not `running` has a `finished_at` — `failed` included |
| `complete_has_counts` | A `complete` run reported all three counts |
| `counts_consistent` | `rows_inserted + rows_skipped <= rows_seen` |

`finished_has_timestamp` binding `failed` as well as `complete` is deliberate.
A run that stopped, stopped at a time, and a `failed` row with a null
`finished_at` would be indistinguishable from one still in flight — which
defeats the one question the table exists to answer. The consequence lands on
the error handler: the `UPDATE` that sets `run_status = 'failed'` must set
`finished_at` in the same statement, and that code path runs precisely when
something has already gone wrong. `error_detail` is deliberately *not* tied to
`run_status` by a constraint for the same reason — a CHECK firing there would
replace a diagnosable failure with an unlogged one.

`counts_consistent` is `<=`, not `=`, because the remainder is rows the
`iem_data` natural key discarded as duplicates — the expected majority on a
nightly run, since consecutive windows overlap. It catches a miscounting bug in
the script, the same way `distance_within_radius` catches a matcher bug.

**`rows_skipped > 0` is the alert condition, and the run still exits 0.**
systemd's job is to say whether the process ran; a unit parked in `failed`
because the NWS invented a report type would train everyone to ignore
`systemctl --failed`. Expect a non-zero value the first time a window covering
2018 is ingested — 76 archive rows carry an unquoted comma in `CITY`.

---

### `iem_ingest_rejects`

One row per input line the parser refused. Child of `ingest_runs`; the only
foreign key either table has.

| Field | Purpose |
|---|---|
| `reject_id` | Surrogate PK |
| `run_id` | FK → `ingest_runs`. Which run threw it out |
| `raw_row` | The input line, verbatim |
| `reason` | Closed enumeration, five values |
| `detail` | The specific instance |
| `rejected_at` | |

**Skipping a malformed row is only defensible because it is not lossy.**
`raw_row` holds the bytes as received, so anything dropped can be read,
replayed, or entered by hand later. Without that column this table would be a
record that something was discarded, which is worse than useless.

**`raw_row` is `TEXT`, not `JSONB`.** A row is in here precisely because it did
not parse, and the malformation that rejected it is often the same thing that
would make it invalid JSON. Storing the raw line keeps the problem visible in
the record instead of losing it to a second parse. This is the one place the
schema stores unstructured input on purpose; `listings.raw_payload` is `JSONB`
because that data arrived well-formed.

**`reason` is a closed `CHECK`, and that is the enforcement point for
skip-and-continue.** These five and only these five are survivable:

`field_count_mismatch` · `unknown_report_type` · `unparseable_timestamp` ·
`unparseable_coordinate` · `unparseable_magnitude`

Any other exception must terminate the run. A free-text column would let the
parser quietly grow a new tolerated failure every time it met something it did
not understand, which is how a skip-and-continue loop turns into silent data
loss. Adding a reason takes a migration and a human decision — deliberately.

**`detail` says which case, `reason` says which class.** Which type pair was
unknown, how many fields were found, what failed to parse. Without it,
diagnosing a reject means re-parsing `raw_row` by hand.

**No cascade on the foreign key.** Deleting an `ingest_runs` row that has
rejects attached fails, which is correct: neither table is ever pruned, and a
reject with no run to explain it is not worth keeping.

Index on `run_id`. `ingest_runs` deliberately has none — one row a night will
never justify one — but this table is one row per *rejected line*, and the first
backfill produces 76 in a single run. Postgres indexes the referenced side of a
foreign key, never the referencing side, so without this the query that actually
gets typed — "show me the rejects for run N" — has nothing to use. Same shape as
`api_call_log_pull_id_idx`.

---

# Open questions

Unresolved as of this writing. Each one is cheaper to settle now than after
there is data.

### 1. Is a "storm" a first-class entity?

The UI concept is *"Hail — August 24 — 14 neighborhoods."* That groups many
individual reports into one event. Currently that grouping is a query
(`GROUP BY date, report_text`), not a table.

A real `storm_events` table would let someone name an event, attach a campaign
to it, and report on it as a unit. `send_log` would reference the event as well
as the match. The cost is a clustering rule — what makes two reports part of the
same storm? Same day? Same day and type? Spatial proximity?

**Deferring is safe** as long as the query-based grouping is consistent. But if
it becomes a table later, existing matches have no event to belong to.

### 2. What is the default buffer radius, and where does it live?

Currently `radius_used` is recorded per match, but nothing states the default.
Options: a constant in config, a row in a settings table, or a per-user
preference. A settings table is the flexible answer and costs one more table.

Related and unanswered: **does the radius vary by event type?** Hail swaths and
straight-line wind damage do not have the same footprint.

**Note the asymmetry, which is not yet justified.** "Does this vary by event
type?" now has two instances, and they were answered differently.
`report_types.min_magnitude` gives the magnitude floor a per-type column. The
radius has no equivalent — `storm_listing_matches.radius_used` records what was
used, but nothing declares a per-type default. These are the same shape of
question, and one got a column while the other did not. Either the radius
belongs on `report_types` alongside `min_magnitude`, or `min_magnitude` belongs
wherever the radius default eventually lives. Recorded rather than resolved,
because the answer depends on question 3 — whether there is a settings table at
all.

### 3. Is there a settings table at all?

Radius default, frequency-cap window, monthly API ceiling, warmup send limit.
All of these are values that will be tuned. Right now none of them has a home.
Putting them in a table means changing them without a deploy — the same argument
that justified `roof_relevant`.

### 4. How are counties handled for browse-by-county?

Three sources of county exist: `iem_data.nws_geo_code` (UGC, null before mid-2022),
`iem_data.county` (free text), and `properties.county_fips` (Census). A crosswalk
table would reconcile them. Whether county *geometry* is also needed depends on
whether counties are ever drawn on a map.

### 5. Does the frequency cap have a hard floor?

Decided in principle: a short window nobody can click past, and a soft warning
above it. The actual numbers are unset, and the hard floor needs enforcing in the
database rather than the application.

### 6. Merge field vocabulary — where is it stored?

Established that it should be reference data rather than a hardcoded list. Not
yet designed. Likely a small table of placeholder name, source expression, and
whether it is required.

### 7. What happens to a listing that goes inactive after a match?

A match points at a listing that may since have sold. Does the UI still show it?
Does it still get emailed? Probably worth surfacing `list_status` at send time
and letting the sender decide, but the rule is unstated.

### 8. Retention of `raw_payload`

`JSONB` of every RentCast response is cheap at current volume but grows without
bound. No policy set. Probably fine indefinitely; worth revisiting if the
listings table gets large.

### 9. Does outreach ever fall back to the office email when an agent has none?

`listingAgent.email` is frequently missing, and `office_email_norm` /
`list_office_email_norm` exist so a batch can at least be pre-flighted against
`dnc_list`. Whether we would ever *send* to an office address is unsettled.

**Suppression already handles it correctly.** The check runs against the address
actually being used, not against a person, so a suppressed `info@` inbox is safe
by construction.

**The frequency cap does not.** Fifteen agents at one brokerage with no email of
their own all resolve to a single `info@` inbox. Each is a distinct
`realtor_id`, so a per-realtor cap counts fifteen separate sends and the shared
inbox receives fifteen emails from one batch. Shared inboxes are also the least
tolerant recipients on a list, and a complaint is precisely the signal that means
bad targeting.

If this is ever built, two things change: the cap needs a **per-address window**
alongside the per-realtor one, and `send_log` likely needs to record whether the
recipient was a person or an office. Without that second column `realtor_id`
quietly stops meaning "who we emailed" and starts meaning "who this was about" —
a different fact under the same name, which is the failure this schema otherwise
works hard to avoid.

### 10. Should append-only be enforced by the database?

`send_log` and `email_templates` are append-only by convention and by code. The
database does not enforce it — no trigger, no rule, no `REVOKE`. Every other rule
this project treats as load-bearing lives in the database, and this one does not,
which makes it the exception rather than the pattern.

Options, cheapest first: `REVOKE UPDATE, DELETE` from the application role, which
is one statement but also blocks the legitimate provider-status update on
`send_log`; a `BEFORE UPDATE OR DELETE` trigger that allows only the status
columns to change; or splitting the status updates into a separate table so the
log itself is genuinely insert-only.

**Deferred to Phase 5**, when sending is actually built and the real update
pattern is known. Deciding it now would be designing against a guess.

---

### 11. Which role sees the operational views?

`ingest_runs` and `iem_ingest_rejects` are the first tables that answer a
question about *the system* rather than about storms, listings, or sends — and
the role model has no answer for them.

The three roles are defined as **cost stages**: `viewer` spends nothing, `sender`
spends money and reputation, `admin` manages users and nothing else. Ingest
health is not a cost stage. It costs nothing to look at, which by the existing
logic makes it `viewer` — but "did last night's ingest run" is an operator
question, and `viewer` is the role given to whoever wants to browse hail.

Each option gives something up:

- **`viewer` sees it.** Consistent with the cost-stage rule, and free data is
  free data. But it puts `raw_row` — unparsed NWS text, occasionally with a
  reporter's name or phone number in the remark — in front of the broadest role.
- **`admin` sees it.** Matches the intuition that this is administration, and
  breaks the rule that `admin` touches users *and nothing else*. That rule is
  load-bearing and stated twice; bending it here is how it stops being true.
- **A fourth role.** Honest about the fact that operational visibility is a
  different axis from spend, and the cost of a role nobody has asked for.
- **Nobody, in Phase 1.** The tables are queried with `psql` by the one person
  who has the server. This is what will happen by default whether or not it is
  decided.

The last option is the current de facto answer and it is fine for Phase 1, since
the operator and the entire user base are the same person. **It stops being fine
at Phase 6**, when someone else starts relying on the storm browser and needs to
know whether the data behind it is current.

Worth noting the question is broader than these two tables: `api_pulls` and
`api_call_log` have the same shape and the same unanswered question. They have
simply never been read by anyone but the operator either.

**Not urgent, but it is the kind of thing that gets decided by whoever writes
the first page that shows it** — which is the argument for settling it before
that page exists rather than after.

---

# Deliberate non-goals

Recorded so they are not re-litigated as oversights.

- **No realtor deduplication.** Over-emailing is recoverable; wrongly suppressing
  a working agent is not.
- **No confidence score or percentage.** A tiered label showing its inputs
  ("Moderate — 3 reports, up to 1.25″, 2 spotters"), computed at query time, not
  stored. A percentage implies a probability the data does not support.
- **No automatic sending, ever.** There is no path from the nightly ingest to an
  outbound email. A human clicks send.
- **No trimming of `iem_data`.** 135k rows is small. Deletion would cost
  historical queries and radius replay.
- **No builder or agent-website fields.** New construction is not accessible to
  RBI, and agent websites are trivially searchable.
- **No listing-agent identity from RentCast.** The API exposes no agent MLS id or
  license number. Email is the only identifier available.
