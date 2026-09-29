-- address_key: coalesce every field, not just two
-- 
-- concat_ws SKIPS a NULL argument rather than leaving an empty slot, so
-- a NULL street name or suffix dropped a field and shifted following fields
-- one field left.  425 of 24,932 keys built with only five fields, putting
-- the zip where the unit should be, and making positional parsing of the
-- key incorrect, 3 groups collided as a result.
--
-- Column is GENERATED from this function, so the column is dropped and rebuilt.


BEGIN;

ALTER TABLE properties DROP COLUMN address_key;

CREATE OR REPLACE FUNCTION address_key(addr text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    -- coalesce on every field, concat_ws skips NULLS, so anything
    -- that isn't coalesced removes its own position from the key.
    SELECT CASE WHEN s.house_num IS NULL THEN NULL
        ELSE concat_ws('|',
            s.house_num,
            coalesce(s.predir, s.sufdir, ''),
            coalesce(s.name, ''),
            coalesce(s.suftype, ''),
            coalesce(s.unit, ''),
            coalesce(s.postcode, ''))
    END
    FROM standardize_address('us_lex', 'us_gaz', 'us_rules', addr) s;
$$;

ALTER TABLE properties
    ADD COLUMN address_key text
    GENERATED ALWAYS AS (address_key(property_address)) STORED;

COMMENT ON COLUMN properties.address_key IS
    'Normalized address identity: house|dir|street|suffix|unit|zip '
    'always 6 fields.  Generated, so it always matches '
    'property_address.  NULL when the address has no house number '
    '(vacant land, "TBD parcels"), which cannot be told apart by '
    'address and are never merged.';

CREATE INDEX properties_address_key_idx ON properties (address_key)
    WHERE address_key IS NOT NULL;

COMMIT;