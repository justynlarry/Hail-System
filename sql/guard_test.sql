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

INSERT INTO cc_sync_runs (triggered_by, watermark)
VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1), '1999-01-05');

INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id,
                         sync_run_id, permission_asserted, created_by,
                         subject, html_body)
SELECT gen_random_uuid(),
       (SELECT realtor_id FROM realtors ORDER BY realtor_id LIMIT 1),
       e,
       (SELECT template_id FROM email_templates WHERE template_name = 'guard-test'),
       (SELECT run_id FROM cc_sync_runs WHERE watermark = '1999-01-05'),
       'none',
       (SELECT emp_id FROM users ORDER BY emp_id LIMIT 1),
       's', '<p>b</p>'
FROM (VALUES ('guard-test-1@example.invalid'),
             ('guard-test-2@example.invalid')) AS t(e);

INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by,
                      email_id)
SELECT (SELECT realtor_id FROM realtors ORDER BY realtor_id LIMIT 1),
       e,
       (SELECT match_id FROM storm_listing_matches ORDER BY match_id LIMIT 1),
       (SELECT template_id FROM email_templates WHERE template_name = 'guard-test'),
       (SELECT emp_id FROM users ORDER BY emp_id LIMIT 1),
       (SELECT email_id FROM sent_emails WHERE recipient_email = e)
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

SELECT pg_temp.expect('description frozen once the template has been used',
  $q$UPDATE email_templates SET description = 'changed'
     WHERE template_name = 'guard-test'$q$, '23001');

INSERT INTO email_templates (template_name, subject, body, created_by)
VALUES ('guard-test-unused', 's', 'b',
        (SELECT emp_id FROM users ORDER BY emp_id LIMIT 1));

SELECT pg_temp.expect('description editable before any use',
  $q$UPDATE email_templates SET description = 'first'
     WHERE template_name = 'guard-test-unused'$q$, 'ok');
SELECT pg_temp.expect('description editable again before any use',
  $q$UPDATE email_templates SET description = 'second'
     WHERE template_name = 'guard-test-unused'$q$, 'ok');
SELECT pg_temp.expect('description over 300 characters refused',
  $q$UPDATE email_templates SET description = repeat('x', 301)
     WHERE template_name = 'guard-test-unused'$q$, '23514');
SELECT pg_temp.expect('body still frozen on an unused template',
  $q$UPDATE email_templates SET body = 'changed'
     WHERE template_name = 'guard-test-unused'$q$, '23001');
-- blocks (sql/043)
INSERT INTO email_templates (template_name, subject, body, blocks, created_by)
VALUES ('guard-test-blocks', 's', 'b', '{"blocks": []}',
        (SELECT emp_id FROM users ORDER BY emp_id LIMIT 1));
SELECT pg_temp.expect('blocks must be a JSON object (an array is refused)',
  $q$INSERT INTO email_templates (template_name, subject, body, blocks, created_by)
     SELECT 'guard-test-blocks-bad', 's', 'b', '[]', emp_id FROM users
     ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('blocks frozen on an existing row',
  $q$UPDATE email_templates SET blocks = '{"blocks": [1]}'
     WHERE template_name = 'guard-test-blocks'$q$, '23001');
SELECT pg_temp.expect('blocks cannot be added to a row that had none',
  $q$UPDATE email_templates SET blocks = '{}'
     WHERE template_name = 'guard-test-unused'$q$, '23001');
SELECT pg_temp.expect('blocks cannot be cleared',
  $q$UPDATE email_templates SET blocks = NULL
     WHERE template_name = 'guard-test-blocks'$q$, '23001');
SELECT pg_temp.expect('a block template can be retired',
  $q$UPDATE email_templates SET is_active = FALSE
     WHERE template_name = 'guard-test-blocks'$q$, 'ok');
SELECT pg_temp.expect('template retire (is_active true -> false)',
  $q$UPDATE email_templates SET is_active = FALSE
     WHERE template_name = 'guard-test'$q$, 'ok');
SELECT pg_temp.expect('template reactivate blocked',
  $q$UPDATE email_templates SET is_active = TRUE
     WHERE template_name = 'guard-test'$q$, '23001');
SELECT pg_temp.expect('template DELETE blocked',
  $q$DELETE FROM email_templates
     WHERE template_name = 'guard-test'$q$, '23001');

-- send_log.email_id (sql/037).  These must run BEFORE the TRUNCATE check below,
-- which empties send_log and would leave them acting on no rows.
SELECT pg_temp.expect('send_log email_id frozen',
  $q$UPDATE send_log
     SET email_id = (SELECT email_id FROM sent_emails
                     WHERE recipient_email = 'guard-test-2@example.invalid')
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('send_log needs an email (NOT NULL)',
  $q$INSERT INTO send_log (realtor_id, recipient_email, match_id, template_id, sent_by)
     SELECT realtor_id, 'guard-test-4@example.invalid', match_id, template_id, sent_by
     FROM send_log WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23502');

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


-- oauth_tokens (sql/034): insert-only; delete only old, non-latest rows.
-- created_at is set by hand to simulate age; the whole file rolls back.
SELECT pg_temp.expect('oauth_tokens insert (40 days old)',
  $q$INSERT INTO oauth_tokens (provider, account_id, access_token_enc,
                               refresh_token_enc, scope, access_expires_at,
                               created_at)
     VALUES ('constant_contact', 'guard-test', '\x01', '\x02',
             'contact_data', now(), now() - interval '40 days')$q$, 'ok');
SELECT pg_temp.expect('oauth_tokens unknown provider blocked',
  $q$INSERT INTO oauth_tokens (provider, account_id, access_token_enc,
                               refresh_token_enc, scope, access_expires_at)
     VALUES ('mailchimp', 'guard-test', '\x01', '\x02',
             'contact_data', now())$q$, '23514');
SELECT pg_temp.expect('oauth_tokens UPDATE blocked',
  $q$UPDATE oauth_tokens SET scope = 'x' WHERE account_id = 'guard-test'$q$, '23001');
SELECT pg_temp.expect('oauth_tokens old but latest: DELETE blocked',
  $q$DELETE FROM oauth_tokens WHERE account_id = 'guard-test'$q$, '23001');
SELECT pg_temp.expect('oauth_tokens newer row inserted',
  $q$INSERT INTO oauth_tokens (provider, account_id, access_token_enc,
                               refresh_token_enc, scope, access_expires_at)
     VALUES ('constant_contact', 'guard-test', '\x03', '\x04',
             'contact_data', now())$q$, 'ok');
SELECT pg_temp.expect('oauth_tokens old and superseded: DELETE allowed',
  $q$DELETE FROM oauth_tokens
     WHERE account_id = 'guard-test' AND access_token_enc = '\x01'$q$, 'ok');
SELECT pg_temp.expect('oauth_tokens young and latest: DELETE blocked',
  $q$DELETE FROM oauth_tokens
     WHERE account_id = 'guard-test' AND access_token_enc = '\x03'$q$, '23001');

-- cc_sync_runs (sql/035).  Rows are told apart from any real ones by watermark.
SELECT pg_temp.expect('cc_sync_runs start a run',
  $q$INSERT INTO cc_sync_runs (triggered_by, watermark)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1), '1999-01-01')$q$, 'ok');
SELECT pg_temp.expect('cc_sync_runs ok without finished_at (CHECK)',
  $q$INSERT INTO cc_sync_runs (triggered_by, watermark, status)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1), '1999-01-03', 'ok')$q$, '23514');
