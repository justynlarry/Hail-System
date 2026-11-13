-- Jurisdiction Trial on Address Search.
-- 
--  settings.jurisdiction_near_boundary_m   how close (metres) a searched point may be to a city or
--      county line before the panel warns "near the line, check the book".  An operator's choice,
--      so its a setting, logged to settings_history.  150 m is about the Census geocoder's 
--      block-level error.
--  jurisdiction_checks     one row per check of the system's answer against the printed jurisdiction
--      book.  The system's answer is copied into the row when the check is recorded, not joined
--      from municipal_boundaries:  load_municipal.sh replaces that table wholesale, so a join
--      would silently re-answer old checks against new boundaries.  Append-only, like the other
--      logs.  One check per search;  searching the address again makes a new search to check.
--
-- Reverse with:
--   DROP TABLE jurisdiction_checks;  DROP FUNCTION jurisdiction_checks_guard();
--   restore log_settings_change() and trg_log_settings_change from sql/067;
--   ALTER TABLE settings_history DROP COLUMN jurisdiction_near_boundary_m;
--   ALTER TABLE settings DROP COLUMN jurisdiction_near_boundary_m;

BEGIN;

ALTER TABLE settings
    ADD COLUMN jurisdiction_near_boundary_m SMALLINT NOT NULL DEFAULT 150
    CONSTRAINT settings_jurisdiction_near_boundary_m_check
        CHECK (jurisdiction_near_boundary_m BETWEEN 10 AND 1000);

COMMENT ON COLUMN settings.jurisdiction_near_boundary_m IS
    'Metres.  A searched point this close to a city or county line, or inside two cities, is shown '
    'as near the boundary on Address Search.';

ALTER TABLE settings_history
    ADD COLUMN jurisdiction_near_boundary_m SMALLINT;

COMMENT ON COLUMN settings_history.jurisdiction_near_boundary_m IS
    'NULL on rows written before 069, not backfilled.';

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
        zone_observer_wording_approved, session_max_hours,
        jurisdiction_near_boundary_m)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
        NEW.rentcast_billing_day, NEW.rentcast_monthly_quota, NEW.listing_freshness_days,
        NEW.match_max_age_days, NEW.email_cap_days,
        NEW.contact_email, NEW.schedule_url, NEW.from_name, NEW.from_email, NEW.reply_to,
        NEW.drip_max_followups, NEW.drip_max_gap_days, NEW.storm_states,
        NEW.zone_observer_wording_approved, NEW.session_max_hours,
        NEW.jurisdiction_near_boundary_m);
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
    session_max_hours, jurisdiction_near_boundary_m
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
        OR OLD.session_max_hours            IS DISTINCT FROM NEW.session_max_hours
        OR OLD.jurisdiction_near_boundary_m IS DISTINCT FROM NEW.jurisdiction_near_boundary_m)
    EXECUTE FUNCTION log_settings_change();

