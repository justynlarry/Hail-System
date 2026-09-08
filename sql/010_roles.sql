-- 010_roles.sql
--
-- Database roles and grants, run at very end so that all 17 tables exist and GRANT can name existing objects.
--
-- POSTGRES Roles -- Login Accounts enforced by the database engine, unrelated to users.role, which holds
-- admin, sender, viewer for the app's own login model and enforced in Python.
--
-- CONVENTION MOVING FORWARD:  Every SQL file that creates a table needs to end with GRANTS for that table

-- Audit Query:  Run after every new schema file:
--
--	SELECT grantee, table_name, string_agg(privilege_type, ',' ORDER BY privilege_type)
--	FROM information_schema.role_table_grants
--	WHERE grantee IN ('hail_ingest', 'hail_app')
--	GROUP BY grantee, table_name
--	ORDER BY grantee, table_name;
--
--  Set Variables in shell first:  HAIL_INGEST_PASSWORD=<> HAIL_APP_PASSWORD=<>
--	psql -v ON_ERROR_STOP=1 -U hail_admin -d weather-property -f sql/010_roles.sql

\set ON_ERROR_STOP on

-- Read the passwords up front and refuse to run without them.
--
-- Two different failures, and they behave differently in psql:
--
--   UNSET   -- \getenv leaves the variable undefined, and psql passes an
--              undefined :'var' through LITERALLY rather than substituting.
--              That is already loud (syntax error, exit 3) but it would not
--              surface until the ALTER ROLE at the bottom, after every GRANT
--              had run.  The \set below normalises it to '' so the check
--              catches it here instead, with a message that names the variable.
--
--   EMPTY   -- an env var that is set to nothing leaves the variable DEFINED,
--              interpolates cleanly, and would set a blank password while
--              reporting success.  This is the case that actually bites:
--              .env.example ships HAIL_APP_PASSWORD="" and a half-filled .env
--              copied from it looks correct.
--
-- The check is a plain statement plus a DO block, NOT :'var' inside the DO
-- block: psql does not interpolate variables inside dollar-quoted text at all,
-- so :'ingest_password' written between $guard$ markers reaches the server
-- verbatim and is a syntax error even when the password is set correctly.
-- set_config carries the ANSWER (a boolean) across that boundary, never the
-- password itself.
--
-- RAISE rather than \warn + \quit because \quit exits psql with status 0, so
-- a "for f in sql/*.sql" loop would read a skipped roles file as a passing one.
\getenv ingest_password	HAIL_INGEST_PASSWORD
\getenv app_password	HAIL_APP_PASSWORD

\if :{?ingest_password}
\else
\set ingest_password ''
\endif

\if :{?app_password}
\else
\set app_password ''
\endif

SELECT set_config('hail.ingest_pw_missing', (:'ingest_password' = '')::text, false),
       set_config('hail.app_pw_missing',    (:'app_password'    = '')::text, false)
\g /dev/null

DO $guard$
BEGIN
    IF current_setting('hail.ingest_pw_missing')::boolean THEN
        RAISE EXCEPTION 'HAIL_INGEST_PASSWORD is not set in the environment';
    END IF;
    IF current_setting('hail.app_pw_missing')::boolean THEN
        RAISE EXCEPTION 'HAIL_APP_PASSWORD is not set in the environment';
    END IF;
END
$guard$;

BEGIN;

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- Roles
--
-- Hail_admin is a POSTGRES_USER in docker-compose.yml, created by postgis image on initialization.
--
-- CREATE ROLE will throw an error if the role exists.  Passwords are set separately below
--
--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hail_ingest') THEN
	CREATE ROLE hail_ingest LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hail_app') THEN
	CREATE ROLE hail_app LOGIN;
    END IF;
END

$$;

COMMENT ON ROLE hail_ingest IS
    'Nightly ingest and backfill, reads report_types, writes iem_data, iem_ingest_rejects and ingest_runs.';

