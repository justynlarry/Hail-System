-- properties
-- realtors
-- listings
-- dnc_list

BEGIN;

CREATE TABLE properties (
	rentcast_id		TEXT		PRIMARY KEY,
	property_address	TEXT		NOT NULL,
	address_1		TEXT,
	address_2		TEXT,
	city			TEXT,
	state			TEXT,
	zip_code		TEXT,
	county			TEXT,
	county_fips		TEXT,
	list_latitude		NUMERIC(9,6),
	list_longitude		NUMERIC(9,6),
	property_type		TEXT,
	bedrooms		SMALLINT,
	bathrooms		NUMERIC(3,1),
	square_footage		INTEGER,
	lot_size		INTEGER,
	year_built		SMALLINT,
	hoa_dues		NUMERIC(10,2),
	first_seen_at		TIMESTAMPTZ	NOT NULL,
	created_date		TIMESTAMPTZ	NOT NULL DEFAULT now()

);

COMMENT ON TABLE properties IS
	'A catalog of buildings with no storm awareness.  The test for whether a '
	'field belongs here: would it still be true in five years, when the house '
	'is listed by a different agent at a different price?';

COMMENT ON A COLUMN properties.rentcast_id IS
	'Derived from the address string.  A formatting change upstream '
	'(hargis St -> Hargis Street) creates a NEW ID for the same physical house. ';

COMMENT ON COLUMN properties.created_date IS
	'When RentCast first saw the property -- this comes from RentCast, not this system.';



CREATE TABLE realtors (
	realtor_id		BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	agent_name		TEXT,
	agent_phone		TEXT,
	agent_email		TEXT,
	agent_email_norm	TEXT GENERATED ALWAYS AS (lower(trim(agent_email))) STORED UNIQUE,
	agent_office_name	TEXT,
	agent_office_phone	TEXT,
	agent_office_email	TEXT GENERATED ALWAYS AS (lower(trim(agent_office_email))) STORED,
	first_seen_at		TIMESTAMPTZ	NOT NULL DEFAULT now(),
	last_seen_at		TIMESTAMPTZ	NOT NULL DEFAULT now()

);

CREATE UNIQUE INDEX realtors_email_norm_uq
	ON realtors (email_norm) WHERE email_norm IS NOT NULL;

COMMENT ON TABLE realtors IS
	'Exists for exactly two reasons: contact frequency capping, and tying to send '
	'history to a person rather than scattered listing rows.  Deduplication is '
	'deliberately NOT attempted -- over-emailing is recoverable, wrongly '
	'silencing a working agent is invisible and permanent.';



CREATE TABLE listings (
	listing_id		BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	rentcast_id		TEXT		NOT NULL REFERENCES properties (rentcast_id),
	list_status		TEXT		NOT NULL CHECK (list_status IN ('Active', 'Inactive')),
	list_price		NUMERIC(12,2),
	list_type		TEXT CHECK (list_type IN
					('Standard', 'New Construction',
					'Foreclosure', 'Short Sale')),
	list_date		TIMESTAMPTZ	NOT NULL,
	list_removed_date	TIMESTAMPTZ,
	list_last_seen		TIMESTAMPTZ,
	list_days_on_market	INTEGER,
	list_mls_name		TEXT,
	list_mls_number		TEXT,
	list_agent_name		TEXT,
	list_agent_phone	TEXT,
	list_agent_email	TEXT,
	list_agent_email_norm	TEXT GENERATED ALWAYS AS (lower(trim(list_agent_email))) STORED UNIQUE,
	list_office_name	TEXT,
	list_office_phone	TEXT,
	list_office_email	TEXT GENERATED ALWAYS AS (upper(trim(office_agent_email))) STORED,
	list_office_website	TEXT,
	realtor_id		BIGINT		NOT NULL,
	raw_payload		JSONB,
	created_date		TIMESTAMPTZ,
	first_seen_at		TIMESTAMPTZ	NOT NULL DEFAULT now(),

	CONSTRAINT uq_listings_natural_key UNIQUE (rentcast_id, list_date)
);

CREATE INDEX listings_realtor_idx ON listings (realtor_id);

COMMENT ON COLUMN listings.realtor_id IS
	'Nullable -- some listings arrive with not agent block at all.'

COMMENT ON COLUMN listings.list_agent_name IS
	'SNAPSHOT of who RentCast said listed it, at the time.  Duplicated intentionally '
	'from realtors.  Listing preserves who listed it, at what time, under what name '
	'the realtor row tracks that person going forward from ingestion, do not normalize.'

COMMENT ON COLUMN listings.list_type IS
	'New construction is not worth outreach -- a brand new roof is under warranty'



CREATE TABLE dnc_list (
	dnc_id		BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	email_raw	TEXT		NOT NULL,
	email_norm	TEXT GENERATED ALWAYS AS (lower(trim(email_raw))) STORED UNIQUE,
	added_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	added_by	TIMESTAMPTZ	NOT NULL REFERENCES user (emp_id),
	source		TEXT		NOT NULL CHECK (source IN
						('reply', 'unsubscribe', 'hard_bounce',
						'complaint', 'manual')),
	reason		TEXT,
	realtor_id	BIGINT REFERENCES realtors (realtor_id),
	name_at_add	TEXT,
	phone_at_add	TEXT,
	removed_at	TIMESTAMPTZ,
	removed_by	BIGINT REFERENCES users (emp_id),

	CONSTRAINT removal_is_complete
		CHECK ((removed_at IS NULL) = (removed_by IS NULL))

);

COMMENT ON TABLE dnc_list IS
	'Answers: may we send to this email address?  The check runs at send time against '
	'dnc_list table in the same transaction as the send.'

COMMENT ON COLUMN dnc_list.email_norm IS
	'UNIQUE enforcement key, keyed on email as key.'

COMMENT ON COLUMN dnc_list.source IS
	'Bounces mean bad data; compaints are bad targeting or copy, count separately.'

COMMENT ON COLUMN dnc_list.name_at_add IS
	'For best-effor second-address pass, feeds human review, now automatic enforcement.'

COMMIT;
