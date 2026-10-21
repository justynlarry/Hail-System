-- Radar layers, slice 1:  Hail Grid, stored.
--
--  radar_parameter_sets    threshold and margin that define the zones, append-only
--  radar_runs              one row per fetch run, nightly or backfill
--  radar_days              one row per local day fetched, with the file and crop box used
--  radar_cells             cells at or above the floor on that day, sparse
--
-- Source:  NOAA MRMS MESH_Max_1440min.  File stamped at the local midnight that ends
-- the day.  A day is a date in the display time zone, zones are built from radar_cells
-- in a later migration, so a changed threshold needs no new download.  Newest
-- radar_parameter_sets row is the row in use, to go back to old values add a 
-- row holding them.
--
-- The floor (0.50 in) is a fixed limit, a threshold under it would have no usable cells.
--
-- Reverse with: DROP TABLE radar_cells, radar_days, radar_runs, radar_parameter_sets;
--  DROP FUNCTION radar_parameter_sets_guard();


BEGIN;

CREATE TABLE radar_parameter_sets (
    set_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                    TEXT NOT NULL
                            CONSTRAINT radar_parameter_sets_name_length
                            CHECK (char_length(name) BETWEEN 1 AND 100),
    hail_threshold_in       NUMERIC(3,2) NOT NULL
                            CONSTRAINT radar_parameter_sets_threshold_range
                            CHECK (hail_threshold_in BETWEEN 0.50 AND 4.00),
    margin_km               NUMERIC(4,1) NOT NULL
                            CONSTRAINT radar_parameter_sets_margin_range
                            CHECK (margin_km BETWEEN 0 AND 50),
    report_radius_km        NUMERIC(4,1) NOT NULL
                            CONSTRAINT radar_parameter_sets_report_radius_range
                            CHECK (report_radius_km BETWEEN 0 AND 50),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by              BIGINT
                            CONSTRAINT fk_radar_parameter_sets_created_by REFERENCES users (emp_id)
);


COMMENT ON TABLE radar_parameter_sets IS
    'Hail threshold, margin and report radius behind the radar zones.  Append-only, the '
    'newest row is in use.  Zones will record the set_id they were built with.';
COMMENT ON COLUMN radar_parameter_sets.created_by IS
    'NULL for the first row, written by the migration.';


-- Placeholders from 2026-10 analysis until inspection data sets them
INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km)
VALUES ('Initial placeholders (analysis, 2026-10)', 1.00, 3.0, 5.0);

CREATE FUNCTION radar_parameter_sets_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'radar_parameter_sets is append-only: add a new row instead'
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE TRIGGER radar_parameter_sets_append_only
BEFORE UPDATE OR DELETE ON radar_parameter_sets
FOR EACH ROW EXECUTE FUNCTION radar_parameter_sets_guard();

CREATE TABLE radar_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_mode        TEXT NOT NULL
                    CONSTRAINT radar_runs_mode_known CHECK (run_mode IN ('nightly', 'backfill')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    run_status      TEXT NOT NULL DEFAULT 'running'
                    CONSTRAINT radar_runs_status_known
                    CHECK (run_status IN ('running', 'complete', 'failed')),
    days_loaded     INTEGER NOT NULL DEFAULT 0
                    CONSTRAINT radar_runs_loaded_nonneg CHECK (days_loaded >= 0),
    days_missing    INTEGER NOT NULL DEFAULT 0
                    CONSTRAINT radar_runs_missing_nonneg CHECK (days_missing >= 0),
    error_detail    TEXT,
    CONSTRAINT radar_runs_finished_has_timestamp
        CHECK (run_status = 'running' OR finished_at IS NOT NULL)
);

CREATE TABLE radar_days (
    day           DATE PRIMARY KEY,
    run_id        BIGINT NOT NULL
                  CONSTRAINT fk_radar_days_run REFERENCES radar_runs (run_id),
    status        TEXT NOT NULL
                  CONSTRAINT radar_days_status_known CHECK (status IN ('loaded', 'missing')),
    stamp_utc     TIMESTAMPTZ NOT NULL,
    source_key    TEXT NOT NULL,
    box_lat_max   DOUBLE PRECISION NOT NULL,
    box_lat_min   DOUBLE PRECISION NOT NULL,
    box_lon_min   DOUBLE PRECISION NOT NULL,
    box_lon_max   DOUBLE PRECISION NOT NULL,
    floor_mm      REAL NOT NULL CONSTRAINT radar_days_floor_positive CHECK (floor_mm > 0),
    cells_in_box  INTEGER,
    cells_stored  INTEGER NOT NULL DEFAULT 0
                  CONSTRAINT radar_days_stored_nonneg CHECK (cells_stored >= 0),
    max_mm        REAL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT radar_days_box_ordered
        CHECK (box_lat_max > box_lat_min AND box_lon_max > box_lon_min),
    CONSTRAINT radar_days_loaded_has_count
        CHECK (status <> 'loaded' OR cells_in_box IS NOT NULL),
    CONSTRAINT radar_days_missing_is_empty
        CHECK (status <> 'missing' OR (cells_in_box IS NULL AND cells_stored = 0 AND max_mm IS NULL))
);

COMMENT ON TABLE radar_days IS
    'One row per local day.  missing = NOAA had no file for that stamp, kept so a backfill '
    'does not ask again until it is told to.  Delete the row to fetch the day again.';
COMMENT ON COLUMN radar_days.max_mm IS
    'Largest value in the crop box, NULL when no cell reached the floor or the day is missing';

CREATE TABLE radar_cells (
    day         DATE NOT NULL
                CONSTRAINT fk_radar_cells_day REFERENCES radar_days (day),
    grid_row    INTEGER NOT NULL,
    grid_col    INTEGER NOT NULL,
    mm          REAL NOT NULL CONSTRAINT radar_cells_mm_positive CHECK (mm > 0),
    geom        geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (day, grid_row, grid_col)
);

CREATE INDEX radar_cells_geom_idx ON radar_cells USING gist (geom);

COMMENT ON TABLE radar_cells IS
    'Cell centres of the NOAA 0.01 degree grid at or above radar_days.floor_mm.  grid_row '
    'and grid_col are the file''s own indexes, geom is the cell centre.';

GRANT SELECT ON radar_parameter_sets, radar_runs, radar_days, radar_cells TO hail_app;
GRANT SELECT ON coverage_zips TO hail_ingest;
GRANT SELECT ON radar_parameter_sets TO hail_ingest;
GRANT SELECT, INSERT, UPDATE ON radar_runs TO hail_ingest;
GRANT SELECT, INSERT, DELETE ON radar_days, radar_cells TO hail_ingest;

COMMIT;