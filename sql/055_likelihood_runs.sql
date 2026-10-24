-- Phase 5c: One row per likelihood job run (nightly, weekly rebuild, backfill, manual).
--
-- Like radar_runs and cocorahs_runs:  scripts/status.sh likelihood reads the newest nightly to say
-- whether the job is fresh.  A day that is skipped (no radar file loaded) is not a failure;  a day
-- that raised is, and makes the run 'failed'.
--
-- Reverse with:  DROP TABLE likelihood_runs;

BEGIN;

CREATE TABLE likelihood_runs (
    run_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_mode       TEXT NOT NULL
                   CONSTRAINT likelihood_runs_mode_known
                   CHECK (run_mode IN ('nightly', 'rebuild', 'backfill', 'manual')),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    run_status     TEXT NOT NULL DEFAULT 'running'
                   CONSTRAINT likelihood_runs_status_known
                   CHECK (run_status IN ('running', 'complete', 'failed')),
    days_built     INTEGER NOT NULL DEFAULT 0
                   CONSTRAINT likelihood_runs_built_nonneg CHECK (days_built >= 0),
    days_skipped   INTEGER NOT NULL DEFAULT 0
                   CONSTRAINT likelihood_runs_skipped_nonneg CHECK (days_skipped >= 0),
    days_failed    INTEGER NOT NULL DEFAULT 0
                   CONSTRAINT likelihood_runs_failed_nonneg CHECK (days_failed >= 0),
    error_detail   TEXT,
    CONSTRAINT likelihood_runs_finished_matches_status
        CHECK ((run_status = 'running') = (finished_at IS NULL))
);

COMMENT ON TABLE likelihood_runs IS
    'One row per likelihood job run.  days_skipped are days with no radar file loaded.  A run with '
    'a failed day ends failed and error_detail names the first ones.';

GRANT SELECT ON likelihood_runs TO hail_app;
GRANT SELECT, INSERT, UPDATE ON likelihood_runs TO hail_ingest;

COMMIT;
