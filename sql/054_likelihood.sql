-- Phase 5c: Hail-likelihood zones.
--
--  likelihood_parameter_sets   what the build uses.  The radar set, the CoCoRaHS size floor
--                              and the 3 distance edges, append-only, newest row in use
--  likelihood_level_rules      layer by distance band to level, 20 rows per parameter set
--  likelihood_builds           one row per day and set built, with the counts of what it saw
--  likelihood_zones            one row per level per build:  agree, one_source, context
--
-- A level is a ranking of where to look first, not a probability.  'none' is a level in the
-- rules (lookups must give an answer for every layer and band) but is never stored as a zone.
-- A build with no zones still has its builds row, so "none" differs from "not built".
-- Backtest builds are not stored here.  Zones are derived data:  a rebuild deletes the build
-- and its zones and writes them again.
--
-- Reverse with:  DROP TABLE likelihood_zones, likelihood_builds, likelihood_level_rules,
--   likelihood_parameter_sets;  DROP FUNCTION likelihood_append_only_guard();


BEGIN;

CREATE TABLE likelihood_parameter_sets (
    set_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            TEXT NOT NULL
                    CONSTRAINT likelihood_sets_name_length
                    CHECK (char_length(name) BETWEEN 1 AND 100),
    radar_set_id    BIGINT NOT NULL
                    CONSTRAINT fk_likelihood_sets_radar REFERENCES radar_parameter_sets (set_id),
    cocorahs_min_in NUMERIC(3,2) NOT NULL
                    CONSTRAINT likelihood_sets_cocorahs_min_range
                    CHECK (cocorahs_min_in BETWEEN 0.50 AND 4.00),
    band_1_mi       NUMERIC(4,1) NOT NULL,
    band_2_mi       NUMERIC(4,1) NOT NULL,
    band_3_mi       NUMERIC(4,1) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT
                    CONSTRAINT fk_likelihood_sets_created_by REFERENCES users (emp_id),
    CONSTRAINT likelihood_sets_bands_ordered
        CHECK (band_1_mi > 0 AND band_1_mi < band_2_mi AND band_2_mi < band_3_mi
               AND band_3_mi <= 50)
);

COMMENT ON TABLE likelihood_parameter_sets IS
    'Behind the likelihood zones.  radar_set_id gives the hail threshold, margin and report '
    'radius.  cocorahs_min_in is the smallest CoCoRaHS filing that counts as an actionable '
    'report.  band_*_mi are the distance edges (to the nearest actionable report) between the '
    'four bands le_1, 1_3, 3_5 and over_5.  Append-only, the newest row is in use.';
COMMENT ON COLUMN likelihood_parameter_sets.created_by IS
    'NULL for the first row, written by the migration.';

CREATE TABLE likelihood_level_rules (
    set_id      BIGINT NOT NULL
                CONSTRAINT fk_likelihood_rules_set REFERENCES likelihood_parameter_sets (set_id),
    layer       TEXT NOT NULL
                CONSTRAINT likelihood_rules_layer_known
                CHECK (layer IN ('core', 'margin', 'radar_indicated', 'outer', 'none')),
    band        TEXT NOT NULL
                CONSTRAINT likelihood_rules_band_known
                CHECK (band IN ('le_1', '1_3', '3_5', 'over_5')),
    level       TEXT NOT NULL
                CONSTRAINT likelihood_rules_level_known
                CHECK (level IN ('agree', 'one_source', 'context', 'none')),
    PRIMARY KEY (set_id, layer, band)
);

COMMENT ON TABLE likelihood_level_rules IS
    'The level for a radar layer at a distance band from the nearest actionable report. '
    'layer none is no radar layer.  A set has all 20 rows.  Append-only with its set.';

