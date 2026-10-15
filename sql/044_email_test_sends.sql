-- One row per test send from the template editor (item 203, piece 6).  A test send emails the
-- draft on screen, rendered with sample storm data, to the admin who clicked, through a
-- temporary Constant Contact campaign that is deleted afterwards.
--
-- Written once, at the end of the attempt, with its outcome: rows are never changed or deleted.
-- The hourly limit counts every row for a user, whatever its outcome, because a failed attempt
-- still costs Constant Contact calls.  No email content is kept, only the subject and its size.
--
-- outcome: accepted = Constant Contact took the test (204: accepted, not delivered);
--          failed   = it refused, or the attempt stopped before the test was posted;
--          unknown  = the POST may have taken effect (no answer, a server error), never retried.
-- campaign_cleanup: deleted = the temporary campaign was removed; left_behind = it could not be,
--          and is still in the Constant Contact account; none = no campaign was created.

BEGIN;

CREATE TABLE email_test_sends (
    test_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sent_by          BIGINT NOT NULL
                     CONSTRAINT fk_email_test_sends_sent_by REFERENCES users (emp_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    recipient        TEXT NOT NULL
                     CONSTRAINT email_test_sends_recipient_address
                     CHECK (recipient ~ '^[^[:space:]@]+@[^[:space:]@]+$'),
    template_name    TEXT
                     CONSTRAINT email_test_sends_template_name_length
                     CHECK (template_name IS NULL OR char_length(template_name) <= 100),
    situation        TEXT NOT NULL
                     CONSTRAINT email_test_sends_situation_length
                     CHECK (char_length(situation) BETWEEN 1 AND 40),
    subject          TEXT NOT NULL,
    html_bytes       INTEGER NOT NULL
                     CONSTRAINT email_test_sends_html_bytes_positive CHECK (html_bytes > 0),
    cc_campaign_id   TEXT,
    cc_activity_id   TEXT,
    outcome          TEXT NOT NULL
                     CONSTRAINT email_test_sends_outcome_known
                     CHECK (outcome IN ('accepted', 'failed', 'unknown')),
    campaign_cleanup TEXT NOT NULL
                     CONSTRAINT email_test_sends_cleanup_known
                     CHECK (campaign_cleanup IN ('deleted', 'left_behind', 'none')),
    detail           TEXT
                     CONSTRAINT email_test_sends_detail_length
                     CHECK (detail IS NULL OR char_length(detail) <= 300),

    CONSTRAINT email_test_sends_accepted_has_ids
        CHECK (outcome <> 'accepted'
               OR (cc_campaign_id IS NOT NULL AND cc_activity_id IS NOT NULL)),
    CONSTRAINT email_test_sends_cleanup_matches_campaign
        CHECK ((cc_campaign_id IS NULL) = (campaign_cleanup = 'none'))
);

CREATE INDEX email_test_sends_user_time_idx ON email_test_sends (sent_by, created_at);

COMMENT ON TABLE email_test_sends IS
    'Log of template-editor test sends (item 203, piece 6).  Written once with the outcome; '
    'rows are never changed or deleted.  The hourly limit per admin counts these rows.';

CREATE FUNCTION email_test_sends_guard() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'email_test_sends is a log: row % cannot be changed or deleted', OLD.test_id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_email_test_sends_guard
    BEFORE UPDATE OR DELETE ON email_test_sends
    FOR EACH ROW
    EXECUTE FUNCTION email_test_sends_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT ON email_test_sends TO hail_app;
REVOKE UPDATE, DELETE, TRUNCATE ON email_test_sends FROM hail_app;

COMMIT;