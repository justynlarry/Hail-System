-- Eligibility knobs for the send list.  Settings, not constraints.  Changes
-- don't need a deploy, and each change is logged to settings_history.
--   match_max_age_days     a storm older than this is not emailed about
--   email_cap_days         a realtor emailed within this many days is held back
--

BEGIN;

ALTER TABLE settings
    ADD COLUMN match_max_age_days       SMALLINT    NOT NULL DEFAULT 30
        CHECK (match_max_age_days BETWEEN 1 AND 90),
    ADD COLUMN email_cap_days           SMALLINT    NOT NULL DEFAULT 14
        CHECK (email_cap_days BETWEEN 1 AND 90);

COMMENT ON COLUMN settings.match_max_age_days IS    
    'A storm day older than this many days is not emailed about.';    

COMMENT ON COLUMN settings.email_cap_days IS
    'A realtor already emailed within this many days is held back from the '
    'next send unless the sender overrides it.  Their matches stay unsent for a later email.';

ALTER TABLE settings_history
    ADD COLUMN match_max_age_days       SMALLINT,
    ADD COLUMN email_cap_days           SMALLINT;

COMMENT ON COLUMN settings_history.match_max_age_days IS
    'NULL on rows written before 038.sql, not backfilled.';

CREATE OR REPLACE FUNCTION log_settings_change() RETURNS TRIGGER AS $$
DECLARE
    emp_id BIGINT;
BEGIN
    emp_id := current_setting('app.current_emp_id')::BIGINT;

    INSERT INTO settings_history
        (changed_by, default_zip_radius_miles, default_match_radius_miles,
        rentcast_billing_day, rentcast_monthly_quota, listing_freshness_days,
        match_max_age_days, email_cap_days)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
        NEW.rentcast_billing_day, NEW.rentcast_monthly_quota, NEW.listing_freshness_days,
        NEW.match_max_age_days, NEW.email_cap_days);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER trg_log_settings_change ON settings;
    CREATE TRIGGER trg_log_settings_change
    AFTER UPDATE OF default_zip_radius_miles, default_match_radius_miles,
                    rentcast_billing_day, rentcast_monthly_quota,
                    listing_freshness_days, match_max_age_days, email_cap_days
    ON settings
    FOR EACH ROW
    WHEN (OLD.default_zip_radius_miles      IS DISTINCT FROM NEW.default_zip_radius_miles
        OR OLD.default_match_radius_miles   IS DISTINCT FROM NEW.default_match_radius_miles
        OR OLD.rentcast_billing_day         IS DISTINCT FROM NEW.rentcast_billing_day
        OR OLD.rentcast_monthly_quota       IS DISTINCT FROM NEW.rentcast_monthly_quota
        OR OLD.listing_freshness_days       IS DISTINCT FROM NEW.listing_freshness_days
        OR OLD.match_max_age_days           IS DISTINCT FROM NEW.match_max_age_days
        OR OLD.email_cap_days               IS DISTINCT FROM NEW.email_cap_days)
    EXECUTE FUNCTION log_settings_change();

COMMIT;