SELECT pg_temp.expect('cc_sync_runs a running row can finish',
  $q$UPDATE cc_sync_runs SET status = 'ok', finished_at = now(), fetched = 3
     WHERE watermark = '1999-01-01'$q$, 'ok');
SELECT pg_temp.expect('cc_sync_runs a finished row is frozen',
  $q$UPDATE cc_sync_runs SET fetched = 9 WHERE watermark = '1999-01-01'$q$, '23001');
SELECT pg_temp.expect('cc_sync_runs DELETE blocked',
  $q$DELETE FROM cc_sync_runs WHERE watermark = '1999-01-01'$q$, '23001');
SELECT pg_temp.expect('cc_sync_runs start a second run',
  $q$INSERT INTO cc_sync_runs (triggered_by, watermark)
     VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1), '1999-01-02')$q$, 'ok');
SELECT pg_temp.expect('cc_sync_runs started_at frozen while running',
  $q$UPDATE cc_sync_runs SET started_at = started_at - interval '1 day'
     WHERE watermark = '1999-01-02'$q$, '23001');

-- cc_sync_conflicts (sql/036): insert-only, once per run and row.
INSERT INTO cc_sync_runs (triggered_by, watermark)
VALUES ((SELECT emp_id FROM users ORDER BY emp_id LIMIT 1), '1999-01-04');
SELECT pg_temp.expect('cc_sync_conflicts insert',
  $q$INSERT INTO cc_sync_conflicts (run_id, dnc_id)
     SELECT (SELECT run_id FROM cc_sync_runs WHERE watermark = '1999-01-04'),
            (SELECT dnc_id FROM dnc_list ORDER BY dnc_id LIMIT 1)$q$, 'ok');
SELECT pg_temp.expect('cc_sync_conflicts same run and row twice blocked',
  $q$INSERT INTO cc_sync_conflicts (run_id, dnc_id)
     SELECT (SELECT run_id FROM cc_sync_runs WHERE watermark = '1999-01-04'),
            (SELECT dnc_id FROM dnc_list ORDER BY dnc_id LIMIT 1)$q$, '23505');
SELECT pg_temp.expect('cc_sync_conflicts UPDATE blocked',
  $q$UPDATE cc_sync_conflicts SET seen_at = now()$q$, '23001');
SELECT pg_temp.expect('cc_sync_conflicts DELETE blocked',
  $q$DELETE FROM cc_sync_conflicts$q$, '23001');

