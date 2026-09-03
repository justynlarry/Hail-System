-- api_pulls
-- api_call_log

BEGIN;

CREATE TABLE api_pulls (
	pull_id		BIGINT		GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	emp_id		BIGINT		NOT NULL
			CONSTRAINT fk_api_pulls_emp
			REFERENCES users (emp_id),
	iem_id		BIGINT
			CONSTRAINT fk_api_pulls_iem_data
			REFERENCES iem_data (iem_id),

	started_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	finished_at	TIMESTAMPTZ,

	zip_count		INTEGER		NOT NULL CHECK (zip_count >=0),
	estimated_api_calls	INTEGER		NOT NULL CHECK (estimated_api_calls >= 0),
	actual_api_calls	INTEGER		CHECK (actual_api_calls >= 0),
	listings_returned	INTEGER		CHECK (listings_returned >=0),
	api_status		TEXT		NOT NULL DEFAULT 'running'
							CHECK (api_status IN ('running', 'complete',
								'failed', 'cancelled'))

);

CREATE INDEX api_pulls_emp_id_started_at_idx ON api_pulls (emp_id, started_at DESC);

COMMENT ON TABLE api_pulls IS
	'This records what the system spent in API calls.';

COMMENT ON COLUMN api_pulls.iem_id IS
	'Nullable - a pull does not need to be tied to a specific storm.';

COMMENT ON COLUMN api_pulls.estimated_api_calls IS
	'What the UI showed before user confirmation - estimated vs. actual.'
	'the estimate should improve over time.';

COMMENT ON COLUMN api_pulls.finished_at IS
	'Null while running or if it died.';


CREATE TABLE api_call_log (
	api_log_id	BIGINT		GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	pull_id		BIGINT		NOT NULL
			CONSTRAINT fk_api_call_log_pull
			REFERENCES api_pulls (pull_id),
	zip_code	TEXT		NOT NULL CHECK (zip_code ~ '^[0-9]{5}$'),
	called_at	TIMESTAMPTZ	NOT NULL DEFAULT now(),
	calls_made	INTEGER		NOT NULL CHECK (calls_made >=0),

	listings_returned	INTEGER NOT NULL CHECK (listings_returned >=0),
	http_status		INTEGER

);

CREATE INDEX api_call_log_zip_code_called_at_idx
	ON api_call_log (zip_code, called_at DESC);
CREATE INDEX api_call_log_pull_id_idx ON api_call_log (pull_id);

COMMENT ON COLUMN api_call_log.zip_code IS
	'Log of what was called.';

COMMENT ON COLUMN api_call_log.http_status IS
	'Created to diagnose partial failures, and catalog denser areas that may generate '
	'a higher API call number.';

COMMIT;
