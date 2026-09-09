# `config/` — per-installation configuration

Everything in this directory is **specific to one customer**. Nothing here is
part of the generic system, and a second installation replaces all of it without
touching a line of code.

The split this directory exists to make visible:

| | Generic — same for every install | Specific — this customer |
|---|---|---|
| Data | 37 NWS report types, 33,791 national ZCTAs, 37,104 zip/city names | the coverage zip list |
| Loaded by | `scripts/load_reference.sh` | `scripts/load_coverage.sh` |
| Seeds live in | `planning/` | here |
| Rerun cadence | once, at install | whenever territory changes |

`coverage_zips.txt` — one five-digit zip per line, blank lines and `#` comments
allowed. RBI's 193 entries, derived from a one-hour drive time from the Fraser
Ave office, extended down I-25 to Colorado Springs and north to the Fort
Collins/Wellington line.

A roofing company in Dallas points `load_coverage.sh` at their own file:

```
docker compose run --rm loader bash /repo/scripts/load_coverage.sh /repo/config/their_zips.txt
```

**This file is the input, not the state.** `coverage_zips` in the database is
the state, and it is authoritative once loaded — the loader inserts what is
missing and never removes or overwrites, because retirement is a marked row
(`removed_at` / `removed_by`) that a truncating reload would silently resurrect.
Deleting a zip from this file therefore does **not** retire it; that is a
deliberate `UPDATE` against the table.
