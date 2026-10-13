-- One row per HTTP attempt to the Constant Contact API.  The daily limit is 10,000
-- calls.  One email costs about 10 plus its polling, so the engine needs the real
-- count.  Written by api.request before each attempt (retries, 429s, and polls are
-- included).  A failed insert means a call wasn't made.
--
-- endpoint is the first path segment only (contacts, emails, activities), not an id.
-- Rows aren't changed or deleted.

BEGIN;

CREATE TABLE cc_api_calls (
    call_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    called_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    method          TEXT NOT NULL
                    CONSTRAINT cc_api_calls_method_known
                    CHECK (method IN ('GET', 'POST', 'PUT', 'DELETE')),
    endpoint        TEXT NOT NULL
);

CREATE INDEX cc_api_calls_called_at_idx ON cc_api_calls (called_at);

COMMENT ON TABLE cc_api_calls is
    'Log of Constant Contact API attempts, counted against the 10,000/day '
    'limit.  Written before each attempt, not changed or deleted.';

CREATE FUNCTION cc_api_calls_guard() RETURNS TRIGGER AS $$

BEGIN
    RAISE EXCEPTION 'cc_api_calls is a log: row % cannot be changed or deleted', OLD.call_id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cc_api_calls_guard
    BEFORE UPDATE OR DELETE ON cc_api_calls
    FOR EACH ROW
    EXECUTE FUNCTION cc_api_calls_guard();

COMMIT;

BEGIN;

GRANT SELECT, INSERT ON cc_api_calls TO hail_app;
REVOKE UPDATE, DELETE, TRUNCATE ON cc_api_calls FROM hail_app;

COMMIT;
