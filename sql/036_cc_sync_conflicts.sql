-- Addresses a Constant Contact sync found unsubscribed whose dnc_list row an
-- admin had removed.  Sync counts these and leaves them alone, this table 
-- keeps which rows, per run, so an admin can review them.  Stores dnc_id
-- not the address.  Insert-only.

BEGIN;

CREATE TABLE cc_sync_conflicts(
    conflict_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          BIGINT      NOT NULL
                    CONSTRAINT fk_cc_sync_conflicts_run
                    REFERENCES cc_sync_runs (run_id),
    dnc_id          BIGINT      NOT NULL
                    CONSTRAINT fk_cc_sync_conflicts_dnc
                    REFERENCES dnc_list (dnc_id),
    seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT cc_sync_conflicts_once_per_run UNIQUE (run_id, dnc_id)
);

COMMENT ON TABLE cc_sync_conflicts IS
    'Insert-only.  dnc_list rows removed by an admin that Constant Contact still '
    'reports unsubscribed, per sync run.  Reported, never undone.';

CREATE FUNCTION cc_sync_conflicts_guard() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
    'cc_sync_conflicts is a log: % of row % refused', TG_OP, OLD.conflict_id
    USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cc_sync_conflicts_guard
    BEFORE UPDATE OR DELETE ON cc_sync_conflicts
    FOR EACH ROW
    EXECUTE FUNCTION cc_sync_conflicts_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT ON cc_sync_conflicts TO hail_app;
REVOKE UPDATE, DELETE, TRUNCATE ON cc_sync_conflicts FROM hail_app;

COMMIT;

