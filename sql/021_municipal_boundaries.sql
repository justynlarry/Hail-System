-- 021_municipal_boundaries.sql - DOLA municipal boundaries (Colorado)
-- Additive.  Loaded by scripts/load_municipal.sh; see the decision log for
-- why DOLA's dissolved layer and not the CU GeoLibrary 2017 snapshot.

BEGIN;

CREATE TABLE municipal_boundaries (
    place_fips		CHAR(5)				PRIMARY KEY
			CHECK (place_fips ~ '^[0-9]{5}$'),
    name		TEXT				NOT NULL,
    geom		GEOMETRY(MultiPolygon, 4326)	NOT NULL,
    source_attrs	JSONB				NOT NULL,
    source_url		TEXT				NOT NULL,
    source_edited_at	TIMESTAMPTZ			NOT NULL,
    loaded_at		TIMESTAMPTZ			NOT NULL DEFAULT now()
);

CREATE INDEX municipal_boundaries_geom_gix
    ON municipal_boundaries USING GIST (geom);

COMMENT ON TABLE municipal_boundaries IS
    'One row per incorporated Colorado municipality, from DOLA''s dissolved '
    'municipal layer.  A point in no row is unincorporated -- resolve its '
    'county from county_boundaries.  Replaced wholesale on reload (DELETE + '
    'INSERT), unlike the ZCTA load: an annexation must replace the old '
    'boundary, and ON CONFLICT DO NOTHING would keep the stale one.';

COMMENT ON COLUMN municipal_boundaries.place_fips IS
    'DOLA''s `city` field.  It is the 5-digit Census place code (Denver '
    '20000, Aurora 04000), not a name -- the name is `first_city`.';

COMMENT ON COLUMN municipal_boundaries.source_attrs IS
    'Every source attribute as received, except DOLA''s literal string '
    '''null'', which is stored as JSON null so it cannot read as a value.';

COMMENT ON COLUMN municipal_boundaries.source_edited_at IS
    'The layer''s editingInfo.dataLastEditDate at fetch time.  DOLA '
    'republishes nightly (~07:00 UTC), so this dates the publish, not the '
    'last boundary change.';

GRANT SELECT ON municipal_boundaries TO hail_app;

COMMIT;
