-- Enforce immutability, hail_app holds UPDATE, which the
-- provider-status lifecycle needs.  The fix is making the
-- audit columns unwritable while leaving status writeable.
--
-- Mutable after insert: send_status, status_updated_at, provider_message_id,
-- error_detail, and sent_at (write-once, NULL -> value).
-- Frozen:  send_id, realtor_id, recipient_email, match_id, template_id,
-- queued_at, sent_by.

BEGIN;

CREATE FUNCTION send_log_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
        'send_log is append-only: row % cannot be deleted', OLD.send_id
        USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.send_id          IS DISTINCT FROM OLD.send_id
    OR NEW.realtor_id       IS DISTINCT FROM OLD.realtor_id
    OR NEW.recipient_email  IS DISTINCT FROM OLD.recipient_email
    OR NEW.match_id         IS DISTINCT FROM OLD.match_id
    OR NEW.template_id      IS DISTINCT FROM OLD.template_id
    OR NEW.queued_at        IS DISTINCT FROM OLD.queued_at
    OR NEW.sent_by          IS DISTINCT FROM OLD.sent_by
    THEN
        RAISE EXCEPTION
            'send_log row %: only provider status columns may be updated',
            OLD.send_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- sent_at records when the message left.  Setting it once is the
    -- normal lifecycle; changing afterward rewrites history.
    IF OLD.sent_at IS NOT NULL
        AND NEW.sent_at IS DISTINCT FROM OLD.sent_at
    THEN
        RAISE EXCEPTION
            'send_log row %: sent_at is write-once', OLD.send_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Status only moves forward, a same-status update is allowed so a 
    -- retired provider webhook is a harmless no-op.  
    IF NEW.send_status IS DISTINCT FROM OLD.send_status
        AND NOT (
            (OLD.send_status = 'queued'  AND NEW.send_status IN ('sent', 'failed'))
         OR (OLD.send_status = 'sent'    AND NEW.send_status IN ('bounced', 'complained'))
         OR (OLD.send_status = 'bounced' AND NEW.send_status = 'complained')
        )
    THEN
        RAISE EXCEPTION
            'send_log row %: illegal status change % -> %',
            OLD.send_id, OLD.send_status, NEW.send_status
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION send_log_guard() IS
    'Enforces append-only rule.  Blocks DELETE and rejects '
    'any UPDATE that touches a column outside the provider-status set.';

CREATE TRIGGER trg_send_log_no_delete
    BEFORE DELETE ON send_log
    FOR EACH ROW
    EXECUTE FUNCTION send_log_guard();

CREATE TRIGGER trg_send_log_immutable
    BEFORE UPDATE ON send_log
    FOR EACH ROW
    EXECUTE FUNCTION send_log_guard();

REVOKE DELETE, TRUNCATE ON send_log FROM hail_app;

COMMIT;

