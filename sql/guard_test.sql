BEGIN;

-- Helper: run a statement and compare the outcome to what we expect.
-- want = 'ok' (must succeed) or a SQLSTATE: 23001 = restrict_violation
-- (our trigger), 23514 = check_violation (sent_has_timestamp).
-- A failed statement rolls back only itself; a successful one stays,
-- so the row's status moves forward across the tests below.
CREATE FUNCTION pg_temp.expect(label text, stmt text, want text)
RETURNS void AS $f$
BEGIN
    EXECUTE stmt;
    IF want = 'ok' THEN
        RAISE NOTICE 'PASS  %', label;
    ELSE
        RAISE NOTICE 'FAIL  % -- expected %, but it succeeded', label, want;
    END IF;
EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = want THEN
        RAISE NOTICE 'PASS  %', label;
    ELSE
        RAISE NOTICE 'FAIL  % -- expected %, got % (%)',
            label, want, SQLSTATE, SQLERRM;
    END IF;
END;
$f$ LANGUAGE plpgsql;

-- Setup. If a subselect finds no row, the NOT NULL fails loudly.
INSERT INTO email_templates (template_name, subject, body, created_by)
VALUES ('guard-test', 's', 'b',
        (SELECT emp_id FROM users ORDER BY emp_id LIMIT 1));

INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by)
SELECT (SELECT realtor_id FROM realtors ORDER BY realtor_id LIMIT 1),
       e,
       (SELECT match_id FROM storm_listing_matches ORDER BY match_id LIMIT 1),
       (SELECT template_id FROM email_templates WHERE template_name = 'guard-test'),
       (SELECT emp_id FROM users ORDER BY emp_id LIMIT 1)
FROM (VALUES ('guard-test-1@example.invalid'),
             ('guard-test-2@example.invalid')) AS t(e);

-- send_log, row 1: frozen columns
SELECT pg_temp.expect('freeze recipient_email',
  $q$UPDATE send_log SET recipient_email = 'x@example.invalid'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('freeze queued_at',
  $q$UPDATE send_log SET queued_at = now() - interval '1 day'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');

-- send_log, row 1: status lifecycle
SELECT pg_temp.expect('queued -> sent without sent_at (CHECK)',
  $q$UPDATE send_log SET send_status = 'sent'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23514');
SELECT pg_temp.expect('queued -> sent with sent_at',
  $q$UPDATE send_log SET send_status = 'sent', sent_at = now(),
            provider_message_id = 'abc', status_updated_at = now()
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, 'ok');
SELECT pg_temp.expect('sent_at is write-once',
  $q$UPDATE send_log SET sent_at = now() + interval '1 hour'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('sent -> queued blocked',
  $q$UPDATE send_log SET send_status = 'queued'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('sent -> bounced',
  $q$UPDATE send_log SET send_status = 'bounced', status_updated_at = now()
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, 'ok');
SELECT pg_temp.expect('bounced -> bounced (retried webhook no-op)',
  $q$UPDATE send_log SET status_updated_at = now()
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, 'ok');
SELECT pg_temp.expect('bounced -> sent blocked',
  $q$UPDATE send_log SET send_status = 'sent'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('bounced -> complained',
  $q$UPDATE send_log SET send_status = 'complained'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, 'ok');

-- send_log, row 2: failed is terminal
SELECT pg_temp.expect('queued -> failed',
  $q$UPDATE send_log SET send_status = 'failed', error_detail = 'boom'
     WHERE recipient_email = 'guard-test-2@example.invalid'$q$, 'ok');
SELECT pg_temp.expect('failed -> sent blocked',
  $q$UPDATE send_log SET send_status = 'sent', sent_at = now()
     WHERE recipient_email = 'guard-test-2@example.invalid'$q$, '23001');

SELECT pg_temp.expect('send_log DELETE blocked',
  $q$DELETE FROM send_log
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');

-- email_templates
SELECT pg_temp.expect('template body frozen',
  $q$UPDATE email_templates SET body = 'changed'
     WHERE template_name = 'guard-test'$q$, '23001');
SELECT pg_temp.expect('template retire (is_active true -> false)',
  $q$UPDATE email_templates SET is_active = FALSE
     WHERE template_name = 'guard-test'$q$, 'ok');
SELECT pg_temp.expect('template reactivate blocked',
  $q$UPDATE email_templates SET is_active = TRUE
     WHERE template_name = 'guard-test'$q$, '23001');
SELECT pg_temp.expect('template DELETE blocked',
  $q$DELETE FROM email_templates
     WHERE template_name = 'guard-test'$q$, '23001');

-- Known gap: the owner can still TRUNCATE. Expected to SUCCEED (still
-- rolled back below). If you add the no_truncate trigger, cha
SELECT pg_temp.expect('KNOWN GAP: owner TRUNCATE send_log',
  $q$TRUNCATE send_log$q$, 'ok');

-- api_pulls: one running pull per storm (sql/033).  23505 = unique_violation.
-- 1999-01-01 cannot collide with a real storm; the whole file rolls back.
SELECT pg_temp.expect('first running pull for a storm',
  $q$INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count,
                            estimated_api_calls, api_status)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1),
             '1999-01-01', 'GUARD-TEST', 1, 1, 'running')$q$, 'ok');
SELECT pg_temp.expect('second running pull, same storm, blocked',
  $q$INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count,
                            estimated_api_calls, api_status)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1),
             '1999-01-01', 'GUARD-TEST', 1, 1, 'running')$q$, '23505');
SELECT pg_temp.expect('running pull for another type is fine',
  $q$INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count,
                            estimated_api_calls, api_status)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1),
             '1999-01-01', 'GUARD-TEST-2', 1, 1, 'running')$q$, 'ok');
SELECT pg_temp.expect('a finished pull never blocks a new one',
  $q$INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count,
                            estimated_api_calls, api_status)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1),
             '1999-01-01', 'GUARD-TEST', 1, 1, 'complete')$q$, 'ok');
SELECT pg_temp.expect('cancelling the running pull frees the storm',
  $q$UPDATE api_pulls SET api_status = 'cancelled', finished_at = now()
     WHERE storm_date = '1999-01-01' AND report_text = 'GUARD-TEST'
       AND api_status = 'running'$q$, 'ok');
SELECT pg_temp.expect('a new running pull after that is allowed',
  $q$INSERT INTO api_pulls (emp_id, storm_date, report_text, zip_count,
                            estimated_api_calls, api_status)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1),
             '1999-01-01', 'GUARD-TEST', 1, 1, 'running')$q$, 'ok');


ROLLBACK;

-- Nothing should have survived.
SELECT (SELECT count(*) FROM email_templates) AS templates,
       (SELECT count(*) FROM send_log)        AS send_log_rows;