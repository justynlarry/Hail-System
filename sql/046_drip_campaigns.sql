-- Drip Campaigns:  Campaign Tables
-- 
--  drip_campaigns      one row per campaign, only status changes
--  drip_steps          follow-ups, frozen at creation except for sends
--  drip_recipients     agents emailed in step 1, list may get smaller, not larger
--  drip_listing_checks one row per RentCast lookup by id, append-only
--  drip_step_pulls     which zip pulls a step spent calls on, append-only
--
-- Step 1 is a normal send.  Campaign points at that batch, and follow-up N is
-- step_no N.  A campaign's matches are the send_log rows of its first batch.
-- Rules are in triggers so the application can't get around them.
--
-- Reverse with: DROP TABLE drip_step_pulls, drip_listing_checks, drip_recipients, drip_steps,
--   drip_campaigns; DROP FUNCTION drip_campaigns_guard(), drip_steps_guard(),
--   drip_recipients_guard(), drip_log_guard();

BEGIN;

CREATE TABLE drip_campaigns (
    campaign_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                TEXT NOT NULL
                        CONSTRAINT drip_campaigns_name_length
                        CHECK (char_length(name) BETWEEN 1 AND 100),
    first_batch_id      UUID NOT NULL
                        CONSTRAINT drip_campaigns_first_batch_uq UNIQUE,
    created_by          BIGINT NOT NULL
                        CONSTRAINT fk_drip_campaigns_created_by REFERENCES users (emp_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    status              TEXT NOT NULL DEFAULT 'active'
                        CONSTRAINT drip_campaigns_status_known
                        CHECK (status IN ('active', 'paused', 'cancelled', 'complete')),
    status_changed_at   TIMESTAMPTZ,
    status_changed_by   BIGINT
                        CONSTRAINT fk_drip_campaigns_status_changed_by REFERENCES users (emp_id),
    CONSTRAINT drip_campaigns_status_change_has_both
        CHECK ((status_changed_at IS NULL) = (status_changed_by IS NULL))
);

CREATE TABLE drip_steps (
    campaign_id         BIGINT NOT NULL
                        CONSTRAINT fk_drip_steps_campaign REFERENCES drip_campaigns (campaign_id),
    step_no             SMALLINT NOT NULL
                        CONSTRAINT drip_steps_step_no_range CHECK (step_no BETWEEN 1 AND 10),
    gap_days      SMALLINT NOT NULL
                  CONSTRAINT drip_steps_gap_days_range CHECK (gap_days BETWEEN 1 AND 30),
    template_name TEXT NOT NULL
                  CONSTRAINT drip_steps_template_name_length
                  CHECK (char_length(template_name) BETWEEN 1 AND 100),
    batch_id      UUID
                  CONSTRAINT drip_steps_batch_uq UNIQUE,
    started_by    BIGINT
                  CONSTRAINT fk_drip_steps_started_by REFERENCES users (emp_id),
    started_at    TIMESTAMPTZ,

    PRIMARY KEY (campaign_id, step_no),
    CONSTRAINT drip_steps_sent_has_all_three
        CHECK ((batch_id IS NULL) = (started_by IS NULL)
               AND (batch_id IS NULL) = (started_at IS NULL))
);

CREATE TABLE drip_recipients (
    campaign_id     BIGINT NOT NULL
                    CONSTRAINT fk_drip_recipients_campaign REFERENCES drip_campaigns (campaign_id),
    realtor_id      BIGINT NOT NULL
                    CONSTRAINT fk_drip_recipients_realtor REFERENCES realtors (realtor_id),
    recipient_email TEXT NOT NULL
                    CONSTRAINT drip_recipients_email_address
                    CHECK (recipient_email ~ '^[^[:space:]@]+@[^[:space:]@]+$'),
    stopped_at      TIMESTAMPTZ,
    stop_reason     TEXT
                    CONSTRAINT drip_recipients_reason_known
                    CHECK (stop_reason IN ('unsubscribed', 'bounced', 'no_active_listings',
                                           'manual', 'replied')),
    stopped_by      BIGINT
                    CONSTRAINT fk_drip_recipients_stopped_by REFERENCES users (emp_id),

    PRIMARY KEY (campaign_id, realtor_id),
    CONSTRAINT drip_recipients_stop_has_reason
        CHECK ((stopped_at IS NULL) = (stop_reason IS NULL)),
    CONSTRAINT drip_recipients_person_stops_by_hand
        CHECK ((stop_reason IN ('manual', 'replied')) = (stopped_by IS NOT NULL)
               OR stop_reason IS NULL AND stopped_by IS NULL)
);

CREATE TABLE drip_listing_checks (
    check_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campaign_id BIGINT NOT NULL,
    step_no     SMALLINT NOT NULL,
    listing_id  BIGINT NOT NULL
                CONSTRAINT fk_drip_listing_checks_listing REFERENCES listings (listing_id),
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    calls_made  INTEGER NOT NULL
                CONSTRAINT drip_listing_checks_calls_not_negative CHECK (calls_made >= 0),
    http_status INTEGER,
    outcome     TEXT NOT NULL
                CONSTRAINT drip_listing_checks_outcome_known
                CHECK (outcome IN ('active', 'inactive', 'not_found', 'error')),
    rc_status   TEXT,
    last_seen   TIMESTAMPTZ,
    removed_at  TIMESTAMPTZ,
    detail      TEXT
                CONSTRAINT drip_listing_checks_detail_length
                CHECK (detail IS NULL OR char_length(detail) <= 300),

    CONSTRAINT fk_drip_listing_checks_step
        FOREIGN KEY (campaign_id, step_no) REFERENCES drip_steps (campaign_id, step_no),
    CONSTRAINT drip_listing_checks_answered_has_status
        CHECK (outcome NOT IN ('active', 'inactive') OR rc_status IS NOT NULL)
);

CREATE INDEX drip_listing_checks_time_idx ON drip_listing_checks (checked_at);

CREATE TABLE drip_step_pulls (
    pull_id     BIGINT PRIMARY KEY
                CONSTRAINT fk_drip_step_pulls_pull REFERENCES api_pulls (pull_id),
    campaign_id BIGINT NOT NULL,
    step_no     SMALLINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_drip_step_pulls_step
        FOREIGN KEY (campaign_id, step_no) REFERENCES drip_steps (campaign_id, step_no)
);

COMMENT ON TABLE drip_campaigns IS
    'A drip campaign (item 204).  Step 1 is the ordinary send in first_batch_id.  Only status '
    'changes, and only along the allowed moves; nothing is deleted.';
COMMENT ON TABLE drip_steps IS
    'The follow-ups of a campaign.  Frozen at creation except for being sent: batch_id, '
    'started_by and started_at are set together, once, in the transaction that queues the emails.';
COMMENT ON TABLE drip_recipients IS
    'The people emailed in step 1.  The list never grows; a recipient can be stopped, once.';
COMMENT ON TABLE drip_listing_checks IS
    'One row per RentCast lookup by id, for the call count and the quota.  Append-only.';
COMMENT ON TABLE drip_step_pulls IS
    'The zip pulls a campaign step spent RentCast calls on (api_pulls holds the calls).  Append-only.';


CREATE FUNCTION drip_campaigns_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drip_campaigns: campaign % cannot be deleted', OLD.campaign_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'active' THEN
            RAISE EXCEPTION 'drip_campaigns: a new campaign starts active'
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM sent_emails WHERE batch_id = NEW.first_batch_id) THEN
            RAISE EXCEPTION 'drip_campaigns: no emails were queued in batch %', NEW.first_batch_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR NEW.name IS DISTINCT FROM OLD.name
       OR NEW.first_batch_id IS DISTINCT FROM OLD.first_batch_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'drip_campaigns: only the status of campaign % can change', OLD.campaign_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status = OLD.status THEN
        IF NEW.status_changed_at IS DISTINCT FROM OLD.status_changed_at
           OR NEW.status_changed_by IS DISTINCT FROM OLD.status_changed_by THEN
            RAISE EXCEPTION 'drip_campaigns: campaign % changed status without a status change',
                OLD.campaign_id USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NOT ((OLD.status = 'active' AND NEW.status IN ('paused', 'cancelled', 'complete'))
            OR (OLD.status = 'paused' AND NEW.status IN ('active', 'cancelled'))) THEN
        RAISE EXCEPTION 'drip_campaigns: campaign % cannot go from % to %',
            OLD.campaign_id, OLD.status, NEW.status USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status = 'complete' AND EXISTS (
            SELECT 1 FROM drip_steps WHERE campaign_id = NEW.campaign_id AND batch_id IS NULL) THEN
        RAISE EXCEPTION 'drip_campaigns: campaign % has a step that was not sent', OLD.campaign_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    NEW.status_changed_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION drip_steps_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drip_steps: step % of campaign % cannot be deleted',
            OLD.step_no, OLD.campaign_id USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.batch_id IS NOT NULL THEN
            RAISE EXCEPTION 'drip_steps: a step is created unsent'
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM drip_steps
                   WHERE campaign_id = NEW.campaign_id AND batch_id IS NOT NULL) THEN
            RAISE EXCEPTION 'drip_steps: campaign % has already sent a step, so none can be added',
                NEW.campaign_id USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.step_no > 1 AND NOT EXISTS (
                SELECT 1 FROM drip_steps
                WHERE campaign_id = NEW.campaign_id AND step_no = NEW.step_no - 1) THEN
            RAISE EXCEPTION 'drip_steps: steps are added in order (step % has no step before it)',
                NEW.step_no USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR NEW.step_no IS DISTINCT FROM OLD.step_no
       OR NEW.gap_days IS DISTINCT FROM OLD.gap_days
       OR NEW.template_name IS DISTINCT FROM OLD.template_name THEN
        RAISE EXCEPTION 'drip_steps: step % of campaign % is frozen', OLD.step_no, OLD.campaign_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.batch_id IS NOT NULL THEN
        IF NEW.batch_id IS DISTINCT FROM OLD.batch_id OR NEW.started_by IS DISTINCT FROM OLD.started_by
           OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
            RAISE EXCEPTION 'drip_steps: step % of campaign % was already sent',
                OLD.step_no, OLD.campaign_id USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.batch_id IS NULL THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM drip_campaigns
                   WHERE campaign_id = NEW.campaign_id AND status = 'active') THEN
        RAISE EXCEPTION 'drip_steps: campaign % is not active', NEW.campaign_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.step_no > 1 AND NOT EXISTS (
            SELECT 1 FROM drip_steps WHERE campaign_id = NEW.campaign_id
                AND step_no = NEW.step_no - 1 AND batch_id IS NOT NULL) THEN
        RAISE EXCEPTION 'drip_steps: step % cannot be sent before the step before it', NEW.step_no
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.batch_id = (SELECT first_batch_id FROM drip_campaigns
                       WHERE campaign_id = NEW.campaign_id) THEN
        RAISE EXCEPTION 'drip_steps: a follow-up cannot reuse the campaign''s first batch'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM sent_emails WHERE batch_id = NEW.batch_id) THEN
        RAISE EXCEPTION 'drip_steps: no emails were queued in batch %', NEW.batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    NEW.started_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION drip_recipients_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drip_recipients: a recipient of campaign % cannot be deleted',
            OLD.campaign_id USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF EXISTS (SELECT 1 FROM drip_steps
                   WHERE campaign_id = NEW.campaign_id AND batch_id IS NOT NULL) THEN
            RAISE EXCEPTION 'drip_recipients: campaign % has sent a follow-up, so its list is fixed',
                NEW.campaign_id USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.stopped_at IS NOT NULL OR NEW.stop_reason IS NOT NULL THEN
            RAISE EXCEPTION 'drip_recipients: a recipient starts active'
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NOT EXISTS (
                SELECT 1 FROM sent_emails e
                JOIN drip_campaigns c ON c.first_batch_id = e.batch_id
                WHERE c.campaign_id = NEW.campaign_id AND e.realtor_id = NEW.realtor_id
                  AND e.recipient_email = NEW.recipient_email) THEN
            RAISE EXCEPTION 'drip_recipients: that address was not emailed in the first batch of campaign %',
                NEW.campaign_id USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
       OR NEW.realtor_id IS DISTINCT FROM OLD.realtor_id
       OR NEW.recipient_email IS DISTINCT FROM OLD.recipient_email THEN
        RAISE EXCEPTION 'drip_recipients: only the stop columns can change'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.stop_reason IS NOT NULL THEN
        IF NEW.stopped_at IS DISTINCT FROM OLD.stopped_at
           OR NEW.stop_reason IS DISTINCT FROM OLD.stop_reason
           OR NEW.stopped_by IS DISTINCT FROM OLD.stopped_by THEN
            RAISE EXCEPTION 'drip_recipients: a stopped recipient stays stopped'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.stop_reason IS NOT NULL THEN
        NEW.stopped_at := now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION drip_log_guard() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% is a log: rows cannot be changed or deleted', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_drip_campaigns_guard
    BEFORE INSERT OR UPDATE OR DELETE ON drip_campaigns
    FOR EACH ROW EXECUTE FUNCTION drip_campaigns_guard();

