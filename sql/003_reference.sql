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
	min_magnitude	NUMERIC(6,2)
			CHECK (min_magnitude IS NULL
				OR mag_unit IN ('inches', 'mph')),

	CONSTRAINT pk_report_types PRIMARY KEY (report_type, report_text)

);

COMMENT ON TABLE report_types IS
	'Reference data, 37 rows. Adding a new roof-relevant type is an UPDATE, '
	'not a deploy -- that is why this table exists.';

COMMENT ON COLUMN report_types.unit_confidence IS
	'Derived from the type name only.  Inferring units from value ranges '
	'produced wrong answers: tornado EF numbers read as inches.';

COMMENT ON COLUMN report_types.min_magnitude IS
	'Outreach floor for this type.  NULL means no floor.  Same scale as '
	'iem_data.magnitude so comparisons need no cast.  The CHECK blocks a floor '
	'on a type with no magnitude to compare against -- a threshold on '
	'TSTM WND DMG would silently exclude everything.  '
	'SEMANTIC THAT IS INVISIBLE HERE: when min_magnitude is set and a report '
	'magnitude is NULL, (magnitude >= min_magnitude) is UNKNOWN, not TRUE, so '
	'the report is EXCLUDED.  For a wind gust with no speed that is correct -- '
	'it cannot be assessed.  But it means a type cannot have both a floor and '
	'an include-the-unmeasured behaviour.';



CREATE TABLE report_sources (
	source		TEXT		PRIMARY KEY
			CHECK (source = upper(trim(source))),
	display_name	TEXT		NOT NULL,
	confidence_tier	TEXT		NOT NULL DEFAULT 'unrated'
			CHECK (confidence_tier IN
				('high', 'moderate', 'low', 'unrated')),
	is_automated	BOOLEAN,
	notes		TEXT,
	added_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	added_by	BIGINT		NOT NULL
			CONSTRAINT fk_report_sources_added_by
			REFERENCES users (emp_id),
	removed_at	TIMESTAMPTZ,
	removed_by	BIGINT
			CONSTRAINT fk_report_sources_removed_by
			REFERENCES users (emp_id),

	CONSTRAINT removal_is_complete
		CHECK ((removed_at IS NULL) = (removed_by IS NULL))

);

COMMENT ON TABLE report_sources IS
	'Lookup, not a constraint.  NO foreign key from iem_data -- report_source is '
	'free text typed at individual NWS offices, and an FK would fail the nightly '
	'ingest on the first new variant.  DEPARTMENT OF HIG and DEPT OF are in the '
	'archive as proof.  Modelled like zcta_boundaries: LEFT JOIN when needed, an '
	'unknown source yields a NULL tier and the UI shows unrated rather than '
	'dropping the report.';

COMMENT ON COLUMN report_sources.source IS
	'Joins to iem_data.report_source_norm, NEVER to raw report_source.  The join '
	'only works because both sides are upper(trim()) -- the CHECK on this column '
	'is what guarantees that, the same way the lowercase CHECKs on users do.';

COMMENT ON COLUMN report_sources.confidence_tier IS
	'Defaults to unrated so a newly discovered source can be inserted before '
	'anyone has judged it.  This is the signal the confidence label uses; '
	'report_qualifier is NOT that signal.';



CREATE TABLE zcta_boundaries (
	zcta5		TEXT				NOT NULL	PRIMARY KEY
			CHECK (zcta5 ~ '^[0-9]{5}$'),
	geom		GEOMETRY(MultiPolygon, 4326)	NOT NULL,
	centroid	GEOMETRY(Point, 4326)		GENERATED ALWAYS AS
			(ST_PointOnSurface(geom)) STORED,
	land_area	BIGINT

);

CREATE INDEX zcta_boundaries_geom_gix ON zcta_boundaries USING GIST (geom);

-- The buffer query is written in geography (metres), not degrees, so it casts:
--   ST_DWithin(geom::geography, point::geography, metres)
-- That cast is computed per row, so it CANNOT use the plain geom index above --
-- measured: parallel seq scan, 17.9 s over 33,791 ZCTAs.  This functional index
-- is on the cast expression itself and brings the same query to 21 ms.
-- Both indexes are kept: geom_gix serves geometry predicates (ST_Intersects,
-- ST_Contains), geog_gix serves distance-in-metres.
CREATE INDEX zcta_boundaries_geog_gix
	ON zcta_boundaries USING GIST ((geom::geography));

COMMENT ON TABLE zcta_boundaries IS
	'Census TIGER/Line ZCTAs, EPSG 4326.  TIGER ships NAD83 (4269) -- '
	'reproject at load. Mixing coordinate systems fails silently.';

COMMIT;
