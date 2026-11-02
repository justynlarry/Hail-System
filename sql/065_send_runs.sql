-- sql/065: send_runs, a record of each send and resume job.
--
-- A send or resume runs in a background thread (hailsys/web/sendjobs.py).  A job that failed before it queued
-- anything leaves only a log line:  the history page reads sent_emails, so a user that clicked Send saw nothing,
-- and couldn't tell "nothing was eligible" from "it failed".  The same pattern as drip_step_runs (sql/047).  One row
-- per job, inserted 'running', updated once with the result.
--
--   running          the job is working
--   complete         emails were queued (send) or delivered (resume);  batch_id says which batch
--   nothing_to_send  the send found nobody to email
--   busy             another send held the lock, so this one did nothing
--   failed           stopped on an error;  detail is the exception's class name
--   abandoned        found still 'running' by a later job:  the earlier one died with its process
--
-- detail never holds message text:  a Constant Contact error can carry the provider's own text (items 275, 047).
-- The full error stays in the web log.
--
-- Reverse with:  DROP TABLE send_runs;  DROP FUNCTION send_runs_guard();

BEGIN;

CREATE TABLE send_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            TEXT NOT NULL
                    CONSTRAINT send_runs_kind_known CHECK (kind IN ('send', 'resume')),
    started_by      BIGINT NOT NULL
                    CONSTRAINT fk_send_runs_started_by REFERENCES users (emp_id),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'running'
                    CONSTRAINT send_runs_status_known
                    CHECK (status IN ('running', 'complete', 'nothing_to_send', 'busy', 'failed', 'abandoned')),
    finished_at     TIMESTAMPTZ,
    batch_id        UUID,
    emails          INTEGER
                    CONSTRAINT send_runs_emails_not_negative CHECK (emails >= 0),
    detail          TEXT
                    CONSTRAINT send_runs_detail_length CHECK (detail IS NULL OR char_length(detail) <= 300),

    CONSTRAINT send_runs_finished_when_not_running
        CHECK ((status = 'running') = (finished_at IS NULL)),
    CONSTRAINT send_runs_resume_names_its_batch
        CHECK (kind <> 'resume' OR batch_id IS NOT NULL),
    CONSTRAINT send_runs_complete_names_its_batch
        CHECK (status <> 'complete' OR batch_id IS NOT NULL)
);

CREATE INDEX send_runs_started_at_idx ON send_runs (started_at DESC);

COMMENT ON TABLE send_runs IS
    'One row per send or resume job (item 196).  Inserted running, updated once to say how the job ended.  '
    'A finished row never changes and nothing is deleted.  detail is an exception class name, never message text.';

CREATE FUNCTION send_runs_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'send_runs: rows cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'running' THEN
            RAISE EXCEPTION 'send_runs: a job starts as running'
                USING ERRCODE = 'restrict_violation';
        END IF;
        NEW.started_at := now();
        RETURN NEW;
    END IF;

    IF OLD.status <> 'running' THEN
        RAISE EXCEPTION 'send_runs: a finished job cannot change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
        OR NEW.kind IS DISTINCT FROM OLD.kind
        OR NEW.started_by IS DISTINCT FROM OLD.started_by
        OR NEW.started_at IS DISTINCT FROM OLD.started_at
        OR (OLD.batch_id IS NOT NULL AND NEW.batch_id IS DISTINCT FROM OLD.batch_id) THEN
        RAISE EXCEPTION 'send_runs: only how the job ended can be set'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.status = 'running' THEN
        RAISE EXCEPTION 'send_runs: an update must say how the job ended'
            USING ERRCODE = 'restrict_violation';
    END IF;
    NEW.finished_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_send_runs_guard
    BEFORE INSERT OR UPDATE OR DELETE ON send_runs
    FOR EACH ROW EXECUTE FUNCTION send_runs_guard();

GRANT SELECT, INSERT, UPDATE ON send_runs TO hail_app;

COMMIT;

