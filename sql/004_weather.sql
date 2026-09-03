-- iem_data
-- coverage_zips

BEGIN;

CREATE TABLE iem_data (
	iem_id			BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, 
	utc_datetime		TIMESTAMPTZ	NOT NULL,
	latitude		NUMERIC(9,6)	NOT NULL,
	longitude		NUMERIC(9,6)	NOT NULL,
	magnitude		NUMERIC(6,2),
	nws_issuer		TEXT		NOT NULL,
	report_type		TEXT		NOT NULL,
	report_text		TEXT		NOT NULL,
	county			TEXT,
	state			TEXT,
	report_source		TEXT,
	report_source_norm	TEXT GENERATED ALWAYS AS (upper(trim(report_source))) STORED,
	remark			TEXT,
	nws_geo_code		TEXT,
	nws_geo_name		TEXT,
	report_qualifier	TEXT		CHECK (report_qualifier IN ('M','E','U')),
	ingested_at		TIMESTAMPTZ	NOT NULL,
	geom			GEOMETRY(Point, 4326)	GENERATED ALWAYS AS
				(ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED,

	CONSTRAINT fk_iem_report_type
		FOREIGN KEY (report_type, report_text)
		REFERENCES report_types (report_type, report_text),

	CONSTRAINT uq_iem_natural_key
		UNIQUE NULLS NOT DISTINCT
		(utc_datetime, latitude, longitude, report_text, magnitude)

);

CREATE INDEX iem_data_geom_gix ON iem_data USING GIST (geom);
CREATE INDEX iem_data_utc_datetime_idx ON iem_data (utc_datetime DESC);

COMMENT ON COLUMN iem_data.magnitude IS
	'IEM sends the literal string None as its null marker.  Coercing to 0 '
	'produces 549 magnitude-zero tornadoes.';

COMMENT ON COLUMN iem_data.report_qualifier IS
	'Tracks reporter training, NOT instrument measurement.  Use report_source '
	'if a confidence signal is needed';



CREATE TABLE coverage_zips (
	zcta5		TEXT		PRIMARY KEY
			CONSTRAINT fk_coverage_zips_zcta_boundaries
			REFERENCES zcta_boundaries (zcta5)
			CHECK (zcta5 ~ '^[0-9]{5}$'),
	area_name	TEXT		NOT NULL,
	reason		TEXT,
	added_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	added_by	BIGINT		NOT NULL
			CONSTRAINT fk_coverage_zips_added_by
			REFERENCES users (emp_id),
	removed_at	TIMESTAMPTZ,
	removed_by	BIGINT
			CONSTRAINT fk_coverage_zips_removed_by
			REFERENCES users (emp_id),

	CONSTRAINT removal_is_complete
		CHECK ((removed_at IS NULL) = (removed_by IS NULL))

);

COMMIT;
