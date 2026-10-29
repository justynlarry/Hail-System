## Docker

### Docker-Postgres
1. Connect using `psql` from inside the container:
```
docker exec -it <database_name> psql -U <postgres_user_name>

<postgres_user_name>=#
```

## Which services see your edits

`web` and `app` bind-mount `./hailsys` read-only (`app` also `./scripts`), so
editing a file there IS deploying it. `web` is live at the next
`docker compose restart web`; `app` is a fresh container on every `run`, so it
sees the change at once. `app` also keeps a read-write mount of
`hailsys/web/static`, because the GeoJSON builders write there.

`loader` bind-mounts the whole repo read-only at `/repo`, so it always reads
the SQL and scripts as they are on disk. Its image carries only the postgis
client tools; rebuild it only when `docker/loader.Dockerfile` changes.

`ingest` does NOT. Its Python is baked into the image at build time. Running
it without rebuilding executes whatever code was current at the last build,
against the live database, with no warning.

    docker compose build ingest   # after a change to anything it imports
                                  # (hailsys/iem/, hailsys/radar/, hailsys/db.py,
                                  # hailsys/tuning.py, hailsys/logconfig.py,
                                  # scripts/iem_*.py, scripts/radar_*.py) or to
                                  # requirements.txt

`scripts/status.sh images` says which images are behind the files they depend
on, and names the files. `web` and `app` need a rebuild only when
`requirements.txt` or `docker/app.Dockerfile` changes.

The scheduled ingest jobs -- nightly `iem_ingest.timer` and weekly
`iem_weekly_replay.timer`, and the radar jobs `radar_ingest.timer` and
`radar_weekly_rebuild.timer` -- deliberately do NOT build first: an unattended
job should run a known artifact, not whatever is half-finished in the
working tree. The cost is that ingest code changes require a manual rebuild
to take effect. This is a choice, not an oversight.

`ingest` imports `hailsys/iem/` (`common.py`, `parse.py`), `hailsys/logconfig.py` and, since the radar
jobs (2026-10-22), `hailsys/db.py`, `hailsys/tuning.py` and the whole `hailsys/radar/` package (checked
2026-10-22 by importing `iem_ingest`, `iem_backfill` and `radar_ingest` and listing `sys.modules`; before
the radar jobs it imported only the IEM files and `logconfig.py`, checked 2026-10-02). A change elsewhere in
the package therefore does not make the ingest image stale. `scripts/status.sh images` holds that list;
re-derive it if ingest gains an import. The image before the radar jobs is still tagged
`hail-system-ingest:before-radar` (rollback: `docker tag hail-system-ingest:before-radar
hail-system-ingest:latest`).

### Radar jobs (Phase 5b)

`scripts/radar_ingest.py` runs in the baked `ingest` image as `hail_ingest`. Nothing here sends anything,
calls RentCast, or touches pulls and matching; NOAA radar and IEM warnings are public and cost nothing.

    docker compose run --rm ingest python3 scripts/radar_ingest.py nightly            # last 3 finished days (timer, 10:30 UTC)
    docker compose run --rm ingest python3 scripts/radar_ingest.py rebuild --days 30  # zones again, no downloads (weekly timer, Sun 11:30 UTC)
    docker compose run --rm ingest python3 scripts/radar_ingest.py backfill [--from D] [--to D] [--limit N]
    docker compose run --rm ingest python3 scripts/radar_ingest.py warnings [--from D] [--to D]
    docker compose run --rm ingest python3 scripts/radar_day.py 2026-06-08 [--rebuild|--refetch] [--set N] [--no-warnings]

- A day is *finished* two hours after its local midnight (the warnings rule), so the newest day a run touches
  is yesterday's. Backfill goes newest first, skips days already held (so it can be stopped and started
  again), and cannot start before 2020-10-14, where NOAA's archive begins. `warnings` fills the NWS
  warnings back to the first stored report for the years before the radar.
- A day that fails is rolled back and named at the end; the run goes on and ends `failed`, exit status 1.
- A day whose file holds a value above 10 in (254 mm) is recorded `suspect` and neither stored nor drawn (item 247); re-fetch it with
  `radar_day.py DAY --refetch` if NOAA ever corrects the file.
- A long backfill outlives a shell that closes only if it was started detached:
  `docker compose run -d --rm ingest python3 scripts/radar_ingest.py backfill`, then
  `docker logs -f <container>`. Started attached from a shell that ends, the container keeps running but
  its output is lost to that shell (it is still in `docker logs` until it exits).
- `scripts/status.sh radar` (own exit status, 1 if no nightly radar run completed in 30 hours) and the
  radar line on the Storm Days page show the state.
- Do not run `tests/test_radar_ingest.py` while a radar job runs: its cleanup deletes the newest
  `radar_runs` rows (item 245). The radar database tests need `hail_admin`, see item 241.

