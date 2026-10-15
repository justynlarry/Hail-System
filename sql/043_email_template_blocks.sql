-- The editable source of a block-built email template.
--
-- subject and body stay the compiled text that is rendered and sent.  Blocks is the 
-- block list they were compiled from, so the editor can open a template again.
-- NULL for the built-in hail_alert, which still comes from its files, and for any 
-- template saved before the builder existed.
--
-- Frozen, a changed template is a new version (supersedes_id),
-- never an edit.  The database cannot check that blocks compiles to body, the 
-- templatestore always compiles from blocks when it saves one.

BEGIN;

ALTER TABLE email_templates
    ADD COLUMN blocks JSONB
    CONSTRAINT email_templates_blocks_object
    CHECK (blocks is NULL OR jsonb_typeof(blocks) = 'object');

COMMENT ON COLUMN email_templates.blocks IS
    'The block list subject and body were compiled from (hailsys/email/blocks.py). '
    'NULL when the template was not built from blocks.  Frozen with the rest of the row.';

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
    OR NEW.blocks           IS DISTINCT FROM OLD.blocks
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

    -- Retiring is one-way, a retired template is not revived, it needs to 
    -- be rewritten
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
    'Enforces append-only rule, blocks DELETE.  The only UPDATES allowed are '
    'is_active true -> false, and a change of description until the template '
    'has been used.';

COMMIT;