-- All years, hail + convective wind types (same as ../storm_tracks). Buffer test: 50 km buffer of the coverage union in UTM 13N
-- (grid distance, as in the storm_tracks run). inside = intersects any active coverage ZCTA (index per zip; testing the
-- 28k-vertex union per row was far too slow).
WITH buf AS (SELECT ST_Buffer(ST_Union(ST_Transform(z.geom,26913)),50000) g
             FROM coverage_zips c JOIN zcta_boundaries z USING (zcta5) WHERE c.removed_at IS NULL)
SELECT d.iem_id, d.report_type, d.report_text, d.magnitude,
       to_char(d.utc_datetime,'YYYY-MM-DD"T"HH24:MI:SS"Z"') utc,
       to_char(d.utc_datetime AT TIME ZONE 'America/Denver','YYYY-MM-DD HH24:MI:SS') local,
       d.latitude, d.longitude,
       round(ST_X(ST_Transform(d.geom,26913))::numeric,1) x, round(ST_Y(ST_Transform(d.geom,26913))::numeric,1) y,
       CASE WHEN EXISTS (SELECT 1 FROM coverage_zips c JOIN zcta_boundaries z USING (zcta5)
                         WHERE c.removed_at IS NULL AND ST_Intersects(z.geom, d.geom)) THEN 'inside' ELSE 'buffer' END zone,
       d.report_source_norm, d.county
FROM iem_data d, buf
WHERE d.report_text IN ('HAIL','TSTM WND GST','TSTM WND DMG','DOWNBURST')
  AND ST_Intersects(ST_Transform(d.geom,26913), buf.g)
ORDER BY d.utc_datetime
