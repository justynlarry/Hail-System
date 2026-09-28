-- Address standardization for property identity.
--
-- RentCast's rentcast_id is a slug of the address as typed, so a single property
-- could potentially hold multiple records.
-- Parsing to components makes the directional's position irrelevant while
-- keeping its value, so 165 Main Street and 165 S Main Street stay distinct.
--
-- City is intentionally omitted from the key, due to data variance from RentCast
-- Zip is stable and included

BEGIN;

CREATE EXTENSION IF NOT EXISTS address_standardizer;
CREATE EXTENSION IF NOT EXISTS address_standardizer_data_us;

ALTER TABLE properties ADD COLUMN address_key text;

COMMENT ON COLUMN properties.address_key IS
    'Normalized address identity:  house|dir|street|suffix|unit|zip.  NULL '
    'when the address does not have a house number (vacant land, "TBD parcels") '
    'which cannot be differentiated by address and are not merged.';

CREATE INDEX properties_address_key ON properties (address_key)
    WHERE address_key IS NOT NULL;

COMMIT;

BEGIN;

CREATE OR REPLACE FUNCTION address_key(addr text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    -- concat_ws over concat.  A NULL directional or unit should not 
    -- swallow the whole key, the separator keeps fields from running
    -- together ("12 B ST" vs "1 2B ST").
    SELECT CASE WHEN s.house_num IS NULL THEN NULL
        ELSE concat_ws('|',
            s.house_num,
            coalesce(s.predir, s.sufdir, ''),
            s.name,
            s.suftype,
            coalesce(s.unit, ''),
            s.postcode)
    END
    FROM standardize_address('us_lex','us_gaz','us_rules', addr) s;
$$;

COMMENT ON FUNCTION address_key(text) IS
    'Normalized address identity, used on write (properties.address_key) '
    'and must be used on any typed search input too, or comparison is '
    'against differently-shaped strings.';

COMMIT;
