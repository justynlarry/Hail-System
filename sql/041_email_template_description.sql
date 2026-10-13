-- A short description of each email template, shown in the template list
-- on the send page.  A note about the template, not part of what is sent,
-- so it can be edited, but only until the template has been used in an email.
-- After that the template and row are frozen, changes require creating
-- a new version.
-- 
-- sent_emails is the record of use.  queue_batch writes it before anything is
-- sent.  A queued, sent, or discarded template counts as used.

BEGIN;


ALTER TABLE email_templates
    ADD COLUMN description TEXT
    CONSTRAINT email_templates_description_length
    CHECK (description IS NULL OR char_length(description) <=300);


-- The built-in template was used before this column existed, so it cannot be
-- edited afterwards.  

UPDATE email_templates
SET description = 'Hail storm alert for listing agents: each listing hit, with hail size and distance, and a link to schedule an inspection. Built-in/First Draft Template.'
WHERE template_name = 'hail_alert';

CREATE OR REPLACE FUNCTION email_templates_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'email_templates is append-only: row % cannot be deleted',
            OLD.template_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.template_id      IS DISTINCT FROM OLD.template_id
    OR NEW.template_name    IS DISTINCT FROM OLD.template_name
    OR NEW.subject          IS DISTINCT FROM OLD.subject
    OR NEW.body             IS DISTINCT FROM OLD.body
    OR NEW.report_type      IS DISTINCT FROM OLD.report_type
    OR NEW.report_text      IS DISTINCT FROM OLD.report_text
    OR NEW.created_at       IS DISTINCT FROM OLD.created_at
    OR NEW.created_by       IS DISTINCT FROM OLD.created_by
    OR NEW.supersedes_id    IS DISTINCT FROM OLD.supersedes_id
    THEN
        RAISE EXCEPTION
            'email_templates row %: templates are superseded, never edited',
            OLD.template_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- The description may change until the template has been used in an email.
    IF NEW.description IS DISTINCT FROM OLD.description
        AND EXISTS (SELECT 1 FROM sent_emails e WHERE e.template_id = OLD.template_id)
    THEN
        RAISE EXCEPTION
            'email_templates row %: the description is frozen once the template has been used',
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
    'Enforces append-only rule, blocks DELETE.  The only UPDATEs allowed are '
    'is_active true -> false, and a change of description until the template '
    'has been used.';

COMMIT;