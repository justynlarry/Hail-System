-- sql/057's slm_zone facts lets a zone row through with a NULL level or layer:
-- NULL IN (...) is NULL, and a CHECK passes on NULL.
--
-- Reverse with:  ALTER TABLE storm_listing_matches DROP CONSTRAINT slm_zone_facts;
--   then add it back as in sql/057.


BEGIN;

ALTER TABLE storm_listing_matches DROP CONSTRAINT slm_zone_facts;

ALTER TABLE storm_listing_matches
    ADD CONSTRAINT slm_zone_facts
        CHECK ((basis = 'report' AND level IS NULL AND layer IS NULL AND set_id IS NULL)
            OR (basis <> 'report' AND set_id IS NOT NULL
                AND level IS NOT NULL AND level IN ('agree', 'one_source')
                AND layer IS NOT NULL
                AND layer IN ('core', 'margin', 'radar_indicated', 'outer', 'none')));

COMMIT;