CREATE TRIGGER trg_drip_steps_guard
    BEFORE INSERT OR UPDATE OR DELETE ON drip_steps
    FOR EACH ROW EXECUTE FUNCTION drip_steps_guard();

CREATE TRIGGER trg_drip_recipients_guard
    BEFORE INSERT OR UPDATE OR DELETE ON drip_recipients
    FOR EACH ROW EXECUTE FUNCTION drip_recipients_guard();

CREATE TRIGGER trg_drip_listing_checks_guard
    BEFORE UPDATE OR DELETE ON drip_listing_checks
    FOR EACH ROW EXECUTE FUNCTION drip_log_guard();

CREATE TRIGGER trg_drip_step_pulls_guard
    BEFORE UPDATE OR DELETE ON drip_step_pulls
    FOR EACH ROW EXECUTE FUNCTION drip_log_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT ON drip_campaigns, drip_steps, drip_recipients,
                        drip_listing_checks, drip_step_pulls TO hail_app;
GRANT UPDATE (status, status_changed_at, status_changed_by) ON drip_campaigns TO hail_app;
GRANT UPDATE (batch_id, started_by, started_at) ON drip_steps TO hail_app;
GRANT UPDATE (stopped_at, stop_reason, stopped_by) ON drip_recipients TO hail_app;
REVOKE DELETE, TRUNCATE ON drip_campaigns, drip_steps, drip_recipients,
                           drip_listing_checks, drip_step_pulls FROM hail_app;
REVOKE UPDATE ON drip_listing_checks, drip_step_pulls FROM hail_app;

COMMIT;
