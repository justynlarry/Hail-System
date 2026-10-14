-- Email images (named, editable in place) and the email identity values, moved out of the 
-- environment (docker-compose.yml) into the database so an admin can change them in the UI.
-- Every change to a settings value is logged to settings_history.
--
--      email_images        logo, badge_cra, badge_bbb are required by the built-in template,
--                          so they can be edited but not retired (a CHECK).
--      settings            contact_email, schedule_url, and sender (from_name, from_email, reply_to)


BEGIN;

CREATE TABLE email_images (
    image_id        BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            TEXT        NOT NULL CHECK (name ~ '^[A-Za-z0-9_-]{1,40}$'),
    url             TEXT        NOT NULL CHECK (url ~ '^https://[^[:space:]]+$' AND length(url) <=500),
    alt_text        TEXT        NOT NULL CHECK (length(btrim(alt_text)) BETWEEN 1 AND 200),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_by      BIGINT      NOT NULL REFERENCES users(emp_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      BIGINT      NOT NULL REFERENCES users(emp_id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT email_images_required_stay_active
        CHECK (is_active OR name NOT IN ('logo', 'badge_cra', 'badge_bbb'))
);

CREATE UNIQUE INDEX email_images_name_key ON email_images (lower(name));

COMMENT ON TABLE email_images IS
    'Named images for email templates.  Edited in place (sent emails keep their own HTML). '
    'These are retired but not deleted.  Server does not fetch the URL, recipients and admin '
    'browser do.  Free-hosting domains are refused in the application (render._check_url).';

GRANT SELECT, INSERT, UPDATE ON email_images TO hail_app;

INSERT INTO email_images (name, url, alt_text, created_by, updated_by)
SELECT v.name, v.url, v.alt_text, u.emp_id, u.emp_id
FROM (VALUES
    ('logo',      'https://files.constantcontact.com/9d5b079f901/01027145-977a-464d-85af-b11a0e72653c.png', 'Roof Brokers Inc.'),
    ('badge_cra', 'https://files.constantcontact.com/9d5b079f901/e2672dcb-efb2-47f3-a776-340a4167f3f2.jpg', 'CRA Member'),
    ('badge_bbb', 'https://files.constantcontact.com/9d5b079f901/bf427d28-22b1-4b9c-bdcb-32dfb93b361f.png', 'BBB Accredited')
    ) AS v(name, url, alt_text)
CROSS JOIN (SELECT emp_id FROM users WHERE role = 'system') u;

ALTER TABLE settings
    ADD COLUMN contact_email TEXT NOT NULL DEFAULT 'justyn@roofbrokersinc-weather.com'
        CHECK (contact_email ~ '^[^[:space:]@]+@[^[:space:]@]+$'),
    ADD COLUMN schedule_url  TEXT NOT NULL DEFAULT 'https://www.roofbrokersinc.com/Orders/Create'
        CHECK (schedule_url ~ '^https://[^[:space:]]+$'),
    ADD COLUMN from_name     TEXT NOT NULL DEFAULT 'Roof Brokers, Inc.'
        CHECK (length(btrim(from_name)) BETWEEN 1 AND 100),
    ADD COLUMN from_email    TEXT NOT NULL DEFAULT 'justyn@roofbrokersinc-weather.com'
        CHECK (from_email ~ '^[^[:space:]@]+@[^[:space:]@]+$'),
    ADD COLUMN reply_to      TEXT NOT NULL DEFAULT 'justyn@roofbrokersinc-weather.com'
        CHECK (reply_to ~ '^[^[:space:]@]+@[^[:space:]@]+$');

COMMENT ON COLUMN settings.from_email IS
    'Must be a verified sender in Constant Contact, or its campaigns are refused.';

ALTER TABLE settings_history
    ADD COLUMN contact_email     TEXT,
    ADD COLUMN schedule_url      TEXT,
    ADD COLUMN from_name         TEXT,
    ADD COLUMN from_email        TEXT,
    ADD COLUMN reply_to          TEXT;

COMMENT ON COLUMN settings_history.contact_email IS
    'NULL on rows written before 042.sql, not backfilled.';

CREATE OR REPLACE FUNCTION log_settings_change() RETURNS TRIGGER AS $$
DECLARE
    emp_id BIGINT;
BEGIN
    emp_id := current_setting('app.current_emp_id')::BIGINT;

    INSERT INTO settings_history
        (changed_by, default_zip_radius_miles, default_match_radius_miles,
        rentcast_billing_day, rentcast_monthly_quota, listing_freshness_days,
        match_max_age_days, email_cap_days,
        contact_email, schedule_url, from_name, from_email, reply_to)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
        NEW.rentcast_billing_day, NEW.rentcast_monthly_quota, NEW.listing_freshness_days,
        NEW.match_max_age_days, NEW.email_cap_days,
        NEW.contact_email, NEW.schedule_url, NEW.from_name, NEW.from_email, NEW.reply_to);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER trg_log_settings_change ON settings;
CREATE TRIGGER trg_log_settings_change
    AFTER UPDATE OF default_zip_radius_miles, default_match_radius_miles,
    rentcast_billing_day, rentcast_monthly_quota,
    listing_freshness_days, match_max_age_days, email_cap_days,
    contact_email, schedule_url, from_name, from_email, reply_to

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
    OR OLD.reply_to                     IS DISTINCT FROM NEW.reply_to)
  EXECUTE FUNCTION log_settings_change();  

COMMIT;