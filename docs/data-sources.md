# Data Sources

Every external source the system depends on. Endpoints, parameters, field
meanings, and the traps found in each.

---
## 1. Iowa Environmental Mesonet — Local Storm Reports

Iowa State's mirror of the NWS realtime storm report feed. Free, no key, no
account, no documented rate limit.

**Key page:** `https://mesonet.agron.iastate.edu/request/gis/lsrs.phtml`
It carries the picklists for report type, WFO, and state. **Read its field
schema with care — see "The published schema is the DBF" below.**

### One endpoint, two jobs

Everything goes through a single CGI endpoint. The nightly job and the backfill
differ only in how the time window is expressed.

```
https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py
```

| Job | Window parameter |
|---|---|
| Nightly | `recent=108000` — a rolling window **in SECONDS**. 108000 = 30 hours. |
| Backfill / replay | `sts=2004-01-01T00:00Z&ets=2026-01-01T00:00Z` — explicit UTC range. `2004-01-01` is the archive floor (decision-log 2026-09-10); the earliest Colorado report is 2004-01-26. |

**`recent` is seconds, not hours, and `hours=` does not exist.** `hours=30`
returns **HTTP 422, "GET start time parameters missing"** — it is not an
alternate spelling, it is an unrecognized parameter, and without `sts`/`ets`
there is then no window at all. Verified 2026-09-08.

**Single-quote these URLs in bash.** Unquoted, `&` backgrounds the job and
silently truncates the query string at the first parameter — curl succeeds and
returns the wrong data rather than erroring.

### Formats — GeoJSON is not one of them

`fmt=` accepts **`csv`, `shp`, `kml`, `xlsx`**. It does **not** accept
`geojson`: the server validates `fmt` against a pattern and returns **HTTP 422**
with `{'type': 'string_pattern_mismatch', 'loc': ('query', 'fmt')}`.

GeoJSON exists only as a static nationwide 24-hour file:

```
https://mesonet.agron.iastate.edu/data/gis/shape/4326/us/lsr_24hour.geojson
```

That file **cannot serve this system**: it is a fixed 24-hour window, so it
cannot supply the 30-hour overlap the nightly job needs to be idempotent, and it
cannot backfill at all. This is the mechanical half of the 2026-09-04 decision
*CSV, not GeoJSON, for both ingest paths* — that entry chose CSV; this is the
note that GeoJSON was never actually on the table for this endpoint.

### Scoping parameters

| Parameter | Notes |
|---|---|
| `state` | Two-letter code. **This is what the ingest uses** — see the 2026-09-04 decision `state=CO`, not a WFO list |
| `wfos` | **Retired.** Colorado is five offices (BOU, PUB, GJT, GLD, CYS), and `wfos=BOU,PUB` silently drops the northeast corner. Superseded by `state` |
| `north` `south` `east` `west` | Bounding box, added 2024-10-24. **Not currently used.** Relevant to open question 12 — see below |
| `recent` | Seconds. Nightly |
| `sts` / `ets` | Explicit UTC range. Backfill and replay |
| `type` | Server-side type filter, takes **TYPETEXT** values (`type=HAIL`), not type codes. **Do not use** |
| `magge` | Minimum magnitude, "mag greater-or-equal". **Do not use** |

**Why `type` and `magge` must not be used.** Storm reports are stored at full
fidelity and filtered on read — filtering at ingest would violate that rule and
would bake today's `roof_relevant` judgment into data we cannot recover later.
The magnitude floors live in `report_types.min_magnitude`, where changing one is
an `UPDATE` rather than a re-ingest.

**The bounding box and open question 12.** Open question 12 records that
`state=CO` excludes out-of-state reports permanently and that no buffer radius
recovers them, because the radius widens the search around a *stored* report.
The bbox parameters are a **second option** for that question — a box crossing
the state line would store the Wyoming report in the first place. This does not
reopen the decision; it means the question now has a mechanism attached rather
than only a description.

> Correction to a previous version of this file: the bbox row said "Preferred
> for production — a Front Range box skips the Western Slope entirely." That
> contradicted the 2026-09-04 `state=CO` decision, which is the one in force.

### Fields — CSV, addressed by name

The CSV has **16 fields**, in this order:

