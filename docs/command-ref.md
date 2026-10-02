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
                                  # (hailsys/iem/, hailsys/logconfig.py,
                                  # scripts/iem_*.py) or to requirements.txt

`scripts/status.sh images` says which images are behind the files they depend
on, and names the files. `web` and `app` need a rebuild only when
`requirements.txt` or `docker/app.Dockerfile` changes.

The scheduled ingest jobs -- nightly `iem_ingest.timer` and weekly
`iem_weekly_replay.timer` -- deliberately do NOT build first: an unattended
job should run a known artifact, not whatever is half-finished in the
working tree. The cost is that ingest code changes require a manual rebuild
to take effect. This is a choice, not an oversight.

`ingest` imports only `hailsys/iem/common.py`, `hailsys/iem/parse.py` and
`hailsys/logconfig.py` (checked 2026-10-02 by importing both scripts and listing
`sys.modules`; it does NOT import `db.py` or `tuning.py`, which this paragraph
used to say). A change elsewhere in the package therefore does not make the
ingest image stale. `scripts/status.sh images` holds that list; re-derive it
if ingest gains an import.

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