-- sent_emails (sql/037)
SELECT pg_temp.expect('sent_emails subject frozen',
  $q$UPDATE sent_emails SET subject = 'x'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('sent_emails html_body frozen',
  $q$UPDATE sent_emails SET html_body = '<p>x</p>'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('sent_emails activity id can be set once',
  $q$UPDATE sent_emails SET cc_activity_id = 'act-1'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, 'ok');
SELECT pg_temp.expect('sent_emails the same value again is a harmless retry',
  $q$UPDATE sent_emails SET cc_activity_id = 'act-1'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, 'ok');
SELECT pg_temp.expect('sent_emails activity id cannot change once set',
  $q$UPDATE sent_emails SET cc_activity_id = 'act-2'
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');
SELECT pg_temp.expect('sent_emails a second email cannot take the same activity id',
  $q$UPDATE sent_emails SET cc_activity_id = 'act-1'
     WHERE recipient_email = 'guard-test-2@example.invalid'$q$, '23505');
SELECT pg_temp.expect('sent_emails scheduled needs an activity id (CHECK)',
  $q$UPDATE sent_emails SET scheduled_at = now()
     WHERE recipient_email = 'guard-test-2@example.invalid'$q$, '23514');
SELECT pg_temp.expect('sent_emails one email per realtor per batch',
  $q$INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id,
                              sync_run_id, permission_asserted, created_by,
                              subject, html_body)
     SELECT batch_id, realtor_id, 'guard-test-3@example.invalid', template_id,
            sync_run_id, 'none', created_by, 's', 'b'
     FROM sent_emails WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23505');
SELECT pg_temp.expect('sent_emails DELETE blocked',
  $q$DELETE FROM sent_emails
     WHERE recipient_email = 'guard-test-1@example.invalid'$q$, '23001');

-- email_images and the email settings (sql/042)
SELECT pg_temp.expect('email_images a required image cannot be retired',
  $q$UPDATE email_images SET is_active = FALSE WHERE name = 'logo'$q$, '23514');
SELECT pg_temp.expect('email_images name unique ignoring case',
  $q$INSERT INTO email_images (name, url, alt_text, created_by, updated_by)
     SELECT 'LOGO', 'https://example.com/x.png', 'x', emp_id, emp_id
     FROM users WHERE role = 'system'$q$, '23505');
SELECT pg_temp.expect('email_images url must be https',
  $q$UPDATE email_images SET url = 'http://example.com/x.png' WHERE name = 'logo'$q$, '23514');
SELECT pg_temp.expect('email_images a new image can be added',
  $q$INSERT INTO email_images (name, url, alt_text, created_by, updated_by)
     SELECT 'guard-extra', 'https://example.com/x.png', 'x', emp_id, emp_id
     FROM users WHERE role = 'system'$q$, 'ok');
SELECT pg_temp.expect('email_images an optional image can be retired',
  $q$UPDATE email_images SET is_active = FALSE WHERE name = 'guard-extra'$q$, 'ok');
SELECT pg_temp.expect('settings contact_email must look like an address',
  $q$UPDATE settings SET contact_email = 'not an address'$q$, '23514');

-- The history trigger reads the acting user from this setting (transaction-local).
SELECT set_config('app.current_emp_id',
                  (SELECT emp_id FROM users ORDER BY emp_id LIMIT 1)::text, true);
SELECT pg_temp.expect('settings an email value change is allowed',
  $q$UPDATE settings SET contact_email = 'guard-test@example.invalid'$q$, 'ok');
-- 1/count(*) fails with division by zero when no history row was written.
SELECT pg_temp.expect('settings that change was logged to settings_history',
  $q$SELECT 1 / count(*) FROM settings_history
     WHERE contact_email = 'guard-test@example.invalid'$q$, 'ok');

-- email_test_sends (sql/044)
SELECT pg_temp.expect('email_test_sends a good row can be written',
  $q$INSERT INTO email_test_sends (sent_by, recipient, template_name, situation, subject,
                                   html_bytes, cc_campaign_id, cc_activity_id, outcome,
                                   campaign_cleanup)
     SELECT emp_id, 'guard-test@example.invalid', 'guard-test', 'one', 'A subject', 1234,
            'camp-1', 'act-1', 'accepted', 'deleted' FROM users ORDER BY emp_id LIMIT 1$q$, 'ok');
SELECT pg_temp.expect('email_test_sends a failed attempt with no campaign can be written',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup, detail)
     SELECT emp_id, 'guard-test@example.invalid', 'one', 'A subject', 99, 'failed', 'none',
            'refused (401)' FROM users ORDER BY emp_id LIMIT 1$q$, 'ok');
SELECT pg_temp.expect('email_test_sends recipient must look like an address',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup)
     SELECT emp_id, 'not an address', 'one', 's', 1, 'failed', 'none'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends accepted needs both Constant Contact ids',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes,
                                   cc_campaign_id, outcome, campaign_cleanup)
     SELECT emp_id, 'a@example.invalid', 'one', 's', 1, 'camp-1', 'accepted', 'deleted'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends cleanup none needs no campaign',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes,
                                   cc_campaign_id, outcome, campaign_cleanup)
     SELECT emp_id, 'a@example.invalid', 'one', 's', 1, 'camp-1', 'failed', 'none'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends a campaign needs a cleanup result',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes,
                                   outcome, campaign_cleanup)
     SELECT emp_id, 'a@example.invalid', 'one', 's', 1, 'failed', 'deleted'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends unknown outcome refused',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup)
     SELECT emp_id, 'a@example.invalid', 'one', 's', 1, 'sent', 'none'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends unknown cleanup value refused',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup)
     SELECT emp_id, 'a@example.invalid', 'one', 's', 1, 'failed', 'maybe'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends html size must be positive',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup)
     SELECT emp_id, 'a@example.invalid', 'one', 's', 0, 'failed', 'none'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends detail limited to 300 characters',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup, detail)
     SELECT emp_id, 'a@example.invalid', 'one', 's', 1, 'failed', 'none', repeat('x', 301)
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends situation must be named and short',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup)
     SELECT emp_id, 'a@example.invalid', '', 's', 1, 'failed', 'none'
     FROM users ORDER BY emp_id LIMIT 1$q$, '23514');
SELECT pg_temp.expect('email_test_sends sent_by must be a real user',
  $q$INSERT INTO email_test_sends (sent_by, recipient, situation, subject, html_bytes, outcome,
                                   campaign_cleanup)
     VALUES (-1, 'a@example.invalid', 'one', 's', 1, 'failed', 'none')$q$, '23503');
SELECT pg_temp.expect('email_test_sends UPDATE blocked',
  $q$UPDATE email_test_sends SET detail = 'changed'
     WHERE recipient = 'guard-test@example.invalid'$q$, '23001');
SELECT pg_temp.expect('email_test_sends DELETE blocked',
  $q$DELETE FROM email_test_sends WHERE recipient = 'guard-test@example.invalid'$q$, '23001');

-- ---- drip campaigns (sql/046) ----
-- Helpers: the first user, the Nth realtor, a fixed batch id per number, a test campaign by number.
CREATE FUNCTION pg_temp.u() RETURNS bigint AS $$
    SELECT emp_id FROM users ORDER BY emp_id LIMIT 1 $$ LANGUAGE sql;
CREATE FUNCTION pg_temp.r(n int) RETURNS bigint AS $$
    SELECT realtor_id FROM realtors ORDER BY realtor_id OFFSET n - 1 LIMIT 1 $$ LANGUAGE sql;
