-- Radar Layers, slice 3: NWS warning polygons, the outer layer.
-- 
--  settings.storm_states      states whose warnings are fetched, comma separated default CO
--  warning_loads              one row per fetch window, so "no warnings" differs from "not fetched"
--  warning_polygons           one row per severe thunderstorm or tornado warning polygon version
--
-- A local day is covered when a warning_loads row for the current storm_states spans the whole
-- day window.  Only then does a zone build add the 'outer' layer:  the day's warning polygons,
-- clipped to the crop box, outside the other three layers.  radar_zone_builds.warnings_used
-- is NULL when the day was not covered.  The admin page does not edit storm_states yet, an
-- admin UPDATE on settings does it.
--
-- Reverse with:  DROP TABLE warning_polygons, warning_loads;
--   ALTER TABLE radar_zone_builds DROP COLUMN warnings_used;
--   DELETE FROM radar_zones WHERE layer = 'outer';
--   ALTER TABLE radar_zones DROP CONSTRAINT radar_zones_layer_known;
--   ALTER TABLE radar_zones ADD CONSTRAINT radar_zones_layer_known
--       CHECK (layer IN ('core', 'margin', 'radar_indicated'));
--   then restore log_settings_change() and the trigger from sql/045, and
--   ALTER TABLE settings_history DROP COLUMN storm_states;
--   ALTER TABLE settings DROP COLUMN storm_states;


BEGIN;

ALTER TABLE settings
    ADD COLUMN storm_states TEXT NOT NULL DEFAULT 'CO'
    CONSTRAINT settings_storm_states_check
    CHECK (storm_states ~ '^[A-Z]{2}(,[A-Z]{2})*$');

COMMENT ON COLUMN settings.storm_states IS
    'States whose NWS warnings are fetched for the radar layers, two letter codes separated by '
    'commas with no spaces, e.g. CO or CO,WY.  Not yet used by the IEM report ingest.';

ALTER TABLE settings_history
    ADD COLUMN storm_states TEXT;

COMMENT ON COLUMN settings_history.storm_states IS
    'NULL on rows written before 050, not backfilled.';

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
        drip_max_followups, drip_max_gap_days, storm_states)
    VALUES
        (emp_id, NEW.default_zip_radius_miles, NEW.default_match_radius_miles,
        NEW.rentcast_billing_day, NEW.rentcast_monthly_quota, NEW.listing_freshness_days,
        NEW.match_max_age_days, NEW.email_cap_days,
        NEW.contact_email, NEW.schedule_url, NEW.from_name, NEW.from_email, NEW.reply_to,
        NEW.drip_max_followups, NEW.drip_max_gap_days, NEW.storm_states);
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
    drip_max_followups, drip_max_gap_days, storm_states
    ON settings
    FOR EACH row
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
        OR OLD.drip_max_gap_days                IS DISTINCT FROM NEW.drip_max_gap_days
        OR OLD.storm_states                     IS DISTINCT FROM NEW.storm_states)
    EXECUTE FUNCTION log_settings_change();

CREATE TABLE warning_loads (
    load_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    states          TEXT NOT NULL
                    CONSTRAINT warning_loads_states_check CHECK (states ~ '^[A-Z]{2}(,[A-Z]{2})*$'),
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id          BIGINT
                    CONSTRAINT fk_warning_loads_run REFERENCES radar_runs (run_id),
    polygons_seen   INTEGER NOT NULL
                    CONSTRAINT warning_loads_seen_nonneg CHECK (polygons_seen >= 0),
    polygons_new    INTEGER NOT NULL
                    CONSTRAINT warning_loads_new_nonneg CHECK (polygons_new >= 0),
    CONSTRAINT warning_loads_window_ordered CHECK (window_end > window_start),
    CONSTRAINT warning_loads_new_within_seen CHECK (polygons_new <= polygons_seen)
);


COMMENT ON TABLE warning_loads IS
    'One row per fetch of IEM warnings for a time window and set of states.  A day is covered '
    'when a row for the current storm_states spans its whole window.  states is sorted, upper case, '
    'comma separated, as stored in settings.';

CREATE TABLE warning_polygons (
    wfo             TEXT NOT NULL,
    phenomena       TEXT NOT NULL
                    CONSTRAINT warning_polygons_phenomena_known CHECK (phenomena IN ('SV', 'TO')),
    significance    TEXT NOT NULL
                    CONSTRAINT warning_polygons_significance_known CHECK (significance = 'W'),
    eventid         INTEGER NOT NULL,
    vtec_year       SMALLINT NOT NULL,
    poly_begin      TIMESTAMPTZ NOT NULL,
    poly_end        TIMESTAMPTZ NOT NULL,
    issued          TIMESTAMPTZ,
    expires         TIMESTAMPTZ,
    product_id      TEXT,
    load_id         BIGINT NOT NULL
                    CONSTRAINT fk_warning_polygons_load REFERENCES warning_loads (load_id),
    geom            geometry(MultiPolygon, 4326) NOT NULL
                    CONSTRAINT warning_polygons_geom_valid CHECK (ST_IsValid(geom)),
    PRIMARY KEY (wfo, phenomena, significance, eventid, vtec_year, poly_begin),
    CONSTRAINT warning_polygons_poly_ordered CHECK (poly_end > poly_begin)
);

CREATE INDEX warning_polygons_geom_idx ON warning_polygons USING gist (geom);
CREATE INDEX warning_polygons_time_idx ON warning_polygons (poly_begin, poly_end);

COMMENT ON TABLE warning_polygons IS
    'NWS severe thunderstorm and tornado warning polygons from IEM, one row per polygon version '
    '(a warning''s shape can change while it is active).  Times are UTC.  A warning belongs to a '
    'local day if its polygon was valid at any time in the day.  Storm-based warnings only.';

ALTER TABLE radar_zones DROP CONSTRAINT radar_zones_layer_known;
ALTER TABLE radar_zones ADD CONSTRAINT radar_zones_layer_known
    CHECK (layer IN ('core', 'margin', 'radar_indicated', 'outer'));

COMMENT ON TABLE radar_zones IS
    'core = patches of hail at or above the threshold within report_radius of an actionable '
    'report;  margin = margin_km around the core, outside it;  radar_indicated = patches no '
    'report is near, outside core and margin;  outer = the day''s NWS warning polygons, clipped '
    'to the crop box, outside the other three.  An empty layer has no row.';

ALTER TABLE radar_zone_builds
    ADD COLUMN warnings_used INTEGER
        CONSTRAINT radar_zone_builds_warnings_nonneg CHECK (warnings_used >= 0);

COMMENT ON COLUMN radar_zone_builds.warnings_used IS
    'Warning polygons that overlapped the day when the outer layer was built.  NULL when the '
    'day was not covered by a warning_loads row, so there is no outer layer and that is not '
    '"no warnings".';

GRANT SELECT ON warning_loads, warning_polygons TO hail_app;
GRANT SELECT, INSERT ON warning_loads, warning_polygons TO hail_ingest;
GRANT SELECT (storm_states) ON settings TO hail_ingest;

COMMIT;