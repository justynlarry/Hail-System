# Radar anchors: how often a non-hail report made radar hail "core" (item 254)

Measured and fixed 2026-10-28. Read-only measurement; the fix is one line in `hailsys/radar/zones.py`.

## The question

The radar layers (`hailsys/radar/zones.py`) call a patch of radar hail **core** when an anchor report lies within the
report radius (5 km). Until 2026-10-28 an anchor was any roof-relevant report at or above its type's minimum: hail, but
also wind gusts, wind damage, tornadoes, snow and ice. The likelihood build (`hailsys/likelihood/build.py`), which
drives the hail zones, pulls, zone matches and zone emails, has always anchored on HAIL only. So the staff Territory
map and the findings panel could show core where no hail report supported it.

## How it was measured

`measure.py` runs on one connection that is never committed, writes only temporary tables and rolls back. For every
loaded radar day it takes the day's patches (cells at or above the radar set's threshold, grouped as the radar build
groups them) and tests each against two anchor sets within the radius:

- **current:** any roof-relevant report at or above its minimum (the old `_REPORTS_SQL`);
- **hail only:** actionable IEM HAIL not sourced from CoCoRaHS, plus CoCoRaHS hail at or above the likelihood set's
  floor (the likelihood build's anchors).

Rerun:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./docs/analysis/radar-anchors-2026-10:/out web python /out/measure.py /out/by_day.csv

It took under two minutes for 2,199 days. `by_day.csv` holds one row per day.

**Check:** the "current" patch and anchored-patch counts equalled the counts the stored radar builds record on all
2,199 of 2,199 days, so the script reproduces the build.

## Results (radar set 1, before the fix)

| | Current rule | Hail only |
|---|---|---|
| Core patches | 741 | 627 |
| Core area | 74,381 km² | 66,030 km² |

- **Wind-only core** (core now, not under hail only): **117 patches, 8,423 km²** (16% of core patches, 11% of core area).
- **Days affected:** 61 of the 196 days with any core.
- **CoCoRaHS-only core** (the other way): 3 patches, 72 km². Negligible.
- **Reports near wind-only patches:** TSTM WND GST 82, TORNADO 57, TSTM WND DMG 33, NON-TSTM WND GST 4, LANDSPOUT 1.
- **Worst days:** 2026-08-21 (all 1,096 km² of its core), 2026-06-24 (676 of 2,330), 2024-06-08 (667 of 1,663),
  2026-08-12 (634 of 634), 2021-07-09 (576 of 803), 2024-08-07 (572 of 732).
- **No day in the last 30 days** (the send window) was affected.

| Year | Wind-only patches | Wind-only km² | Core km² (current) |
|---|---|---|---|
| 2021 | 15 | 972 | 4,351 |
| 2022 | 12 | 432 | 6,191 |
| 2023 | 15 | 542 | 27,468 |
| 2024 | 27 | 2,106 | 10,893 |
| 2025 | 11 | 672 | 10,510 |
| 2026 | 37 | 3,699 | 14,967 |

## The fix and the rebuild

`_REPORTS_SQL` gained `AND i.report_text = 'HAIL'` (IEM hail, all sources). CoCoRaHS filings are not used by the radar
layer: that would tie the radar set to the likelihood set's size floor for 72 km² over six years. All 2,199 days were
rebuilt from their stored cells (`scripts/radar_ingest.py rebuild --days 2300`, 22:32 to 22:35 UTC, no download).

**Rebuild check:** 624 anchored patches in total, equal to the current core minus the wind-only patches (741 − 117) on
every one of the 2,199 days. Core area 65,958 km², 72 km² under the hail-only measurement (the CoCoRaHS-only
patches). 2026-08-21 now has no core. The likelihood zones, the zone matches and `send_log` were unchanged.
