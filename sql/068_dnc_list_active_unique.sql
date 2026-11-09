-- One active suppression per address, not one row per address (item 311).
--
-- email_norm was UNIQUE over every row, so once a row was marked removed, nothing could suppress the
-- address again.  Every insert path is ON CONFLICT DO NOTHING, and the removed row was the conflict.
-- An upload previewed it as 'already listed', a hard bounce inserted nothing, and the send checks,
-- which read removed_at IS NULL, treated the address as sendable.
--
-- The rule now holds only over active rows.  A removed row stays as history, and a new active row can
-- sit beside it.  Every insert must name this index:  ON CONFLICT (email_norm) WHERE removed_at IS NULL.
-- The unsubscribe sync still doesn't re-suppress an address an admin removed (decision log 2026-10-07):
-- it inserts only when the address has no row at all, and reports the rest as conflicts.
--
-- Reverse with:  DROP INDEX dnc_list_email_norm_active;
--   ALTER TABLE dnc_list ADD CONSTRAINT dnc_list_email_norm_key UNIQUE (email_norm);
--   (which fails if an address by then has a removed row and an active one).

BEGIN;

ALTER TABLE dnc_list DROP CONSTRAINT dnc_list_email_norm_key;

CREATE UNIQUE INDEX dnc_list_email_norm_active
    ON dnc_list (email_norm)
    WHERE removed_at IS NULL;

COMMIT;
