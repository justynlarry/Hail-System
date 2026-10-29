-- sql/062:  Record of which migration files this database has had.
--
-- Migrations are applied by hand, one file at a time, to hail-dev and (from its build on)
-- the production server.  sql/011 was never applied to hail-dev, and only a full schema diff
-- found it.  scripts/migrate.sh writes one row here per file, right after the file commit,
-- with the file's sha256, so it can say which files are pending and whether a file was edited
-- after it was applied.
--
--   how   applied    run by migrate.sh
--         baseline   recorded without running:  hail-dev's 001 to 062, checked identical to
--                    a fresh build on 2026-10-29 (item 277)
--
-- Insert-only.  Only hail_admin (the loader) uses it:  no grants to hail_app or hail_ingest.
--
-- Reverse with:  DROP TABLE schema_migrations;  DROP FUNCTION schema_migrations_guard();


BEGIN;

CREATE TABLE schema_migrations (
    filename            TEXT PRIMARY KEY
                        CONSTRAINT schema_migrations_filename_shape
                        CHECK (filename ~ '^[0-9]{3}_[a-z0-9_]+\.sql$'),
    sha256              TEXT NOT NULL
                        CONSTRAINT schema_migrations_sha256_shape
                        CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    how                 TEXT NOT NULL
                        CONSTRAINT schema_migrations_how_known
                        CHECK (how IN ('applied', 'baseline')),
    applied_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by          TEXT NOT NULL DEFAULT current_user
);

COMMENT ON TABLE schema_migrations IS
    'One row per sql/ migration file this database has had, written by scripts/migrate.sh '
    'right after the file commits.  sha256 is the file as applied, so an edit made afterwards '
    'shows.  how is applied (run by migrate.sh) or baseline (recorded without running). '
    'Insert-only.';

CREATE FUNCTION schema_migrations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'schema_migrations is insert-only:  add a row, never change one'
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE TRIGGER schema_migrations_insert_only
BEFORE UPDATE OR DELETE ON schema_migrations
FOR EACH ROW EXECUTE FUNCTION schema_migrations_guard();

COMMIT;