-- Placeholders from the 2026-10-23 design grid until the backtest and inspection data say more.
WITH s AS (
    INSERT INTO likelihood_parameter_sets
        (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi)
    SELECT 'Initial placeholders (design, 2026-10-23)', max(set_id), 1.00, 1.0, 3.0, 5.0
    FROM radar_parameter_sets
    RETURNING set_id
)
INSERT INTO likelihood_level_rules (set_id, layer, band, level)
SELECT s.set_id, v.layer, v.band, v.level
FROM s, (VALUES
    ('core',            'le_1',   'agree'),
    ('core',            '1_3',    'agree'),
    ('core',            '3_5',    'agree'),
    ('core',            'over_5', 'one_source'),
    ('margin',          'le_1',   'agree'),
    ('margin',          '1_3',    'agree'),
    ('margin',          '3_5',    'one_source'),
    ('margin',          'over_5', 'one_source'),
    ('radar_indicated', 'le_1',   'one_source'),
    ('radar_indicated', '1_3',    'one_source'),
    ('radar_indicated', '3_5',    'one_source'),
    ('radar_indicated', 'over_5', 'one_source'),
    ('outer',           'le_1',   'one_source'),
    ('outer',           '1_3',    'one_source'),
    ('outer',           '3_5',    'context'),
    ('outer',           'over_5', 'context'),
    ('none',            'le_1',   'one_source'),
    ('none',            '1_3',    'one_source'),
    ('none',            '3_5',    'one_source'),
    ('none',            'over_5', 'none')
) AS v(layer, band, level);

CREATE FUNCTION likelihood_append_only_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: add a new set instead', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE TRIGGER likelihood_parameter_sets_append_only
BEFORE UPDATE OR DELETE ON likelihood_parameter_sets
FOR EACH ROW EXECUTE FUNCTION likelihood_append_only_guard();

CREATE TRIGGER likelihood_level_rules_append_only
BEFORE UPDATE OR DELETE ON likelihood_level_rules
FOR EACH ROW EXECUTE FUNCTION likelihood_append_only_guard();

CREATE TABLE likelihood_builds (
    day             DATE NOT NULL
                    CONSTRAINT fk_likelihood_builds_day REFERENCES radar_days (day),
    set_id          BIGINT NOT NULL
                    CONSTRAINT fk_likelihood_builds_set
                    REFERENCES likelihood_parameter_sets (set_id),
    built_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    built_by        BIGINT
                    CONSTRAINT fk_likelihood_builds_by REFERENCES users(emp_id),
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    iem_reports     INTEGER NOT NULL
                    CONSTRAINT likelihood_builds_iem_nonneg CHECK (iem_reports >= 0),
    cocorahs_reports    INTEGER NOT NULL
                        CONSTRAINT likelihood_builds_cocorahs_nonneg CHECK (cocorahs_reports >= 0),
    duplicates_dropped  INTEGER NOT NULL
                        CONSTRAINT likelihood_builds_dups_nonneg CHECK (duplicates_dropped >= 0),
    patches             INTEGER NOT NULL
                        CONSTRAINT likelihood_builds_patches_nonneg CHECK (patches >= 0),
    patches_anchored    INTEGER NOT NULL
                        CONSTRAINT likelihood_builds_anchored_nonneg CHECK (patches_anchored >= 0),
    PRIMARY KEY (day, set_id),
    CONSTRAINT likelihood_builds_window_ordered CHECK (window_end > window_start),
    CONSTRAINT likelihood_builds_anchored_within CHECK (patches_anchored <= patches)
);

COMMENT ON TABLE likelihood_builds IS
    'One row per day and parameter set built.  iem_reports and cocorahs_reports are the '
    'actionable reports the build used, after duplicates_dropped IEM reports whose source '
    'says CoCoRaHS were left out.  Window is the local day in UTC.';

CREATE TABLE likelihood_zones (
    day       DATE NOT NULL,
    set_id    BIGINT NOT NULL,
    level     TEXT NOT NULL
              CONSTRAINT likelihood_zones_level_known
              CHECK (level IN ('agree', 'one_source', 'context')),
    geom      geometry(MultiPolygon, 4326) NOT NULL
              CONSTRAINT likelihood_zones_geom_valid CHECK (ST_IsValid(geom)),
    area_km2  DOUBLE PRECISION NOT NULL
              CONSTRAINT likelihood_zones_area_positive CHECK (area_km2 > 0),
    PRIMARY KEY (day, set_id, level),
    CONSTRAINT fk_likelihood_zones_build FOREIGN KEY (day, set_id)
        REFERENCES likelihood_builds (day, set_id)
);

CREATE INDEX likelihood_zones_geom_idx ON likelihood_zones USING gist (geom);

COMMENT ON TABLE likelihood_zones IS
    'The area at each level for a day.  Levels never overlap.  An empty level has no row. '
    'area_km2 is spherical, from geography.';

GRANT SELECT ON likelihood_parameter_sets, likelihood_level_rules,
    likelihood_builds, likelihood_zones TO hail_app, hail_ingest;
GRANT INSERT, DELETE ON likelihood_builds, likelihood_zones TO hail_app, hail_ingest;

COMMIT;