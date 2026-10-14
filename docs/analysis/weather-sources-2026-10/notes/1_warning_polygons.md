# 1. NWS severe thunderstorm and tornado warning polygons

**Verdict: passes Part A.** It is free and live, and covers every good-fit storm. Each polygon carries
a forecaster's hail-size tag (from 2010-04). It is only partly independent of MESH.

| | Finding | Verified how |
|---|---|---|
| Access | IEM. GeoJSON API: `https://mesonet.agron.iastate.edu/api/1/vtec/sbw_interval.geojson?begints=…&endts=…&wfo=BOU` (no tags). Shapefile/CSV export with tags: `https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py?accept=shapefile&sts=…&ets=…&limitps=1&phenomena=SV,TO&significance=W,W&wfo[]=BOU&wfo[]=PUB&limit1=1` | fetched 2026-10-14 |
| Live | NWS API `https://api.weather.gov/alerts/active?area=CO`. It returned 7 active Colorado alerts at fetch time. It needs a `User-Agent` header. | fetched |
| Cost, terms | Free, no key. NWS products are US government data. | the terms page itself was **not read** |
| History | Polygons back to at least 2004 (Boulder, summer 2004: 236). Hail and wind tags start **2010-04-13** (Boulder/Pueblo: no tags in 2009, 373 of 378 severe-thunderstorm warnings tagged in 2010). | fetched 2004, 2006, 2008–2011, 2012–2023 samples |
| Latency | Minutes, via the NWS API. IEM's own ingest latency **not measured**. | |
| Coverage | Boulder and Pueblo between them cover the whole territory, every warned storm. Summer counts: 146 to 875 severe-thunderstorm polygons (2012: 146, 2023: 875). | fetched |
| Format | GeoJSON (stdlib `json`), or shapefile plus a CSV of attributes. No new dependency for GeoJSON. | read |
| Fields | issue/expire, polygon begin/end, `HAILTAG` (0.75–2.75 in seen), `WINDTAG`, `TORNADOTAG`, `DAMAGETAG`, area in km², forecaster | read |
| Independence | **Partial.** Forecasters warn mainly from the same WSR-88D radars that feed MRMS. Most tornado tags read "RADAR INDICATED". The polygon is a human forecast of where the storm *will* go over 30–60 min, also informed by spotter reports. So it is neither a radar measurement nor a ground report. | reasoning, **not tested** (Part B would test it against MESH) |
| Pre-2020 | The only source here with storm-shaped geometry before MESH exists (2004 on). That is the question Part B would test. | |

Traps found:
- `limitps=1` without an explicit `phenomena=` list silently returns **tornado warnings only**. My
  first pull showed zero severe-thunderstorm warnings in six summers.
- `limit1=yes` means "storm-based warnings only", not "first".

Samples: `samples/warnings/`.
