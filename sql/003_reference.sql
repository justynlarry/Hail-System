-- report_types:	categorization of storms based on a Letter or Number, points at nothing, reference
-- zcta_boundaries:	US Census Data Map, key/value pairs, points at nothing

BEGIN;

CREATE TABLE report_types (
	report_type	TEXT	NOT NULL,
	report_text	TEXT	NOT NULL,
	mag_unit	TEXT
			CHECK (mag_unit IN ('inches', 'mph', 'none')),
	unit_confidence	TEXT	NOT NULL
			CHECK (unit_confidence IN ('certain', 'inferred', 'unknown')),
	roof_relevant	BOOLEAN	NOT NULL DEFAULT FALSE,

	CONSTRAINT pk_report_types PRIMARY KEY (report_type, report_text)

);

COMMENT ON TABLE report_types IS
	'Reference data, 37 rows. Adding a new roof-relevant type is an UPDATE, '
	'not a deploy -- that is why this table exists.';

COMMENT ON COLUMN report_types.unit_confidence IS
	'Derived from the type name only.  Inferring units from value ranges '
	'produced wrong answers: tornado EF numbers read as inches.';



CREATE TABLE zcta_boundaries (
	zcta5		TEXT				NOT NULL	PRIMARY KEY
			CHECK (zcta5 ~ '^[0-9]{5}$'),
	geom		GEOMETRY(MultiPolygon, 4326)	NOT NULL,
	centroid	GEOMETRY(Point, 4326)		GENERATED ALWAYS AS
			(ST_PointOnSurface(geom)) STORED,
	land_area	BIGINT

);

CREATE INDEX zcta_boundaries_geom_gix ON zcta_boundaries USING GIST (geom);

COMMENT ON TABLE zcta_boundaries IS
	'Census TIGER/Line ZCTAs, EPSG 4326.  TIGER ships NAD83 (4269) -- '
	'reproject at load. Mixing coordinate systems fails silently.';

COMMIT;
