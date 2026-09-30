-- Send-time freshness threshold for listings.
--
-- RentCast is queried with status=Active, so a listing that sells simply
-- stops appearing in the active response, so list_status stays 'Active'
-- indefinitely.  We also get lastSeenDate, stored as listings.list_last_seen
-- so 'still on the market' is how recently the system saw it, not an 
-- actual status field.
--
-- Setting not a constant, the correct number depends on how long
-- the system users leave between pulling a storm and sending it.

BEGIN;

ALTER TABLE settings
    ADD COLUMN listing_freshness_days   SMALLINT NOT NULL DEFAULT 7
        CHECK (listing_freshness_days BETWEEN 1 AND 90);

COMMENT ON COLUMN settings.listing_freshness_days IS
    'A listing RentCast has not seen within this many days is not '
    'emailed about, it may have been sold or withdrawn.  7 assumes '
    'a storm is pulled and sent the same or next day, with a week '
    'of slack';

ALTER TABLE settings_history
    ADD COLUMN listing_freshness_days SMALLINT;

COMMENT ON COLUMN settings_history.listing_freshness_days IS
    'NULL on rows written before 028, not backfilled.';


CREATE OR REPLACE FUNCTION log_settings_change() RETURNS TRIGGER AS $$
DECLARE
    emp_id BIGINT;

BEGIN
    emp_id := current_setting('app.current_emp_id')::BIGINT;

    INSERT INTO settings_history
        (changed_by, default_zip_radius_miles, default_match_radius_miles,
         rentcast_billing_day, rentcast_monthly_quota,
         listing_freshness_days)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
         NEW.rentcast_billing_day, NEW.rentcast_monthly_quota,
         NEW.listing_freshness_days);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger's OF list and WHEN clause are part of the TRIGGER, not the function
-- CREATE or REPLACE on the function alone would leave a change to the new 
-- column unlogged.
DROP TRIGGER trg_log_settings_change ON settings;

CREATE TRIGGER trg_log_settings_change
    AFTER UPDATE OF default_zip_radius_miles, default_match_radius_miles,
                    rentcast_billing_day, rentcast_monthly_quota,
                    listing_freshness_days
    ON settings
    FOR EACH ROW
    WHEN (OLD.default_zip_radius_miles      IS DISTINCT FROM NEW.default_zip_radius_miles
        OR OLD.default_match_radius_miles   IS DISTINCT FROM NEW.default_match_radius_miles
        OR OLD.rentcast_billing_day         IS DISTINCT FROM NEW.rentcast_billing_day
        OR OLD.rentcast_monthly_quota       IS DISTINCT FROM NEW.rentcast_monthly_quota
        OR OLD.listing_freshness_days       IS DISTINCT FROM NEW.listing_freshness_days)
    EXECUTE FUNCTION log_settings_change();

COMMIT;