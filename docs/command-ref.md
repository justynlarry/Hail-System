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

