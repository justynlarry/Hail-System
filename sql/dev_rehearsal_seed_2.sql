-- DEV ONLY.  Test rows for the UI send rehearsal (parking-lot item 180, piece 5).  Never run on
-- production.  Seed 1 is spent: its realtor (+t2) was emailed and is inside the cap window, so
-- this adds a fresh realtor (+t3, same Gmail inbox), listing and match on the same storm.
-- Safe to re-run: it refreshes list_last_seen so the freshness rule still passes.

BEGIN;

INSERT INTO realtors (agent_name, agent_email, agent_office_name)
SELECT 'Justyn Rehearsal Two', 'rbi.justyn+t3@gmail.com', 'RBI Rehearsal Office'
WHERE NOT EXISTS (SELECT 1 FROM realtors r WHERE r.email_norm = lower(trim('rbi.justyn+t3@gmail.com')));

INSERT INTO properties (rentcast_id, property_address, address_1, city, state, zip_code,
                        county, list_latitude, list_longitude, property_type)
VALUES ('TEST-REHEARSAL-3', '300 Rehearsal Test Way, Denver, CO 80202',
        '300 Rehearsal Test Way', 'Denver', 'CO', '80202', 'Denver',
        39.7405, -104.9915, 'Single Family')
ON CONFLICT (rentcast_id) DO NOTHING;

INSERT INTO listings (rentcast_id, list_status, list_type, list_date, list_last_seen,
                      list_price, list_agent_name, list_agent_email, realtor_id)
SELECT 'TEST-REHEARSAL-3', 'Active', 'Standard', timestamptz '2026-09-18 12:00+00', now(),
       450000, r.agent_name, r.agent_email, r.realtor_id
FROM realtors r WHERE r.email_norm = lower(trim('rbi.justyn+t3@gmail.com'))
ON CONFLICT (rentcast_id, list_date)
DO UPDATE SET list_last_seen = now(), list_status = 'Active';

INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used)
SELECT i.iem_id, l.listing_id, 1.80, s.default_match_radius_miles
FROM listings l
CROSS JOIN settings s
CROSS JOIN LATERAL (
    SELECT iem_id FROM iem_data
    WHERE report_text = 'HAIL' AND magnitude <> 'NaN'
      AND (utc_datetime AT TIME ZONE 'America/Denver')::date = DATE '2026-09-19'
    ORDER BY magnitude DESC, iem_id LIMIT 1) i
WHERE l.rentcast_id = 'TEST-REHEARSAL-3'
ON CONFLICT (iem_id, listing_id, radius_used) DO NOTHING;

SELECT r.agent_email, l.listing_id, m.match_id, m.iem_id, m.distance_miles, m.radius_used
FROM listings l
JOIN realtors r USING (realtor_id)
LEFT JOIN storm_listing_matches m ON m.listing_id = l.listing_id
WHERE l.rentcast_id = 'TEST-REHEARSAL-3';

COMMIT;