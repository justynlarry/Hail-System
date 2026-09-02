# Data Sources

Every external source the system depends on. Endpoints, parameters, field
meanings, and the traps found in each.

---

## 1. Iowa Environmental Mesonet — Local Storm Reports

Iowa State's mirror of the NWS realtime storm report feed. Free, no key, no
account, no documented rate limit.

**Key page (bookmark this):**
`https://mesonet.agron.iastate.edu/request/gis/lsrs.phtml`
It carries the field schema and the full picklists for report type, WFO, and state.

### Realtime — last 24 hours, regenerated every 5 minutes

```
https://mesonet.agron.iastate.edu/geojson/lsr.geojson?states=CO&hours=168
```

Formats: GeoJSON, CSV, shapefile.

### Archive — arbitrary date range, back to 2003

```
https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py
    ?state=CO
    &sts=2021-01-01T00:00Z
    &ets=2026-01-01T00:00Z
    &fmt=csv
```

Formats: csv, shapefile, xlsx, kml.

**Single-quote these URLs in bash.** Unquoted, `&` backgrounds the job and
silently truncates the query string at the first parameter — curl succeeds and
returns the wrong data rather than erroring.

### Scoping parameters

| Parameter | Notes |
|---|---|
| `state` / `states` | Two-letter code |
| `wfos` | Forecast office. **Colorado: BOU, PUB, GJT, plus GLD and CYS on the borders.** `wfos=BOU,PUB` would silently drop the northeast corner |
| bounding box (`west`,`east`,`north`,`south`) | Preferred for production — a Front Range box skips the Western Slope entirely |
| `hours=N` | Rolling recent window. Use for the nightly job |
| `sts` / `ets` | Explicit UTC range. Use for backfill and replay testing |
| `typetext` | Server-side type filter. **We ingest everything and filter at query time instead** |
| `magnitude` | Minimum. Blunt — does not handle types without a magnitude. Filter in SQL |

### Fields

CSV and GeoJSON use different key names for the same content. Normalize at the
parser; keep one internal shape.

| CSV | GeoJSON | Meaning |
|---|---|---|
| `VALID` | — | `YYYYMMDDHHMM` compact, **UTC** |
| `VALID2` | `valid` | Human-readable / ISO, **UTC** |
| `LAT` `LON` | `lat` `lon` | Decimal degrees, ~2 decimals of real precision (≈1 km) |
| `MAG` | `magnitude` | **Units depend on type.** See traps |
| `WFO` | `wfo` | Forecast office |
| `TYPECODE` | `type` | One-char IEM code. **Not unique** |
| `TYPETEXT` | `typetext` | Textual type. This is the documented picklist — match on this |
| `CITY` | `city` | **Not a city.** A position relative to a landmark: `2 SW Great Divide` |
| `COUNTY` `STATE` | `county` `st` | As reported |
| `SOURCE` | `source` | Free text, entered by the reporting office |
| `REMARK` | `remark` | Free text. On damage reports with no magnitude, the content is here |
| `UGC` | — | NWS code, e.g. `COC081` = CO + county-type + FIPS 081 |
| `UGCNAME` | — | County name |
| `QUALIFY` | `qualifier` | `M` measured / `E` estimated / `U` unknown |
| — | `product_id` | NWS text product. **Not unique per report** — one product carries several |

### Report types

~50 nationally, 37 observed in ten years of Colorado. Roof-relevant subset:

`HAIL`, `TSTM WND GST`, `TSTM WND DMG`, `NON-TSTM WND GST`, `NON-TSTM WND DMG`,
`HIGH SUST WINDS`, `DOWNBURST`, `TORNADO`, `LANDSPOUT`, `HEAVY SNOW`,
`SNOW/ICE DMG`, `ICE STORM`, `FREEZING RAIN`, `WILDFIRE`, `DEBRIS FLOW`

The rest are marine, tide, temperature, fog, and flood types. See
`reference/report_types.csv` for the authoritative list with counts.

### Traps

- **`MAG` contains the literal string `None`** as the null marker in 3,353 of
  135,856 rows. Coerced to 0 this produces 629 magnitude-zero flash floods and
  549 magnitude-zero tornadoes.
- **Units come from the type name, never the value range.** Range inference was
  actively wrong: tornado EF numbers (0–2) read as inches, dense fog visibility
  (0.08–0.25 mi) as inches, excessive heat (44–105 °F) as mph.
- **`TYPECODE` is not unique.** Nine codes map to two texts each — `R` is both
  RAIN and HEAVY RAIN, `S` both SNOW and HEAVY SNOW.
- **76 rows have unquoted commas inside `CITY`** (`BISON LAKE, GLENWOOD 15`),
  producing 17 fields instead of 16. Never split on commas.
- **`QUALIFIER` of `M` on hail does not mean instrument-measured.** 97.8% of M
  and 94.9% of E hail values land on the same coin/ball catalog. M tracks
  reporter training. Use `SOURCE` for a confidence signal instead.
- **Timestamps are UTC.** A Front Range evening storm crosses midnight UTC and
  will split across two calendar days if grouped naively.
- **`UGC` is null before mid-2022.** The cross-reference was added July 2022,
  and IEM describes it as working in about 99% of cases.
- Before December 2006, no distinction between snow and sleet reports.
- `SOURCE` is free text with case variants (`PUBLIC` / `Public`). Normalize.

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
  | psql -d hailsystem
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
