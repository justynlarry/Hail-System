-- Make sure PostGis is installed on the system first

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

COMMIT;