CREATE FUNCTION pg_temp.b(n int) RETURNS uuid AS $$
    SELECT ('00000000-0000-4000-8000-0000000000a' || n)::uuid $$ LANGUAGE sql;
CREATE FUNCTION pg_temp.c(n int) RETURNS bigint AS $$
    SELECT campaign_id FROM drip_campaigns WHERE name = 'guard-test-campaign-' || n $$ LANGUAGE sql;

-- Emailed batches: 1 = campaign 1's first batch (realtors 1 and 2), 2 and 3 = its follow-ups,
-- 4 = campaign 2's first batch, 5 = a spare.  Batch 9 deliberately has no emails.
INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, sync_run_id,
                         permission_asserted, created_by, subject, html_body)
SELECT pg_temp.b(v.batch), pg_temp.r(v.realtor),
       'guard-test-drip-' || v.realtor || '@example.invalid',
       (SELECT template_id FROM email_templates WHERE template_name = 'guard-test'),
       (SELECT run_id FROM cc_sync_runs WHERE watermark = '1999-01-05'),
       'none', pg_temp.u(), 's', '<p>b</p>'
FROM (VALUES (1, 1), (1, 2), (2, 1), (3, 1), (4, 1), (5, 1)) AS v(batch, realtor);

-- drip_campaigns: creation
SELECT pg_temp.expect('drip_campaigns needs a batch that has emails',
  $q$INSERT INTO drip_campaigns (name, first_batch_id, created_by)
     VALUES ('guard-test-campaign-1', pg_temp.b(9), pg_temp.u())$q$, '23001');
SELECT pg_temp.expect('drip_campaigns a new campaign starts active',
  $q$INSERT INTO drip_campaigns (name, first_batch_id, created_by, status)
     VALUES ('guard-test-campaign-1', pg_temp.b(1), pg_temp.u(), 'paused')$q$, '23001');
SELECT pg_temp.expect('drip_campaigns name must be 1 to 100 characters',
  $q$INSERT INTO drip_campaigns (name, first_batch_id, created_by)
     VALUES ('', pg_temp.b(1), pg_temp.u())$q$, '23514');
SELECT pg_temp.expect('drip_campaigns a good campaign can be created',
  $q$INSERT INTO drip_campaigns (name, first_batch_id, created_by)
     VALUES ('guard-test-campaign-1', pg_temp.b(1), pg_temp.u())$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns one first batch cannot start two campaigns',
  $q$INSERT INTO drip_campaigns (name, first_batch_id, created_by)
     VALUES ('guard-test-campaign-x', pg_temp.b(1), pg_temp.u())$q$, '23505');

-- drip_steps: creation
SELECT pg_temp.expect('drip_steps step 2 cannot be added before step 1',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 2, 2, 'guard-test')$q$, '23001');
SELECT pg_temp.expect('drip_steps a step cannot be created already sent',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name,
                             batch_id, started_by, started_at)
     VALUES (pg_temp.c(1), 1, 2, 'guard-test', pg_temp.b(2), pg_temp.u(), now())$q$, '23001');
SELECT pg_temp.expect('drip_steps gap_days must be 1 to 30',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 1, 0, 'guard-test')$q$, '23514');
SELECT pg_temp.expect('drip_steps step_no must be 1 to 10',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 0, 2, 'guard-test')$q$, '23514');
SELECT pg_temp.expect('drip_steps template name must not be empty',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 1, 2, '')$q$, '23514');
SELECT pg_temp.expect('drip_steps step 1 can be added',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 1, 2, 'guard-test')$q$, 'ok');
SELECT pg_temp.expect('drip_steps step 2 can be added',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 2, 3, 'guard-test')$q$, 'ok');
SELECT pg_temp.expect('drip_steps a step number cannot be used twice',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 2, 3, 'guard-test')$q$, '23505');

-- drip_recipients: creation and stopping
SELECT pg_temp.expect('drip_recipients a realtor not emailed in the first batch is refused',
  $q$INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email)
     VALUES (pg_temp.c(1), pg_temp.r(3), 'guard-test-drip-3@example.invalid')$q$, '23001');
SELECT pg_temp.expect('drip_recipients a different address for a real recipient is refused',
  $q$INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email)
     VALUES (pg_temp.c(1), pg_temp.r(1), 'someone-else@example.invalid')$q$, '23001');
SELECT pg_temp.expect('drip_recipients a recipient cannot start stopped',
  $q$INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email, stop_reason)
     VALUES (pg_temp.c(1), pg_temp.r(1), 'guard-test-drip-1@example.invalid', 'bounced')$q$, '23001');
SELECT pg_temp.expect('drip_recipients recipient 1 can be added',
  $q$INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email)
     VALUES (pg_temp.c(1), pg_temp.r(1), 'guard-test-drip-1@example.invalid')$q$, 'ok');
SELECT pg_temp.expect('drip_recipients recipient 2 can be added',
  $q$INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email)
     VALUES (pg_temp.c(1), pg_temp.r(2), 'guard-test-drip-2@example.invalid')$q$, 'ok');
SELECT pg_temp.expect('drip_recipients a stop time needs a reason',
  $q$UPDATE drip_recipients SET stopped_at = now()
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(1)$q$, '23514');
SELECT pg_temp.expect('drip_recipients the realtor is frozen',
  $q$UPDATE drip_recipients SET realtor_id = pg_temp.r(3)
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(1)$q$, '23001');
SELECT pg_temp.expect('drip_recipients the address is frozen',
  $q$UPDATE drip_recipients SET recipient_email = 'x@example.invalid'
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(1)$q$, '23001');
SELECT pg_temp.expect('drip_recipients a stop reason must be a known one',
  $q$UPDATE drip_recipients SET stop_reason = 'whatever'
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(1)$q$, '23514');
SELECT pg_temp.expect('drip_recipients a manual stop needs a person',
  $q$UPDATE drip_recipients SET stop_reason = 'manual'
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(2)$q$, '23514');
SELECT pg_temp.expect('drip_recipients a system stop must not name a person',
  $q$UPDATE drip_recipients SET stop_reason = 'bounced', stopped_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(2)$q$, '23514');
