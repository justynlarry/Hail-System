-- One running pull per storm.  Two users clicking Pull on the same storm day
-- within seconds of each other each spent a full set of RentCast calls on the
-- same zips.  The rule lives here, not in the app: a
-- check in Python is check-then-insert and loses the race; a unique index
-- cannot.
--
-- Scope: a storm is (storm_date, report_text).  Manual-zip pulls have
-- storm_date NULL (storm_link_paired, sql/013) and are not covered.  Only
-- 'running' rows count, so a finished, failed or cancelled pull never blocks a
-- new one.
--
-- A pull that dies without reaching a final status stays 'running' until
-- something cancels it.  The startup sweep only cancels rows older than
-- PULL_STALE_AFTER and only at startup, so the app also cancels stale rows for
-- the storm just before inserting (jobs.py).  Without that, a restart a few
-- minutes into a pull would block its storm.
--
-- Reverse with: DROP INDEX api_pulls_one_running_per_storm;

BEGIN;

CREATE UNIQUE INDEX api_pulls_one_running_per_storm
    ON api_pulls (storm_date, report_text)
    WHERE api_status = 'running' AND storm_date IS NOT NULL;

COMMENT ON INDEX api_pulls_one_running_per_storm IS
    'At most one running pull per (storm_date, report_text).';

COMMIT;