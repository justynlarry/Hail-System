-- sql/063: Two rules that held only in application code, now in database.
--
--  send_log  a report send records the level at the house and the set it was looked up under
--            together, or neither (item 266):  a level without its set cannot be read later.
--            send_log_snapshot_shape (sql/060) left this open for basis 'report'.
--  pull_zips the zips of a pull are written with the pull, in the transaction that creates it
--            (jobs._create_zone_pull), never added to an older pull.  now() is the transaction's
--            start, so a pull created in this transaction has started_at = now();  the same test
--            sql/056 uses for likelihood rules.
--
-- Both hold for every row on hail-dev (checked 2026-10-29).  Nothing here sends or pulls anything.
--
-- Reverse with:  ALTER TABLE send_log DROP CONSTRAINT send_log_report_level_with_set;
--   DROP TRIGGER pull_zips_with_pull ON pull_zips;  DROP FUNCTION pull_zips_with_pull_guard();

BEGIN;

ALTER TABLE send_log
    ADD CONSTRAINT send_log_report_level_with_set
        CHECK (basis <> 'report' OR (level IS NULL) = (zone_set_id IS NULL));

CREATE FUNCTION pull_zips_with_pull_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM api_pulls p
                   WHERE p.pull_id = NEW.pull_id AND p.started_at = now()) THEN
        RAISE EXCEPTION 'pull % was not created in this transaction: its zips are written with it',
            NEW.pull_id USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER pull_zips_with_pull
BEFORE INSERT ON pull_zips
FOR EACH ROW EXECUTE FUNCTION pull_zips_with_pull_guard();

COMMIT;