CREATE TABLE jurisdiction_checks (
    check_id            BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    search_id           BIGINT      NOT NULL
                        CONSTRAINT fk_jurisdiction_checks_search REFERENCES address_searches (search_id)
                        CONSTRAINT jurisdiction_checks_one_per_search UNIQUE,

    -- What the system said, copied when the check was recorded.
    place_count         SMALLINT    NOT NULL
                        CONSTRAINT jurisdiction_checks_place_count_nonneg CHECK (place_count >= 0),
    place_fips          TEXT
                        CONSTRAINT jurisdiction_checks_place_fips_format
                        CHECK (place_fips IS NULL OR place_fips ~ '^[0-9]{5}$'),
    place_name          TEXT,
    county_fips         TEXT
                        CONSTRAINT jurisdiction_checks_county_fips_format
                        CHECK (county_fips IS NULL OR county_fips ~ '^[0-9]{5}$'),
    county_name         TEXT,
    nearest_line_kind   TEXT
                        CONSTRAINT jurisdiction_checks_line_kind_known
                        CHECK (nearest_line_kind IS NULL OR nearest_line_kind IN ('municipal', 'county')),
    nearest_line_name   TEXT,
    nearest_line_m      NUMERIC(7,1)
                        CONSTRAINT jurisdiction_checks_line_m_nonneg
                        CHECK (nearest_line_m IS NULL OR nearest_line_m >= 0),
    near_boundary       BOOLEAN     NOT NULL,
    near_boundary_m     SMALLINT    NOT NULL
                        CONSTRAINT jurisdiction_checks_near_m_range CHECK (near_boundary_m BETWEEN 10 AND 1000),
    boundaries_loaded_at TIMESTAMPTZ NOT NULL,

    -- What the book said.
    verdict             TEXT        NOT NULL
                        CONSTRAINT jurisdiction_checks_verdict_known CHECK (verdict IN ('agrees', 'differs')),
    book_answer         TEXT
                        CONSTRAINT jurisdiction_checks_book_answer_length
                        CHECK (book_answer IS NULL OR char_length(book_answer) BETWEEN 1 AND 200),
    cause               TEXT
                        CONSTRAINT jurisdiction_checks_cause_known
                        CHECK (cause IS NULL OR cause IN ('geocoder', 'boundary', 'book_outdated', 'unknown')),
    new_build           BOOLEAN     NOT NULL DEFAULT false,
    note                TEXT
                        CONSTRAINT jurisdiction_checks_note_length
                        CHECK (note IS NULL OR char_length(note) <= 1000),

    checked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_by          BIGINT      NOT NULL
                        CONSTRAINT fk_jurisdiction_checks_checked_by REFERENCES users (emp_id),

    -- A city, when there is one, has both its code and its name; none means unincorporated.
    CONSTRAINT jurisdiction_checks_place_matches_count
        CHECK ((place_count = 0) = (place_fips IS NULL)
               AND (place_fips IS NULL) = (place_name IS NULL)),
    CONSTRAINT jurisdiction_checks_county_pair
        CHECK ((county_fips IS NULL) = (county_name IS NULL)),
    -- NULL line:  no city or county line within the query's search distance.
    CONSTRAINT jurisdiction_checks_line_triple
        CHECK ((nearest_line_kind IS NULL) = (nearest_line_name IS NULL)
               AND (nearest_line_name IS NULL) = (nearest_line_m IS NULL)),
    -- The flag must agree with the facts it was made from.
    CONSTRAINT jurisdiction_checks_near_boundary_consistent
        CHECK (near_boundary = (place_count > 1
                                OR (nearest_line_m IS NOT NULL AND nearest_line_m <= near_boundary_m))),
    -- A CHECK passes on NULL (item 259), so each required field is named IS NOT NULL.
    CONSTRAINT jurisdiction_checks_verdict_fields
        CHECK ((verdict = 'agrees'  AND cause IS NULL)
            OR (verdict = 'differs' AND cause IS NOT NULL AND book_answer IS NOT NULL))
);

COMMENT ON TABLE jurisdiction_checks IS
    'Item 190 step 1:  the system''s jurisdiction answer for one Address Search, checked against the '
    'printed jurisdiction book.  The system''s side is a copy made at check time (municipal_boundaries '
    'is replaced on reload).  Append-only.  place_count 0 is unincorporated; above 1 means overlapping '
    'city polygons, and place_fips/place_name hold the first by name.';

COMMENT ON COLUMN jurisdiction_checks.cause IS
    'Why the book and the system differ, as the checker judged it:  geocoder = the point is in the wrong '
    'place;  boundary = the boundary data is wrong;  book_outdated = the book is wrong (an annexation, '
    'a new build), which counts as the system right;  unknown = not decided yet.';

CREATE FUNCTION jurisdiction_checks_guard() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'jurisdiction_checks is a log: row % cannot be changed or deleted', OLD.check_id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_jurisdiction_checks_guard
    BEFORE UPDATE OR DELETE ON jurisdiction_checks
    FOR EACH ROW
    EXECUTE FUNCTION jurisdiction_checks_guard();

GRANT SELECT, INSERT ON jurisdiction_checks TO hail_app;
REVOKE UPDATE, DELETE, TRUNCATE ON jurisdiction_checks FROM hail_app;

COMMIT;