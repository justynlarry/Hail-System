-- 010_roles.sql
--
-- Database roles and grants, run at very end so that all 17 tables exist and GRANT can name existing objects.
--
-- POSTGRES Roles -- Login Accounts enforced by the database engine, unrelated to users.role, which holds
-- admin, server, viewer for the app's own login model and enforced in Python.
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
--	psql -v ON_ERROR_STOP=1 -U hail_admin -d hail -f sql/010_roles.sql

\set ON_ERROR_STOP on

BEGIN;

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- Roles
--
-- Hail_admin is a POSTGRES_USER in docker-compose.yml, created by postgis image on initialization.
--
-- CREATE ROLE will throw an error is role exists.  Passwords are set separately below
--
--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolnam = 'hail_ingest') THEN
	CREATE ROLE hail_ingest LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hail_app') THEN
	CREATE ROLE hail_app LOGIN;
    END IF;
END

$$;

COMMENT ON ROLE hail_ingest IS
    'Nightly ingest and backfill, reads report_types, writes iem_data, iem_ingest_rejects and ingest_runs.'

COMMENT ON ROLE hail_app IS
    'Web application, one database role serving all three app roles: admin/sender/viewer split enforced in '

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- Connect and Schema Access
--
-- Both required, CONNECT makes it possible to open a session, USAGE makes GRANT reachable
--
--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------

GRANT CONNECT ON DATABASE hail TO hail_ingest, hail_app;
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

GRANT SELECT, INSERT ON ingest_runs TO hail_ingest;

--  ------ ------ ------ ------ --------  ------ ------ ------ ------ --------  ------ ------ ------ ------ -------
-- hail_app
--
-- Gropued by the cost stage the application role maps to, list can be read against the free-browse / paid-pull
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

\getenv ingest_password	HAIL_INGEST_PASSWORD
\getenv app_password	HAIL_APP_PASSWORD

ALTER ROLE hail_ingest PASSWORD : 'ingest_password';
ALTER ROLE hail_app		: 'app_password';

COMMIT;
