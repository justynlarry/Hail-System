# 4. mPING public reports

**Verdict: passes Part A (updated 2026-10-14, with an API key).** It is live, dense, and on the ground,
so independent of radar.
- Large-hail reports are mostly already in our IEM reports (as PUBLIC).
- What it adds is mostly small hail and many extra points.
- **The licence offered is "Research" only.** Product use needs mPING's agreement (see below).

| | Finding | Verified how |
|---|---|---|
| Access | API v2 `https://mping.ou.edu/mping/api/v2/reports`, header `Authorization: Token <key>`. Filters: `category=Hail`, `obtime_gte` / `obtime_lte` (`YYYY-MM-DD HH:MM:SS`, UTC), `in_bbox=lon_min,lat_min,lon_max,lat_max`, `year`. Paginated via `next`. Without a key: 401. | fetched 2026-10-14 (6 requests) |
| Key | `MPING_API_KEY` in `~/Hail-System/.env`, read by `probe_mping.py` and never printed or logged (checked: the key string appears in no log or sample file). | |
| Cost, terms | Free. Registration at `https://mping.ou.edu/registration/register/` offers **only a "Research" licence**. The licence text was not read. RBI's use would be commercial, so **ask mPING before using it in the product**. | form read |
| History | The territory's first hail report is 2013-03-17; none in 2012. 2013: 88 territory hail reports; 2014: 373. | fetched |
| Latency | **Live.** The newest report anywhere was 2.3 min old at query time (16:03:49 UTC vs 16:06:08 UTC). | fetched |
| Coverage | Territory, April–September 2023: **2,370 hail reports on 88 days** (CoCoRaHS: 639). Sizes: 0.25 in 1,486; 0.50 in 446; 0.75 in 211; **≥ 1.00 in: 227**. Test day 2023-05-10: 71 territory hail reports, 17 at ≥ 1.00 in. | fetched |
| Format | JSON (stdlib). Each report: id, obtime (UTC), description (size picklist, e.g. "Quarter (1.00 in.)"), point geometry (phone GPS, full precision). | read |
| Independence | Ground reports, independent of radar. **Overlap with our IEM HAIL reports (2023, within 3 km and 60 min):** 615 of 2,370 (26%) overall, but **185 of 227 (81%) of mPING reports ≥ 1.00 in**. The matched IEM reports are mostly `PUBLIC` (357) and `TRAINED SPOTTER` (195), consistent with parking-lot item 7. So mPING adds about 1,750 small-hail points and about 42 large-hail points a season. | computed |

**Unverified:**
- the licence text;
- whether the API returns every public hail report (the docs say some categories are NWS-only;
  hail sizes are marked public);
- the earliest 2013 reports (March) may include graupel tapped as "Pea";
- the 3 km / 60 min match rule is mine.

Samples: `samples/mping/` (query caches `q_*.json`, `probe_mping.json`). Script: `probe_mping.py`.
