-- properties gains generated geom + GIST index, which
-- matches iem_data.  Point-to-point matching runs a 
-- distance predicate against properties in range.  
-- Added early rather than later, not strictly necessary
-- but low cost with no records.
-- Generated so that the column can't drift from the lat/lon
-- it derives from.


ALTER TABLE properties
    ADD COLUMN geom geometry(Point, 4326)
    GENERATED ALWAYS AS (
        ST_SetSRID(ST_MakePoint(list_longitude, list_latitude), 4326)
    ) STORED;

CREATE INDEX properties_geom_gix ON properties USING GIST (geom);

CREATE INDEX properties_geog_gix ON properties USING GIST ((geom::geography));

COMMENT ON COLUMN properties.geom IS
    'GEOMETRY(Point, 4326) is generated from list_longitude/list_latitude. '
    'Matches iem_data.geom''s pattern, SRID 4326 ';
