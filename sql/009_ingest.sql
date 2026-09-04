-- ingest_runs
-- iem_ingest_rejects

BEGIN;

CREATE TABLE ingest_runs (
	run_id		BIGINT		GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	run_mode	TEXT		NOT NULL
			CHECK (run_mode IN ('nightly', 'backfill', 'replay')),
	window_start	TIMESTAMPTZ	NOT NULL,
	window_end	TIMESTAMPTZ	NOT NULL,
	started_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	finished_at	TIMESTAMPTZ,
	rows_seen	INTEGER		CHECK (rows_seen     >=0),
	rows_inserted	INTEGER		CHECK (rows_inserted >=0),
	rows_skipped	INTEGER		CHECK (rows_skipped  >=0),
	run_status	TEXT		NOT NULL DEFAULT 'running'
			CHECK (run_status IN ('running', 'complete', 'failed')),
	error_detail	TEXT,

	CONSTRAINT window_ordered
		CHECK (window_end > window_start),

	CONSTRAINT finished_has_timestamp
		CHECK (run_status = 'running' OR finished_at IS NOT NULL),

	CONSTRAINT complete_has_counts
		CHECK (run_status <> 'complete'
			OR  (rows_seen		IS NOT NULL
			AND rows_inserted	IS NOT NULL
			AND rows_skipped	IS NOT NULL)),

	CONSTRAINT counts_consistent
		CHECK (rows_inserted + rows_skipped <= rows_seen)

);

COMMENT ON TABLE ingest_runs IS
	'One row per execution of an IEM ingest script, written before the work '
	'starts and updated when it finishes.  Deliberately shaped like api_pulls: '
	'a row exists before the attempt so a crash leaves something to reconcile '
	'against.  No emp_id, because this is system-initiated -- the point of the '
	'table is a queryable record of the ABSENCE of a run, which a script that '
	'never fired cannot report about itself.';

COMMENT ON COLUMN ingest_runs.run_mode IS
	'nightly / backfill / replay.  The one-time historical load will outnumber '
	'nightly rows by orders of magnitude, so without this column every '
	'operational question about the recurring job -- did it run, how much did '
	'it see -- would have to guess at the distinction from window width or '
	'row counts.  Separates the two when reading counts months later.';

COMMENT ON COLUMN ingest_runs.window_start IS
	'The UTC range actually requested, not the parameter that produced it.  The '
	'nightly job is invoked with hours=N and computes its own window from '
	'now(); recording the derived range means the run is reproducible without '
	'knowing when it happened to fire.';

COMMENT ON COLUMN ingest_runs.rows_skipped IS
	'Rows written to iem_ingest_rejects during this run.  Non-zero is the alert '
	'condition; the run itself still exits 0, because the process succeeded '
	'even though some input did not.  Expect a non-zero value the first time a '
	'window covering 2018 is ingested -- 76 rows in the archive carry an '
	'unquoted comma in CITY.';

COMMENT ON CONSTRAINT counts_consistent ON ingest_runs IS
	'rows_inserted + rows_skipped <= rows_seen.  The remainder is rows the '
	'natural key discarded as duplicates, which is the expected majority on a '
	'nightly run.  Passes while a run is in progress because the counts are '
	'null and a CHECK fails only on definite false, not on unknown, so one '
	'constraint covers both states.  Catches a miscounting bug in the script, '
	'the same way distance_within_radius catches a matcher bug.';

COMMENT ON CONSTRAINT finished_has_timestamp ON ingest_runs IS
	'Anything that is not running has a finished_at.  This binds failed as well '
	'as complete, which is intended: a run that stopped, stopped at a time, and '
	'the whole point of the table is answering "did last night happen" from the '
	'row alone.  A failed run with a null finished_at would be indistinguishable '
	'from one still in flight.  Consequence for the error handler: the UPDATE '
	'that sets run_status = failed must set finished_at in the same statement.';

COMMENT ON COLUMN ingest_runs.error_detail IS
	'Why a failed run failed.  Deliberately NOT tied to run_status by a '
	'constraint: the moment this column is being written is the moment the '
	'script is already handling something it did not expect, and a CHECK that '
	'fires there would replace a diagnosable failure with an unlogged one.  '
	'Note that finished_has_timestamp already binds failed runs, so the same '
	'UPDATE must set finished_at -- see that constraint.';

COMMENT ON CONSTRAINT complete_has_counts ON ingest_runs IS
	'A run cannot claim to have completed without reporting what it did.  '
	'Counts are nullable rather than DEFAULT 0 because "not yet counted" and '
	'"counted zero" are different facts, and a zero-defaulted crashed run '
	'would read as a successful ingest that found no data.';



CREATE TABLE iem_ingest_rejects (
	reject_id	BIGINT		GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	run_id		BIGINT		NOT NULL
			CONSTRAINT fk_iem_ingest_rejects_run
			REFERENCES ingest_runs (run_id),
	raw_row		TEXT		NOT NULL,
	reason		TEXT		NOT NULL
			CHECK (reason IN	('field_count_mismatch',
						'unknown_report_type',
						'unparseable_timestamp',
						'unparseable_coordinate',
						'unparseable_magnitude')),
	detail		TEXT,
	rejected_at	TIMESTAMPTZ	NOT NULL DEFAULT now()

);

-- Postgres indexes the referenced side of a foreign key, never the referencing
-- side.  This table is not one row per night -- it is one row per rejected line,
-- and the first backfill produces 76 in a single run.  The query that gets typed
-- is "show me the rejects for run N".  Matches api_call_log_pull_id_idx, which
-- is the same shape: child table, FK to a parent run, indexed on the FK.
CREATE INDEX iem_ingest_rejects_run_id_idx ON iem_ingest_rejects (run_id);

COMMENT ON TABLE iem_ingest_rejects IS
	'Rows the parser refused, one per rejected input line.  Skipping a row is '
	'only acceptable because it is not lossy: raw_row holds the input verbatim, '
	'so anything dropped can be inspected, replayed, or entered by hand.  '
	'Skip-and-continue applies ONLY to the reasons enumerated below -- any '
	'other exception must terminate the run.';

COMMENT ON COLUMN iem_ingest_rejects.raw_row IS
	'TEXT, not JSONB.  A row is in here precisely because it did not parse, and '
	'the malformation that rejected it is often the thing that would make it '
	'invalid JSON or invalid CSV as well.  Storing the bytes as received keeps '
	'the problem visible in the record instead of losing it to a second parse.';

COMMENT ON COLUMN iem_ingest_rejects.reason IS
	'Closed enumeration, enforced by CHECK.  Adding a new reject reason takes a '
	'migration and a human decision, which is the point: the alternative is a '
	'free-text column that quietly grows a new value every time the parser '
	'meets something it does not understand.';

COMMENT ON COLUMN iem_ingest_rejects.detail IS
	'The specific instance -- which type pair was unknown, how many fields were '
	'found, what failed to parse.  reason says which class of problem, detail '
	'says which case; without it, diagnosing a reject means re-parsing raw_row '
	'by hand.';

COMMIT;
