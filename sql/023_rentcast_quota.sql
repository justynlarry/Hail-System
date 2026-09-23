-- Item 50: nothing sums RentCast usage against the monthly ceiling.
-- Both numbers live in settings so a plan change doesn't need a deploy.
-- Extends settings_history, to log changes to the system.

BEGIN;
    ALTER TABLE settings
    ADD COLUMN rentcast_billing_day     SMALLINT NOT NULL DEFAULT 9
        CHECK (rentcast_billing_day BETWEEN 1 AND 28),
    ADD COLUMN rentcast_monthly_quota   INTEGER NOT NULL DEFAULT 1000
        CHECK (rentcast_monthly_quota > 0);

COMMENT ON COLUMN settings.rentcast_billing_day IS
    'Day of month RentCast billing starts (currently the 9th). ';

COMMENT ON COLUMN settings.rentcast_monthly_quota IS
    'Requests included in the plan per billing period, Overage '
    'is billed, pull estimate warns and allows.';

ALTER TABLE settings_history
    ADD COLUMN rentcast_billing_day     SMALLINT,
    ADD COLUMN rentcast_monthly_quota   INTEGER;

COMMENT ON COLUMN settings_history.rentcast_monthly_quota IS
    'NULL on rows written before 023, not backfilled.';

CREATE OR REPLACE FUNCTION log_settings_change() RETURNS TRIGGER AS $$
DECLARE
    emp_id BIGINT;
BEGIN
    emp_id := current_setting('app.current_emp_id')::BIGINT;

    INSERT INTO settings_history
        (changed_by, default_zip_radius_miles, default_match_radius_miles,
         rentcast_billing_day, rentcast_monthly_quota)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
         NEW.rentcast_billing_day, NEW.rentcast_monthly_quota);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER trg_log_settings_change ON settings;

CREATE TRIGGER trg_log_settings_change
    AFTER UPDATE OF default_zip_radius_miles, default_match_radius_miles,
                    rentcast_billing_day, rentcast_monthly_quota
    ON settings
    FOR EACH ROW
    WHEN (OLD.default_zip_radius_miles      IS DISTINCT FROM NEW.default_zip_radius_miles
        OR OLD.default_match_radius_miles   IS DISTINCT FROM NEW.default_match_radius_miles
        OR OLD.rentcast_billing_day         IS DISTINCT FROM NEW.rentcast_billing_day
        OR OLD.rentcast_monthly_quota       IS DISTINCT FROM NEW.rentcast_monthly_quota)
    EXECUTE FUNCTION log_settings_change();

COMMIT;
