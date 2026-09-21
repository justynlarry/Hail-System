-- report_zip_distances: report-to-zip nearest-edge distances, stored
-- instead of computed on demand.  

BEGIN;

-- Ceiling:  16093.44 m = 10 Miles
-- Changing the ceiling requires a CREATE or REPLACE AND a 
-- full recompute (scripts/backfill_zip_distances.py after a TRUNCATE)

CREATE FUNCTION hail_pair_ceiling_m() RETURNS double precision
LANGUAGE sql IMMUTABLE AS $$ SELECT 16093.44::double precision $$;

-- Called from storms.py's shared WHERE clause, every storm query checks
-- its radius before running.

CREATE FUNCTION hail_assert_radius_within_ceiling(radius_m double precision)
RETURNS boolean LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF radius_m > hail_pair_ceiling_m() THEN
    RAISE EXCEPTION 'radius % m exceeds report_zip_distances ceiling %m',
        radius_m, hail_pair_ceiling_m()
        USING HINT = 'Raise hail_pair_ceiling_m() and return the backfill.';
    END IF;
    RETURN true;
END $$;

CREATE TABLE report_zip_distances (
    iem_id      BIGINT              NOT NULL
            CONSTRAINT fk_rzd_iem_data REFERENCES iem_data (iem_id),
    zcta5        TEXT                NOT NULL,
    distance_m   DOUBLE PRECISION    NOT NULL CHECK (distance_m >=0),

    CONSTRAINT report_zip_distances_pkey PRIMARY KEY (iem_id, zcta5)
);

-- No FK on zcta5

CREATE FUNCTION hail_compute_zip_distances() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO report_zip_distances (iem_id, zcta5, distance_m)
    SELECT NEW.iem_id, z.zcta5,
        ST_Distance(NEW.geom::geography, z.geom::geography)
    FROM zcta_boundaries z
    WHERE ST_DWithin(NEW.geom::geography, z.geom::geography, hail_pair_ceiling_m());
    RETURN NULL;
END $$;

CREATE TRIGGER iem_data_compute_zip_distances
AFTER INSERT ON iem_data
FOR EACH ROW EXECUTE FUNCTION hail_compute_zip_distances();

COMMENT ON TABLE report_zip_distances IS
'Every zip within hail_pair_ceiling_m() of every report with nearest-edge '
'distaince in metres.  Filled by trigger on iem_data insert, so a report cannot '
'exist without its distances, absent rows signify no zip withing ceiling '
'and are never "not computed," entirely derived. ';

COMMENT ON COLUMN report_zip_distances.distance_m IS
'Report point to nearest edge of zip polygon, geography (spherical) math. '
'0 when report falls inside the zip, radius_m unchanged, miles are for '
'display only.';

GRANT SELECT ON zcta_boundaries TO hail_ingest;
GRANT SELECT, INSERT ON report_zip_distances TO hail_ingest;

GRANT SELECT ON report_zip_distances TO hail_app;

COMMIT;