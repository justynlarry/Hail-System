-- The email as it was actually sent.  One row per email (one realtor,
-- one send click, however many properties it lists).  send_log keeps
-- one row per match and points here through email_id.
--
-- Constant Contact will not return the HTML of a sent campaign and a 
-- campaign can be deleted on the CC side, so this table is a record of
-- what a person was sent.  It also records the unsubscribe sync that 
-- clears the Constant Contact permissions (implicit, explicit, or none
-- for an existing active contact).
--
-- Never deleted, the email as sent doesn't change.  The Constant Contact
-- ids and the progress markers are filled in as the send moves along and 
-- can be set once.

BEGIN;

-- send_log.email_id is NOT NULL -> Only works while send_log is empty.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM send_log) THEN
        RAISE EXCEPTION
            'send_log is not empty: email_id cannot be added NOT NULL; backfill first';
    END IF;
END;
$$;

CREATE TABLE sent_emails (
    email_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id        UUID        NOT NULL,
    realtor_id      BIGINT      NOT NULL
                    CONSTRAINT fk_sent_emails_realtor
                    REFERENCES realtors (realtor_id),
    recipient_email TEXT        NOT NULL,
                    CONSTRAINT sent_emails_recipient_not_empty
                    CHECK (recipient_email <> ''),
    template_id     BIGINT      NOT NULL
                    CONSTRAINT  fk_sent_emails_template
                    REFERENCES  email_templates (template_id),
    sync_run_id     BIGINT      NOT NULL
                    CONSTRAINT fk_sent_emails_sync_run
                    REFERENCES cc_sync_runs (run_id),
    permission_asserted TEXT    NOT NULL
                    CONSTRAINT sent_emails_permission_known
                    CHECK (permission_asserted IN ('implicit', 'explicit', 'none')),
    created_by      BIGINT      NOT NULL
                    CONSTRAINT fk_sent_emails_created_by
                    REFERENCES users (emp_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject         TEXT        NOT NULL
                    CONSTRAINT sent_emails_subject_not_empty CHECK (subject <> ''),
    html_body       TEXT        NOT NULL
                    CONSTRAINT sent_emails_body_not_empty CHECK (html_body <> ''),
    cc_contact_id   TEXT,
    cc_list_id      TEXT,
    cc_campaign_id  TEXT,
    cc_activity_id  TEXT,
    scheduled_at    TIMESTAMPTZ,
    list_deleted_at TIMESTAMPTZ,
    error_detail    TEXT,

    CONSTRAINT sent_emails_one_per_batch UNIQUE (batch_id, realtor_id),
    CONSTRAINT sent_emails_schedule_needs_activity
        CHECK (scheduled_at IS NULL OR cc_activity_id IS NOT NULL),
    CONSTRAINT sent_emails_list_delete_needs_list
        CHECK (list_deleted_at IS NULL OR cc_list_id IS NOT NULL)
);

CREATE UNIQUE INDEX sent_emails_activity_uq
    ON sent_emails (cc_activity_id) WHERE cc_activity_id IS NOT NULL;
CREATE UNIQUE INDEX sent_emails_campaign_uq
    ON sent_emails (cc_campaign_id) WHERE cc_campaign_id IS NOT NULL;
CREATE INDEX sent_emails_realtor_created_idx
    ON sent_emails (realtor_id, created_at DESC);

COMMENT ON TABLE sent_emails IS
    'The email as sent, one row per realtor per send click.  Never deleted, '
    'the rendered subject and body do not change.  Constant Contact ids and '
    'progress markers are write-once.';

CREATE FUNCTION sent_emails_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'sent_emails is append-only: row % cannot be deleted', OLD.email_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.email_id            IS DISTINCT FROM OLD.email_id
    OR NEW.batch_id            IS DISTINCT FROM OLD.batch_id
    OR NEW.realtor_id          IS DISTINCT FROM OLD.realtor_id
    OR NEW.recipient_email     IS DISTINCT FROM OLD.recipient_email
    OR NEW.template_id         IS DISTINCT FROM OLD.template_id
    OR NEW.sync_run_id         IS DISTINCT FROM OLD.sync_run_id
    OR NEW.permission_asserted IS DISTINCT FROM OLD.permission_asserted
    OR NEW.created_by          IS DISTINCT FROM OLD.created_by
    OR NEW.created_at          IS DISTINCT FROM OLD.created_at
    OR NEW.subject             IS DISTINCT FROM OLD.subject
    OR NEW.html_body           IS DISTINCT FROM OLD.html_body
    THEN
        RAISE EXCEPTION
            'sent_emails row %: the email as sent cannot change', OLD.email_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Write-once: NULL -> value is the normal lifecycle; changing afterward
    -- rewrites history.  Writing the same value again is allowed (a retry).
    IF (OLD.cc_contact_id   IS NOT NULL AND NEW.cc_contact_id   IS DISTINCT FROM OLD.cc_contact_id)
    OR (OLD.cc_list_id      IS NOT NULL AND NEW.cc_list_id      IS DISTINCT FROM OLD.cc_list_id)
    OR (OLD.cc_campaign_id  IS NOT NULL AND NEW.cc_campaign_id  IS DISTINCT FROM OLD.cc_campaign_id)
    OR (OLD.cc_activity_id  IS NOT NULL AND NEW.cc_activity_id  IS DISTINCT FROM OLD.cc_activity_id)
    OR (OLD.scheduled_at    IS NOT NULL AND NEW.scheduled_at    IS DISTINCT FROM OLD.scheduled_at)
    OR (OLD.list_deleted_at IS NOT NULL AND NEW.list_deleted_at IS DISTINCT FROM OLD.list_deleted_at)
    OR (OLD.error_detail    IS NOT NULL AND NEW.error_detail    IS DISTINCT FROM OLD.error_detail)
    THEN
        RAISE EXCEPTION
            'sent_emails row %: a Constant Contact id or progress marker is write-once',
            OLD.email_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION sent_emails_guard() IS
    'Refuses DELETE, freezes the email as sent, and makes the Constant Contact '
    'ids and progress markers write-once.';

CREATE TRIGGER trg_sent_emails_guard
    BEFORE UPDATE OR DELETE ON sent_emails
    FOR EACH ROW
    EXECUTE FUNCTION sent_emails_guard();

ALTER TABLE send_log
    ADD COLUMN email_id BIGINT NOT NULL
    CONSTRAINT fk_send_log_email REFERENCES sent_emails (email_id);

CREATE INDEX send_log_email_id_idx ON send_log (email_id);

CREATE FUNCTION send_log_email_guard() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.email_id IS DISTINCT FROM OLD.email_id THEN
        RAISE EXCEPTION
            'send_log row %: email_id cannot change', OLD.send_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_send_log_email_frozen
    BEFORE UPDATE ON send_log
    FOR EACH ROW
    EXECUTE FUNCTION send_log_email_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT, UPDATE ON sent_emails TO hail_app;
REVOKE DELETE, TRUNCATE ON sent_emails FROM hail_app;

COMMIT;


