-- Phase 5c Steps 2b/3:  A match can rest on hail zones, not an IEM report.
--
-- storm_listing_matches gets a basis and the zone facts:
--  storm_date  local storm day, on every row (backfilled from the report)
--  basis       report | radar | observer | both
--  level       agree | one_source, zone rows only
--  layer       the radar layer at the house, zone rows only
--  set_id      the likelihood parameter set the level came from, zone rows only
--
-- Only a 'report' row has an iem_id, distance_miles and radius_used, and every row
-- there was before this is a 'report' row.  Every reader of the table inner-joins
-- iem_data on iem_id, so a zone row is invisible to all of them
-- (tests/test_match_readers.py pins that).  A guard on send_log refuses a zone row
-- outright until sends by level are waived.
--
-- Reverse with:  DROP TRIGGER trg_send_log_report_match_only ON send_log;
--   DROP FUNCTION send_log_report_match_guard();
--   DROP TRIGGER slm_fill_storm_date ON storm_listing_matches;
--   DROP FUNCTION slm_fill_storm_date();
--   DELETE FROM storm_listing_matches WHERE basis <> 'report';
--   then drop the constraints, indexes and columns added below, and put NOT NULL
--   back on iem_id, distance_miles and radius_used.

BEGIN;

ALTER TABLE storm_listing_matches
    ADD COLUMN storm_date   DATE,
    ADD COLUMN basis        TEXT NOT NULL DEFAULT 'report',
    ADD COLUMN level        TEXT,
    ADD COLUMN layer        TEXT,
    ADD COLUMN set_id       BIGINT
                    CONSTRAINT fk_slm_likelihood_set REFERENCES likelihood_parameter_sets (set_id);

-- The local day, same expression the storm browser uses (storms.py LOCAL_TIME_EXPR).
UPDATE storm_listing_matches m
SET storm_date = (i.utc_datetime AT TIME ZONE 'America/Denver')::date
FROM iem_data i
WHERE i.iem_id = m.iem_id;

ALTER TABLE storm_listing_matches
    ALTER COLUMN storm_date     SET NOT NULL,
    ALTER COLUMN iem_id         DROP NOT NULL,
    ALTER COLUMN distance_miles DROP NOT NULL,
    ALTER COLUMN radius_used    DROP NOT NULL;

ALTER TABLE storm_listing_matches
    ADD CONSTRAINT slm_basis_known
        CHECK (basis IN ('report', 'radar', 'observer', 'both')),
    ADD CONSTRAINT slm_iem_id_only_on_report
        CHECK ((iem_id IS NOT NULL) = (basis = 'report')),
    ADD CONSTRAINT slm_report_distance_together
        CHECK ((iem_id IS NULL) = (distance_miles IS NULL)
            AND (iem_id IS NULL) = (radius_used IS NULL)),
    ADD CONSTRAINT slm_zone_facts
        CHECK ((basis = 'report' AND level IS NULL AND layer IS NULL AND set_id IS NULL)
            OR (basis <> 'report' AND set_id IS NOT NULL
                AND level IN ('agree', 'one_source')
                AND layer IN ('core', 'margin', 'radar_indicated', 'outer', 'none')));

-- A report row keeps uq_slm_natural_key.  A zone has no iem_id, so NULLs would
-- never collide there.  One zone row per house, day, and set.
CREATE UNIQUE INDEX slm_zone_natural_key
    ON storm_listing_matches (storm_date, listing_id, set_id)
    WHERE iem_id IS NULL;

CREATE INDEX storm_listing_matches_storm_date_idx
    ON storm_listing_matches (storm_date);

-- The report matcher does not write storm_date, so fill it here and leave the matcher
-- as it is.  basis already defaults to 'report'.
CREATE FUNCTION slm_fill_storm_date() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.storm_date IS NULL AND NEW.iem_id IS NOT NULL THEN
        SELECT (i.utc_datetime AT TIME ZONE 'America/Denver')::date
        INTO NEW.storm_date
        FROM iem_data i
        WHERE i.iem_id = NEW.iem_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER slm_fill_storm_date
BEFORE INSERT ON storm_listing_matches
FOR EACH ROW EXECUTE FUNCTION slm_fill_storm_date();

-- Nothing that rests on the zones alone is sent, whatever the application does.
CREATE FUNCTION send_log_report_match_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM storm_listing_matches m
               WHERE m.match_id = NEW.match_id AND m.iem_id IS NULL) THEN
        RAISE EXCEPTION 'match % rests on the hail zones, not an IEM report: sending '
            'by zone is not waived in', NEW.match_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_send_log_report_match_only
BEFORE INSERT ON send_log
FOR EACH ROW EXECUTE FUNCTION send_log_report_match_guard();

COMMENT ON COLUMN storm_listing_matches.basis IS
    'report: an IEM report within the match radius (the only basis with an iem_id) '
    'radar, observer, both: a house inside an Agree or One source zone with no IEM '
    'report near it (radar alone, a CoCoRaHS filing alone, or both).';
COMMENT ON COLUMN storm_listing_matches.level IS
    'agree or one_source, zone rows only.  A report row has none: its level is looked '
    'up against the set in use when it is needed.';

COMMIT;