```
VALID, VALID2, LAT, LON, MAG, WFO, TYPECODE, TYPETEXT, CITY, COUNTY,
STATE, SOURCE, REMARK, UGC, UGCNAME, QUALIFIER
```

| Field | Meaning |
|---|---|
| `VALID` | `YYYYMMDDHHMM` compact, **UTC** |
| `VALID2` | Human-readable duplicate, **UTC**. Not mapped |
| `LAT` `LON` | Decimal degrees, ~2 decimals of real precision (≈1 km) |
| `MAG` | **Units depend on type.** See traps |
| `WFO` | Forecast office |
| `TYPECODE` | One-char IEM code. **Not unique** |
| `TYPETEXT` | Textual type. This is the documented picklist — the key is the pair |
| `CITY` | **Not a city.** A position relative to a landmark: `2 SW Great Divide`. Not mapped |
| `COUNTY` `STATE` | As reported |
| `SOURCE` | Free text, entered by the reporting office |
| `REMARK` | Free text. On damage reports with no magnitude, the content is here |
| `UGC` | NWS code, e.g. `COC081` = CO + county-type + FIPS 081 |
| `UGCNAME` | IEM-computed county name. Not mapped |
| `QUALIFIER` | `M` measured / `E` estimated / `U` unknown |

**The published schema on `lsrs.phtml` documents the shapefile DBF, not the
CSV.** The DBF carries **15** fields, spells the last one **`QUALIFY`**, and
orders them differently — LAT/LON sit near the end. Anyone building against the
published table and then parsing CSV positionally gets silently misaligned data.
**This is why `iem_parse.py` addresses fields by name and never by position**,
and why `EXPECTED_FIELDS` is checked rather than assumed.

**The live CSV header is byte-identical to the 2016–2026 archive header.**
Verified 2026-09-08 by pulling both and comparing bytes. This is what lets one
parser module serve both the nightly and the backfill with no format branch.

### Report types

~50 nationally, 37 observed in ten years of Colorado. Roof-relevant subset:

`HAIL`, `TSTM WND GST`, `TSTM WND DMG`, `NON-TSTM WND GST`, `NON-TSTM WND DMG`,
`HIGH SUST WINDS`, `DOWNBURST`, `TORNADO`, `LANDSPOUT`, `HEAVY SNOW`,
`SNOW/ICE DMG`, `ICE STORM`, `FREEZING RAIN`, `WILDFIRE`, `DEBRIS FLOW`

The rest are marine, tide, temperature, fog, and flood types. See
`reference/report_types.csv` for the authoritative list with counts, and
`planning/report_types.csv` for the curated seed with the `roof_relevant`
judgments.

### Traps

- **`MAG` contains the literal string `None`** as the null marker in 3,353 of
  135,856 rows. Coerced to 0 this produces 629 magnitude-zero flash floods and
  549 magnitude-zero tornadoes.
- **`Decimal()` accepts `'NaN'` and `'Infinity'`** — neither raises
  `InvalidOperation`, so neither is caught by a naive numeric parse. Worse, an
  ordered comparison against a `Decimal` NaN *signals* `InvalidOperation`, so a
  range check like `-90 <= value <= 90` **raises** and the exception escapes the
  parser. And Postgres `NUMERIC` accepts `NaN`, so an unguarded magnitude lands
  in `iem_data.magnitude` and reads as a real measurement. `iem_parse.py` guards
  both with `is_finite()` *before* any range test.
- **A misspelled filter parameter is silently ignored, not rejected.** Verified
  against 2018-06-19 (177 reports, 142 of them hail): `type=HAIL` → 142 rows and
  `magge=1.75` → 75 rows, but **`typetext=HAIL` → 177 rows and
  `magnitude=1.75` → 177 rows** — the full unfiltered set, HTTP 200, no warning.
  A wrong parameter name here does not error; it returns everything, and a
  script that trusted it would look like it was filtering and would not be.
- **Units come from the type name, never the value range.** Range inference was
  actively wrong: tornado EF numbers (0–2) read as inches, dense fog visibility
  (0.08–0.25 mi) as inches, excessive heat (44–105 °F) as mph.
- **`TYPECODE` is not unique.** Nine codes map to two texts each — `R` is both
  RAIN and HEAVY RAIN, `S` both SNOW and HEAVY SNOW. The key is the pair
  `(report_type, report_text)`.
