-- Additive:  api-pulls gains storm_date/report_text so each pull can be traced
-- back to what the user clicked in the storm browser.  iem_id stays nullable
-- and unused for grouped-storm pulls.  Storm in this context is defined as:
-- (date, report_text) query, not a single iem_data row


ALTER TABLE api_pulls
    ADD COLUMN storm_date date,
    ADD COLUMN report_text text;

ALTER TABLE api_pulls
    ADD CONSTRAINT storm_link_paired CHECK (
        (storm_date IS NULL) = (report_text IS NULL)
    );

COMMENT ON COLUMN api_pulls.storm_date IS
    'Local storm day pull targeted, NULL for pull not tied '
    'to one browsed storm.';
COMMENT ON COLUMN api_pulls.report_text IS
    'Report type the storm day was filtered to paired with '
    'storm_date.  storm_link_paired enforces both or none.'