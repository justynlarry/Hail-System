-- DEV ONLY.  Test rows for the drip campaign rehearsal (parking-lot item 204, piece 7).  Never run on
-- production.  Four fresh realtors (+t10 to +t13, the same Gmail inbox as the others, never emailed, so the
-- 14-day cap holds none of them back), one test listing and one match each on the 2026-09-20 storm, the
-- largest recent HAIL day.  That day is past the 30-day match age, so the rehearsal raises
-- match_max_age_days on the admin page first and puts it back after.
--
--   campaign A (+t10, +t11):  run on the screens with real RentCast.  The listings are in 80202, whose last
--     pull cost 2 calls, so the follow-up checks by id;  a TEST id is a 404, so the follow-up finds nothing
--     to send.
--   campaign B (+t12, +t13):  step 1 on the screens, the follow-ups with scripts/rehearse_drip.py, which
--     answers RentCast's lookup itself.  One listing in 80202 and one in 80203 (2 calls each), so two
--     lookups cost less than the pulls and the check is by id.
--
-- Safe to re-run:  it refreshes list_last_seen so the freshness rule still passes.  Once a send_log row
-- points at a seeded match, that match can no longer be deleted.

BEGIN;

INSERT INTO realtors (agent_name, agent_email, agent_office_name)
SELECT v.name, v.email, 'RBI Rehearsal Office'
FROM (VALUES ('Justyn Drip Ten',      'rbi.justyn+t10@gmail.com'),
             ('Justyn Drip Eleven',   'rbi.justyn+t11@gmail.com'),
             ('Justyn Drip Twelve',   'rbi.justyn+t12@gmail.com'),
             ('Justyn Drip Thirteen', 'rbi.justyn+t13@gmail.com')) AS v(name, email)
WHERE NOT EXISTS (SELECT 1 FROM realtors r WHERE r.email_norm = lower(trim(v.email)));

INSERT INTO properties (rentcast_id, property_address, address_1, city, state, zip_code,
                        county, list_latitude, list_longitude, property_type)
VALUES ('TEST-REHEARSAL-DRIP-10', '1010 Rehearsal Drip Way, Denver, CO 80202',
        '1010 Rehearsal Drip Way', 'Denver', 'CO', '80202', 'Denver', 39.7410, -104.9920, 'Single Family'),
       ('TEST-REHEARSAL-DRIP-11', '1111 Rehearsal Drip Way, Denver, CO 80202',
        '1111 Rehearsal Drip Way', 'Denver', 'CO', '80202', 'Denver', 39.7412, -104.9922, 'Single Family'),
       ('TEST-REHEARSAL-DRIP-12', '1212 Rehearsal Drip Way, Denver, CO 80202',
        '1212 Rehearsal Drip Way', 'Denver', 'CO', '80202', 'Denver', 39.7414, -104.9924, 'Single Family'),
       ('TEST-REHEARSAL-DRIP-13', '1313 Rehearsal Drip Way, Denver, CO 80203',
        '1313 Rehearsal Drip Way', 'Denver', 'CO', '80203', 'Denver', 39.7310, -104.9810, 'Single Family')
ON CONFLICT (rentcast_id) DO NOTHING;

INSERT INTO listings (rentcast_id, list_status, list_type, list_date, list_last_seen,
                      list_price, list_agent_name, list_agent_email, realtor_id)
SELECT p.rentcast_id, 'Active', 'Standard', timestamptz '2026-09-18 12:00+00', now(),
       450000, r.agent_name, r.agent_email, r.realtor_id
FROM (VALUES ('TEST-REHEARSAL-DRIP-10', 'rbi.justyn+t10@gmail.com'),
             ('TEST-REHEARSAL-DRIP-11', 'rbi.justyn+t11@gmail.com'),
             ('TEST-REHEARSAL-DRIP-12', 'rbi.justyn+t12@gmail.com'),
             ('TEST-REHEARSAL-DRIP-13', 'rbi.justyn+t13@gmail.com')) AS p(rentcast_id, email)
JOIN realtors r ON r.email_norm = lower(trim(p.email))
ON CONFLICT (rentcast_id, list_date)
DO UPDATE SET list_last_seen = now(), list_status = 'Active';

INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used)
SELECT i.iem_id, l.listing_id, 1.50, s.default_match_radius_miles
FROM listings l
CROSS JOIN settings s
CROSS JOIN LATERAL (
    SELECT iem_id FROM iem_data
    WHERE report_text = 'HAIL' AND magnitude <> 'NaN'
      AND (utc_datetime AT TIME ZONE 'America/Denver')::date = DATE '2026-09-20'
    ORDER BY magnitude DESC, iem_id LIMIT 1) i
WHERE l.rentcast_id LIKE 'TEST-REHEARSAL-DRIP-%'
ON CONFLICT (iem_id, listing_id, radius_used) DO NOTHING;

SELECT r.agent_email, p.zip_code, l.listing_id, m.match_id, m.iem_id, m.distance_miles
FROM listings l
JOIN realtors r USING (realtor_id)
JOIN properties p USING (rentcast_id)
LEFT JOIN storm_listing_matches m ON m.listing_id = l.listing_id
WHERE l.rentcast_id LIKE 'TEST-REHEARSAL-DRIP-%'
ORDER BY r.agent_email;

COMMIT;