- **76 rows have unquoted commas inside `CITY`** (`BISON LAKE, GLENWOOD 15`)
  — 75 from 2018 and **one from 2026-08-31, so this is ongoing, not a
  historical artifact** (see the 2026-09-09 reversal entry),
  producing 17 fields instead of 16. Never split on commas — but note that a
  real CSV parser **detects** these rows and cannot **repair** them: the quotes
  were never written, so the field boundary is unrecoverable. They are rejected
  as `field_count_mismatch`, which is why rejecting is not lossy — `raw_row`
  keeps the line verbatim.
- **`SOURCE` arrives truncated occasionally, at no consistent width.** Two
  instances in 135,856 rows, both mangling "Department of Highways":
  `'DEPT OF'` (7 chars, 2019-03-09, BOU, in a *well-formed* row) and
  `'Department of Hig'` (17 chars, 2026-08-31, GJT, in the malformed row above).
  The canonical `'DEPT OF HIGHWAYS'` / `'Dept of Highways'` also exists.

  **There is no length cap to code against, and 16 is not one.** The longest
  well-formed `SOURCE` is 16 characters, but all ten values at that length are
  complete, deliberately-abbreviated picklist entries — `DEPT OF HIGHWAYS`,
  `FIRE DEPT/RESCUE`, `NWS STORM SURVEY`, `OFFICIAL NWS OBS`,
  `PARK/FOREST SRVC`, and their mixed-case twins. None is cut mid-word.
  `'Department of Hig'` is **17** characters — longer than any well-formed
  value — so whatever produced it was not a 16-character truncation.

  Length distribution of well-formed `SOURCE`, for reference: 4, 6, 7, 8, 9,
  10, 11, 12, 13, 14, 15, 16 — nothing above 16, and the 9/10/11 buckets hold
  17, 3 and 1 rows respectively.

  **Consequence for `report_sources`:** the table is a lookup joined on
  `report_source_norm`, and a truncated value will simply never match — which
  is the behaviour already designed for. A `LEFT JOIN` yields a NULL tier and
  the UI shows "unrated" rather than dropping the report. This is the concrete
  evidence behind the `NO FOREIGN KEY` comment in `sql/003_reference.sql`,
  which already names both of these values.
- **`QUALIFIER` of `M` on hail does not mean instrument-measured.** 97.8% of M
  and 94.9% of E hail values land on the same coin/ball catalog. M tracks
  reporter training. Use `SOURCE` for a confidence signal instead. A value
  outside `{M, E, U}` **ends the run** — see the 2026-09-06 decision.
- **A quiet day returns a header line and no data rows.** `rows_seen = 0` is a
  normal `complete` run, not a failure. Verified: a 30-hour `recent` window
  returned 101 bytes — the header alone.
- **Timestamps are UTC.** A Front Range evening storm crosses midnight UTC and
  will split across two calendar days if grouped naively.
- **`UGC` is null before mid-2022.** The cross-reference was added July 2022,
  and IEM describes it as working in about 99% of cases.
- Before December 2006, no distinction between snow and sleet reports.
- `SOURCE` is free text with case variants (`PUBLIC` / `Public`). Normalize;
  match on `report_source_norm`.

---

## 2. US Census TIGER/Line — boundaries

Free, no account. Annual vintage. Loaded once, never written to.

**Index:** `https://www2.census.gov/geo/tiger/TIGER2025/`

| Directory | File | Rows | Size |
|---|---|---|---|
| `ZCTA520/` | `tl_2025_us_zcta520.zip` | ~33,000 | 505 MB zipped, 785 MB `.shp` |
| `COUNTY/` | `tl_2025_us_county.zip` | ~3,200 | 80 MB zipped, 126 MB `.shp` |

Both are **national files** — no state-level split exists for the ZCTA layer.

A shapefile is a set: `.shp` geometry, `.dbf` attributes, `.prj` coordinate
system, `.shx` index. All must be present. Extracting only the `.shp` fails.

### Loading

```bash
shp2pgsql -I -s 4269:4326 -D tl_2025_us_zcta520.shp public.zcta_boundaries \
  | psql -d weather-property
```

- `-s 4269:4326` reprojects NAD83 → WGS84. **TIGER ships in 4269; IEM and
  RentCast are 4326.** Mixing them fails silently — the join runs, returns too
  few rows, and never errors.
