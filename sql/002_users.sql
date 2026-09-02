-- Internal User Database

BEGIN;

CREATE TABLE users (
	emp_id		BIGINT		GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	user_name	TEXT		NOT NULL UNIQUE,
	emp_fname	TEXT		NOT NULL,
	emp_lanme	TEXT		NOT NULL,
	emp_email	TEXT		NOT NULL UNIQUE,
	password_hash	TEXT		NOT NULL,
	role		TEXT		NOT NULL
			CHECK (role IN ('admin', 'sender', 'viewer', 'system')),
	is_active	BOOLEAN		NOT NULL DEFAULT TRUE,
	created_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	created_by	BIGINT		REFERENCES users(emp_id),
	last_login	TIMESTAMPTZ,

	CONSTRAINT user_name_is_lower CHECK (user_name = lower(user_name)),
	CONSTRAINT emp_email_is_lower CHECK (emp_email = lower(emp_email)),
	CONSTRAINT system_account_cannot_log_in
		CHECK (role <> 'system'
			OR (is_active = FALSE AND password_hash = "!"))

);

COMMENT ON TABLE users IS
	'Logins.  Never deleted -- every send_by and created_by depends on thes rows.';

COMMENT ON COLUMN users.created_by IS
	'Nullable on ly for the bootstrap account, which has no creator.';

COMMENT ON COLUMN users.role IS
	'admin manages users and NOTHING else -- cannot touch templates, '
	'suppression, or sending.  Enforced in application code, not here.';

INSERT INTO users (user_name, emp_fname, emp_lname, emp_email,
		password_hash, role, is_active, created_by)
VALUES ('system', 'System', 'Account', 'system@rbit.invalid',
	'!', 'system', FALSE, NULL);


COMMIT;
