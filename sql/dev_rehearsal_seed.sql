-- DEV ONLY.  Test rows for the send rehearsal (parking-lot item 180, piece 4).  Never run on
-- production.  Safe to re-run: it refreshes list_last_seen so the freshness rule still passes.
-- Attaches two fake listings to the largest finite hail report of the 2026-09-19 Denver day.
-- Once a send_log row points at a seeded match, that match can no longer be deleted.

BEGIN;

INSERT INTO realtors (agent_name, agent_email, agent_office_name)
SELECT v.n, v.e, 'RBI Rehearsal Office'
FROM (VALUES ('Justyn Rehearsal', 'rbi.justyn+t2@gmail.com'),
             ('Justyn Negative',  'rbi.justyn@gmail.com')) AS v(n, e)
WHERE NOT EXISTS (SELECT 1 FROM realtors r WHERE r.email_norm = lower(trim(v.e)));

INSERT INTO properties (rentcast_id, property_address, address_1, city, state, zip_code,
                        county, list_latitude, list_longitude, property_type)
VALUES ('TEST-REHEARSAL-1', '100 Rehearsal Test Way, Denver, CO 80202',
        '100 Rehearsal Test Way', 'Denver', 'CO', '80202', 'Denver',
        39.7392, -104.9903, 'Single Family'),
       ('TEST-REHEARSAL-2', '200 Rehearsal Test Way, Denver, CO 80202',
        '200 Rehearsal Test Way', 'Denver', 'CO', '80202', 'Denver',
        39.7400, -104.9910, 'Single Family')
ON CONFLICT (rentcast_id) DO NOTHING;

INSERT INTO listings (rentcast_id, list_status, list_type, list_date, list_last_seen,
                      list_price, list_agent_name, list_agent_email, realtor_id)
SELECT p.rid, 'Active', 'Standard', timestamptz '2026-09-18 12:00+00', now(),
       450000, r.agent_name, r.agent_email, r.realtor_id
FROM (VALUES ('TEST-REHEARSAL-1', 'rbi.justyn+t2@gmail.com'),
             ('TEST-REHEARSAL-2', 'rbi.justyn@gmail.com')) AS p(rid, email)
JOIN realtors r ON r.email_norm = lower(trim(p.email))
ON CONFLICT (rentcast_id, list_date)
DO UPDATE SET list_last_seen = now(), list_status = 'Active';

INSERT INTO storm_listing_matches (iem_id, listing_id, distance_miles, radius_used)
SELECT i.iem_id, l.listing_id, v.d, s.default_match_radius_miles
FROM (VALUES ('TEST-REHEARSAL-1', 1.20), ('TEST-REHEARSAL-2', 2.40)) AS v(rid, d)
JOIN listings l ON l.rentcast_id = v.rid
CROSS JOIN settings s
CROSS JOIN LATERAL (
    SELECT iem_id FROM iem_data
    WHERE report_text = 'HAIL' AND magnitude <> 'NaN'
      AND (utc_datetime AT TIME ZONE 'America/Denver')::date = DATE '2026-09-19'
    ORDER BY magnitude DESC, iem_id LIMIT 1) i
ON CONFLICT (iem_id, listing_id, radius_used) DO NOTHING;

SELECT r.agent_email, l.listing_id, m.match_id, m.iem_id, m.distance_miles, m.radius_used
FROM listings l
JOIN realtors r USING (realtor_id)
LEFT JOIN storm_listing_matches m ON m.listing_id = l.listing_id
WHERE l.rentcast_id LIKE 'TEST-REHEARSAL-%'
ORDER BY l.rentcast_id;

COMMIT;