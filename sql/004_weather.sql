-- iem_data
-- coverage_zips

BEGIN;

CREATE TABLE iem_data (
	iem_id		BIGINT		NOT NULL	PRIMARY KEY, 
	utc_datetime		TIMESTAMPTZ	NOT NULL,
	latitude		NUM(9,6)	NOT NULL,
	longitude		NUM(9,6)	NOT NULL,
	magnitude		NUM(6,2),
	nws_issuer		TEXT		NOT NULL,
	report_type		TEXT		NOT NULL,
	report_text		TEXT		NOT NULL,
	county			TEXT		NOT NULL,
	state			TEXT		NOT NULL,
	report_source		TEXT		NOT NULL,
	remark			TEXT,
	nws_geo_code		TEXT		NOT NULL,
	nws_geo_name		TEXT,
	report_qualifier	TEXT,
	ingested_at		TIMESTAMPTZ	NOT NULL,
	report_source_norm	TEXT,
	geom			GEOMETRY(Point, 4326)	NOT NULL

);