- `-I` builds the GiST index during load. That index is the entire performance
  story for the buffer query.
- `-D` uses the faster dump format.

### Caveats

ZCTAs are the Census approximation of USPS zip codes, built from census blocks.
They do not match exactly at the edges, and some PO-box-only zips have no ZCTA.
Fine for finding storm areas; not authoritative for mail delivery.

Zips do not nest inside cities or counties — one zip can straddle a county line.
Roll-ups work, but not as a one-to-one hierarchy.

---

## 3. RentCast — property listings

Paid. Monthly subscription with a lookup allowance. **Every call costs.**

**Docs:** `https://developers.rentcast.io/reference/property-listings-schema`
Appending `.md` to any docs URL returns a clean markdown version.

**Endpoint:** `GET /listings/sale` — paginated, up to 500 per response, sorted
by `lastSeenDate` descending. Search by address, city, state, zip, or a circular
geographic area.

### Fields we use

**Identity:** `id` — a RentCast **property** id built from the address string.

**Location:** `formattedAddress`, `addressLine1/2`, `city`, `state`, `zipCode`,
`county`, `stateFips`, `countyFips`, `latitude`, `longitude`.

**Property:** `propertyType`, `bedrooms`, `bathrooms`, `squareFootage`,
`lotSize`, `yearBuilt`, `hoa.fee`. Values of `propertyType` seen in our 508
pulled properties (2026-09-22): Condo 175, Single Family 171, Land 93,
Townhouse 61, Manufactured 8. The list is what we have seen, not a documented
enumeration — expect others.

**Listing:** `status` (`Active` / `Inactive` only), `price`, `listingType`
(Standard / New Construction / Foreclosure / Short Sale), `listedDate`,
`removedDate`, `createdDate`, `lastSeenDate`, `daysOnMarket`, `mlsName`,
`mlsNumber`.

**Contacts:** `listingAgent.{name,phone,email,website}`,
`listingOffice.{name,phone,email,website}`, `builder.*` (new construction only),
`history` (keyed by date string).

### Traps

- **`id` identifies a property, not a listing.** A house listed twice reuses it.
  Hence the properties/listings split.
- **`id` is derived from the address string**, so an upstream formatting change
  mints a new id for the same building.
- **Ids are case-sensitive** and must be passed back exactly as returned.
- **`listingAgent.email` is frequently missing.** Handle null — it is our only
  identifier for a person. Measured 2026-09-22: 40 of the 231 listings in the
  2026-09-21 pull (17%), 49 of all 508 (9.6%), and it swings by zip from 0 of
  68 (80135) to 28 of 78 (80136). Parking-lot item 54 tracks it as a rate.
- **No agent MLS id or license number is exposed.** Dedupe on email only.
- **Agent name is a single display string** — MLS feeds do not split first/last.
- **`history` carries no agent and no MLS number** — only event, price, listing
  type, dates, and days on market. Reconstructed past listings will have null
  agent fields.
- **New Construction is not worth outreach.** A brand-new roof is not a hail claim.
- **Vacant land is in the sale-listings results.** `propertyType` `Land` was 93
  of our 508 properties (18%). It has no roof and no hail claim, so
  `matcher.py` excludes it at match time (decision log, 2026-09-22); the
  listings themselves are still stored and still count against the pull.

### HTTP status codes

Recorded ahead of the Phase 3 RentCast client so the error-handling design
has this to build against, rather than being worked out live against a paid
API. Not built yet — no code reads these codes today.

| Code | What it actually means | User-facing text |
|---|---|---|
| 401 | Bad or missing API key | "RentCast rejected our API key. This is a configuration problem, not something a retry fixes — check RENTCAST_KEY." |
| 403 | Key restricted, or a billing/subscription issue | "RentCast declined this request — check the account's billing status or key restrictions on the RentCast dashboard." |
| 404 | Not an error — zero listings matched the query | No popup. A normal empty result, not a failure — worth naming because it inverts the usual REST convention where 404 means "broken." |
| 429 | Sent requests faster than 20/sec, per key | "RentCast asked us to slow down. Retrying automatically." Should self-heal — see below. |
| 500 | RentCast's server had an internal error | "RentCast had an error on their end. Safe to retry — try again in a minute." |
| 504 | RentCast's server timed out | "RentCast didn't respond in time. Safe to retry." |
| 400 | Malformed/invalid query parameter | "Something about this request wasn't valid — likely a bad zip or filter value." Shouldn't happen if the params are built correctly, but worth a message if it does. |
| 405 | Wrong HTTP method | Shouldn't ever fire — we only `GET`. If it does: "Unexpected response from RentCast — this points at a bug in our request, not RBI's data." |
| (none) | Network failure — timeout, DNS, connection refused, on our end | "Couldn't reach RentCast at all — check network/DNS, or RentCast may be down." Distinct from the above: there is no HTTP response to read a code from. |

