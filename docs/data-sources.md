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
`lotSize`, `yearBuilt`, `hoa.fee`.

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
  identifier for a person.
- **No agent MLS id or license number is exposed.** Dedupe on email only.
- **Agent name is a single display string** — MLS feeds do not split first/last.
- **`history` carries no agent and no MLS number** — only event, price, listing
  type, dates, and days on market. Reconstructed past listings will have null
  agent fields.
- **New Construction is not worth outreach.** A brand-new roof is not a hail claim.

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
