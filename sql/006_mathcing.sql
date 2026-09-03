-- storm_listing_matches

BEGIN;

CREATE TABLE storm_listing_matches (
	match_id	BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

	iem_id		BIGINT		NOT NULL REFERENCES iem_data (iem_id),
	listing_id	BIGINT		NOT NULL REFERENCES listings (listing_id),

	distance_miles	NUMERIC(6,2)	NOT NULL CHECK (distance_miles >=0),
	radius_used	NUMERIC(5,2)	NOT NULL CHECK (radius_used > 0),
	matched_at	TIMESTAMPTZ	NOT NULL DEFAULT now()

	CONSTRAINT distance_within_radius
		CHECK (distance_miles <= radius_used)

);

CREATE INDEX slm_listing_idx ON storm_listing_matches (listing_id);

COMMENT ON TABLE storm_listing_matches IS
	'Expensive to compute (spatial work plus a paid API call), it is what '
	'send_long points at, distance is historical fact, which is why it is '
	'a table not a query.'

COMMENT ON COLUMN storm_listing_matches.listing_id IS
	'Points at the LISTING, not property since houses are sold and relisted over '
	'over the years, and listing agent will likely change.'

COMMENT ON COLUMN storm_listing_matches.radius_used IS
	'Unique constraint intentionally, re-running at varying distances will not'
	'fail or overwrite so that both may coexist.'

COMMIT;
