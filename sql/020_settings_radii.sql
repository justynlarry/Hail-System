-- Zip/Match radius settings and change history.
-- Attribution uses a session variable the route sets
-- before UPDATE.


BEGIN;

ALTER TABLE settings
    ADD COLUMN default_zip_radius_miles NUMERIC(4,1)
        NOT NULL DEFAULT 5.0
        CHECK (default_zip_radius_miles > 0
            AND default_zip_radius_miles <= 10.0),
    ADD COLUMN default_match_radius_miles NUMERIC(4,1)
        NOT NULL DEFAULT 5.0
        CHECK (default_match_radius_miles > 0
            AND default_match_radius_miles <= 10.0),
    ADD CONSTRAINT match_within_zip_radius
        CHECK (default_match_radius_miles <= default_zip_radius_miles);

COMMENT ON COLUMN settings.default_zip_radius_miles IS
    'Must stay <= 10.0, the hail_pair_ceiling is precomputed to 10 '
    'miles.  Raising this past the ceiling needs a migration and full '
    'recompute (database-schema.md), not just a settings change.';

COMMENT ON COLUMN settings.default_match_radius_miles IS
    'What we claim in an outreach email, it must not exceed the zip '
    'radius (match_within_zip_radius) or the ceiling.';

CREATE TABLE settings_history (
    history_id                  BIGINT          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_at                  TIMESTAMPTZ     NOT NULL DEFAULT now(),
    changed_by                  BIGINT          NOT NULL REFERENCES users (emp_id),
    default_zip_radius_miles    NUMERIC(4,1)    NOT NULL,
    default_match_radius_miles  NUMERIC(4,1)    NOT NULL
);

COMMENT ON TABLE settings_history IS
    'One row per settings change, snapshotting the resulting values '
    'Populated by trg_log_settings_change, not written directly by '
    'application code.';

CREATE FUNCTION log_settings_change() RETURNS TRIGGER AS $$
DECLARE
    emp_id BIGINT;
BEGIN
    -- missing_ok = false.  A settings UPDATE with no attribution set is
    -- a bug in the calling route, not a gap to log silently.
    emp_id := current_setting('app.current_emp_id')::BIGINT;

    INSERT INTO settings_history
        (changed_by, default_zip_radius_miles, default_match_radius_miles)
    VALUES 
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles);
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_log_settings_change
    AFTER UPDATE OF default_zip_radius_miles, default_match_radius_miles 
    ON settings
    FOR EACH ROW
    WHEN (OLD.default_zip_radius_miles IS DISTINCT FROM NEW.default_zip_radius_miles
        OR OLD.default_match_radius_miles IS DISTINCT FROM NEW.default_match_radius_miles)
    EXECUTE FUNCTION log_settings_change();

GRANT SELECT, INSERT ON settings_history TO hail_app;
GRANT USAGE ON SEQUENCE settings_history_history_id_seq TO hail_app;

COMMIT;