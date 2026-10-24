# Top hail areas, last five years: zips and counties ranked by storm days (2026-10-24)

Analysis only. Written in-session by Claude. It reads `iem_data`, `report_zip_distances`, `coverage_zips` and `county_boundaries` through a
read-only session (`SET default_transaction_read_only = on`) and writes nothing to the database. Nothing here touches RentCast or any email,
and nothing here feeds a pull, the matcher or a send.

**What it answers.** Which areas did hail hit most over the last five years (2021-10-24 to 2026-10-24), in order?

**Method.** NWS Local Storm Reports in `iem_data` with `report_text = 'HAIL'` and `magnitude >= 1.00` in, the roof-relevant floor in
`report_types`. A report counts for a zip when it falls inside that zip (`report_zip_distances.distance_m = 0`). Zips are ranked by distinct
storm days (Denver local date), with report count as the tiebreak. Counties come from `ST_Contains` against `county_boundaries` (Colorado,
`state_fips = '08'`). "In territory" means a current `coverage_zips` row (`removed_at IS NULL`).

**Window.** The first qualifying report is 2022-05-01, because there is no 1 in or larger hail from late October to April. The last is
2026-09-22. That gives 2,131 reports on 215 storm days.

**Files**
- `top5y.sql`: the queries below, run with `psql` as a read-only session against the `postgis` container. Not yet in this folder; see the
  note at the end.

## 1. Top 10 zips, all of Colorado

| # | Zip | Storm days | Reports | Largest (in) | >= 1.75 in | Last hit | In territory? |
|---|---|---|---|---|---|---|---|
| 1 | 80758 | 24 | 78 | 4.00 | 35 | 2026-09-20 | no |
| 2 | 80759 | 18 | 39 | 4.00 | 20 | 2026-06-22 | no |
| 3 | 80751 | 17 | 39 | 4.00 | 18 | 2026-09-05 | no |
| 4 | 80828 | 16 | 46 | 3.00 | 16 | 2026-06-08 | no |
| 5 | 80808 Calhan | 16 | 19 | 2.00 | 6 | 2026-06-23 | **yes** |
| 6 | 80821 | 15 | 44 | 3.50 | 25 | 2026-09-20 | no |
| 7 | 80807 | 15 | 40 | 4.00 | 19 | 2026-06-09 | no |
| 8 | 80831 Peyton | 14 | 27 | 2.50 | 2 | 2026-06-24 | **yes** |
| 9 | 80720 | 13 | 39 | 4.00 | 25 | 2026-06-24 | no |
| 10 | 80742 | 13 | 20 | 3.25 | 7 | 2026-07-03 | no |

The "no" zips are eastern-plains zips outside `coverage_zips`. Town and county names were not looked up.

## 2. Top 10 zips inside `coverage_zips`

| # | Zip | Area | Storm days | Reports | Largest (in) | >= 1.75 in | Last hit |
|---|---|---|---|---|---|---|---|
| 1 | 80808 | Calhan | 16 | 19 | 2.00 | 6 | 2026-06-23 |
| 2 | 80831 | Peyton | 14 | 27 | 2.50 | 2 | 2026-06-24 |
| 3 | 80911 | Colorado Springs | 10 | 14 | 2.00 | 1 | 2025-07-06 |
| 4 | 80915 | Colorado Springs | 8 | 20 | 1.75 | 3 | 2026-06-23 |
| 5 | 80908 | Colorado Springs | 8 | 19 | 2.00 | 2 | 2026-06-23 |
| 6 | 80107 | Elizabeth | 8 | 16 | 1.75 | 5 | 2026-06-01 |
| 7 | 80106 | Elbert | 8 | 13 | 2.00 | 3 | 2025-06-08 |
| 8 | 80909 | Colorado Springs | 8 | 10 | 1.50 | 0 | 2026-06-23 |
| 9 | 80817 | Fountain | 8 | 10 | 2.50 | 1 | 2026-06-23 |
| 10 | 80631 | Greeley | 7 | 19 | 2.00 | 1 | 2026-06-20 |

Ranks 3 to 9 are close: seven of them sit on 8 to 10 storm days, so their order is not a real difference.

## 3. Top 10 counties

| # | County | Reports | Storm days | Largest (in) |
|---|---|---|---|---|
| 1 | El Paso | 338 | 56 | 4.00 |
| 2 | Weld | 162 | 53 | 3.25 |
| 3 | Yuma | 162 | 46 | 5.00 |
| 4 | Lincoln | 129 | 38 | 4.00 |
| 5 | Elbert | 115 | 32 | 3.50 |
| 6 | Kit Carson | 113 | 34 | 4.00 |
| 7 | Washington | 105 | 33 | 4.50 |
| 8 | Arapahoe | 104 | 24 | 2.50 |
| 9 | Logan | 88 | 33 | 4.00 |
| 10 | Morgan | 79 | 22 | 3.00 |

Ranked by reports, not storm days.

## 4. Against the full record (2004-05-10 to 2026-09-22)

The same query over every year gave 7,937 qualifying reports. What moved:
- **El Paso, Weld and Yuma stay the top three** counties. Lincoln (4th) passed Kit Carson (6th), which was 4th over the full record.
- **Calhan and Peyton stay the top two in territory.** Colorado Springs zips (80911, 80915, 80908, 80909) and Fountain now fill several of
  the next places.
- **Fort Collins (80524, 80525) dropped out of the territory top ten**, and Elizabeth and Deer Trail fell: their older counts, and the 4.25 in
  maxima, came before this window.
- **80758 now leads the whole-state list**; 80759 led over the full record.
- **Counts are small.** Five years is 215 storm days, so a difference of one or two storm days is noise.

## 5. Caveats

- **Local Storm Reports only.** They depend on where people are watching and reporting. Zips with more people and spotters log more, and
  large zips have more room to catch a report. This is a count of reports, not a measure of hail intensity. The radar layers (Phase 5b) and
  CoCoRaHS (Phase 5c) are not used here and could change the order.
- **A storm day means at least one report inside the zip**, not that the whole zip was hit.
- **Reports just outside a zip's edge do not count for it** (`distance_m = 0`), so a zip beside a storm can be undercounted.
- **The 1.00 in floor is the system's own** (`report_types.min_magnitude` for HAIL). A different floor would reorder close ranks.
- **Nothing here feeds email, pulls or the matcher.**
