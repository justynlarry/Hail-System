## Docker

### Docker-Postgres
1. Connect using `psql` from inside the container:
```
docker exec -it <database_name> psql -U <postgres_user_name>

<postgres_user_name>=#
```

## Which services see your edits

`web` bind-mounts `./hailsys`, so editing a file there IS deploying it --
the change is live at the next `docker compose restart web`.

`loader` bind-mounts the whole repo read-only at `/repo`, so it always reads
the SQL and scripts as they are on disk. Its image carries only the postgis
client tools; rebuild it only when `docker/loader.Dockerfile` changes.

`app` and `ingest` do NOT. Their Python is baked into the image at build
time. Running them without rebuilding executes whatever code was current at
the last build, against the live database, with no warning.

    docker compose build app      # before any `docker compose run --rm app`
    docker compose build ingest   # after ANY change under hailsys/ or
                                  # scripts/

The scheduled ingest jobs -- nightly `iem_ingest.timer` and weekly
`iem_weekly_replay.timer` -- deliberately do NOT build first: an unattended
job should run a known artifact, not whatever is half-finished in the
working tree. The cost is that ingest code changes require a manual rebuild
to take effect. This is a choice, not an oversight.

Note that `ingest` imports from `hailsys/` broadly -- db.py and tuning.py
included -- so a change anywhere in the package can leave the ingest image
stale even when scripts/iem_ingest.py hasn't moved.

Cost us a false test result on 2026-09-24: the same workstate check
returned a pre-migration answer through `app` and the correct one through
`web`.

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

