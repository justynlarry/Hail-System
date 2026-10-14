# 5. NCEI Storm Events Database

**Verdict: fails on independence for hail.** In the territory it is a near-copy of the IEM reports we
already have, with no damage figures for hail, and it lags 3–4 months.

| | Finding | Verified how |
|---|---|---|
| Access | Bulk CSV: `https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/StormEvents_details-ftp_v1.0_dYYYY_cYYYYMMDD.csv.gz`, plus `locations` and `fatalities` files. No key. | fetched 2023 (12.9 MB) and 2026 (7.5 MB) |
| Cost, terms | Free; NOAA public data. **Terms page not read.** | |
| History | Files from 1950 (`d1950`); hail is recorded from 1955 (the 1955 file exists; its hail content **not checked**). | listing |
| Latency | **About 3–4 months.** On 2026-10-14 the 2026 file (compiled 2026-09-18) runs to June 2026. Files are recompiled, sometimes years later (2023 file compiled 2026-03-23). Historical use only. | read |
| Coverage | Colorado 2023: 2,102 events, 769 hail; **470 hail events in the territory**. | computed |
| Format | gzip CSV, latin-1, stdlib. Times are local **standard** time with a `CZ_TIMEZONE` label (`MST-7` all year, including summer). Applying the label's offset gives an exact UTC match with IEM on 99.8% of matched events; reading it as Denver local time would be **1 h off in summer**. | tested |
| Independence | **Almost none for hail.** 467 of 470 territory hail events (99%) match an IEM hail report within 3 km and 3 h, 99.8% at the identical time and 98% with the identical size. The 3 unmatched are 2 storm-chaser reports and 1 trained-spotter report. Every hail event is a point (begin = end). **`DAMAGE_PROPERTY` is blank or 0 on every territory hail event in 2023.** | computed |
| What it adds | Episode and event narratives, county/zone names, and tornado path length and width. For hail, nothing a lead list can use. | read |

Samples: `samples/stormevents/`.
