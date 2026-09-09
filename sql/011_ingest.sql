-- 011_ingest.sql
--
-- Additive amendment to comments on ingest_runs, created 2026-09-09.
--
-- WHY THIS IS A NEW FILE RATHER THAN AN EDIT TO 009_ingest.sql:
-- the backfill has run (run_id = 4, 2021-01-01 -> 2026-09-09, 84,268 rows),
-- which is the line set by the 2026-09-04 decision "sql/ is a build directory
-- until the backfill runs; additive after".  Past that point 001-009 are
-- history, not source: the built database will not have an edit made to an
-- early file, and no rebuild will reveal the gap because there will be no
-- rebuild.  A COMMENT ON is idempotent and safe to re-run.
--
-- WHAT CHANGED: the rows_skipped comment in 009 told the operator to expect a
-- non-zero value "the first time a window covering 2018 is ingested", on the
-- premise that all 76 unquoted-comma CITY rows were from 2018.  That premise is
-- wrong -- 75 are from 2018 and one is from 2026-08-31.  See the 2026-09-09
-- reversal entry in docs/decision-log.md.

BEGIN;

COMMENT ON COLUMN ingest_runs.rows_skipped IS
	'Rows written to iem_ingest_rejects during this run.  The run itself still '
	'exits 0, because the process succeeded even though some input did not.  '
	'Expect a non-zero value OCCASIONALLY AND INDEFINITELY: 76 rows in the '
	'2016-2026 archive carry an unquoted comma in CITY, and while 75 are from '
	'2018, one is from 2026-08-31 -- the malformation is ongoing upstream, not '
	'a historical artifact.  Roughly one row in 84,000 over five years.  '
	'DO NOT alert on rows_skipped > 0 as though it were an emergency; it is the '
	'documented normal state of this feed, and an alert that fires on normal '
	'operation gets muted, which is worse than not having one.  The condition '
	'worth alerting on is a SUSTAINED or SUDDEN rise, not a non-zero value.';

COMMIT;
