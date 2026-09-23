-- Item 40: Match run that finds nothing does not leave a trace
-- so "matched, nothing in range" is the same as "never matched."
-- This mirros api_pulls and ingest_runs, the row is written before
-- work is done, so a process that doesn't complete leaves a record
-- to reconcile against.


BEGIN;

CREATE TABLE match_runs (
    match_run_id            BIGINT              GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    emp_id                  BIGINT              NOT NULL REFERENCES users (emp_id),
    storm_date              DATE                NOT NULL,
    report_text             TEXT                NOT NULL,
    radius_miles            NUMERIC(4,1)        NOT NULL,
    started_at              TIMESTAMPTZ         NOT NULL DEFAULT now(),
    finished_at             TIMESTAMPTZ,
    matches_created         INTEGER,
    run_status              TEXT                NOT NULL DEFAULT 'running'
            CHECK (run_status IN ('running', 'complete', 'failed')),
    error_detail            TEXT,

    CONSTRAINT finished_has_timestamp
        CHECK (run_status = 'running' or finished_at IS NOT NULL)
);

COMMENT ON TABLE match_runs IS
    'One row per match attempt, it exists so a run that wrote zero rows is a '
    'record instead of an absence.  storm_listing_matches cannot distinguish '
    ' "ran, nothing in range" from "never ran".';

COMMENT ON COLUMN match_runs.matches_created IS
    'New rows only, _MATCH_SQL uses ON CONFLICT DO NOTHING, so re-running '
    'an already-matched storm records 0.  Query storm_listing_matches to '
    'find out how many matches a storm has.';

COMMENT ON COLUMN match_runs.radius_miles IS
    'Match radius in effect when this ran, which is Admin-editable since 020';

CREATE INDEX match_runs_storm_idx
    ON match_runs (storm_date, report_text);

GRANT SELECT, INSERT, UPDATE ON match_runs TO hail_app;

COMMIT;