# Radar-shaped storm areas (item 206) and the HRRR fallback capsule: prototype on the 60 storms

Analysis only (2026-10-14). Work is in `~/hail-analysis/radar_areas/`. No database, no git, no matcher
change.

**mPING was used for internal validation only, under its research licence (item 215).** It is not shown
on any map, and its raw points are not in the repo. Every map shows LSR and CoCoRaHS points only.

**Inputs** are the same 60 MESH storms, hourly MESH windows and 30 km domains as `mesh60`:
- warning polygons and HRRR winds from `weather_sources/partb/`;
- independent ground points (mPING + CoCoRaHS) from `weather_sources/partb/ground/`.

**Scripts:** `tiers.py` (step 2), `capsule.py` and `stretch.py` (step 3), `maps.py`. Each checkpoints
per storm. Memory stayed under 0.5 GB, with no downloads (everything came from caches).

**Placeholders, to be set by inspections (items 207, 208):**
- the core threshold, MESH ≥ 1.00 in, which is presence, not value (decision log 2026-09-16);
- the anchor distance, 5 km;
- the margin width, 3 km (1 and 5 km shown);
- area-matching in the capsule test.

## Step 2: three tiers

| Tier | Rule (placeholder) |
|---|---|
| **Core** | Connected (8-neighbour) patches of MESH ≥ 1.00 in that come within **5 km** of an actionable report. A swath with no report near it is left out: nobody has said hail fell there (item 206). |
| **Margin** | Cells within **3 km** of the core, outside it |
| **Outer** | Warning polygons (SVR/TOR overlapping the MESH window), outside core and margin |
| Rest | The rest of the 30 km domain |

- **Core coverage:** 51 of 60 storms have a core. 153 of 337 MESH ≥ 1 in patches are anchored to a
  report, but they hold **82.5%** of the MESH ≥ 1 in area (19,094 km²).
- **Unanchored swaths:** these are the "hail nobody reported" area (17.5%). By item 206's rule they
  fall to the margin or outer tier.

**Check against independent ground reports** (mPING + CoCoRaHS, which neither MESH nor the reports
used). Pooled over 60 storms, 3 km margin:

| Tier | Area km² | ≥ 1 in ground points (share) | per 100 km² | **≥ 1 in as share of all ground points there** | Today's circle area in this tier |
|---|---|---|---|---|---|
| Core | 15,743 | 148 (44%) | **0.94** | **52%** | 10,582 (20%) |
| Margin (3 km) | 19,334 | 112 (34%) | **0.58** | **34%** | 11,942 (23%) |
| Outer (warnings) | 137,748 | 65 (20%) | **0.05** | **13%** | 26,399 (50%) |
| Rest | 202,054 | 8 (2%) | 0.004 | 6% | 4,089 (8%) |

- **The tiers separate likelihood.** The density of large-hail ground reports falls by roughly 2×,
  then 12×, then 12× again from core to rest.
- **The large-hail share of ground reports falls 52% → 34% → 13% → 6%.** This is the cleaner
  measure: it does not depend on how many people live there, since it compares big against small
  hail among reports at the same place.
- **Core + margin (35,077 km²) hold 78% of the independent large-hail points.** Today's circles
  (53,013 km²) hold 95%, but they are centred on the reports, and ground reporters cluster where
  the reports are (the same people and places). So the circle's 95% is partly co-location, not
  skill.
- **Half of today's circle area falls in the outer tier, and 8% outside every tier.** That is the
  "Tier 3" of item 205, now measured: 58% of circle area is outside core and margin.
- **Margin sensitivity:**

  | Margin | Large-hail points per 100 km² | Large-hail share of ground points |
  |---|---|---|
  | 1 km | 0.91 | 47% |
  | 3 km | 0.58 | 34% |
  | 5 km | 0.42 | 30% |

  The density falls quickly past about 1–3 km, so the margin should be narrow. Inspections set the
  real width.

### The same validation with CoCoRaHS only (no mPING)

mPING is research-licensed (item 215), so the validation was rerun without it. The full tables for
1, 3 and 5 km margins are in `validation_by_source.txt`. 3 km margin:

| Tier | ≥ 1 in points (share) | per 100 km² | **≥ 1 in share of all CoCoRaHS points there** |
|---|---|---|---|
| Core | 25 (37%) | **0.159** | **60%** (n = 42) |
| Margin (3 km) | 22 (33%) | **0.114** | **33%** (n = 66) |
| Outer (warnings) | 16 (24%) | **0.012** | **20%** (n = 82) |
| Rest | 4 (6%) | 0.002 | 10% (n = 42) |

- **The ranking holds without mPING** on both measures (density and size share), so **the validation
  does not depend on research-licensed data.**
- **The CoCoRaHS sample is small:** 232 points, 67 of them ≥ 1 in, in 39 storms.
- **One wrinkle at the 1 km margin:** there, the margin's density (0.227, 8 points) exceeds the
  core's (0.159). The size share still ranks correctly (60% vs 53%). At 3 and 5 km the order holds on
  both measures.

