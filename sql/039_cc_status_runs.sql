-- One row per run of the Constant Contact bounce check.
-- Check asks Constant Contact about recent sends and records bounces
-- in send_log.  This table is its log, and records when it ran, what
-- it found, and whether it finished.  Newest 'ok' row is the last good
-- check.
--
-- Run is inserted as 'running' and committed before any work, so a 
-- crash leaves a visible row.  Rows are never deleted and a finished row 
-- doesn't change.  Python takes an advisory lock for the whole run, so a 
-- 'running' row seen at the start of a new run belongs to a dead process and
-- is closed as failed.

BEGIN;

CREATE TABLE cc_status_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CONSTRAINT cc_status_runs_status_known
                    CHECK (status IN ('running', 'ok', 'failed')),
    triggered_by    BIGINT      NOT NULL
                    CONSTRAINT fk_cc_status_runs_triggered_by
                    REFERENCES users (emp_id),
    checked         INTEGER     NOT NULL DEFAULT 0
                    CONSTRAINT cc_status_runs_checked_nonneg CHECK (checked >= 0),
    bounced         INTEGER     NOT NULL DEFAULT 0
                    CONSTRAINT cc_status_runs_bounced_nonneg CHECK (bounced >= 0),
    suppressed      INTEGER     NOT NULL DEFAULT 0
                    CONSTRAINT cc_status_runs_suppressed_nonneg CHECK (suppressed >= 0),
    error_detail    TEXT,

    CONSTRAINT cc_status_runs_finished_when_done
        CHECK ((status = 'running') = (finished_at IS NULL))
);

COMMENT ON TABLE cc_status_runs IS
    'Log of Constant Contact bounce checks, a finished row never changes. '
    'Newest status = ok, row is last good check.';

CREATE FUNCTION cc_status_runs_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'cc_status_runs is a log: row % cannot be deleted', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status <> 'running' THEN
        RAISE EXCEPTION
            'cc_status_runs row %: a finished run cannot change.', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.run_id       IS DISTINCT FROM OLD.run_id
    OR NEW.started_at   IS DISTINCT FROM OLD.started_at
    OR NEW.triggered_by IS DISTINCT FROM OLD.triggered_by
    THEN
        RAISE EXCEPTION
            'cc_status_runs row %: only the outcome of a running run may change.', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cc_status_runs_guard() IS
    'Refuses DELETE, or any change to a finished run, and freezes the identifying '
    'columns of a running one.';

CREATE TRIGGER trg_cc_status_runs_guard
    BEFORE UPDATE OR DELETE ON cc_status_runs
    FOR EACH ROW
    EXECUTE FUNCTION cc_status_runs_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT, UPDATE ON cc_status_runs TO hail_app;
REVOKE DELETE, TRUNCATE ON cc_status_runs FROM hail_app;

COMMIT;
