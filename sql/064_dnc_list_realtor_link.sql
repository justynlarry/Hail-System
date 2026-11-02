-- sql/064:  dnc_list.realtor_id is kept by the database.
--
-- A suppression is linked to the realtor with the same address, whichever of the two comes first:
--      dnc_list    BEFORE INSERT: a row with no realtor_id gets the realtor whose address matches.  email
--                  norm is a generated column and is not computed yet in a BEFORE trigger, so the trigger
--                  repeats its expression, lower(trim(email_raw)), same as realtors.email_norm uses.
--      realtors    AFTER INSERT:  a new realtor links every suppression with its address.  Nothing changes
--                  agent_email after insert (upsert's conflict key is email_norm), so UPDATE is not covered.
-- Then the suppressions already unlinked are linked.
--
-- Link is a convenience: suppression is enforced on dnc_list.email_norm at send time.
--
-- Reverse with:  DROP TRIGGER dnc_list_link_realtor ON dnc_list;  DROP TRIGGER realtors_link_dnc ON realtors;
--   DROP FUNCTION dnc_list_link_realtor();  DROP FUNCTION realtors_link_dnc();
--   The links already made stay, and are correct either way.

BEGIN;

CREATE FUNCTION dnc_list_link_realtor() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.realtor_id IS NULL THEN
        NEW.realtor_id := (SELECT r.realtor_id FROM realtors r
                           WHERE r.email_norm = lower(trim(NEW.email_raw)));
    
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER dnc_list_link_realtor
BEFORE INSERT ON dnc_list
FOR EACH ROW EXECUTE FUNCTION dnc_list_link_realtor();

CREATE FUNCTION realtors_link_dnc() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.email_norm IS NOT NULL THEN
        UPDATE dnc_list SET realtor_id = NEW.realtor_id
        WHERE email_norm = NEW.email_norm AND realtor_id IS NULL;
    END IF;
    RETURN NULL;
END $$;


CREATE TRIGGER realtors_link_dnc
AFTER INSERT ON realtors
FOR EACH ROW EXECUTE FUNCTION realtors_link_dnc();

UPDATE dnc_list d SET realtor_id = r.realtor_id
FROM realtors r
WHERE r.email_norm = d.email_norm AND d.realtor_id IS NULL;

COMMIT;