-- DEV ONLY.  Test rows for the zone send rehearsal (Phase 5c step 4, piece 6).  Never run on
-- production.  Two test realtors (+t8, +t9, the developer's own Gmail inbox), each with one
-- listing at a point inside the 2026-10-12 One source zone of the set in use.  No matches here:
-- the Match button on /pull/zones writes them through the real zone matcher.
-- Safe to re-run:  it refreshes list_last_seen so the freshness rule still passes.

BEGIN;

-- Loud, not silent:  with no zone the properties below would quietly not be written.
DO $$
BEGIN
    IF (SELECT count(*) FROM likelihood_zones
        WHERE day = DATE '2026-10-12' AND level = 'one_source'
          AND set_id = (SELECT max(set_id) FROM likelihood_parameter_sets)) <> 1 THEN
        RAISE EXCEPTION 'expected one One source zone on 2026-10-12 for the set in use';
    END IF;
END $$;

INSERT INTO realtors (agent_name, agent_email, agent_office_name)
SELECT v.name, v.email, 'RBI Rehearsal Office'
FROM (VALUES ('Justyn Zone Rehearsal One', 'rbi.justyn+t8@gmail.com'),
             ('Justyn Zone Rehearsal Two', 'rbi.justyn+t9@gmail.com')) AS v(name, email)
WHERE NOT EXISTS (SELECT 1 FROM realtors r WHERE r.email_norm = lower(trim(v.email)));

-- The point is computed from the zone, so the houses are inside it whatever its shape.
INSERT INTO properties (rentcast_id, property_address, address_1, city, state, zip_code,
                        county, list_latitude, list_longitude, property_type)
SELECT v.rid, v.addr || ', Granby, CO 80446', v.addr, 'Granby', 'CO', '80446', 'Grand',
       ST_Y(p.pt), ST_X(p.pt), 'Single Family'
FROM (VALUES ('TEST-ZONE-REHEARSAL-1', '801 Zone Rehearsal Way'),
             ('TEST-ZONE-REHEARSAL-2', '802 Zone Rehearsal Way')) AS v(rid, addr)
CROSS JOIN (
    SELECT ST_PointOnSurface(z.geom) AS pt
    FROM likelihood_zones z
    WHERE z.day = DATE '2026-10-12' AND z.level = 'one_source'
      AND z.set_id = (SELECT max(set_id) FROM likelihood_parameter_sets)) AS p
ON CONFLICT (rentcast_id) DO NOTHING;

INSERT INTO listings (rentcast_id, list_status, list_type, list_date, list_last_seen,
                      list_price, list_agent_name, list_agent_email, realtor_id)
SELECT v.rid, 'Active', 'Standard', timestamptz '2026-10-01 12:00+00', now(),
       450000, r.agent_name, r.agent_email, r.realtor_id
FROM (VALUES ('TEST-ZONE-REHEARSAL-1', 'rbi.justyn+t8@gmail.com'),
             ('TEST-ZONE-REHEARSAL-2', 'rbi.justyn+t9@gmail.com')) AS v(rid, email)
JOIN realtors r ON r.email_norm = lower(trim(v.email))
ON CONFLICT (rentcast_id, list_date)
DO UPDATE SET list_last_seen = now(), list_status = 'Active';

-- What the seed left:  two Active listings, each inside the zone, each with its realtor.
SELECT l.rentcast_id, r.agent_email, l.listing_id, l.list_status,
       ST_Contains(z.geom, p.geom) AS inside_zone
FROM listings l
JOIN realtors r USING (realtor_id)
JOIN properties p USING (rentcast_id)
CROSS JOIN likelihood_zones z
WHERE l.rentcast_id LIKE 'TEST-ZONE-REHEARSAL-%'
  AND z.day = DATE '2026-10-12' AND z.level = 'one_source'
  AND z.set_id = (SELECT max(set_id) FROM likelihood_parameter_sets)
ORDER BY 1;

COMMIT;