Cost us a false test result on 2026-09-24: the same workstate check
returned a pre-migration answer through `app` and the correct one through
`web`.

Before 2026-10-02 `app` baked both `hailsys/` and `scripts/`, and
`scripts/verify_zip_distances.py`'s docstring worked around it with
`-v "$PWD/scripts:/app/scripts:ro"`. Item 30 made `app` read the working tree,
so that workaround is no longer needed.

The `loader` image was built 2026-09-09 and already includes the
`docker/loader.Dockerfile` change from that day (checked 2026-10-02 with
`docker history`; parking-lot item 101). Rebuild it only when the Dockerfile
changes. A from-scratch build has not been tried since the base's EOL date
(item 3).

**To check what an image actually runs**, print the source of the function
you changed from inside it. This reads the baked-in copy, not your working
tree:

    docker compose run --rm ingest python -c \
      "import inspect, hailsys.iem.common as c; print(inspect.getsource(c.configure_logging))"

Swap in the module and function you care about. If it
prints the old code, rebuild before testing anything. That turns a would-be
false result ("my change didn't work") into a clear one ("the image is
stale") in two commands.

## Running the tests

The full suite runs in the `test` service (item 222): the app image plus Node, so the `tests/js/` checks run
with it, and `systemd/` and `planning/` mounted for the tests that read them. Only started by hand, under the
`test` profile:

    docker compose --profile test run --rm -T test

It bind-mounts `hailsys`, `scripts` and `tests`, so code and test edits need no rebuild. Rebuild it after a
change to `requirements.txt` or `docker/app.Dockerfile`:

    docker compose --profile test build test

One file, in `web` (each test file's docstring gives its own command):

    docker compose run --rm --no-deps -v ./tests:/app/tests:ro web python -m unittest tests.test_cc_tokens

A run as `hail_app` ends with errors that are not bugs: 256 on 2026-10-29, "permission denied" on the radar
tables and three others (item 241). A count above that, or a module not in item 241's list, is a real failure.

## Reading logs

    docker compose logs -f web            # the web app, pull thread and matcher
    docker compose logs --since 1h web    # just the last hour
    journalctl -u iem_ingest.service      # the nightly ingest (systemd, not Docker)

`web` logs go to Docker's `json-file` driver, rotated at 20 MB x 5, so
`journalctl` doesn't have them. Each line reads
`level=INFO logger=<module> event=...`.

**`restart` vs `up -d`.** `docker compose restart web` restarts the same
container: it picks up code changes (the bind-mounted `hailsys/`) but keeps
the container's existing configuration. A change to `docker-compose.yml`
itself -- logging options, environment, ports, volumes -- only takes effect
when the container is recreated:

    docker compose up -d web    # recreates web if its compose config changed

## Posgres (in Docker)
1. Create Database:
```
# createdb <database_name>
```
1a. Drop Database:
```
# dropdb <database_name>
```

2. Create Table:
```
CREATE TABLE <table_name> (
> <field_name>		<field_type>(<length>),
> );
```

2a. Drop Table
```
# drop TABLE <table_name>;
```

3. Add rows to an existing table:
```
# INSERT INTO <table_name> VALUES ('<field-01>', <field-02>, etc.);
```
- Strings and dates get quotes around them, numbers do not

4. Copy large amounts of data into an existing table
```
# COPY <table_name> FROM '<file_name>.txt';
```

5. Delete data from a table:
```
# DELETE FROM <table_name> WHERE <field> = '<value>';
```

### Postgres Fields:
`TEXT` - Postgres stores `TEXT`,`VARCHAR(n)`, and `VARCHAR` identically, `VARCHAR(n)` adds only length check.
`CHAR(n)` - blank-padded, treated as a 'trap,' not an option
`CHECK` - When you need a rule, in my DB -> `CHECK (zcta5 ~ '^[0-9]{5}$')` -> says something true, `CHAR(5)` pads.
`INTEGER` - for counts
`BIGINT` - surrogate keys, because widening the column later is painful, and it's an extra 4 bytes
`NUMERIC(p,s)` - stores exact decimal - `p` total digits, `s` after the decimal point, so:
	`NUMERIC(6,2)` holds up to 9999.99
	- Rule: `NUMERIC` for anything a person reads or compares (money, magnitude, coordinates)
`TIMESTAMPTZ` - Converts input to UTC on write and back to the session time zone on read. 
	- `TIMESTAMP` - stores wall-clock text with no zone awareness
	- `DATE`  - Calendar day with no time
	- `INTERVAL` - for durations
`BOOLEAN` - 3-valued unless `NOT NULL` is specified - `TRUE`,`FALSE`,`NULL`
`JSONB` - for `listings.raw_payload` - Binary, indexable, deduplicates keys.  Stores the literal text and reparses
	on every access.
`GEOMETRY(Point,4326)` - storage and GiST indexing -> cast to `::geography` for distance in meters.  

