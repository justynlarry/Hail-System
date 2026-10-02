-- Remove tiger and topology from the search_path for the two roles that run
-- application queries.  The database default is
--      "$user", public, topology, tiger
-- and tiger holds empty SRID 4269 tables (county, place, zcta5, egdges...),
-- so an unqualified table name could query an empty NAD83 table instead
-- of failing.
--
-- Extensions remain installed (a local TIGER load would need them).
--      Reverse with ALTER ROLE ... RESET search_path.
--
-- Nothing in hailsys/ or sql/ references the topology or tiger schemas,
-- and address_standardizer / PostGIS functions are public.


BEGIN;

ALTER ROLE hail_app     SET search_path = "$user", public;
ALTER ROLE hail_ingest  SET search_path = "$user", public;

COMMIT;

-- Verify
--  SHOW search_path;
--  SELECT stanadardize_address('us_lex','us_gaz','us_rules','1 Main St. Denver CO');
--  SELECT ST_AsText(ST_SimplifyPreserveTopology(
--         'LINESTRING(0 0,1 1,2 0)'::geometry, 0.1));