SELECT pg_temp.expect('drip_recipients a system stop is allowed',
  $q$UPDATE drip_recipients SET stop_reason = 'bounced'
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(2)$q$, 'ok');
SELECT pg_temp.expect('drip_recipients the stop time is set by the database',
  $q$SELECT 1 / count(*) FROM drip_recipients
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(2) AND stopped_at IS NOT NULL$q$, 'ok');
SELECT pg_temp.expect('drip_recipients a stopped recipient stays stopped',
  $q$UPDATE drip_recipients SET stop_reason = 'manual', stopped_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(2)$q$, '23001');
SELECT pg_temp.expect('drip_recipients a recipient cannot be deleted',
  $q$DELETE FROM drip_recipients WHERE campaign_id = pg_temp.c(1)$q$, '23001');

-- drip_campaigns: status moves
SELECT pg_temp.expect('drip_campaigns name is frozen',
  $q$UPDATE drip_campaigns SET name = 'changed' WHERE campaign_id = pg_temp.c(1)$q$, '23001');
SELECT pg_temp.expect('drip_campaigns first_batch_id is frozen',
  $q$UPDATE drip_campaigns SET first_batch_id = pg_temp.b(5) WHERE campaign_id = pg_temp.c(1)$q$, '23001');
SELECT pg_temp.expect('drip_campaigns a status change must record who made it',
  $q$UPDATE drip_campaigns SET status = 'paused' WHERE campaign_id = pg_temp.c(1)$q$, '23514');
SELECT pg_temp.expect('drip_campaigns active -> paused',
  $q$UPDATE drip_campaigns SET status = 'paused', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns the change time is set by the database',
  $q$SELECT 1 / count(*) FROM drip_campaigns
     WHERE campaign_id = pg_temp.c(1) AND status_changed_at IS NOT NULL$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns the change record cannot be edited without a status change',
  $q$UPDATE drip_campaigns SET status_changed_at = now() - interval '1 day'
     WHERE campaign_id = pg_temp.c(1)$q$, '23001');
SELECT pg_temp.expect('drip_steps a paused campaign cannot send a step',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(2), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_campaigns paused -> active',
  $q$UPDATE drip_campaigns SET status = 'active', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, 'ok');

-- drip_steps: sending
SELECT pg_temp.expect('drip_steps gap_days is frozen',
  $q$UPDATE drip_steps SET gap_days = 5 WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_steps template_name is frozen',
  $q$UPDATE drip_steps SET template_name = 'other' WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_steps step 2 cannot be sent before step 1',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(3), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND step_no = 2$q$, '23001');
SELECT pg_temp.expect('drip_steps a step cannot be sent with a batch that has no emails',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(9), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_steps a follow-up cannot reuse the first batch',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(1), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_steps sending must record who sent it',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(2)
     WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23514');
SELECT pg_temp.expect('drip_campaigns cannot complete while a step is unsent',
  $q$UPDATE drip_campaigns SET status = 'complete', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, '23001');
SELECT pg_temp.expect('drip_steps step 1 can be sent',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(2), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, 'ok');
SELECT pg_temp.expect('drip_steps the start time is set by the database',
  $q$SELECT 1 / count(*) FROM drip_steps
     WHERE campaign_id = pg_temp.c(1) AND step_no = 1 AND started_at IS NOT NULL$q$, 'ok');
SELECT pg_temp.expect('drip_steps a sent step cannot be sent again with another batch',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(3) WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_steps a sent step cannot be unset',
  $q$UPDATE drip_steps SET batch_id = NULL, started_by = NULL, started_at = NULL
     WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_steps one batch cannot serve two steps',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(2), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND step_no = 2$q$, '23505');
SELECT pg_temp.expect('drip_steps no step can be added once one was sent',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(1), 3, 2, 'guard-test')$q$, '23001');
SELECT pg_temp.expect('drip_recipients the list is fixed once a step was sent',
  $q$INSERT INTO drip_recipients (campaign_id, realtor_id, recipient_email)
     VALUES (pg_temp.c(1), pg_temp.r(1), 'guard-test-drip-1@example.invalid')$q$, '23001');
SELECT pg_temp.expect('drip_recipients stopping is still allowed after a step was sent',
  $q$UPDATE drip_recipients SET stop_reason = 'replied', stopped_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND realtor_id = pg_temp.r(1)$q$, 'ok');
SELECT pg_temp.expect('drip_steps step 2 can be sent',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(3), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1) AND step_no = 2$q$, 'ok');
SELECT pg_temp.expect('drip_steps a step cannot be deleted',
  $q$DELETE FROM drip_steps WHERE campaign_id = pg_temp.c(1) AND step_no = 1$q$, '23001');

-- drip_campaigns: completing and the end states
SELECT pg_temp.expect('drip_campaigns active -> paused (campaign 1, every step sent)',
  $q$UPDATE drip_campaigns SET status = 'paused', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns paused -> complete is not a move even with every step sent',
  $q$UPDATE drip_campaigns SET status = 'complete', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, '23001');
SELECT pg_temp.expect('drip_campaigns paused -> active (campaign 1, every step sent)',
  $q$UPDATE drip_campaigns SET status = 'active', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns active -> complete once every step was sent',
  $q$UPDATE drip_campaigns SET status = 'complete', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns a complete campaign stays complete',
  $q$UPDATE drip_campaigns SET status = 'active', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, '23001');
SELECT pg_temp.expect('drip_campaigns a complete campaign cannot be cancelled',
  $q$UPDATE drip_campaigns SET status = 'cancelled', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(1)$q$, '23001');
SELECT pg_temp.expect('drip_campaigns campaign 2 can be created',
  $q$INSERT INTO drip_campaigns (name, first_batch_id, created_by)
     VALUES ('guard-test-campaign-2', pg_temp.b(4), pg_temp.u())$q$, 'ok');
SELECT pg_temp.expect('drip_steps campaign 2 gets a step',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(2), 1, 2, 'guard-test')$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns active -> paused (campaign 2)',
  $q$UPDATE drip_campaigns SET status = 'paused', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(2)$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns paused -> complete is not a move',
  $q$UPDATE drip_campaigns SET status = 'complete', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(2)$q$, '23001');
