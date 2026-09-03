## Docker

### Docker-Postgres
1. Connect using `psql` from inside the container:
```
docker exec -it <database_name> psql -U <postgres_user_name>

<postgres_user_name>=#
```

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

