-- Phase 5c step 2b:  A pull can come from the hail zones, and records why each zip was pulled.
--
-- api_pulls gains:
--   pull_source   reports (a pull from storm reports, as every pull so far) or zones
--   zone_set_id   the likelihood parameter set the zone list came from, zones pulls only
-- pull_zips is one row per zip of a pull, with the reason:
--   basis         zones | reports | both   (the zone list is unioned with the report list)
--   best_level    agree | one_source | context, NULL exactly for a reports-only zip
-- Append-only.  The pull itself is still started by a person's click and run by run_pull;
-- nothing here calls RentCast.
--
-- Named zone_set_id, not set_id, to avoid collisions with a joined table's set_id
-- (parking-lot item 258).
--
-- Reverse with:  DROP TABLE pull_zips; DROP FUNCTION pull_zips_append_only_guard();
--   ALTER TABLE api_pulls DROP CONSTRAINT api_pulls_zone_set_iff_zones,
--       DROP CONSTRAINT api_pulls_zones_have_a_storm, DROP CONSTRAINT api_pulls_source_known,
--       DROP COLUMN zone_set_id, DROP COLUMN pull_source;

BEGIN;

ALTER TABLE api_pulls
    ADD COLUMN pull_source TEXT NOT NULL DEFAULT 'reports',
    ADD COLUMN zone_set_id BIGINT
        CONSTRAINT fk_api_pulls_zone_set REFERENCES likelihood_parameter_sets (set_id);

ALTER TABLE api_pulls
    ADD CONSTRAINT api_pulls_source_known
        CHECK (pull_source IN ('reports', 'zones')),
    ADD CONSTRAINT api_pulls_zone_set_iff_zones
        CHECK ((pull_source = 'zones') = (zone_set_id IS NOT NULL)),
    ADD CONSTRAINT api_pulls_zones_have_a_storm
        CHECK (pull_source <> 'zones' OR storm_date IS NOT NULL);

CREATE TABLE pull_zips (
    pull_id         BIGINT NOT NULL
                    CONSTRAINT fk_pull_zips_pull REFERENCES api_pulls (pull_id),
    zcta5           TEXT NOT NULL
                    CONSTRAINT pull_zips_zcta5_shape CHECK (zcta5 ~ '^[0-9]{5}$'),
    basis           TEXT NOT NULL
                    CONSTRAINT pull_zips_basis_known CHECK (basis IN ('zones', 'reports', 'both')),
    best_level      TEXT
                    CONSTRAINT pull_zips_level_known
                    CHECK (best_level IN ('agree', 'one_source', 'context')),
    PRIMARY KEY (pull_id, zcta5),
    CONSTRAINT pull_zips_level_iff_zones
        CHECK ((basis = 'reports') = (best_level IS NULL))
);

COMMENT ON TABLE pull_zips IS
    'Why each zip was in a pull.  basis reports is a zip within the zip radius of an '
    'actionable report and outside the zones.  zones and both carry the best level the '
    'zone list gave it.  Append-only.';

CREATE FUNCTION pull_zips_append_only_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'pull_zips is append-only'
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE TRIGGER pull_zips_append_only
BEFORE UPDATE OR DELETE ON pull_zips
FOR EACH ROW EXECUTE FUNCTION pull_zips_append_only_guard();

GRANT SELECT, INSERT ON pull_zips TO hail_app;

COMMIT;
