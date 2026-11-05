-- Sign-in Limits.
--
--  settings.session_max_hours  How long a sign-in lasts, counted from signing in.
--      checked on every request by load_current_user (hailsys/web/auth.py), so a change
--      applies to sessions already open.  An operator's choice, so it is a setting, logged
--      to settings_history.
--  login_attempts              one row per sign-in attempt.  The name typed (an account or
--      not) and the outcome, ok, failed, or locked.  The failure limit reads it (5 in 15 minutes
--      per name, since the name's last good sign-in).  Those numbers are safety limits and live in
--      auth.py.  hail_app can read and add rows, but can't change or remove them.
--
-- Reverse with:
--   DROP TABLE login_attempts;
--   restore log_settings_change() and trg_log_settings_change from sql/060;
--   ALTER TABLE settings_history DROP COLUMN session_max_hours;
--   ALTER TABLE settings DROP COLUMN session_max_hours;


BEGIN;

ALTER TABLE settings
    ADD COLUMN session_max_hours SMALLINT NOT NULL DEFAULT 12
    CONSTRAINT settings_session_max_hours_check CHECK (session_max_hours BETWEEN 1 AND 72);

COMMENT ON COLUMN settings.session_max_hours IS
    'Hours a sign-in lasts, counted from signing in.  Checked on every request, so a change '
    'applies to sessions already open.  A send already running is not stopped.';

ALTER TABLE settings_history
    ADD COLUMN session_max_hours SMALLINT;

COMMENT ON COLUMN settings_history.session_max_hours IS
    'NULL on rows written before 067, not backfilled.';

CREATE OR REPLACE FUNCTION log_settings_change() RETURNS TRIGGER AS $$
DECLARE
    emp_id BIGINT;
BEGIN
    emp_id := current_setting('app.current_emp_id')::BIGINT;

    INSERT INTO settings_history
        (changed_by, default_zip_radius_miles, default_match_radius_miles,
        rentcast_billing_day, rentcast_monthly_quota, listing_freshness_days,
        match_max_age_days, email_cap_days,
        contact_email, schedule_url, from_name, from_email, reply_to,
        drip_max_followups, drip_max_gap_days, storm_states,
        zone_observer_wording_approved, session_max_hours)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
        NEW.rentcast_billing_day, NEW.rentcast_monthly_quota, NEW.listing_freshness_days,
        NEW.match_max_age_days, NEW.email_cap_days,
        NEW.contact_email, NEW.schedule_url, NEW.from_name, NEW.from_email, NEW.reply_to,
        NEW.drip_max_followups, NEW.drip_max_gap_days, NEW.storm_states,
        NEW.zone_observer_wording_approved, NEW.session_max_hours);
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
    drip_max_followups, drip_max_gap_days, storm_states, zone_observer_wording_approved,
    session_max_hours
    ON settings
    FOR EACH ROW
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
                                            IS DISTINCT FROM NEW.zone_observer_wording_approved
        OR OLD.session_max_hours            IS DISTINCT FROM NEW.session_max_hours)
    EXECUTE FUNCTION log_settings_change();

CREATE TABLE login_attempts (
    attempt_id      BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_name       TEXT        NOT NULL
                    CONSTRAINT login_attempts_user_name_length CHECK (length(user_name) <= 200),
    outcome         TEXT        NOT NULL
                    CONSTRAINT login_attempts_outcome_known CHECK (outcome IN ('ok', 'failed', 'locked')),
    attempted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The failure count reads one name's rows, newest by attempt_id.
CREATE INDEX login_attempts_by_name ON login_attempts (user_name, attempt_id);

COMMENT ON TABLE login_attempts IS
    'One row per sign-in attempt.  user_name is as typed, an account or not.  outcome: ok, '
    'failed (password checked and wrong, or no such active account), locked (refused by the failure '
    'limit without checking the password, not counted toward it).';

GRANT SELECT, INSERT ON login_attempts to hail_app;

COMMIT;
