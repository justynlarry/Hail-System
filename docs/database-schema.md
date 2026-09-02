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
  IEM (free, nightly)              RentCast (paid, on demand)
         │                                    │
         ▼                                    ▼
    iem_data ──┐                    properties ──> listings
   (points)    │                                      │
               │                                      │
               └──────> storm_listing_matches <───────┘
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

Thirteen tables. Grouped by which half of the system they belong to.

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
| `mag_unit` | `inches`, `mph`, or `none`. Blank where confidence is `unknown` |
| `unit_confidence` | `certain` / `inferred` / `unknown`. Derived from the type name only — inferring units from value ranges produced wrong answers (tornado EF numbers read as inches, heat index as mph) |
| `roof_relevant` | Whether this type triggers outreach. **The reason this table exists** — adding wildfire later is an `UPDATE`, not a deploy |

Referenced by `iem_data` and optionally by `email_templates`.

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
| `remark` | Free text. On damage reports with no magnitude, this is where the content is |
| `ingested_at` | When we pulled it |

Unique on `(utc_datetime, latitude, longitude, report_text, magnitude)`. This is
what makes the nightly job safe: it pulls a 30-hour overlapping window and lets
`ON CONFLICT DO NOTHING` discard repeats, so a failed night self-heals rather
than leaving a permanent hole.

Never trimmed. 135k rows is small for Postgres; deleting old data would cost the
ability to answer "when did this zip last get hit" and to replay history at a
different radius.

---

### `zcta_boundaries`

Zip code shapes from Census TIGER/Line. ~33,000 rows nationwide, a few hundred MB.
Loaded once, never written to.

| Field | Purpose |
|---|---|
| `zcta5` | 5-digit ZCTA code, PK |
| `geom` | `GEOMETRY(MultiPolygon, 4326)`. The outline — often thousands of coordinate pairs per zip |
| `centroid` | Convenience point |
| `land_area` | From Census attributes |

**No foreign keys.** Joined spatially, not relationally. Nothing in the database
enforces a relationship between a storm report and a zip — that is computed on
every query.

Appears in exactly one operation: turning a storm report point into a list of
zips to hand to RentCast. Everything downstream works with zip strings.

Loaded in EPSG 4326 to match IEM and RentCast coordinates. Mixing coordinate
systems fails silently — the join runs, returns too few rows, and never errors.

ZCTAs are the Census approximation of USPS zip codes. They do not match exactly
at the edges, and some PO-box-only zips have no ZCTA. Fine for finding storm
areas; not authoritative for mail.

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
| `created_date` | When RentCast first saw the property |

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
| `list_office_*` | Brokerage snapshot |
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
| `added_at`, `added_by` | Who suppressed it, or `system` |
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
| `status` | `queued` → `sent` → (`bounced` \| `complained`), or `queued` → `failed` |
| `provider_message_id` | The ESP's id. **How a bounce finds its way home** |
| `status_updated_at` | When a bounce or complaint arrived |
| `error_detail` | Provider error text |

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
| `role` | `admin` / `sender` / `viewer` |
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
| `estimated_calls` | **What the UI showed before the user confirmed** |
| `actual_calls` | What it really cost |
| `listings_returned` | |
| `status` | running / complete / failed / cancelled |

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
