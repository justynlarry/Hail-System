WITH cov AS (
  SELECT ST_Union(z.geom) g FROM coverage_zips c JOIN zcta_boundaries z USING (zcta5) WHERE c.removed_at IS NULL),
cov_utm AS (SELECT ST_Transform(g,26913) g FROM cov)
SELECT d.iem_id, d.report_type, d.report_text, d.magnitude,
       to_char(d.utc_datetime,'YYYY-MM-DD"T"HH24:MI:SS"Z"') utc,
       to_char(d.utc_datetime AT TIME ZONE 'America/Denver','YYYY-MM-DD HH24:MI:SS') local,
       d.latitude, d.longitude,
       round(ST_X(ST_Transform(d.geom,26913))::numeric,1) x, round(ST_Y(ST_Transform(d.geom,26913))::numeric,1) y,
       round((ST_Distance(ST_Transform(d.geom,26913), cu.g)/1000)::numeric,2) km_to_cov,
       d.report_source_norm, d.county
FROM iem_data d, cov_utm cu
WHERE d.report_text IN ('HAIL','TSTM WND GST','TSTM WND DMG','DOWNBURST')
  AND d.utc_datetime >= (timestamp '2025-10-13' AT TIME ZONE 'America/Denver')
ORDER BY d.utc_datetime
