-- Phase 5c step 4, piece 2:  What a zone send requires from the database.
--
--   email_templates.match_kind   report (every template so far) or zone.  Frozen with the rest.
--   send_log.basis, level, zone_set_id   what the send rested on, as sent.  basis defaults to
--       'report', so the existing rows and the existing send code stay truthful unchanged.
--   trg_send_log_kind_pairing    replaces trg_send_log_report_match_only (sql/057):  a zone
--       match is sent only with a zone-kind template, a report match only with a report-kind
--       one, the record says what the match says, and observer wording needs the setting.
--   settings.zone_observer_wording_approved   false until the developer has read the CoCoRaHS
--       usage policy in full and recorded it in the decision log.  Logged to settings_history.
--
-- Nothing here sends anything.  Reverse with:  restore the guards from sql/030 to 044, then
--   DROP TRIGGER trg_send_log_kind_pairing ON send_log; DROP FUNCTION send_log_kind_guard();
--   ALTER TABLE send_log DROP COLUMN zone_set_id, DROP COLUMN level, DROP COLUMN basis;
--   ALTER TABLE email_templates DROP COLUMN match_kind;
--   ALTER TABLE settings_history DROP COLUMN zone_observer_wording_approved;
--   ALTER TABLE settings DROP COLUMN zone_observer_wording_approved;
--   and recreate sql/057's send_log_report_match_guard and trigger.

BEGIN;

-- 1. The kind of a template, frozen like its text.
ALTER TABLE email_templates
    ADD COLUMN match_kind TEXT NOT NULL DEFAULT 'report'
        CONSTRAINT email_templates_match_kind_known CHECK (match_kind IN ('report', 'zone'));

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
    OR NEW.match_kind       IS DISTINCT FROM OLD.match_kind
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

-- 2. What each send rested on, as sent.
ALTER TABLE send_log
    ADD COLUMN basis TEXT NOT NULL DEFAULT 'report',
    ADD COLUMN level TEXT,
    ADD COLUMN zone_set_id BIGINT
        CONSTRAINT fk_send_log_zone_set REFERENCES likelihood_parameter_sets (set_id);

-- A zone send must name its level and set.  IS NOT NULL is said out loud:  level IN (...)
-- alone is NULL for a NULL level, and a CHECK passes on NULL (parking-lot item 259).
ALTER TABLE send_log
    ADD CONSTRAINT send_log_basis_known
        CHECK (basis IN ('report', 'radar', 'observer', 'both')),
    ADD CONSTRAINT send_log_snapshot_shape
        CHECK ((basis = 'report'
                AND (level IS NULL OR level IN ('agree', 'one_source', 'context', 'none')))
            OR (basis <> 'report' AND level IS NOT NULL
                AND level IN ('agree', 'one_source') AND zone_set_id IS NOT NULL));

CREATE OR REPLACE FUNCTION send_log_guard() RETURNS TRIGGER AS $$
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
    OR NEW.basis            IS DISTINCT FROM OLD.basis
    OR NEW.level            IS DISTINCT FROM OLD.level
    OR NEW.zone_set_id      IS DISTINCT FROM OLD.zone_set_id
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

-- 3. The observer-wording gate, a setting so the rule lives in the database and the change
-- is logged like every other setting.
ALTER TABLE settings
    ADD COLUMN zone_observer_wording_approved BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN settings.zone_observer_wording_approved IS
    'False until the developer has read the CoCoRaHS usage policy in full and recorded it in '
    'the decision log.  While false, send_log refuses a send whose match rests on a '
    'volunteer observer report (basis observer or both).';

ALTER TABLE settings_history
    ADD COLUMN zone_observer_wording_approved BOOLEAN;

COMMENT ON COLUMN settings_history.zone_observer_wording_approved IS
    'NULL on rows written before 060, not backfilled.';

CREATE OR REPLACE FUNCTION log_settings_change() RETURNS TRIGGER AS $$
DECLARE
    emp_id  BIGINT;
BEGIN
    emp_id := current_setting('app.current_emp_id')::BIGINT;

    INSERT INTO settings_history
        (changed_by, default_zip_radius_miles, default_match_radius_miles,
        rentcast_billing_day, rentcast_monthly_quota, listing_freshness_days,
        match_max_age_days, email_cap_days,
        contact_email, schedule_url, from_name, from_email, reply_to,
        drip_max_followups, drip_max_gap_days, storm_states,
        zone_observer_wording_approved)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
        NEW.rentcast_billing_day, NEW.rentcast_monthly_quota, NEW.listing_freshness_days,
        NEW.match_max_age_days, NEW.email_cap_days,
        NEW.contact_email, NEW.schedule_url, NEW.from_name, NEW.from_email, NEW.reply_to,
        NEW.drip_max_followups, NEW.drip_max_gap_days, NEW.storm_states,
        NEW.zone_observer_wording_approved);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- The OF list and the WHEN clause belong to the trigger, not the function.
