
-- Activity feed filters iem_data by ingested_at, this index
-- prevents it from being a full archive on every page load
CREATE INDEX iem_data_ingested_at_idx ON iem_data (ingested_at DESC);