SELECT pg_temp.expect('drip_campaigns paused -> cancelled',
  $q$UPDATE drip_campaigns SET status = 'cancelled', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(2)$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns a cancelled campaign stays cancelled',
  $q$UPDATE drip_campaigns SET status = 'active', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(2)$q$, '23001');
SELECT pg_temp.expect('drip_steps a cancelled campaign cannot send a step',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(5), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(2) AND step_no = 1$q$, '23001');
SELECT pg_temp.expect('drip_campaigns a campaign cannot be deleted',
  $q$DELETE FROM drip_campaigns WHERE campaign_id = pg_temp.c(2)$q$, '23001');

-- drip_listing_checks and drip_step_pulls: append-only logs
SELECT pg_temp.expect('drip_listing_checks a lookup can be logged',
  $q$INSERT INTO drip_listing_checks (campaign_id, step_no, listing_id, calls_made, http_status,
                                      outcome, rc_status, last_seen)
     VALUES (pg_temp.c(1), 1, (SELECT listing_id FROM listings ORDER BY listing_id LIMIT 1),
             1, 200, 'active', 'Active', now())$q$, 'ok');
SELECT pg_temp.expect('drip_listing_checks a failed lookup needs no status',
  $q$INSERT INTO drip_listing_checks (campaign_id, step_no, listing_id, calls_made, outcome, detail)
     VALUES (pg_temp.c(1), 1, (SELECT listing_id FROM listings ORDER BY listing_id LIMIT 1),
             0, 'error', 'timed out')$q$, 'ok');
SELECT pg_temp.expect('drip_listing_checks an answered lookup needs the status',
  $q$INSERT INTO drip_listing_checks (campaign_id, step_no, listing_id, calls_made, outcome)
     VALUES (pg_temp.c(1), 1, (SELECT listing_id FROM listings ORDER BY listing_id LIMIT 1),
             1, 'inactive')$q$, '23514');
SELECT pg_temp.expect('drip_listing_checks outcome must be a known one',
  $q$INSERT INTO drip_listing_checks (campaign_id, step_no, listing_id, calls_made, outcome)
     VALUES (pg_temp.c(1), 1, (SELECT listing_id FROM listings ORDER BY listing_id LIMIT 1),
             1, 'maybe')$q$, '23514');
SELECT pg_temp.expect('drip_listing_checks calls cannot be negative',
  $q$INSERT INTO drip_listing_checks (campaign_id, step_no, listing_id, calls_made, outcome)
     VALUES (pg_temp.c(1), 1, (SELECT listing_id FROM listings ORDER BY listing_id LIMIT 1),
             -1, 'error')$q$, '23514');
SELECT pg_temp.expect('drip_listing_checks the step must exist',
  $q$INSERT INTO drip_listing_checks (campaign_id, step_no, listing_id, calls_made, outcome)
     VALUES (pg_temp.c(1), 9, (SELECT listing_id FROM listings ORDER BY listing_id LIMIT 1),
             1, 'error')$q$, '23503');
SELECT pg_temp.expect('drip_listing_checks detail limited to 300 characters',
  $q$INSERT INTO drip_listing_checks (campaign_id, step_no, listing_id, calls_made, outcome, detail)
     VALUES (pg_temp.c(1), 1, (SELECT listing_id FROM listings ORDER BY listing_id LIMIT 1),
             0, 'error', repeat('x', 301))$q$, '23514');
SELECT pg_temp.expect('drip_listing_checks UPDATE blocked',
  $q$UPDATE drip_listing_checks SET calls_made = 5$q$, '23001');
SELECT pg_temp.expect('drip_listing_checks DELETE blocked',
  $q$DELETE FROM drip_listing_checks$q$, '23001');
SELECT pg_temp.expect('drip_step_pulls a pull can be linked to a step',
  $q$WITH p AS (INSERT INTO api_pulls (emp_id, zip_count, estimated_api_calls)
                VALUES (pg_temp.u(), 1, 1) RETURNING pull_id)
     INSERT INTO drip_step_pulls (pull_id, campaign_id, step_no)
     SELECT pull_id, pg_temp.c(1), 1 FROM p$q$, 'ok');
SELECT pg_temp.expect('drip_step_pulls the step must exist',
  $q$WITH p AS (INSERT INTO api_pulls (emp_id, zip_count, estimated_api_calls)
                VALUES (pg_temp.u(), 1, 1) RETURNING pull_id)
     INSERT INTO drip_step_pulls (pull_id, campaign_id, step_no)
     SELECT pull_id, pg_temp.c(1), 9 FROM p$q$, '23503');
SELECT pg_temp.expect('drip_step_pulls UPDATE blocked',
  $q$UPDATE drip_step_pulls SET step_no = 2$q$, '23001');
SELECT pg_temp.expect('drip_step_pulls DELETE blocked',
  $q$DELETE FROM drip_step_pulls$q$, '23001');

