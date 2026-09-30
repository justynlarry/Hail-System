-- email_templates is append-only.  Templates may be 
-- retired (is_active true -> false) when it is replaced.
-- Everything else is frozen to keep an inventory of what
-- was sent.

BEGIN;

CREATE FUNCTION email_templates_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'email_templates is append-only: row % cannot be deleted',
            OLD.template_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.template_id          IS DISTINCT FROM OLD.template_id
    OR NEW.template_name        IS DISTINCT FROM OLD.template_name
    OR NEW.subject              IS DISTINCT FROM OLD.subject
    OR NEW.body                 IS DISTINCT FROM OLD.body
    OR NEW.report_type          IS DISTINCT FROM OLD.report_type
    OR NEW.report_text          IS DISTINCT FROM OLD.report_text
    OR NEW.created_at           IS DISTINCT FROM OLD.created_at
    OR NEW.created_by           IS DISTINCT FROM OLD.created_by
    OR NEW.supersedes_id        IS DISTINCT FROM OLD.supersedes_id
    THEN
        RAISE EXCEPTION
            'email_templates row %: templates are superseded, never edited',
            OLD.template_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Retiring is one-way, a retired template is not revived, it needs
    -- to be rewritten
    IF OLD.is_active = FALSE AND NEW.is_active = TRUE THEN
    RAISE EXCEPTION
        'email_templates row %: a retired template cannot be reactivated',
        OLD.template_id
        USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION email_templates_guard() IS
    'Enforces append-only rule, blocks DELETE.  The only UPDATE '
    'allowed is is_active true -> false.';

CREATE TRIGGER trg_email_templates_no_delete
    BEFORE DELETE ON email_templates
    FOR EACH ROW
    EXECUTE FUNCTION email_templates_guard();

CREATE TRIGGER trg_email_templates_immutable
    BEFORE UPDATE ON email_templates
    FOR EACH ROW
    EXECUTE FUNCTION email_templates_guard();

REVOKE DELETE, TRUNCATE ON email_templates FROM hail_app;

COMMIT;