### Not used

Property records, valuation/AVM, rent estimates, market statistics, rental
listings.

---

## 4. Email provider — TBD

Unselected. Requirements:

- Must **explicitly permit** outreach to recipients who did not opt in. Several
  providers terminate accounts for this — a worse failure than a slow ramp.
- Webhook or API for bounce and complaint notifications, returning a message id
  matchable to `send_log.provider_message_id`.
- Throttling or scheduled send support for warmup.
- Separate sending identity (subdomain), not RBI's primary domain.

**Prior history:** a contractor-built system used Mailchimp and led to
blacklisting. Confirm during Phase 0 whether the main company domain took
reputation damage — if so, remediation is its own line item, not something to
absorb silently.

---

## 5. Permits and jurisdiction

Researched 2026-09-23. **Parked until the system is running** (decision log,
*Permits are parked; the claim rule; jurisdiction is a polygon, never a
mailing city*). Only the municipal boundaries are loaded. No permit data has
a schema or a loader. The permit snapshots in `data/raw/permits/` are raw
captures, taken because Aurora's history may roll off.

Items marked ⚠ were not verified against the live source.

### DOLA municipal boundaries (loaded)

**Service:**
`https://services3.arcgis.com/DgjqnJA1rgO92Soi/arcgis/rest/services/DOLA_Municipalities_(Boundaries_Dissolved)/FeatureServer/0`
It is listed on geodata.colorado.gov as `public_authoritative`, owned by OIT
for DOLA. Fetched by `scripts/fetch_municipal.py` into `data/raw/dola/` and
loaded by `scripts/load_municipal.sh` into `municipal_boundaries`
(`sql/021`).

**Layer choice:** the dissolved layer (274 rows, one per municipality), not
`Municipal_Boundary` (1,911 rows, one per base polygon or annexation). The
dissolved geometry does include annexed land: every city's area matches the
union of its base and annexation polygons to within 1%. The "does not show
annexations" note means only that the annexation attributes are dropped.
Do not use the CU GeoLibrary copy, which is a 2017 snapshot.

**Fields:** `city` (the 5-digit Census place code, not a name),
`first_city` (the name), `OBJECTID`, `Shape__Area`, `Shape__Length`.

### Traps

- **Native SRID is 3857 (Web Mercator).** Always query with `outSR=4326`.
- **Missing values are the literal string `'null'`** in the 1,911-row
  layer, the same shape as IEM's `None`. The loader converts them to JSON
  null.
- **The edit date is meaningless.** DOLA republishes nightly at about
  07:00 UTC, so `editingInfo.dataLastEditDate` dates the publish, not a
  boundary change. The real annexation history is `cl_re_date` (the
  recording date) in the **1,911-row** layer. The dissolved layer has no
  equivalent.
- **Hudson is under two codes:** `37820` (the town) and `03782` (one 2024
  annexation, mistyped). This is a known source error, loaded as received.
  It is to be fixed by a correction table (parking-lot item 75).
- 65 of 274 geometries arrive invalid. Use `ST_MakeValid`, then
  `ST_CollectionExtract(…, 3)`, then `ST_Multi`.
- **Jurisdiction is point-in-polygon, never the mailing city.** USPS city
  names are post-office areas, not municipalities.

### Permit sources evaluated

The three jurisdictions with the most stored `properties` as of 2026-09-23.
That ranking reflects which 5 zips had been pulled, not the territory. By
area, unincorporated El Paso, Weld and Adams lead. Snapshots dated
2026-09-23 are in `data/raw/permits/<source>/`, each with its layer
metadata beside it.

