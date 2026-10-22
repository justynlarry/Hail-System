-- Radar Layers:  A day with an impossible value is recorded, but not drawn.
--
--  radar_days.status       adds 'suspect': NOAA's file held a value above the plausibility ceiling
--                          (10 inches, 254 mm, fixed in code), so the day stores no cells and
--                          doesn't build zones.  Record is kept, max_mm is the largest value in 
--                          the file and cells_in_box is set.
--  radar_runs.days_suspect days a run recorded as suspect
--
-- 2022-01-03 and 2022-01-11 held values up to 1,007.7 mm.  Re-fetch them (radar_day.py --refetch)
-- after this migration and the code change to turn them from 'loaded' into 'suspect'.
--
-- Reverse with:  DELETE FROM radar_days WHERE status = 'suspect';  ALTER TABLE radar_runs DROP COLUMN days_suspect;
--   ALTER TABLE radar_days DROP CONSTRAINT radar_days_suspect_has_evidence_and_no_cells;
--   ALTER TABLE radar_days DROP CONSTRAINT radar_days_status_known;
--   ALTER TABLE radar_days ADD CONSTRAINT radar_days_status_known CHECK (status IN ('loaded', 'missing'));


BEGIN;

ALTER TABLE radar_days DROP CONSTRAINT radar_days_status_known;
ALTER TABLE radar_days ADD CONSTRAINT radar_days_status_known
    CHECK (status IN ('loaded', 'missing', 'suspect'));

ALTER TABLE radar_days ADD CONSTRAINT radar_days_suspect_has_evidence_and_no_cells
    CHECK (status <> 'suspect' OR (cells_in_box IS NOT NULL AND cells_stored = 0 AND max_mm IS NOT NULL));

ALTER TABLE radar_runs
    ADD COLUMN days_suspect INTEGER NOT NULL DEFAULT 0
        CONSTRAINT radar_runs_suspect_nonneg CHECK (days_suspect >= 0);

COMMENT ON COLUMN radar_days.max_mm IS
    'Largest value in the crop box, NULL when no cell reached the floor or the day is missing. '
    'For a suspect day it is the impossible value that makes the day suspect (1,007 mm in January 2022)';

COMMIT;
