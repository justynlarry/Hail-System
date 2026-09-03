-- storm_listing_matches

BEGIN;

CREATE TABLE storm_listing_matches (
	match_id	BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

	iem_id		BIGINT		NOT NULL
			CONSTRAINT fk_slm_iem_data
			REFERENCES iem_data (iem_id),
	listing_id	BIGINT		NOT NULL
			CONSTRAINT fk_slm_listings
			REFERENCES listings (listing_id),

	distance_miles	NUMERIC(6,2)	NOT NULL CHECK (distance_miles >=0),
	radius_used	NUMERIC(5,2)	NOT NULL CHECK (radius_used > 0),
	matched_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),

	CONSTRAINT uq_slm_natural_key
		UNIQUE (iem_id, listing_id, radius_used),

	CONSTRAINT distance_within_radius
		CHECK (distance_miles <= radius_used)

);

CREATE INDEX storm_listing_matches_listing_id_idx
	ON storm_listing_matches (listing_id);

COMMENT ON TABLE storm_listing_matches IS
	'Expensive to compute (spatial work plus a paid API call), it is what '
	'send_log points at, distance is historical fact, which is why it is '
	'a table not a query.';

COMMENT ON COLUMN storm_listing_matches.listing_id IS
	'Points at the LISTING, not the property, since houses are sold and relisted '
	'over the years and the listing agent will likely change.';

COMMENT ON COLUMN storm_listing_matches.radius_used IS
	'Part of uq_slm_natural_key on purpose: re-running at a different radius '
	'neither fails nor overwrites, so both results coexist and can be compared.';

COMMIT;