| | Aurora | Unincorporated Adams | Unincorporated Douglas |
|---|---|---|---|
| Issuer | City of Aurora Building Division | Adams County Community & Economic Development (unincorporated only) | Douglas County Building Division (unincorporated only) |
| Coverage, by point-in-polygon on roofing permits | 99.95% in Aurora | 100% in unincorporated Adams | 100% in unincorporated Douglas |
| Endpoint | `https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44` | `https://services3.arcgis.com/4PNQOtAivErR7nbT/arcgis/rest/services/Building_Permits_Eye_On_Adams/FeatureServer/0` | `https://services.arcgis.com/seTexOicoRXDvRsJ/arcgis/rest/services/All_Permits_View/FeatureServer/0` (a table) |
| Records (2026-09-23) | 162,233 | 72,249 | 285,635 |
| History start | 2021-09-24 ⚠ looks like a rolling 5 years | 2011-01-03 | 1990-01-01 |
| Update cadence | ⚠ not stated; newest record the day before | ⚠ not stated; edited that day | Nightly (item description); full rebuild |
| Roofing identified by | Its own subtype: `SubDesc` `Roofing-RT2` and `Roofing Commercial-NT2` (36,634 together) | `TypeOfWork = 'Re Roof'` through 2016; after that mostly keywords in `Description` with a blank type ⚠ | Its own type: `PERMIT_JOB_TYPE = 'Roofing'` (75,703) |
| Coordinates | Point geometry, native 2232, served in 4326 | `X`/`Y` already in degrees ⚠ datum not stated; 856 records with no geometry | `LOCATION` text `(lat, lon)` ⚠ datum not stated; **51% of roofing permits have none** |
| Terms of use | ⚠ Disclaimer plus indemnity, no licence grant | ⚠ None published | ⚠ None published |
| Records per page | 2000 | 2000 | 1000 |

- **Aurora** blocks urllib's default User-Agent (403). Send one.
- **Adams:** the keyword filter used for the counts here (`REROOF`,
  `RE-ROOF`, `RE ROOF`, `ROOFING`, `SHINGLE`) has not been checked for false
  matches ⚠.
- **Douglas:** `CREATED_DATE_TIME` is identical on every row, so each
  nightly export is a full rebuild. Its older layer, `Building Permits
  (2014 to 2016)` on `apps.douglas.co.us/geopendata`, returned 502 on every
  attempt and was not examined ⚠.

### Corroboration against hail

Hail reports of at least 1.00″ inside each jurisdiction (`iem_data`),
compared with roofing permits in the 90 days after each storm and in the
same 90 days one year earlier:

| Storm | Jurisdiction | 90 days after | Same window, year before |
|---|---|---|---|
| 2023-05-10 | Aurora | 4,294 | 803 |
| 2023-05-10 | Unincorporated Adams | 382 | 86 |
| 2023-06-22 | Unincorporated Douglas | 3,926 | 500 |
| 2012-06-06 | Unincorporated Douglas | 5,543 | 624 |

**The Aurora 2024 miss is baseline contamination.** After 2024-05-30,
Aurora shows 4,784 permits against 5,181. The comparison window was the
tail of the 2023 surge, not a quiet year. A year-over-year baseline breaks
down whenever the prior year was itself a hail year.

### Commercial aggregators (evaluated, not chosen)

From research outside the 2026-09-23 session. Not re-verified ⚠.

- **Shovels:** the free tier has 1 year of history, 10 results per query
  and no downloads. Basic is $599/month for full history. Updates on the 1st
  and 15th. Its jurisdiction CSV was a dead end. **Still a possible fallback
  for jurisdictions with no open data.**
- **PermitStack:** its pricing and coverage claims are internally
  inconsistent.
- **Apify community scrapers:** rejected as unreliable.

### Denver (not examined)

From research outside the 2026-09-23 session. Not re-verified ⚠.

The ArcGIS RESCON (residential construction) layer covers 2015 on, native
SRID 2877, with invalid addresses placed at (0, 0). **Open:** whether
reroofs are in RESCON or under a separate ROOFSIDE type. Permit numbers like
`2021-ROOFSIDE-…` suggest a separate type. The 2017 known answer is
**18,475** roof permits, 54.6% above 2016, after the May 2017 hailstorm.
Parking-lot item 81.
