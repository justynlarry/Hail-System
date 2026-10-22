-- Radar Layers, slice 4: Nightly job and backfill
--
--  radar_runs.run_mode     adds 'rebuild' (zones again from stored cells, the weekly job)
--  radar_runs.days_built   days whose zones were built in the run
--  radar_runs.days_failed  days that failed and were rolled back, named in error_detail
--
-- A run that finished with failed days has run_status 'failed' and days_loaded, days_built
-- still count what succeeded.
--
-- Reverse with:  ALTER TABLE radar_runs DROP COLUMN days_built, DROP COLUMN days_failed;
--   DELETE FROM radar_runs WHERE run_mode = 'rebuild';   (after removing what refers to them)
--   ALTER TABLE radar_runs DROP CONSTRAINT radar_runs_mode_known;
--   ALTER TABLE radar_runs ADD CONSTRAINT radar_runs_mode_known
--       CHECK (run_mode IN ('nightly', 'backfill', 'manual'));

BEGIN;

ALTER TABLE radar_runs DROP CONSTRAINT radar_runs_mode_known;
ALTER TABLE radar_runs ADD CONSTRAINT radar_runs_mode_known
    CHECK (run_mode IN ('nightly', 'backfill', 'manual', 'rebuild'));

ALTER TABLE radar_runs
    ADD COLUMN days_built   INTEGER NOT NULL DEFAULT 0
        CONSTRAINT radar_runs_built_nonneg CHECK (days_built >= 0),
    ADD COLUMN days_failed INTEGER NOT NULL DEFAULT 0
        CONSTRAINT radar_runs_failed_nonneg CHECK (days_failed >= 0);

COMMENT ON COLUMN radar_runs.days_failed IS
    'Days that failed (after the downloader''s retries) and were rolled back.  The run goes on '
    'and ends failed, error_detail names the first of them.';

COMMIT;