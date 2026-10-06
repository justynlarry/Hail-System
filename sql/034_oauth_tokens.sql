-- OAuth grants for outside providers.  One row per token issue:
-- the first authorization and every refresh insert a new row, and
-- the latest row by token_id is the current grant.
-- A row may be deleted only once it is older than 30 days AND a newer row
-- exists for the same provider and account.
-- 
-- Rotating refresh tokens make every older row a dead credential, so 
-- keeping them leaks nothing live.  Tokens are encrypted in Python before
-- they reach this table, so the database never sees plaintext.  If the key
-- is lost, the grant is lost, and authorization needs to be re-run by hand.

BEGIN;

CREATE TABLE oauth_tokens (
    token_id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider                TEXT        NOT NULL
                            CONSTRAINT oauth_tokens_provider_known
                            CHECK (provider IN ('constant_contact')),
    -- The provider's own account identifier.  A row says which account
    -- the grant belongs to rather than implying.
    account_id              TEXT        NOT NULL
                            CONSTRAINT oauth_tokens_account_not_empty
                            CHECK (account_id <> ''),
    access_token_enc        BYTEA       NOT NULL,
    refresh_token_enc       BYTEA       NOT NULL,
    -- Which encryption key sealed this row, so a key can be rotated without
    -- guessing which rows each one opens.
    key_version             SMALLINT    NOT NULL DEFAULT 1
                            CONSTRAINT oauth_tokens_key_version_positive
                            CHECK (key_version > 0),
    scope                   TEXT        NOT NULL,
    -- Stored so that it can be compared rather than decoded each call.
    access_expires_at       TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);


COMMENT ON TABLE oauth_tokens is
    'Insert-Only, old rows may be purged after 30-days.  Latest row per (provider, account_id) '
     'by token_id is the current grant.  Order by token_id, not created_at, because two '
     'refreshes in one transaction share a timestamp.  Tokens encrypted before insert.';


CREATE FUNCTION oauth_tokens_guard() RETURNS TRIGGER AS $$
BEGIN
    -- No field on a token row is ever amended, a new grant is a new row.
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'oauth_tokens is insert-only: row % cannot be updated', OLD.token_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- DELETE is a retention purge, not a way to remove current grant.
    IF OLD.created_at > now() - interval '30 days' THEN
        RAISE EXCEPTION
            'oauth_tokens row %: younger than 30 days, cannot be deleted',
            OLD.token_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- A row with no newer sibling is the current grant.
    IF NOT EXISTS (
        SELECT 1 FROM oauth_tokens
        WHERE provider   = OLD.provider
          AND account_id = OLD.account_id
          AND token_id   > OLD.token_id
    ) THEN
        RAISE EXCEPTION
            'oauth_tokens row %: it is the latest grant, cannot be deleted.',
            OLD.token_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION oauth_tokens_guard() IS
    'Refuses every UPDATE, allows DELETE only of a row older than 30 days that '
    'has a newer row for the same provider and account.';

CREATE TRIGGER trg_oauth_tokens_guard
    BEFORE UPDATE OR DELETE ON oauth_tokens
    FOR EACH ROW
    EXECUTE FUNCTION oauth_tokens_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT, DELETE ON oauth_tokens TO hail_app;
REVOKE UPDATE, TRUNCATE ON oauth_tokens FROM hail_app;

COMMIT;