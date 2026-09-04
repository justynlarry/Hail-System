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


COMMENT ON TABLE ingest_runs IS
	'One row per execution of an IEM ingest sciprt, written before the work '
	'starts and updated when it finishes.  Deliberately shaped like api_pulls: '
	'a row exists before the attempt so a crash leaves something to reconcile '
	'against.  Not tracking employees, since this is system-initiated, created '
	'so that there is a queryable record showing the absense of a run.';

COMMENT ON COLUMN ingest_runs.run_mode IS
	'nightly / backfill / replay exists because backfill rows will outnumber '
	'nightly ones by orders of magnitude on row counts, and every operational '
	'query about the nightly job would otherwise start with a filter that '
	'cannot be expressed.  Distinguishes the one-time historical load from '
	'the recurring job when reading counts months after it has run.';

COMMENT ON COLUMN ingest_runs.window_start IS
	'The UTC range actually requested, not the parameter that produced it.  The '
	'nightly job is invoked with hours=N and computes its own window from '
	'now(); recording the derived range means the run is reproducible without '
	'knowing when it happened to fire.';

COMMENT ON COLUMN ingest_runs.rows.skipped IS
	'Rows written to iem_ingest_rejects during this run.  Non-zero is the alert '
	'condition; the run itself still exits 0, because the process succeeded '
	'even though some input did not.  Expect a non-zero value the first time a '
	'window covering 2018 is ingested -- 76 rows in the archive carry an '
	'upquoted comma in CITY.';

COMMENT ON CONSTRAINT counts_consistent ON ingest_runs IS
	'rows_inserted + rows_skipped <= rows_seen.  The remainder is rows the '
	'natural key discarded as duplicates, which is the expected majoirty on a '
	'nightly run.  Passes while a run is in progress because the counts are '
	'null and a CHECK fails on ly on definite false, not on unknown, so one '
	'constraint covers both states.  Catches a miscounting bug in the script, '
	'the same way distance_within_radius cathces a matcher bug.';

COMMENT ON CONSTRAINT complete_has_count ON ingest_runs IS
	'A run cannot claim ot have completed w/o reporting what it did.  Counts '
	'are nullable rather than DEFAULT=0 because "not yet" "counted" and '
	'"counted_zero" are different facts and zero-defaulted crashed run '
	'would read as successful ingest with no data.';


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

COMMENT ON TABLE iem_ingest_rejects IS
	'Rows the parser refused, one per rejected input line.  Rejecting only '
	'acceptable because it is not lossy, raw_row holds the input verbatim '
	'so anything dropped can be inspected, replayed or recoverable manually '
	'Skip-and-continue applies on ly to the reasons below, and other exception '
	'must terminate the run.';

COMMENT ON COLUMN iem_ingest_rejects.raw_row IS
	'TEXT instead of JSONB so that a malformed rejected row that would make JSON '
	'or CVS invalid, keeping raw data shows the problem in the rocrd.';

COMMENT ON COLUMN iem_ingest_rejects.reason IS
	'Closed enumeration enforced by CHECK, adding a new reject reason requires '
	'a migration and a user decision to keep drift and errors in check. ';

COMMENT ON COLUMN iem_ingest_rejects.detail IS
	'The specific instance -> whcih type pair was unknown, how many fields were '
	'found, what failed to parse.  Reason says which class of problem, detail '
	'says which case -- without it diagnosing a reject means re-parsing raw_row '
	'by hand.';



COMMIT;
