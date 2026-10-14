# 3. CoCoRaHS hail reports

**Verdict: passes Part A.** It is free, dense on the Front Range, a ground measurement independent of
radar, and mostly *not* already in our IEM reports. It is mostly small hail, which suits it for
lowering confidence as well as raising it.

| | Finding | Verified how |
|---|---|---|
| Access | `https://data.cocorahs.org/export/exportreports.aspx?ReportType=Hail&dtf=1&Format=CSV&State=CO&ReportDateType=reportdate&StartDate=M/D/YYYY&EndDate=M/D/YYYY`. No key. | fetched 2026-10-14 |
| Cost, terms | Free download. **Terms of use not read**: check before using it in anything RBI sends. | |
| History | Back to at least 1998: the June–August 1998 Colorado sample has 27 reports (CoCoRaHS began in Colorado). | fetched |
| Latency | Entry delay (EntryDateTime minus observation time), territory, 2023 season: median **0.7 h**, p90 20.5 h. 2010: median 14.8 h. Usable within hours, not during the storm. | computed |
| Coverage | Territory, April–September 2023: **639 reports from 191 stations on 75 days**. In 2010: 476 reports, 200 stations. Largest stone (2023): 433 under 0.75 in, 52 at 0.75–1.00 in, **73 at 1.00 in or more**, 81 with no size. | computed |
| Hail pads | Only 8 of 639 reports in 2023 carry a pad stone count (142 in 2010). "Hail pad" is now mostly a visual report with sizes, not a pad. | computed |
| Format | CSV (stdlib). Fields include smallest/average/largest size, duration, timing, stone consistency, a damage note, angle of impact, pad stone count. **Values have leading spaces**: read with `skipinitialspace=True`. Sizes can be `NA`. | read |
| Independence | **Independent of radar** (ground observers). **Partly in our reports already**: IEM carries some as source `COCORAHS` (191 reports in `reports_raw.csv`; 13 in 2023). Of 639 territory reports in 2023, only **151 (24%)** have an IEM hail report within 3 km on the same date, and 26 of those match an IEM report whose source is COCORAHS. So about 3 in 4 are new points, mostly small hail. | computed |

**Unverified:**
- whether ObservationTime is the start of hail or the time of the daily observation (it looks
  like hail time);
- the 3 km / same-date match rule is mine.

Samples: `samples/cocorahs/`.