COMMENT ON ROLE hail_app IS
    'Web application, one database role serving all three app roles: admin/sender/viewer split enforced in '
    'Python, not here.';

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- Connect and Schema Access
--
-- Both required, CONNECT makes it possible to open a session, USAGE makes GRANT reachable
--
--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------

-- The database name is weather-property, which contains a hyphen and would have
-- to be double-quoted in a literal GRANT.  current_database() sidesteps that and
-- keeps this file correct if POSTGRES_DB ever changes.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO hail_ingest, hail_app',
                   current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO hail_ingest, hail_app;

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- hail_ingest
--
-- No sequence grants, each primary key is GENERATED ALWAYS AS IDENTITY, which is reachable through INSERT on the 
-- table.  Cannot DELETE or UPDATE iem_data
--
--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------

GRANT SELECT ON report_types TO hail_ingest;

GRANT SELECT, INSERT ON iem_ingest_rejects TO hail_ingest;

-- iem_data is the whole point of this role.  No UPDATE and no DELETE: storm
-- reports are never modified or removed once written.
GRANT SELECT, INSERT ON iem_data TO hail_ingest;

-- UPDATE is required, not optional: ingest_runs writes the row before the work
-- starts and sets finished_at / run_status / the counts when it ends.  See the
-- finished_has_timestamp comment in 009_ingest.sql.
GRANT SELECT, INSERT, UPDATE ON ingest_runs TO hail_ingest;

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- hail_app
--
-- Grouped by the cost stage the application role maps to, list can be read against the free-browse / paid-pull
-- human-send progression
--
--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------

-- Free Browse - Read-Only Reference and Weather Data
GRANT SELECT ON report_types	TO hail_app;
GRANT SELECT ON report_sources	TO hail_app;
GRANT SELECT ON zcta_boundaries TO hail_app;
GRANT SELECT ON iem_data	TO hail_app;

-- coverage_zips can be edited if the territory that the company using the application changes, rows are retired
-- and marked, but not deleted.

GRANT SELECT, INSERT, UPDATE ON coverage_zips TO hail_app;

-- report_types contains roof_relevant and min_magnitude so that adding a condition is an UPDATE instead of 
-- a deploy, and allows the application to change the type of storm that triggers an outreach.

GRANT UPDATE ON report_types 	TO hail_app;

-- API Pull, written during a request to Rentcast's API

GRANT SELECT, INSERT, UPDATE ON properties		TO hail_app;
GRANT SELECT, INSERT, UPDATE ON listings		TO hail_app;
GRANT SELECT, INSERT, UPDATE ON realtors		TO hail_app;
GRANT SELECT, INSERT         ON storm_listing_matches	TO hail_app;

-- api_pulls written before calls go out, updated when finished, api_call_log is append-only.
GRANT SELECT, INSERT, UPDATE ON api_pulls	TO hail_app;
GRANT SELECT, INSERT	     ON api_call_log	TO hail_app;

GRANT SELECT, INSERT, UPDATE ON send_log	TO hail_app;
GRANT SELECT, INSERT, UPDATE ON email_templates TO hail_app;

-- dnc_list - INSERT for new suppressions including automatic bounce/complaint handling, UPDATE only
-- for records that are removed_at / removed_by, not deleted.

GRANT SELECT, INSERT, UPDATE ON dnc_list TO hail_app;

-- users - created and deactivated accounts are recorded at last_login_at, not deleted, marked is_active.

GRANT SELECT, INSERT, UPDATE ON users TO hail_app;

GRANT SELECT ON ingest_runs		TO hail_app; 
GRANT SELECT ON iem_ingest_rejects	TO hail_app;

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- Passwords
--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------

-- Both variables were read and checked at the top of this file.
-- :'name' with NO space is the psql interpolation form; ": 'name'" is a syntax
-- error, and omitting PASSWORD is a different one.
ALTER ROLE hail_ingest	PASSWORD :'ingest_password';
ALTER ROLE hail_app	PASSWORD :'app_password';

COMMIT;
