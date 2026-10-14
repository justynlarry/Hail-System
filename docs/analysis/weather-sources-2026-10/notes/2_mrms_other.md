# 2. MRMS products beyond MESH: rotation tracks, POSH, SHI (and radar quality)

**Verdict:**
- **SHI and POSH fail on independence.** They are MESH in other forms.
- **Rotation tracks pass, but weakly.** They are the one partly independent MRMS signal: velocity,
  not reflectivity.
- **RadarQualityIndex**, not a hail product, is worth carrying into Part B as a covariate for the
  open foothills question.

| | Finding | Verified how |
|---|---|---|
| Access | NOAA Open Data, `https://noaa-mrms-pds.s3.amazonaws.com/CONUS/<product>/YYYYMMDD/MRMS_<product>_YYYYMMDD-HHMMSS.grib2.gz`, anonymous HTTPS, same as MESH | listed and fetched |
| Products present | `RotationTrack{30,60,120,240,360,1440}min_00.50` (0–2 km), `RotationTrackML…` (3–6 km), `MergedAzShear_0-2kmAGL` and `_3-6kmAGL`, `POSH_00.50`, `SHI_00.50`, `VII`, `VIL`, `RadarQualityIndex_00.00`, and others | bucket listing |
| Cost, terms | Free, the NODD licence, as for MESH | as for MESH |
| History | All start **2020-10-14** on S3. IEM mtarchive has only `RotationTrack1440min` (from 2019, sampled 2019-09-01) and `RadarQualityIndex`; there is no POSH or SHI there. | listing |
| Latency | Published every 2 min in near real time (S3 LastModified is about 1–2 min after the stamp on the 2026-06-08 MESH files). Exact latency of these products **not measured**. | |
| Coverage | Same CONUS grid as MESH. Rotation tracks are on a **finer 0.005° grid** (14,000 × 7,000). | decoded |
| Format | GRIB2, PNG packing; the mesh60 pygrib reader works. pygrib reports name and units as "unknown" (MRMS local tables, discipline 209). Units below are inferred, **unverified** against the MRMS table. | decoded 2023-05-10 23:00 UTC |
| Memory | Rotation track decode: **995 MB peak RSS** (the full float64 grid is about 0.8 GB). That is under the 2 GB line but four times MESH. 0.01° products stay at about 0.4 GB. | measured |
| File size | Rotation track about 1.3–1.6 MB per file; POSH, SHI and MESH 40–80 KB | fetched |

Values over the territory at the test hour:
- **RotationTrack60min:** integers −5 to 30, with 69% of cells > 0 (background noise; a threshold is needed).
- **RotationTrackML60min:** −2 to 22.
- **POSH:** 0–100 (%), with 1.2% of cells > 0.
- **SHI:** up to 213, with 2.4% > 0.
- **RadarQualityIndex:** 0.5–1.0 in 5 steps.

**Independence test (2023-05-10 23:00:39 UTC, 1,944 hail cells):**
- MESH equals **2.54 × √SHI** to within 0.2 mm, with correlation 0.99997, and the two have identical
  footprints. **SHI is MESH.**
- POSH is non-zero only where SHI is, and rank-correlates with SHI at 0.94. The rest is its
  freezing-level adjustment. **POSH is MESH rescaled by the environment**, so it adds no new location
  information.
- Rotation tracks come from Doppler velocity (azimuthal shear), a different radar measurement from
  the reflectivity behind MESH. But they are the same radars with the same beam blockage, and they
  mark rotating storms, not hail. **Partly independent**; hail relevance is indirect.

**RadarQualityIndex** marks where the radar beam is blocked or high. The foothills question
(mesh60: "where radar is weak" is undefined) needs exactly this. It is on S3 from 2020-10-14 and on
IEM mtarchive earlier (sampled 2019-09-01 and 2020-06-08).

Samples: `samples/mrms/` (`.grib2.gz`, cropped `.npz`, `probe_mrms.json`). Script: `probe_mrms.py`.
