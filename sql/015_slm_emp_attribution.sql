-- storm_listing_matches: attribution + the activity feed's index.

BEGIN;

ALTER TABLE storm_listing_matches
    ADD COLUMN emp_id BIGINT
    CONSTRAINT fk_slm_users REFERENCES users (emp_id);

-- Activity feed -> what was matched since a given point in time
CREATE INDEX storm_listing_matches_matched_at_idx
ON storm_listing_matches (matched_at DESC);

COMMENT ON COLUMN storm_listing_matches.emp_id IS
'User that ran the match.  Nullable, since rows written '
'before this column was created have no attribution. ';

COMMENT ON COLUMN storm_listing_matches.distance_miles IS
'Point to point, report coordinates -> report coordinates '
'Not the nearest-edge-of-zip distance used by storm browser '
'This number appears in the email to the listing agent';

COMMENT ON COLUMN storm_listing_matches.matched_at IS
'When computed, indexed descending for activity feed.';

COMMIT;