### Unanchored swaths as their own tier (decided 2026-10-14)

The 17.5% of MESH ≥ 1 in area with no report within 5 km becomes **its own tier**, provisionally
"radar-indicated hail in your area" (wording to settle with the claim question, items 205 and 206).
It is refined once inspection data exists. Split out at the 3 km margin:
- the tier is **3,285 km²**;
- it holds **0 large-hail ground points** (7 points in all, every one mPING and under 1 in; CoCoRaHS:
  none);
- only **0.2%** of today's circle area falls in it.

This is expected: unanchored means nobody reported there, and ground reporters are the same kind of
people in the same places. **Ground reports cannot validate this tier**; inspections or property
counts can (item 208). The other tiers' numbers are unchanged by the split (`five_tier_3km` in each
`per_storm/*.json`).

## Step 3: a fallback capsule aligned with HRRR (reports + HRRR only, for pre-2020 storms and radar gaps)

**3a. Same area as the circle.** Each actionable report gets a capsule (segment ± L along the
direction, buffered by b), area-matched to the 5-mile circle.

| Shape (equal area per report) | Area km² | MESH ≥ 1 in recall, pooled / median | Precision | Jaccard median | Ground ≥ 1 in |
|---|---|---|---|---|---|
| Circle (today) | 53,013 | **56.0%** / 67.2% | 20.2% | **0.176** | 95% |
| Capsule b = 4 km, HRRR right-mover | 53,710 | **57.1%** / 64.2% | 20.3% | 0.151 | 95% |
| Capsule b = 4 km, fitted direction (best case) | 51,898 | 57.2% / 66.5% | 21.0% | 0.185 | 95% |
| Capsule b = 4 km, HRRR 500 mb | 56,507 | 51.8% / 60.3% | 17.5% | 0.135 | 95% |
| Capsule b = 4 km, **perpendicular (control)** | 59,277 | 48.3% / 56.6% | 15.5% | 0.110 | 95% |
| Capsule b = 3 km, HRRR right-mover | 54,937 | 55.1% / 58.0% | 19.1% | 0.139 | 95% |

- **Direction matters.** The right-mover capsule beats the perpendicular control on 38 of 52 storms
  (recall 57% vs 48%).
- **At equal area, though, a capsule does not beat the circle.** 57% vs 56% pooled; the median and
  Jaccard are slightly worse. Even the true fitted direction gets only 57%.
- **Why:** a storm's reports already string out along its track, so their overlapping circles form a
  rough capsule. And hail far from every report cannot be reached by any report-based shape.

**3b. Adding area.** Each circle is stretched by ± L along the direction, against enlarging its
radius to the same total area.

| L | Enlarge radius: gain per 1,000 km² added | **Stretch along HRRR** | Stretch along fitted | Stretch perpendicular |
|---|---|---|---|---|
| 4 km (R → 10.3 km) | 69 km² MESH | **82** | 84 | 48 |
| 8 km (R → 12.1 km) | 57 | **70** | 72 | 36 |
| 16 km (R → 15.1 km) | 46 | **57** | 59 | 29 |

- When outreach goes wider than today's circle, **stretching along the HRRR right-mover gains about
  20–25% more radar hail per km² than enlarging the circle.** That is nearly as good as knowing the
  storm's real track, and about twice as good as the wrong direction.
- With ± 8 km along HRRR, recall rises from 56% to **72%** (enlarged circle at equal area: 70%).

**Reading for the fallback:**
- **Keep the circle as the inner shape.**
- **Use the HRRR direction to decide which way to widen.** It stretches the outer tier along the
  track instead of in every direction.
- This applies to pre-2020 storms (HRRR from 2014-07-30; Denver soundings for 2004–2014) and to
  radar gaps.
- **The gain is real but modest.** A report-only shape cannot see the hail far from reports, which is
  what MESH adds.

## Maps

`maps/A02164.png`, `A02418.png` and `A02156.png` each show:
- core / margin / outer fill (darker = more likely);
- today's circles, and the HRRR-stretched fallback (± 8 km, dashed);
- the HRRR right-mover arrow;
- LSR and CoCoRaHS points (mPING is not mapped: item 215).

## Unverified and limits

- **Every threshold and width is a placeholder** until inspections (items 207, 208). Ground reports
  are a stand-in for damage, not damage.
- **The ground points are not independent of population.** They cluster where people and reports
  are. The size-share measure controls for that only partly.
- **The outer tier is clipped to the 30 km domain.**
- **The anchored-core rule drops 17.5% of MESH ≥ 1 in area** (unreported swaths, like A02156's
  eastern plains). Whether those should be core is a product-claim question (items 205 and 206,
  "estimated" vs "reported").
- **The capsule tests use actionable reports only, the same as the circles.**
- **The 60 storms are 2021–2026.** The fallback's pre-2020 performance is inferred, not tested: there
  is no MESH to test against before 2020-10-14.
- The mountains are still untested: none of the 60 storms is centred west of −105.06.
