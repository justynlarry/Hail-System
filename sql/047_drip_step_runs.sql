-- Drip Campaigns:  What a step's job did
--
--  drip_step_runs      one row per attempt to run a step:  inserted 'running', then updated
--                      once to record result.  Nothing else changed/deleted.
-- A step's job spends RentCast calls and can stop for reasons a person has to be able to read
-- on the step screen:  key rejected, unsubscribe sync failed, too many lookups failed, nobody
-- left to email.  This is the record of that, in addition to the log entries.
--
--  running         job is working
--  complete        emails queued, step recorded
--  nothing_to_send everything checked, no one left to email
--  failed          stopped on a problem, named in detail
--  abandoned       found still 'running' by a later job, earlier job died
--
-- detail never holds text from a RentCast or Constant Contact response.
--
-- Reverse with:  DROP TABLE drip_step_runs;  DROP FUNCTION drip_step_runs_guard();


BEGIN;

CREATE TABLE drip_step_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campaign_id     BIGINT NOT NULL,
    step_no         SMALLINT NOT NULL,
    started_by      BIGINT NOT NULL
                    CONSTRAINT fk_drip_step_runs_started_by REFERENCES users (emp_id),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'running'
                    CONSTRAINT drip_step_runs_status_known
                    CHECK (status IN ('running', 'complete', 'nothing_to_send', 'failed',
                                      'abandoned')),
    finished_at     TIMESTAMPTZ,
    method          TEXT
                    CONSTRAINT drip_step_runs_method_known
                    CHECK (method IN ('by_id', 'zip_pull')),
    calls_made      INTEGER
                    CONSTRAINT drip_step_runs_calls_not_negative CHECK (calls_made >= 0),
    emails          INTEGER
                    CONSTRAINT drip_step_runs_emails_not_negative CHECK (emails >= 0),
    detail          TEXT
                    CONSTRAINT drip_step_runs_detail_length
                    CHECK (detail IS NULL OR char_length(detail) <= 300),
    
    CONSTRAINT fk_drip_step_runs_step
        FOREIGN KEY (campaign_id, step_no) REFERENCES drip_steps (campaign_id, step_no),
    CONSTRAINT drip_step_runs_finished_when_not_running
        CHECK ((status = 'running') = (finished_at IS NULL))
);


CREATE UNIQUE INDEX drip_step_runs_one_running
    ON drip_step_runs (campaign_id, step_no) WHERE status = 'running';
CREATE INDEX drip_step_runs_step_idx
    ON drip_step_runs (campaign_id, step_no, started_at DESC);


COMMENT ON TABLE drip_step_runs IS
    'One row per attempt to run a drip step (item 204).  Inserted running, updated once to say '
    'how run ended.  Finished row never changes and nothing is deleted.';

CREATE FUNCTION drip_step_runs_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drip_step_runs: rows cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'running' THEN
            RAISE EXCEPTION 'drip_step_runs: a run starts as running'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NOT EXISTS (SELECT 1 FROM drip_campaigns
                        WHERE campaign_id = NEW.campaign_id AND status = 'active') THEN
            RAISE EXCEPTION 'drip_step_runs: the campaign is not active'
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM drip_steps
                   WHERE campaign_id = NEW.campaign_id AND step_no = NEW.step_no
                     AND batch_id IS NOT NULL) THEN
            RAISE EXCEPTION 'drip_step_runs: that step was already sent'
                USING ERRCODE = 'restrict_violation';
        END IF;
        NEW.started_at := now();
        RETURN NEW;
    END IF;

    IF OLD.status <> 'running' THEN
        RAISE EXCEPTION 'drip_step_runs: a finished run cannot change'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.run_id IS DISTINCT FROM OLD.run_id
        OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
        OR NEW.step_no IS DISTINCT FROM OLD.step_no
        OR NEW.started_by IS DISTINCT FROM OLD.started_by
        OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
            RAISE EXCEPTION 'drip_step_runs: only how the run ended can be set'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.status = 'running' THEN
        RAISE EXCEPTION 'drip_step_runs: an update must say how the run ended'
            USING ERRCODE = 'restrict_violation';
    END IF;
    NEW.finished_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_drip_step_runs_guard
    BEFORE INSERT OR UPDATE OR DELETE ON drip_step_runs
    FOR EACH ROW EXECUTE FUNCTION drip_step_runs_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT ON drip_step_runs TO hail_app;
GRANT UPDATE (status, method, calls_made, emails, detail) ON drip_step_runs TO hail_app;
REVOKE DELETE, TRUNCATE ON drip_step_runs FROM hail_app;

COMMIT;