-- drip_step_runs: what a step's job did (sql/047).  Campaign 3 is the one active campaign
-- with an unsent step.
SELECT pg_temp.expect('drip_campaigns campaign 3 can be created',
  $q$INSERT INTO drip_campaigns (name, first_batch_id, created_by)
     VALUES ('guard-test-campaign-3', pg_temp.b(5), pg_temp.u())$q$, 'ok');
SELECT pg_temp.expect('drip_steps campaign 3 gets a step',
  $q$INSERT INTO drip_steps (campaign_id, step_no, gap_days, template_name)
     VALUES (pg_temp.c(3), 1, 2, 'guard-test')$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a cancelled campaign cannot start a run',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(2), 1, pg_temp.u())$q$, '23001');
SELECT pg_temp.expect('drip_step_runs a complete campaign cannot start a run',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(1), 2, pg_temp.u())$q$, '23001');
SELECT pg_temp.expect('drip_step_runs a run can be started',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 1, pg_temp.u())$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a new run is running, unfinished, and timed by the database',
  $q$SELECT 1 / count(*) FROM drip_step_runs
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'
       AND finished_at IS NULL AND started_at IS NOT NULL$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs only one run per step can be running',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 1, pg_temp.u())$q$, '23505');
SELECT pg_temp.expect('drip_step_runs a run cannot be inserted already finished',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by, status, finished_at)
     VALUES (pg_temp.c(3), 1, pg_temp.u(), 'failed', now())$q$, '23001');
