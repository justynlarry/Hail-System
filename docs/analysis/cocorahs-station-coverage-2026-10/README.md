# CoCoRaHS stations against the daily radar: where observers are, and what the ones inside radar hail filed (2026-10-23)

Analysis only. Written in-session by Claude, not keyed in. It reads public data (NOAA's `MESH_Max_1440min`, the CoCoRaHS public
export) and CSVs already collected, and writes only under `--out` and the two cache folders. Nothing here touches the database, RentCast
or any email. It answers part of parking-lot item 253 (can absence be recorded?) and feeds the hail-likelihood design (decision log
2026-10-23, points 7 and 9).

**What it answers.** A CoCoRaHS *hail* report is filed only when hail fell, so it cannot say where hail did not fall. A station that files a
*daily precipitation* report is an observer who was there. For every station that filed the daily report covering a storm day:
- did it file a hail report, only rain, or was it dry (0.00)? How does that change with the radar value within 3, 5 and 8 km, and with whether
  an independent IEM hail report is within 5 km?
- how much of the radar hail area has any observer within 5 km at all (reporting, wet, or filing hail)? The precision study found 1% of
  radar-only area had a CoCoRaHS *hail* point; this says how much had a *station*.

A dry station inside radar hail is the strongest absence signal there is (hail leaves precipitation). A wet station with no hail report is a
weak one (observers do not always file hail). Neither tells us whether the radar was right at a roof, only whether observers a few km away
saw what it said.

**Files**
- `station_coverage.py`: the study (standard library only, one process per worker, resumable).
- Inputs are not in git (`output/` is ignored): the CoCoRaHS hail CSVs and the IEM CSV from the precision study (`hail_CO_*.csv`,
  `iem_lsr_CO.csv`), and a list of days.

## 1. Inputs (on the workstation; the precision study's folder already has most of it)

    cd ~/Hail-System && git pull
    ls ~/hail-analysis/cocorahs_precision/out/days | sed 's/\.json$//' > ~/station-days.txt     # 677 days: those with CoCoRaHS hail points or radar

The daily MESH files are already cached in `~/hail-analysis/cocorahs_precision/cache/` (676 of them; the one NOAA lacks is skipped). The
CoCoRaHS daily reports are downloaded by the script itself, one small public request per day, about 90 KB each, into `--daily-cache`.

## 2. Run it

    D=~/hail-analysis/cocorahs_precision
    python3 docs/analysis/cocorahs-station-coverage-2026-10/station_coverage.py \
        --hail $D/data/hail_CO_*.csv --iem $D/data/iem_lsr_CO.csv --days ~/station-days.txt \
        --out ~/station-coverage-out --mesh-cache $D/cache --daily-cache ~/cocorahs-daily \
        --workers 2 --limit 2                                  # a two day trial first, about a minute
    nice -n 10 nohup python3 docs/analysis/cocorahs-station-coverage-2026-10/station_coverage.py \
        --hail $D/data/hail_CO_*.csv --iem $D/data/iem_lsr_CO.csv --days ~/station-days.txt \
        --out ~/station-coverage-out --mesh-cache $D/cache --daily-cache ~/cocorahs-daily \
        --workers 5 --contact "your name or an address CoCoRaHS can write to" > ~/station-coverage.log 2>&1 &
    tail -f ~/station-coverage.log

(Run the trial with its own `--out`, or delete it, before the full run, since the full run carries on from the day files already there.)
`--contact` goes in the User-Agent of the CoCoRaHS requests so they can reach whoever runs this; leave it out and the requests say so.
`--pause` (default 1 second) is the wait after each request; the downloads are serial on purpose. **Time:** the 677 downloads take about
12 minutes, the processing about 4.5 seconds a day per worker (about 10 minutes with 5), so roughly 25 minutes. Memory is small.
Stop it any time; running the same command again carries on from the files already in `--cache` and `--out`.
`--summarize --out ~/station-coverage-out` prints the tables for what is there.

## 3. Outputs, in `--out`

`summary.txt` (the tables, below), `station_days.csv` (one row per station-day inside or near radar hail, or with a hail filing; the dry-country
baseline of zeros is left out), `days/*.json` (one record per day). Bring `summary.txt` back to hail-dev and record the result in the decision
log. The tables: **A** the radar hail area and the observers near it; **B** station-days by the radar value at the station (dry, wet with no hail,
hail filed); **C** the same for MESH of 1.00 and 0.75 in by whether an independent IEM report is near, and for 3 and 8 km; **D** radar-only
station-days by year and region; **E** hail filings of 1 in or more against the radar.

## Caveats

- **The day.** A daily report covers the 24 hours ending about 7 AM on its date, so a storm on local day D is read from the report dated D+1
  (checked on 2024-05-30: evening hail stations read 0.15 to 0.64 in on 5/31 and about 0.00 on 5/30). Hail from midnight to 7 AM on D is in
  the D report and is not read here. The hail filings use the date the hail fell.
- **Wet is any precipitation, including a trace (`T`).** A wet station with no hail report may simply not have filed one. Rain falls where
  hail does not, so "wet, no hail" is a weak absence at best; "dry" (0.00) is strong at the station, a few km from the radar cell.
- **Radar and a station disagree by a few km by nature.** MESH is a 1 km grid and the radar value is a maximum within 3, 5 or 8 km, which
  reads high against a point (item 205 evidence). A station 4 km from a cell is not evidence about the cell.
- **Observers cluster where people live.** The tables count stations, not area; table A is the area view.
- **Stations that filed hail but no daily report** are kept (precipitation unknown) and counted separately.
- Hail size is the largest stone, else the average when only that was filed. Days over NOAA's 10 in ceiling, and the four days known to
  hold impossible values (items 247 and 250), are left out.
- Hail reports from the IEM are independent ones only (CoCoRaHS-sourced IEM reports are the same observation relayed).
