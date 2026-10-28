-- Items 268 and 269:  a DNC upload or a typed entry records where it came from.
--
--   dnc_list.source gains 'newsletter':  unsubscribed from RBI's newsletter account, a
--       separate Constant Contact account (item 129), brought in by hand.  Not 'unsubscribe',
--       which is the hail system's own account and which the by-level report credits to
--       our emails.
--   dnc_import_batches.source   what the batch's rows become:  newsletter, manual or
--       legacy_import.  Every batch before this one was a legacy import.  No default after
--       this, so the application must say.
--   dnc_import_batches.note     an optional note typed with addresses on the admin page.
--   The 2026-10-28 upload (batch 6) and its hand correction become 'newsletter'.  On a
--   database without those rows (production) nothing is relabelled.
--
-- Reverse with:  UPDATE dnc_list SET source = 'legacy_import' WHERE source = 'newsletter';
--   ALTER TABLE dnc_import_batches DROP COLUMN note, DROP COLUMN source;
--   and restore dnc_list_source_check without 'newsletter'.

BEGIN;

ALTER TABLE dnc_list DROP CONSTRAINT dnc_list_source_check;
ALTER TABLE dnc_list ADD CONSTRAINT dnc_list_source_check
    CHECK (source IN ('reply', 'unsubscribe', 'hard_bounce', 'complaint', 'manual',
                      'legacy_import', 'newsletter'));

ALTER TABLE dnc_import_batches
    ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy_import'
        CONSTRAINT dnc_import_batches_source_known
        CHECK (source IN ('newsletter', 'manual', 'legacy_import')),
    ADD COLUMN note TEXT
        CONSTRAINT dnc_import_batches_note_length CHECK (length(note) <= 200);
ALTER TABLE dnc_import_batches ALTER COLUMN source DROP DEFAULT;

UPDATE dnc_import_batches SET source = 'newsletter'
WHERE batch_id = 6 AND filename = 'unsubs_10_28_26.csv';

DO $$
DECLARE n integer;
BEGIN
    UPDATE dnc_list SET source = 'newsletter'
    WHERE dnc_id BETWEEN 4656 AND 4664 AND source = 'legacy_import'
      AND reason LIKE 'Uploaded DNC list: unsubs!_10!_28!_26.csv%' ESCAPE '!';
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n NOT IN (0, 9) THEN
        RAISE EXCEPTION 'expected 9 rows from the 2026-10-28 upload (or none), found %', n;
    END IF;
END $$;

COMMIT;