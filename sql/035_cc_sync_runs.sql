-- One row per run of the Constant Contact unsubscribe sync.
-- Sync copies Constant Contact's unsubscribed contacts into dnc_list.
-- This table is its log: when it ran, how far back it looked, what it
-- found, and whether it finished.  Newest 'ok' row is the 'last good sync"
--
-- A run is inserted as 'running' and committed before any work starts, so a
-- crash leaves a visible row.  Rows aren't deleted and never change.
-- Python takes an advisory lock for the whole run, so a 'running' row
-- seen at the start of a new run belongs to a dead process and is closed
-- as failed.  error_detail is fixed text.

BEGIN;

CREATE TABLE cc_sync_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CONSTRAINT cc_sync_runs_status_known
                    CHECK (status IN ('running', 'ok', 'failed')),
    triggered_by    BIGINT      NOT NULL
                    CONSTRAINT fk_cc_sync_runs_triggered_by
                    REFERENCES users (emp_id),
    -- updated_after used, NULL for a full pull.
    watermark       TIMESTAMPTZ,
    fetched         INTEGER     NOT NULL DEFAULT 0
                    CONSTRAINT cc_sync_runs_fetched_nonneg CHECK (fetched >=0),
    inserted        INTEGER     NOT NULL DEFAULT 0
                    CONSTRAINT cc_sync_runs_inserted_nonneg CHECK (inserted >=0),
    already_present INTEGER     NOT NULL DEFAULT 0
                    CONSTRAINT cc_sync_runs_present_nonneg CHECK (already_present >= 0),
    conflicts       INTEGER     NOT NULL DEFAULT 0
                    CONSTRAINT cc_sync_runs_conflicts_nonneg CHECK (conflicts >= 0),
    error_detail    TEXT,

    CONSTRAINT cc_sync_runs_finished_when_done
        CHECK ((status = 'running') = (finished_at IS NULL))
);


COMMENT ON TABLE cc_sync_runs IS
    'Log of Constant Contact unsubscribe syncs.  Never deleted. '
    'A finished row never changes.  Newest status = ok, row is last good sync.';

CREATE FUNCTION  cc_sync_runs_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION
        'cc_sync_runs is a log: row % cannot be deleted', OLD.run_id
        USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status <> 'running' THEN
        RAISE EXCEPTION
            'cc_sync_runs row %: a finished run cannot change.',
            OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.run_id       IS DISTINCT FROM OLD.run_id
    OR NEW.started_at   IS DISTINCT FROM OLD.started_at
    OR NEW.triggered_by IS DISTINCT FROM OLD.triggered_by
    OR NEW.watermark    IS DISTINCT FROM OLD.watermark
    THEN
        RAISE EXCEPTION
            'cc_sync_runs row %: only the outcome of a running run may change',
            OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cc_sync_runs_guard() IS
    'Refuses DELETE, refuses any change to a finished run, and freezes the '
    'identifying columns of a running one.';

CREATE TRIGGER trg_cc_sync_runs_guard
    BEFORE UPDATE OR DELETE ON cc_sync_runs
    FOR EACH ROW
    EXECUTE FUNCTION cc_sync_runs_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT, UPDATE ON cc_sync_runs TO hail_app;
REVOKE DELETE, TRUNCATE ON cc_sync_runs FROM hail_app;

COMMIT;