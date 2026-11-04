-- DEV ONLY.  Never run on production:  production's permission list comes from RBI's real lists,
-- through the admin upload (parking-lot item 189, docs/deploy.md).
--
-- Gives every rehearsal realtor permission to be emailed, so rehearsals keep working with the
-- eligibility gate (sql/066) in place.  The rehearsal seeds (sql/dev_rehearsal_seed*.sql) put all
-- of them in the office 'RBI Rehearsal Office', so they are chosen by that, not by address.  Safe to
-- run again:  an address already on the list as a service client is skipped.
--
--   docker compose run --rm -T loader psql -X -v ON_ERROR_STOP=1 -f /repo/sql/dev_permission_seed.sql

BEGIN;

INSERT INTO send_permission (email_raw, basis, as_of, detail, added_by)
SELECT r.agent_email, 'service_client', current_date,
       'dev rehearsal seed (sql/dev_permission_seed.sql, item 189)', u.emp_id
FROM realtors r
CROSS JOIN (SELECT emp_id FROM users WHERE role = 'system' ORDER BY emp_id LIMIT 1) u
WHERE r.agent_office_name = 'RBI Rehearsal Office'
  AND r.agent_email IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM send_permission p
                  WHERE p.email_norm = r.email_norm AND p.basis = 'service_client'
                    AND p.removed_at IS NULL);

COMMIT;
