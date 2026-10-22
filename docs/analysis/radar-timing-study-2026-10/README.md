# Radar against hail reports, with timing, on many days (2026-10-22)

Analysis only. Written in-session by Claude, not keyed in. It reads public NOAA data (MRMS `MESH_Max_60min`, no key, no
cost) and a CSV of IEM hail reports, and writes only under `--out`. Nothing here touches the database, RentCast or any
email. It is the large version of the checks recorded in `docs/decision-log.md`, 2026-10-22 ("What the radar supports...").

**What it answers.** For every hail report: what did the radar show within 3, 5 and 8 km, in the report's own hour and
the hour either side (so timing, not only place)? Then: how does radar size compare with reported size when the hours
match, how does agreement vary by region and month, and which way do the hail swaths move?

**Files**
- `radar_study.py`: the study (standard library only, one process per worker, resumable).
- Inputs are not in git (`output/` is ignored): `reports.csv` and `days.txt`, exported from the `hail-dev` database.

## 1. Export the inputs (on hail-dev, in the repo folder)

    mkdir -p output/radar-study-inputs
    docker compose exec -T postgis psql -U hail_admin -d weather-property -At -F, -c "
    select (utc_datetime at time zone 'America/Denver')::date,
           to_char(utc_datetime,'YYYY-MM-DD\"T\"HH24:MI:SS'), magnitude, longitude, latitude
    from iem_data where report_type='H' and magnitude is not null
      and utc_datetime >= '2020-10-15' and utc_datetime < '2026-10-22'
      and latitude between 37 and 41.5 and longitude between -106.3 and -101.4
    order by utc_datetime" > output/radar-study-inputs/reports.csv

    docker compose exec -T postgis psql -U hail_admin -d weather-property -At -c "
    select day from (
      select day from radar_days where status='loaded' and max_mm >= 25.4
      union
      select (utc_datetime at time zone 'America/Denver')::date from iem_data
       where report_type='H' and magnitude is not null and utc_datetime >= '2020-10-15' and utc_datetime < '2026-10-22'
         and latitude between 37 and 41.5 and longitude between -106.3 and -101.4) d
    where day not in ('2022-01-03','2022-01-11') and day between '2020-10-15' and '2026-10-21'
    order by day" > output/radar-study-inputs/days.txt

(2022-01-03 and 2022-01-11 are the two days NOAA's file holds impossible values, item 247. 2026-10-22 export: 3,002
reports, 495 days. A list of every loaded day, 2,194, makes `days_all.txt`.)

## 2. Run it (on the workstation)

    git pull
    mkdir -p output/radar-study-inputs          # then copy reports.csv and days.txt into it (scp)
    python3 docs/analysis/radar-timing-study-2026-10/radar_study.py \
        --reports output/radar-study-inputs/reports.csv --days output/radar-study-inputs/days.txt \
        --out ~/radar-study-out --workers 2 --limit 2          # a two day trial first, about 3 minutes
    nice -n 10 nohup python3 docs/analysis/radar-timing-study-2026-10/radar_study.py \
        --reports output/radar-study-inputs/reports.csv --days output/radar-study-inputs/days.txt \
        --out ~/radar-study-out --workers 5 > ~/radar-study.log 2>&1 &
    tail -f ~/radar-study.log

Stop it any time; running the same command again carries on from the day files already in `--out`
(`--summarize --out ~/radar-study-out` prints the tables for what is there). **Time:** about 4.5 s per hourly file per
worker on the workstation (3.4 s decode, about 1 s download): 495 days is 11,880 files, roughly 3 hours with 5 workers; all
2,194 days is about 52,700 files, roughly 13 hours. Disk: each file is deleted after it is read; the output is a few MB.
Memory: about 0.3 GB per worker. Use `--cache DIR` to keep the downloads (about 100 KB each) if a re-run is likely.

## 3. Outputs, in `--out`

`summary.txt` (the tables), `reports_timing.csv` (one row per report with the radar values in its own and neighbouring
hours), `days/*.json` (one record per day, including the storm tracks). Bring `summary.txt` and `reports_timing.csv` back
to hail-dev and record the result in the decision log.

## Caveats

- Hail reports only, from the IEM, which are the reports the zones use; reports cluster where people are.
- The radar value is a maximum over 3, 5 or 8 km in one hour, which reads high against a single report (item 205 evidence).
- A storm track is the centroid of a connected swath of MESH of 1 inch or more (at least 15 cells), linked hour to hour
  within 100 km; swaths that merge or split, or grow without moving, give odd tracks. The trial on 3 days found the first
  version, which followed one chain through a whole day, was wrong when two storms overlap; this version keeps every
  swath as its own chain.
- Day files from an older version of the script have a `track` key, not `tracks`; start a new `--out` after editing the script.
