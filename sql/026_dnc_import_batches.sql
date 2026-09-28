-- Staging for DNC uploads.  Admin UI previews a file, then commits it
-- to the database.  The parsed rows live here rather than in the session
-- or in a re-upload.  

BEGIN;

CREATE TABLE dnc_import_batches (
    batch_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token           text        NOT NULL UNIQUE,
    filename        text        NOT NULL,
    uploaded_by     bigint      NOT NULL REFERENCES users(emp_id),
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    committed_at     timestamptz,
    row_count       integer     NOT NULL
);

CREATE TABLE dnc_import_rows (
    batch_id    bigint  NOT NULL REFERENCES dnc_import_batches(batch_id)
                    ON DELETE CASCADE,
    line_no     integer NOT NULL,
    email_raw   text,
    name_at_add text,
    added_at    timestamptz,
    -- NULL when row parsed cleanly, the reason it was rejected
    -- otherwise.
    rejected    text,
    PRIMARY KEY (batch_id, line_no)     
);

COMMIT;

GRANT SELECT, INSERT, DELETE ON dnc_import_batches TO hail_app;
GRANT SELECT, INSERT, DELETE ON dnc_import_rows TO hail_app;
