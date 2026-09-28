-- address_key becomes generated, not written by upsert
-- 
-- Upsert's ON CONFLICT updates property_address, a key written
-- only at insert would go stale on address corrections.  A generated
-- column recomputes on every write and cannot be forgotten by a new
-- write path.  Same logic as realtors.email_norm.

BEGIN;

ALTER TABLE properties DROP COLUMN address_key;

ALTER TABLE properties
    ADD COLUMN address_key text
    GENERATED ALWAYS AS (address_key(property_address)) STORED;

COMMENT ON COLUMN properties.address_key IS
    'Normalized address identity: house|dir|street|suffix|unit|zip. '
    'Generated so it always matches property_address, NULL when the '
    'address has no house number (vacant land, TBD parcels), those '
    'cannot be told apart by address and are not merged.';

    CREATE INDEX properties_address_key_idx ON properties (address_key)
        WHERE address_key IS NOT NULL;

COMMIT;