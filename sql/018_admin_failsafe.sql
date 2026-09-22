-- Admin fail-safe:  forced logout, per-user and system-wide
-- Corrects stale commit: admin was originally scoped to 
-- "users only," reversed 09/21/2026.  Admin is now full
-- superset of sender

BEGIN;

ALTER TABLE users
    ADD COLUMN sessions_invalidated_at TIMESTAMPTZ;

COMMENT ON COLUMN users.sessions_invalidated_at IS
    'Admin sets this to now() to force this single user to be '
    'booted on their next request.  Login still works afterward. '
    'This is not deactivation, compared against session [''issued_at''] '
    ' at login time.';

COMMENT ON COLUMN users.role IS
    'admin can do anything, superset of sender.  Enforced in '
    'application code via role_required.';

CREATE TABLE settings (
    id                              SMALLINT        PRIMARY KEY DEFAULT 1,
    global_sessions_invalidated_at  TIMESTAMPTZ,
    CONSTRAINT settings_is_singleton CHECK (id=1)
);

COMMENT ON TABLE settings IS
    'Single-row typed settings, read per request, no caching, so no '
    'invalidation to get wrong across gunicorn workers.  Minimal for now '
    'Later Phases will extend with zip/match radius and change '
    'history when admin settings UI is constructed.';

COMMENT ON COLUMN settings.global_sessions_invalidated_at IS
    'Admin sets this to now() to force every logged-in user off the '
    'system, including admin.';

INSERT INTO settings (id) VALUES (1);

GRANT SELECT, UPDATE ON settings TO hail_app;

COMMIT;
