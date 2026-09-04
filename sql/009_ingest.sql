-- ingest_runs
-- iem_ingest_rejects


BEGIN;

CREATE TABLE ingest_runs (
	run_id		BIGINT		GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	run_mode	TEXT		NOT NULL
			CHECK (role in ('nightly', 'backfill', 'replay')),
	window_start	TIMESTAMPTZ	NOT NULL,
	window_end	TIMSTAMPTZ	NOT NULL,
	started_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	finished_at	TIMESTAMPTZ,
	rows_seen	INTEGER CHECK (rows_seen     >=0),
	rows_inserted	INTEGER CHECK (rows_inserted >=0),
	rows_skipped	INTEGER CHECK (rows_skipped  >=0),
	run_status	TEXT		NOT NULL DEFAULT 'running'
			CHECK (role IN ('running', 'complete', 'failed')),
	error_detail	TEXT,

	CONSTRAINT window_ordered
		CHECK (window_end > window_start);
	CONSTRAINT finished_has_timestamp
		CHECK (run_status = 'running' OR finished_at IS NOT NULL);

	CONSTRAINT complete_has_counts
		CHECK (run_status <> 'complete'
			OR  (rows_seen		IS NOT NULL
			AND rows_inserted	IS NOT NULL
			AND rows_skipped	IS NOT NULL)),

	CONSTRAINT counts_consistent
		CHECK (rows_inserted + rows_skipped <= rows_seen)

);


CREATE TABLE iem_ingest_rejects (
	reject_id	BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	run_id		BIGINT		NOT NULL,
	raw_row		TEXT		NOT NULL,
	reason		TEXT		NOT NULL
			CHECK (reason IN	('field_count_mismatch',
						'unknown_report_type',
						'unparseable_timestamp',
						'unparseable_coordinate',
						'unparseable_magnitude')),
	detail		TEXT,
	rejected_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),

	CONSTRAINT fk_rejects_run
		FOREIGN KEY (run_id) REFERENCES ingest_runs (run_id)

);

COMMIT;
