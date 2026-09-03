-- email_templates
-- send_log

BEGIN;

CREATE TABLE email_templates (
	template_id	BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	template_name	TEXT		NOT NULL,
	subject		TEXT		NOT NULL,
	body		TEXT		NOT NULL,

	report_type	TEXT		NOT NULL REFERENCES report_types (pk_report_type),
	report_text	TEXT,

	is_active	BOOLEAN		NOT NULL DEFAULT TRUE,
	created_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	created_by	BIGINT		NOT NULL REFERENCES users (emp_id),
	supersedes_id	BIGINT		REFERENCES email_templates (template_id),

	CONSTRAINT fk_template_report_type
		FOREIGN KEY (report_type, report_text)
		REFERENCES report_types (report_type, report_text),

	CONSTRAINT report_type_pair_complete
		CHECK ((report_type IS NULL) = (report_text IS NULL))

);

COMMENT ON TABLE email_templates IS
	'Editing in place is FORBIDDEN.  Changing the body would destroy historical send '
	'claim and original text.  To change a template: insert a new row, set supersedes_id '
	'set the old row is_active to false.';

COMMENT ON COLUMN email_templates.report_type IS
	'Nullable pair -- NULL means the template applies to any event type.'



CREATE TABLE send_log (
	send_id		BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

	realtor_id	BIGINT NOT NULL REFERENCES realtors (realtor_id),
	recipient_email	TEXT	NOT NULL,

	match_id	BIGINT	NOT NULL REFERENCES storm_listing_matches (match_id),
	template_id	BIGINT	NOT NULL REFERENCES email_templates (template_id),

	queued_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	sent_at		TIMESTAMPTZ,
	sent_by		BIGINT		NOT NULL REFERENCES users (emp_id),

	send_status	TEXT		NOT NULL DEFAULT 'queued'
					CHECK (send_status IN ('queued', 'sent', 'bounced',
						'complained', 'failed')),
	provider_message	TEXT,
	status_updated_at	TIMESTAMPTZ,
	error_detail		TEXT

	CONSTRAINT sent_has_timestamp
		CHECK (status = 'queued' OR status = 'failed' OR sent_at IS NOT NULL)
);
 
CREATE INDEX send_log_realtor_sent_idx ON send_log (realtor_id, sent_at);
CREATE INDEX send_log_match_idx	       ON send_log (match_id);

COMMENT ON TABLE send_log IS
	'Append-Only: it is the audit trail, and can show if/when an suppressed email '
	'address was emailed, record must be immutable.'

COMMENT ON COLUMN send_log.recipient_email IS
	'SNAPSHOT of the address actually used, if the address for a particular agent '
	'is changed at a later date, the history shows where the message really went.'

COMMENT ON COLUMN send_log.realtor_id IS
	'Denormalized - reachable via match_id -> listing_id -> realtor_id.  Stored '
	'directly because the frequency cap queries it on every send.'

COMMENT ON COLUMN send_log.queued_at IS
	'Row is created before the send attempt, so a crash mid-attempt leaves a record '
	'to reconcile against.  Frequency cap counts queued rows.'

COMMIT;