SELECT pg_temp.expect('drip_step_runs the step must exist',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 7, pg_temp.u())$q$, '23503');
SELECT pg_temp.expect('drip_step_runs a run must say who started it',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no) VALUES (pg_temp.c(3), 1)$q$, '23502');
SELECT pg_temp.expect('drip_step_runs the start time cannot be changed',
  $q$UPDATE drip_step_runs SET started_at = now() - interval '1 day', status = 'failed'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23001');
SELECT pg_temp.expect('drip_step_runs the campaign cannot be changed',
  $q$UPDATE drip_step_runs SET campaign_id = pg_temp.c(2), status = 'failed'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23001');
SELECT pg_temp.expect('drip_step_runs the step cannot be changed',
  $q$UPDATE drip_step_runs SET step_no = 7, status = 'failed'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23001');
SELECT pg_temp.expect('drip_step_runs who started it cannot be changed',
  $q$UPDATE drip_step_runs SET started_by = -1, status = 'failed'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23001');
SELECT pg_temp.expect('drip_step_runs an update must say how the run ended',
  $q$UPDATE drip_step_runs SET calls_made = 3
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23001');
SELECT pg_temp.expect('drip_step_runs status must be a known one',
  $q$UPDATE drip_step_runs SET status = 'bogus'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23514');
SELECT pg_temp.expect('drip_step_runs method must be a known one',
  $q$UPDATE drip_step_runs SET status = 'failed', method = 'carrier-pigeon'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23514');
SELECT pg_temp.expect('drip_step_runs calls cannot be negative',
  $q$UPDATE drip_step_runs SET status = 'failed', calls_made = -1
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23514');
SELECT pg_temp.expect('drip_step_runs emails cannot be negative',
  $q$UPDATE drip_step_runs SET status = 'failed', emails = -1
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23514');
SELECT pg_temp.expect('drip_step_runs detail limited to 300 characters',
  $q$UPDATE drip_step_runs SET status = 'failed', detail = repeat('x', 301)
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, '23514');
SELECT pg_temp.expect('drip_step_runs a run can finish',
  $q$UPDATE drip_step_runs SET status = 'failed', method = 'by_id', calls_made = 3, emails = 0,
            detail = 'RentCast rejected the key'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs the finish time is set by the database',
  $q$SELECT 1 / count(*) FROM drip_step_runs
     WHERE campaign_id = pg_temp.c(3) AND status = 'failed' AND finished_at IS NOT NULL$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a finished run cannot change',
  $q$UPDATE drip_step_runs SET detail = 'something else'
     WHERE campaign_id = pg_temp.c(3) AND status = 'failed'$q$, '23001');
SELECT pg_temp.expect('drip_step_runs DELETE blocked',
  $q$DELETE FROM drip_step_runs$q$, '23001');
SELECT pg_temp.expect('drip_step_runs a new run can start once the last one finished',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 1, pg_temp.u())$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a run can end with nothing to send',
  $q$UPDATE drip_step_runs SET status = 'nothing_to_send', emails = 0
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs another run can start',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 1, pg_temp.u())$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a run left running can be marked abandoned',
  $q$UPDATE drip_step_runs SET status = 'abandoned'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs one more run can start',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 1, pg_temp.u())$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a run can complete',
  $q$UPDATE drip_step_runs SET status = 'complete', method = 'zip_pull', calls_made = 4, emails = 2
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a run can be inserted with a made-up start time',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by, started_at)
     VALUES (pg_temp.c(3), 1, pg_temp.u(), '2000-01-01')$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs the database overrides a made-up start time',
  $q$SELECT 1 / count(*) FROM drip_step_runs
     WHERE campaign_id = pg_temp.c(3) AND status = 'running' AND started_at > '2001-01-01'$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs that run can be marked abandoned',
  $q$UPDATE drip_step_runs SET status = 'abandoned'
     WHERE campaign_id = pg_temp.c(3) AND status = 'running'$q$, 'ok');
SELECT pg_temp.expect('drip_campaigns campaign 3 can be paused',
  $q$UPDATE drip_campaigns SET status = 'paused', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(3)$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a paused campaign cannot start a run',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 1, pg_temp.u())$q$, '23001');
SELECT pg_temp.expect('drip_campaigns campaign 3 can be resumed',
  $q$UPDATE drip_campaigns SET status = 'active', status_changed_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(3)$q$, 'ok');

INSERT INTO sent_emails (batch_id, realtor_id, recipient_email, template_id, sync_run_id,
                         permission_asserted, created_by, subject, html_body)
SELECT pg_temp.b(6), pg_temp.r(1), 'guard-test-drip-1@example.invalid',
       (SELECT template_id FROM email_templates WHERE template_name = 'guard-test'),
       (SELECT run_id FROM cc_sync_runs WHERE watermark = '1999-01-05'),
       'none', pg_temp.u(), 's', '<p>b</p>';
SELECT pg_temp.expect('drip_steps campaign 3 step 1 can be sent',
  $q$UPDATE drip_steps SET batch_id = pg_temp.b(6), started_by = pg_temp.u()
     WHERE campaign_id = pg_temp.c(3) AND step_no = 1$q$, 'ok');
SELECT pg_temp.expect('drip_step_runs a step that was sent cannot be run again',
  $q$INSERT INTO drip_step_runs (campaign_id, step_no, started_by)
     VALUES (pg_temp.c(3), 1, pg_temp.u())$q$, '23001');

INSERT INTO radar_runs (run_mode) VALUES ('backfill');
SELECT pg_temp.expect('radar_parameter_sets cannot be updated',
  $q$UPDATE radar_parameter_sets SET margin_km = 4 WHERE set_id = 1$q$, '23001');
SELECT pg_temp.expect('radar_parameter_sets cannot be deleted',
  $q$DELETE FROM radar_parameter_sets WHERE set_id = 1$q$, '23001');
SELECT pg_temp.expect('radar_parameter_sets a new row can be added',
  $q$INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km)
     VALUES ('guard-test', 1.25, 2.0, 5.0)$q$, 'ok');
SELECT pg_temp.expect('radar_parameter_sets threshold under the 0.50 in floor is refused',
  $q$INSERT INTO radar_parameter_sets (name, hail_threshold_in, margin_km, report_radius_km)
     VALUES ('guard-test', 0.40, 2.0, 5.0)$q$, '23514');
SELECT pg_temp.expect('radar_runs a finished run needs a finish time',
  $q$INSERT INTO radar_runs (run_mode, run_status) VALUES ('nightly', 'complete')$q$, '23514');
SELECT pg_temp.expect('radar_days a loaded day can be added',
  $q$INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min,
       box_lon_min, box_lon_max, floor_mm, cells_in_box, cells_stored, max_mm)
     VALUES ('1999-01-01', (SELECT max(run_id) FROM radar_runs), 'loaded', now(), 'k',
       41.5, 36.5, -109.7, -101.4, 12.7, 100, 1, 20)$q$, 'ok');
SELECT pg_temp.expect('radar_days a loaded day needs its cell count',
  $q$INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min,
       box_lon_min, box_lon_max, floor_mm)
     VALUES ('1999-01-02', (SELECT max(run_id) FROM radar_runs), 'loaded', now(), 'k',
       41.5, 36.5, -109.7, -101.4, 12.7)$q$, '23514');
SELECT pg_temp.expect('radar_days a missing day holds no cells',
  $q$INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min,
       box_lon_min, box_lon_max, floor_mm, cells_stored)
     VALUES ('1999-01-03', (SELECT max(run_id) FROM radar_runs), 'missing', now(), 'k',
       41.5, 36.5, -109.7, -101.4, 12.7, 5)$q$, '23514');
SELECT pg_temp.expect('radar_days the box must be ordered',
  $q$INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min,
       box_lon_min, box_lon_max, floor_mm, cells_in_box)
     VALUES ('1999-01-04', (SELECT max(run_id) FROM radar_runs), 'loaded', now(), 'k',
       36.5, 41.5, -109.7, -101.4, 12.7, 100)$q$, '23514');
SELECT pg_temp.expect('radar_cells a cell can be added',
  $q$INSERT INTO radar_cells (day, grid_row, grid_col, mm, geom)
     VALUES ('1999-01-01', 1, 1, 20, ST_SetSRID(ST_MakePoint(-105, 40), 4326))$q$, 'ok');
SELECT pg_temp.expect('radar_cells a cell is stored once per day',
  $q$INSERT INTO radar_cells (day, grid_row, grid_col, mm, geom)
     VALUES ('1999-01-01', 1, 1, 21, ST_SetSRID(ST_MakePoint(-105, 40), 4326))$q$, '23505');
SELECT pg_temp.expect('radar_cells a cell must have hail in it',
  $q$INSERT INTO radar_cells (day, grid_row, grid_col, mm, geom)
     VALUES ('1999-01-01', 1, 2, 0, ST_SetSRID(ST_MakePoint(-105, 40), 4326))$q$, '23514');

ROLLBACK;

-- Nothing should have survived.
SELECT (SELECT count(*) FROM email_templates) AS templates,
       (SELECT count(*) FROM send_log)        AS send_log_rows,
       (SELECT count(*) FROM oauth_tokens WHERE account_id = 'guard-test') AS oauth_token_rows,
       (SELECT count(*) FROM sent_emails WHERE recipient_email LIKE 'guard-test-%') AS sent_email_rows,
       (SELECT count(*) FROM cc_sync_runs WHERE watermark IN ('1999-01-01', '1999-01-02', '1999-01-04', '1999-01-05')) AS sync_test_rows,
       (SELECT count(*) FROM email_test_sends) AS test_send_rows,
       (SELECT count(*) FROM drip_campaigns) + (SELECT count(*) FROM drip_steps)
         + (SELECT count(*) FROM drip_recipients) + (SELECT count(*) FROM drip_listing_checks)
         + (SELECT count(*) FROM drip_step_pulls) + (SELECT count(*) FROM drip_step_runs) AS drip_rows,
       (SELECT count(*) FROM radar_runs) + (SELECT count(*) FROM radar_days)
         + (SELECT count(*) FROM radar_cells) + (SELECT count(*) FROM radar_parameter_sets) - 1 AS radar_rows;