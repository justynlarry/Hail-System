-- Phase 5c, Slice 7:  Admin Page can add hail-zone parameter sets.
--
-- Until now only the migration and the tests wrote the three set tables.  The admin page
-- (hail_app) now needs to add a radar set, a likelihood set and its 20 rules.  Two guards
-- keep that safe, in the database and not in the page:
--  a likelihood set cannot be committed unless it has all 20 rules (checked at commit)
--  a rule can only be added in the same transaction that added its set
-- so a set is never half there and an old set never gains or loses a rule.
--
-- Reverse with:  DROP TRIGGER likelihood_sets_complete ON likelihood_parameter_sets;
--   DROP TRIGGER likelihood_rules_with_set ON likelihood_level_rules;
--   DROP FUNCTION likelihood_set_complete_check(); DROP FUNCTION likelihood_rule_with_set_guard();
--   REVOKE INSERT ON radar_parameter_sets, likelihood_parameter_sets,
--       likelihood_level_rules FROM hail_app;

BEGIN;

GRANT INSERT ON radar_parameter_sets, likelihood_parameter_sets,
    likelihood_level_rules TO hail_app;

CREATE FUNCTION likelihood_set_complete_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    have INTEGER;
BEGIN
    SELECT count(*) INTO have FROM likelihood_level_rules WHERE set_id = NEW.set_id;
    IF have <> 20 THEN
        RAISE EXCEPTION 'likelihood set % has % rules, it needs 20', NEW.set_id, have
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;

-- Deferred, so the set and its rules can go in one transaction in either order.
CREATE CONSTRAINT TRIGGER likelihood_sets_complete
AFTER INSERT ON likelihood_parameter_sets
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION likelihood_set_complete_check();

CREATE FUNCTION likelihood_rule_with_set_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- created_at defaults to now(), which is the transaction's start, so a set made
    -- in this transaction matches and an older set does not.
    IF NOT EXISTS (SELECT 1 FROM likelihood_parameter_sets s
                   WHERE s.set_id = NEW.set_id AND s.created_at = now()) THEN
        RAISE EXCEPTION 'rules can only be added in the transaction that adds set %',
            NEW.set_id USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER likelihood_rules_with_set
BEFORE INSERT ON likelihood_level_rules
FOR EACH ROW EXECUTE FUNCTION likelihood_rule_with_set_guard();

COMMIT;