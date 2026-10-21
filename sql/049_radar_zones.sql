-- Radar Layers, slice 2:  Zones built from radar_cells
-- 
--  radar_zone_builds   one row per (day, parameter set) built, with counts
--  radar_zones         one row per layer per build:  core, margin, radar_indicated
--
-- A build with no hail still gets radar_zone_builds row and no radar_zones rows, so
-- "none" is different from "not built".  Zones are derived data, a rebuild deletes the
-- zones and the build row and writes them again.  The set_id says which threshold and
-- margin made them.  Layers never overlap:  margin excludes core, radar_indicated
-- excludes both.  Warnings layer is a later migration.
--
-- Reverse with:  DROP TABLE radar_zones, radar_zone_builds;
--   ALTER TABLE radar_runs DROP CONSTRAINT radar_runs_mode_known;
--   ALTER TABLE radar_runs ADD CONSTRAINT radar_runs_mode_known
--       CHECK (run_mode IN ('nightly', 'backfill'));


BEGIN;

ALTER TABLE radar_runs DROP CONSTRAINT radar_runs_mode_known;
ALTER TABLE radar_runs ADD CONSTRAINT radar_runs_mode_known
    CHECK (run_mode IN ('nightly', 'backfill', 'manual'));

CREATE TABLE radar_zone_builds (
    day                     DATE NOT NULL
                            CONSTRAINT fk_radar_zone_builds_day REFERENCES radar_days (day),
    set_id                  BIGINT NOT NULL
                            CONSTRAINT fk_radar_zone_builds_set REFERENCES radar_parameter_sets (set_id),
    built_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    built_by                BIGINT
                            CONSTRAINT fk_radar_zone_builds_by REFERENCES users (emp_id),
    window_start            TIMESTAMPTZ NOT NULL,
    window_end              TIMESTAMPTZ NOT NULL,
    cells_over_threshold    INTEGER NOT NULL
                            CONSTRAINT radar_zone_builds_cells_nonneg CHECK (cells_over_threshold >= 0),
    patches                 INTEGER NOT NULL
                            CONSTRAINT radar_zone_builds_patches_nonneg CHECK (patches >= 0),
    patches_anchored        INTEGER NOT NULL
                            CONSTRAINT radar_zone_builds_anchored_nonneg CHECK (patches_anchored >= 0),
    anchor_reports          INTEGER NOT NULL
                            CONSTRAINT radar_zone_builds_reports_nonneg CHECK (anchor_reports >= 0),
    PRIMARY KEY (day, set_id),
    CONSTRAINT radar_zone_builds_window_ordered CHECK (window_end > window_start),
    CONSTRAINT radar_zone_builds_anchored_within_patches CHECK (patches_anchored <= patches)
);

COMMENT ON TABLE radar_zone_builds IS
    'One row per day and parameter set built.  Counts are what the build saw, cells at or '
    'above the threshold, 8-neighbour patches of them, patches within report_radius of an '
    'actionable report, and those reports.  Window is local day in UTC.';

COMMENT ON COLUMN radar_zone_builds.built_by IS
    'NULL when the nightly job or a backfill built it, the admin when a button did.';

CREATE TABLE radar_zones (
    day         DATE NOT NULL,
    set_id      BIGINT NOT NULL,
    layer       TEXT NOT NULL
                CONSTRAINT radar_zones_layer_known
                CHECK (layer IN ('core', 'margin', 'radar_indicated')),
    geom        geometry(MultiPolygon, 4326) NOT NULL
                CONSTRAINT radar_zones_geom_valid CHECK (ST_IsValid(geom)),
    area_km2    DOUBLE PRECISION NOT NULL
                CONSTRAINT radar_zones_area_positive CHECK (area_km2 > 0),
    PRIMARY KEY (day, set_id, layer),
    CONSTRAINT fk_radar_zones_build FOREIGN KEY (day, set_id)
        REFERENCES radar_zone_builds (day, set_id)
);

CREATE INDEX radar_zones_geom_idx ON radar_zones USING gist (geom);

COMMENT ON TABLE radar_zones IS
    'core = patches of hail at or above the threshold within report_radius of an actionable '
    'report.  margin = margin_km around the core, outside it.  radar_indicated = patches no '
    'report is near, outside core and margin.  Empty layer has no row.';

COMMENT ON COLUMN radar_zones.area_km2 IS
    'Spherical area from geography, not a projected one.';

GRANT SELECT ON radar_zone_builds, radar_zones TO hail_app, hail_ingest;
GRANT INSERT, DELETE ON radar_zone_builds, radar_zones TO hail_app, hail_ingest;

COMMIT;