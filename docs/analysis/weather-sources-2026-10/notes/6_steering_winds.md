# 6. Upper-level steering winds: Denver soundings and HRRR

**Verdict: passes Part A, as two halves.** HRRR covers 2014-07-30 on and can be used live. Denver
soundings cover 1919 to **2022-07-09** and then stop. Together they cover all 180 good-fit storms.
Fully independent of radar and reports.

## Denver soundings

| | Finding | Verified how |
|---|---|---|
| Access | IEM: `https://mesonet.agron.iastate.edu/cgi-bin/request/raob.py?station=KDNR&sts=…&ets=…` (CSV). NOAA IGRA2: `https://www.ncei.noaa.gov/data/integrated-global-radiosonde-archive/access/data-por/USM00072469-data.txt.zip` (77 MB, fixed-width text). | fetched both |
| Cost, terms | Free; NOAA public data. Terms **not read**. | |
| History | IGRA2: 83,085 soundings, 1919 to **2022-07-09**. The file was updated 2026-10-13, so the end is not a stale archive. IEM: complete through 2022-06 (about 60 soundings a month), nothing from 2022-09 on. **Why Denver stopped, or whether it continues under another ID, is unverified.** In IGRA2's list the nearest active sites are Grand Junction (USM00072476, west of the Divide, about 300 km) and North Platte, NE (USM00072562, about 330 km); neither represents Front Range steering flow. | fetched |
| Latency | Two launches a day (00 and 12 UTC); 00 UTC is 6 pm MDT, close to afternoon storms. | |
| Coverage | One point (Stapleton/DIA area) for the whole territory. Covers 125 of the 180 good-fit storms, but only **5 of the 60** MESH storms. | computed |
| Format | IGRA2 fixed-width text, stdlib. Streamed: 16 MB RSS. 700/500/400/300 mb winds extracted to `samples/winds/igra2_denver_steering.csv` (230,817 rows). | done |

## HRRR (model analysis winds)

| | Finding | Verified how |
|---|---|---|
| Access | `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.YYYYMMDD/conus/hrrr.tHHz.wrfprsf00.grib2`, anonymous. Each file has a `.idx`, so **byte-range** requests fetch single fields. | fetched |
| Size | A full hourly pressure file is **409 MB**. The 8 wind fields (u and v at 700/500/400/300 mb) are **4.8 MB** by byte range. A full file is never needed. | fetched |
| Cost, terms | Free; NODD. | |
| History | From **2014-07-30** on AWS (first prefix). Covers 110 of the 180 good-fit storms and all 60. | listing |
| Latency | Hourly analyses and forecasts in near real time, so usable live (as a forecast of storm motion). **Exact AWS posting latency not measured.** | |
| Format | GRIB2 on a Lambert grid (1,059 × 1,799); pygrib reads it. Peak 159 MB RSS. **Winds are grid-relative** (`uvRelativeToGrid = 1`), so they must be rotated to true north: about −4° near 104 °W (central longitude 97.5 °W, cone factor ≈ 0.62). | decoded |
| RAP | The `noaa-rap-pds` bucket exists; its contents and history **not checked**. HRRR is enough for 2014 on. | listing |

**One-storm smoke test (not a result).** A01973 (2023-05-10), 22 UTC HRRR at the storm centroid,
grid-relative winds:
- 700 mb blowing toward 317°;
- 500 mb toward 15°;
- 300 mb toward 24°.

The storm's fitted motion was toward 54° (PCA axis 55°), so 500 mb is about 40° to the left of
storm motion. Severe storms often move right of the mean wind (supercell deviant motion), so a raw
level wind may not be the right predictor. Part B would test which level or layer mean works best.

Samples: `samples/winds/`.
