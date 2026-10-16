-- Drip Campaigns:  Two limits that Admin can change:
-- 
--  drip_max_followups  the most follow-ups a new campaign may have, 0 turns campaign off
--  drip_max_gap_days   the longest gap, in days, between two steps of a campaign
--
-- Values are checked when a campaign is started.  A change affects campaigns started
-- afterwards, a running campaign keeps the steps it was created with.  The bounds
-- here are fixed in code and cannot be set past by the admin page.
--
-- Reverse with:  restore log_settings_change() and the trigger from sql/042, then
--  ALTER TABLE settings_history DROP COLUMN drip_max_followups, DROP COLUMN drip_max_gap_days;
--  ALTER TABLE settings DROP COLUMN drip_max_followups, DROP COLUMN drip_max_gap_days;


BEGIN;

ALTER TABLE settings
    ADD COLUMN drip_max_followups   SMALLINT NOT NULL DEFAULT 3
        CONSTRAINT settings_drip_max_followups_check CHECK (drip_max_followups BETWEEN 0 AND 10),
    ADD COLUMN drip_max_gap_days    SMALLINT NOT NULL DEFAULT 7
        CONSTRAINT settings_drip_max_gap_days_check CHECK (drip_max_gap_days BETWEEN 1 AND 30);
    
COMMENT ON COLUMN settings.drip_max_followups IS
    'Most follow-up emails a new drip campaign may have.  0 turns new campaigns off. '
    'Does not change a campaign already started.';

COMMENT ON COLUMN settings.drip_max_gap_days IS
    'Longest gap, in days, between two steps of a new drip campaign. '
    'Does not change a campaign already in progress.';

ALTER TABLE settings_history
    ADD COLUMN drip_max_followups   SMALLINT,
    ADD COLUMN drip_max_gap_days    SMALLINT;

COMMENT ON COLUMN settings_history.drip_max_followups IS
    'NULL on rows written before 045, not backfilled.';

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
        drip_max_followups, drip_max_gap_days)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
        NEW.rentcast_billing_day, NEW.rentcast_monthly_quota, NEW.listing_freshness_days,
        NEW.match_max_age_days, NEW.email_cap_days,
        NEW.contact_email, NEW.schedule_url, NEW.from_name, NEW.from_email, NEW.reply_to,
        NEW.drip_max_followups, NEW.drip_max_gap_days);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- The OF list and the WHEN clause belong to the trigger, not the function.  Replacing
-- the function alone would leave a change to the new columns unlogged.
DROP TRIGGER trg_log_settings_change ON settings;

CREATE TRIGGER trg_log_settings_change
    AFTER UPDATE OF default_zip_radius_miles, default_match_radius_miles,
    rentcast_billing_day, rentcast_monthly_quota,
    listing_freshness_days, match_max_age_days, email_cap_days,
    contact_email, schedule_url, from_name, from_email, reply_to,
    drip_max_followups, drip_max_gap_days
    ON settings
    FOR EACH ROW
    WHEN (OLD.default_zip_radius_miles      IS DISTINCT FROM NEW.default_zip_radius_miles
        OR OLD.default_match_radius_miles       IS DISTINCT FROM NEW.default_match_radius_miles
        OR OLD.rentcast_billing_day             IS DISTINCT FROM NEW.rentcast_billing_day
        OR OLD.rentcast_monthly_quota           IS DISTINCT FROM NEW.rentcast_monthly_quota
        OR OLD.listing_freshness_days           IS DISTINCT FROM NEW.listing_freshness_days
        OR OLD.match_max_age_days               IS DISTINCT FROM NEW.match_max_age_days
        OR OLD.email_cap_days                   IS DISTINCT FROM NEW.email_cap_days
        OR OLD.contact_email                    IS DISTINCT FROM NEW.contact_email
        OR OLD.schedule_url                     IS DISTINCT FROM NEW.schedule_url
        OR OLD.from_name                        IS DISTINCT FROM NEW.from_name
        OR OLD.from_email                       IS DISTINCT FROM NEW.from_email
        OR OLD.reply_to                         IS DISTINCT FROM NEW.reply_to
        OR OLD.drip_max_followups               IS DISTINCT FROM NEW.drip_max_followups
        OR OLD.drip_max_gap_days                IS DISTINCT FROM NEW.drip_max_gap_days)
    EXECUTE FUNCTION log_settings_change();

COMMIT;