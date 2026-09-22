-- Refuse to leave system with zero active admin accounts
-- DEFERRABLE INITIALLY DEFERRED checks at COMMIT, once
-- the whole transactions changes are applied.


BEGIN;

CREATE FUNCTION enforce_last_admin() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM users WHERE role = 'admin' AND is_active
    ) THEN
        RAISE EXCEPTION
            'Refusing: this would leave zero active admins.';
    END IF;
    RETURN NULL;
END;

$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION enforce_last_admin IS
    'Checked at COMMIT (deferred), not per-statement, so '
    'a same-transaction admin swap does not trip on its own '
    'transient state.';

CREATE CONSTRAINT TRIGGER trg_last_admin
    AFTER UPDATE OR DELETE ON users
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION enforce_last_admin();

COMMIT;
