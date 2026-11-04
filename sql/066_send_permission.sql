-- Eligibility Gate:  Who will be emailed
--
-- Constant Contact's permission policy allows only warm contacts.  The Company's service clients
-- (the in-house database) and Constant Contact subscribers.  A listing agent RentCast returns is 
-- neither.  One row per address and basis, a row is removed, not deleted.  An empty table is the 
-- result of no eligible recipients.
--
-- Rows come in through the admin upload (staged in permission_import_*, previewed, committed), like
-- the DNC import (sql/026).  The send list leaves out anyone without an active row, the trigger at the
-- bottom refuses to queue an email to such an address, so the rule holds in the database too.

BEGIN;

CREATE TABLE permission_import_batches (
    batch_id        BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token           TEXT        NOT NULL UNIQUE,
    filename        TEXT        NOT NULL,
    basis           TEXT        NOT NULL
                    CONSTRAINT permission_import_basis_known
                    CHECK (basis IN ('service_client', 'cc_subscriber')),
    as_of           DATE        NOT NULL,
    note            TEXT
                    CONSTRAINT permission_import_note_length CHECK (note IS NULL OR length(note) <= 200),
    uploaded_by     BIGINT      NOT NULL REFERENCES users (emp_id),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at    TIMESTAMPTZ,
    row_count       INTEGER     NOT NULL
);

CREATE TABLE permission_import_rows (
    batch_id        BIGINT NOT NULL REFERENCES permission_import_batches (batch_id) ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,
    email_raw       TEXT,
    name            TEXT,
    -- NULL when the row parsed cleanly, reason it was rejected otherwise.
    rejected        TEXT,
    PRIMARY KEY (batch_id, line_no)
);

CREATE TABLE send_permission (
    permission_id       BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email_raw           TEXT        NOT NULL,
    -- lower(trim()) to normalize, matching every normalized column in schema.
    email_norm          TEXT        GENERATED ALWAYS AS (lower(trim(email_raw))) STORED,
    basis               TEXT        NOT NULL
                        CONSTRAINT send_permission_basis_known
                        CHECK (basis IN ('service_client', 'cc_subscriber')),
    as_of               DATE        NOT NULL,
    batch_id            BIGINT      REFERENCES permission_import_batches (batch_id),
    detail              TEXT,
    added_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by            BIGINT      NOT NULL REFERENCES users (emp_id),
    removed_at          TIMESTAMPTZ,
    removed_by          BIGINT      REFERENCES users (emp_id),
    CONSTRAINT send_permission_removal_pair CHECK ((removed_at IS NULL) = (removed_by IS NULL))
);

-- One active row per address and basis;  an address can be both a client and a subscriber.
CREATE UNIQUE INDEX send_permission_active_basis
    ON send_permission (email_norm, basis) WHERE removed_at IS NULL;
CREATE INDEX send_permission_active_email
    ON send_permission (email_norm) WHERE removed_at IS NULL;

-- The gate in the database:  no email is queued to an address without an active permission row.
-- Fires on every insert into sent_emails (a send, a resume, a drip follow-up), not on test sends to 
-- the clicking admin (email_test_sends), which are not sends.
CREATE FUNCTION sent_emails_require_permission() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM send_permission p
                   WHERE p.email_norm = lower(trim(NEW.recipient_email))
                     AND p.removed_at IS NULL) THEN
        RAISE EXCEPTION 'no recorded permission to email % (item 189): add it under Admin, Permission list',
            NEW.recipient_email
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER sent_emails_require_permission
BEFORE INSERT ON sent_emails
FOR EACH ROW EXECUTE FUNCTION sent_emails_require_permission();

-- A drip follow-up stops a recipient whose permission has been removed (item 189):  one more reason.
ALTER TABLE drip_recipients DROP CONSTRAINT drip_recipients_reason_known;
ALTER TABLE drip_recipients ADD CONSTRAINT drip_recipients_reason_known
    CHECK (stop_reason IN ('unsubscribed', 'bounced', 'no_active_listings', 'manual', 'replied',
                           'no_permission'));

COMMIT;

BEGIN;

GRANT SELECT, INSERT, UPDATE, DELETE ON permission_import_batches TO hail_app;
GRANT SELECT, INSERT, DELETE ON permission_import_rows TO hail_app;
-- UPDATE for removal only;  nothing deletes a permission row.
GRANT SELECT, INSERT, UPDATE ON send_permission TO hail_app;

COMMIT;
