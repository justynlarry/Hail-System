-- DEV ONLY.  Test rows for the resume rehearsal (parking-lot item 180, piece 5d).  Never run on
-- production.  Two fresh realtors (+t4, +t5, same Gmail inbox as +t2/+t3), one listing and one match
-- each on the 2026-09-19 storm, so one send can go out and the other stay queued for a resume.
-- Safe to re-run: it refreshes list_last_seen so the freshness rule still passes.

BEGIN;

INSERT INTO realtors (agent_name, agent_email, agent_office_name)
SELECT v.name, v.email, 'RBI Rehearsal Office'
FROM (VALUES ('Justyn Rehearsal Three', 'rbi.justyn+t4@gmail.com'),
             ('Justyn Rehearsal Four',  'rbi.justyn+t5@gmail.com')) AS v(name, email)
WHERE NOT EXISTS (SELECT 1 FROM realtors r WHERE r.email_norm = lower(trim(v.email)));

INSERT INTO properties (rentcast_id, property_address, address_1, city, state, zip_code,
                        county, list_latitude, list_longitude, property_type)
VALUES ('TEST-REHEARSAL-5', '500 Rehearsal Resume Way, Denver, CO 80202',
        '500 Rehearsal Resume Way', 'Denver', 'CO', '80202', 'Denver',
        39.7405, -104.9915, 'Single Family'),
       ('TEST-REHEARSAL-6', '600 Rehearsal Resume Way, Denver, CO 80202',
        '600 Rehearsal Resume Way', 'Denver', 'CO', '80202', 'Denver',
        39.7405, -104.9915, 'Single Family')
ON CONFLICT (rentcast_id) DO NOTHING;

INSERT INTO listings (rentcast_id, list_status, list_type, list_date, list_last_seen,
                      list_price, list_agent_name, list_agent_email, realtor_id)
SELECT p.rentcast_id, 'Active', 'Standard', timestamptz '2026-09-18 12:00+00', now(),
       450000, r.agent_name, r.agent_email, r.realtor_id
FROM (VALUES ('TEST-REHEARSAL-5', 'rbi.justyn+t4@gmail.com'),
             ('TEST-REHEARSAL-6', 'rbi.justyn+t5@gmail.com')) AS p(rentcast_id, email)
JOIN realtors r ON r.email_norm = lower(trim(p.email))
ON CONFLICT (rentcast_id, list_date)
DO UPDATE SET list_last_seen = now(), list_status = 'Active';

INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used)
SELECT i.iem_id, l.listing_id, 1.90, s.default_match_radius_miles
FROM listings l
CROSS JOIN settings s
CROSS JOIN LATERAL (
    SELECT iem_id FROM iem_data
    WHERE report_text = 'HAIL' AND magnitude <> 'NaN'
      AND (utc_datetime AT TIME ZONE 'America/Denver')::date = DATE '2026-09-19'
    ORDER BY magnitude DESC, iem_id LIMIT 1) i
WHERE l.rentcast_id IN ('TEST-REHEARSAL-5', 'TEST-REHEARSAL-6')
ON CONFLICT (iem_id, listing_id, radius_used) DO NOTHING;

SELECT r.agent_email, l.listing_id, m.match_id, m.iem_id, m.distance_miles, m.radius_used
FROM listings l
JOIN realtors r USING (realtor_id)
LEFT JOIN storm_listing_matches m ON m.listing_id = l.listing_id
WHERE l.rentcast_id IN ('TEST-REHEARSAL-5', 'TEST-REHEARSAL-6')
ORDER BY r.agent_email;

COMMIT;
