-- Phase 5c: The CoCoRaHS hail report ingest.
--
--  cocorahs_runs           one row per job run (nightly, weekly replay, backfill, manual)
--  cocorahs_loads          one row per fetched window and state, so "no reports" differs from "not fetched"
--  cocorahs_hail_reports   one row per station and time, updated when the station edits its report, never deleted
--
-- Times are UTC:  the export is asked for with TimesInGMT=True.  Sizes are inches, NULL when the form left one blank
-- ('NA' in the export is a missing value, never 0).  hail_ingest reads settings.contact_email for the User-Agent.
--
-- Reverse with:  DROP TABLE cocorahs_hail_reports, cocorahs_loads, cocorahs_runs;
--   REVOKE SELECT (contact_email) ON settings FROM hail_ingest;

BEGIN;

CREATE TABLE cocorahs_runs (
    run_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_mode          TEXT NOT NULL
                      CONSTRAINT cocorahs_runs_mode_known CHECK (run_mode IN ('nightly', 'weekly', 'backfill', 'manual')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    run_status        TEXT NOT NULL DEFAULT 'running'
                      CONSTRAINT cocorahs_runs_status_known CHECK (run_status IN ('running', 'complete', 'failed')),
    loads             INTEGER NOT NULL DEFAULT 0 CONSTRAINT cocorahs_runs_loads_nonneg CHECK (loads >= 0),
    reports_seen      INTEGER NOT NULL DEFAULT 0 CONSTRAINT cocorahs_runs_seen_nonneg CHECK (reports_seen >= 0),
    reports_new       INTEGER NOT NULL DEFAULT 0 CONSTRAINT cocorahs_runs_new_nonneg CHECK (reports_new >= 0),
    reports_changed   INTEGER NOT NULL DEFAULT 0 CONSTRAINT cocorahs_runs_changed_nonneg CHECK (reports_changed >= 0),
    reports_rejected  INTEGER NOT NULL DEFAULT 0 CONSTRAINT cocorahs_runs_rejected_nonneg CHECK (reports_rejected >= 0),
    error_detail      TEXT,
    CONSTRAINT cocorahs_runs_finished_matches_status CHECK ((run_status = 'running') = (finished_at IS NULL))
);

COMMENT ON TABLE cocorahs_runs IS
    'One row per CoCoRaHS job run.  A run that is not running has a finish time;  a run with a failed window ends failed '
    'and error_detail names the first.';

CREATE TABLE cocorahs_loads (
    load_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            BIGINT NOT NULL
                      CONSTRAINT fk_cocorahs_loads_run REFERENCES cocorahs_runs (run_id),
    state             TEXT NOT NULL CONSTRAINT cocorahs_loads_state_check CHECK (state ~ '^[A-Z]{2}$'),
    date_type         TEXT NOT NULL
                      CONSTRAINT cocorahs_loads_date_type_known CHECK (date_type IN ('reportdate', 'timestamp')),
    window_start      DATE NOT NULL,
    window_end        DATE NOT NULL,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    rows_seen         INTEGER NOT NULL CONSTRAINT cocorahs_loads_seen_nonneg CHECK (rows_seen >= 0),
    rows_new          INTEGER NOT NULL CONSTRAINT cocorahs_loads_new_nonneg CHECK (rows_new >= 0),
    rows_changed      INTEGER NOT NULL CONSTRAINT cocorahs_loads_changed_nonneg CHECK (rows_changed >= 0),
    rows_rejected     INTEGER NOT NULL CONSTRAINT cocorahs_loads_rejected_nonneg CHECK (rows_rejected >= 0),
    CONSTRAINT cocorahs_loads_window_ordered CHECK (window_end >= window_start),
    CONSTRAINT cocorahs_loads_new_changed_within_seen CHECK (rows_new + rows_changed <= rows_seen)
);

COMMENT ON TABLE cocorahs_loads IS
    'One row per export request:  a state, a date type (the export''s Report Date or Report Timestamp) and a window.  '
    'Counts are set after the reports are written, so the row is inserted at zero and updated.';

CREATE TABLE cocorahs_hail_reports (
    report_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_number    TEXT NOT NULL,
    station_name      TEXT,
    observed_at       TIMESTAMPTZ NOT NULL,
    latitude          NUMERIC(9, 6) NOT NULL
                      CONSTRAINT cocorahs_reports_lat_check CHECK (latitude BETWEEN -90 AND 90),
    longitude         NUMERIC(9, 6) NOT NULL
                      CONSTRAINT cocorahs_reports_lon_check CHECK (longitude BETWEEN -180 AND 180),
    geom              geometry(Point, 4326) GENERATED ALWAYS AS
                      (ST_SetSRID(ST_MakePoint(longitude::double precision, latitude::double precision), 4326)) STORED,
    smallest_in       NUMERIC(6, 3) CONSTRAINT cocorahs_reports_smallest_check CHECK (smallest_in BETWEEN 0 AND 10),
    average_in        NUMERIC(6, 3) CONSTRAINT cocorahs_reports_average_check CHECK (average_in BETWEEN 0 AND 10),
    largest_in        NUMERIC(6, 3) CONSTRAINT cocorahs_reports_largest_check CHECK (largest_in BETWEEN 0 AND 10),
    duration_minutes  INTEGER CONSTRAINT cocorahs_reports_duration_check CHECK (duration_minutes >= 0),
    stone_consistency TEXT,
    damage            TEXT,
    entered_at        TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    raw_row           TEXT NOT NULL,
    load_id           BIGINT NOT NULL
                      CONSTRAINT fk_cocorahs_reports_load REFERENCES cocorahs_loads (load_id),
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cocorahs_station_time UNIQUE (station_number, observed_at)
);

CREATE INDEX cocorahs_reports_geom_idx ON cocorahs_hail_reports USING gist (geom);
CREATE INDEX cocorahs_reports_observed_idx ON cocorahs_hail_reports (observed_at);
CREATE INDEX cocorahs_reports_load_idx ON cocorahs_hail_reports (load_id);

COMMENT ON TABLE cocorahs_hail_reports IS
    'CoCoRaHS hail reports from the public export, UTC.  One row per station and observation time;  a station editing its '
    'report updates the row (updated_at, source_updated_at, load_id = the load that last wrote it).  Never deleted.  '
    'A size is NULL when the form left it blank ("NA" in the export), never 0.  NUMERIC accepts NaN, which sorts above '
    '10, so the range checks refuse it.  raw_row is the source line verbatim.  Attribution is required when displayed '
    '(CC BY 4.0).';

GRANT SELECT ON cocorahs_runs, cocorahs_loads, cocorahs_hail_reports TO hail_app;
GRANT SELECT, INSERT, UPDATE ON cocorahs_runs, cocorahs_loads, cocorahs_hail_reports TO hail_ingest;
GRANT SELECT (contact_email) ON settings TO hail_ingest;

COMMIT;
