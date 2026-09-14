-- 012_counties.sql - TIGER 2025 county boundaries
-- Additive.  004_weather.sql is frozen post-backfill and is not edited

CREATE TABLE county_boundaries (
    county_fips		CHAR(5) 			PRIMARY KEY,
    state_fips		CHAR(2)				NOT NULL,
    name		TEXT				NOT NULL,
    geom		GEOMETRY(MultiPolygon, 4326)	NOT NULL
);

CREATE INDEX county_boundaries_geom_gix ON county_boundaries USING GIST (geom);

GRANT SELECT ON county_boundaries TO hail_app;