DROP TRIGGER trg_log_settings_change ON settings;

CREATE TRIGGER trg_log_settings_change
    AFTER UPDATE OF default_zip_radius_miles, default_match_radius_miles,
    rentcast_billing_day, rentcast_monthly_quota,
    listing_freshness_days, match_max_age_days, email_cap_days,
    contact_email, schedule_url, from_name, from_email, reply_to,
    drip_max_followups, drip_max_gap_days, storm_states, zone_observer_wording_approved
    ON settings
    FOR EACH row
    WHEN (OLD.default_zip_radius_miles      IS DISTINCT FROM NEW.default_zip_radius_miles
        OR OLD.default_match_radius_miles   IS DISTINCT FROM NEW.default_match_radius_miles
        OR OLD.rentcast_billing_day         IS DISTINCT FROM NEW.rentcast_billing_day
        OR OLD.rentcast_monthly_quota       IS DISTINCT FROM NEW.rentcast_monthly_quota
        OR OLD.listing_freshness_days       IS DISTINCT FROM NEW.listing_freshness_days
        OR OLD.match_max_age_days           IS DISTINCT FROM NEW.match_max_age_days
        OR OLD.email_cap_days               IS DISTINCT FROM NEW.email_cap_days
        OR OLD.contact_email                IS DISTINCT FROM NEW.contact_email
        OR OLD.schedule_url                 IS DISTINCT FROM NEW.schedule_url
        OR OLD.from_name                    IS DISTINCT FROM NEW.from_name
        OR OLD.from_email                   IS DISTINCT FROM NEW.from_email
        OR OLD.reply_to                     IS DISTINCT FROM NEW.reply_to
        OR OLD.drip_max_followups           IS DISTINCT FROM NEW.drip_max_followups
        OR OLD.drip_max_gap_days            IS DISTINCT FROM NEW.drip_max_gap_days
        OR OLD.storm_states                 IS DISTINCT FROM NEW.storm_states
        OR OLD.zone_observer_wording_approved
                                            IS DISTINCT FROM NEW.zone_observer_wording_approved)
    EXECUTE FUNCTION log_settings_change();

-- 4. The pairing trigger replaces the blanket guard of sql/057.
DROP TRIGGER trg_send_log_report_match_only ON send_log;
DROP FUNCTION send_log_report_match_guard();

CREATE FUNCTION send_log_kind_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    m    RECORD;
    kind TEXT;
BEGIN
    SELECT iem_id, basis, level, set_id INTO m
    FROM storm_listing_matches WHERE match_id = NEW.match_id;
    IF NOT FOUND THEN
        RETURN NEW;                 -- the foreign key reports a match that does not exist
    END IF;
    SELECT match_kind INTO kind FROM email_templates WHERE template_id = NEW.template_id;

    IF m.iem_id IS NULL THEN        -- a match that rests on the hail zones
        IF kind IS DISTINCT FROM 'zone' THEN
            RAISE EXCEPTION 'match % rests on the hail zones: it can only be sent with a '
                'zone-kind template', NEW.match_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.basis IS DISTINCT FROM m.basis OR NEW.level IS DISTINCT FROM m.level
           OR NEW.zone_set_id IS DISTINCT FROM m.set_id THEN
            RAISE EXCEPTION 'send_log for match %: its basis, level and set must be the '
                'match''s own', NEW.match_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF m.basis IN ('observer', 'both')
           AND NOT COALESCE((SELECT zone_observer_wording_approved
                             FROM settings WHERE id = 1), false) THEN
            RAISE EXCEPTION 'match % rests on a volunteer observer report: that wording is '
                'not approved in settings', NEW.match_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSE                            -- a match that rests on a National Weather Service report
        IF kind IS DISTINCT FROM 'report' THEN
            RAISE EXCEPTION 'match % rests on a National Weather Service report: it can only '
                'be sent with a report-kind template', NEW.match_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.basis IS DISTINCT FROM 'report' THEN
            RAISE EXCEPTION 'send_log for match %: a report match is recorded with basis '
                'report', NEW.match_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_send_log_kind_pairing
BEFORE INSERT ON send_log
FOR EACH ROW EXECUTE FUNCTION send_log_kind_guard();

COMMIT;
