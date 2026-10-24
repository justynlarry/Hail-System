SET default_transaction_read_only=on;
\echo === window ===
SELECT min(utc_datetime)::date first_rpt, max(utc_datetime)::date last_rpt, count(*) reports, count(DISTINCT (utc_datetime AT TIME ZONE 'America/Denver')::date) storm_days
FROM iem_data WHERE report_text='HAIL' AND magnitude>=1.00 AND utc_datetime >= '2021-10-24 00:00-06';
\echo === zips, whole database ===
WITH h AS (
  SELECT d.magnitude, (d.utc_datetime AT TIME ZONE 'America/Denver')::date AS day, z.zcta5
  FROM iem_data d JOIN report_zip_distances z ON z.iem_id=d.iem_id AND z.distance_m=0
  WHERE d.report_text='HAIL' AND d.magnitude>=1.00 AND d.utc_datetime >= '2021-10-24 00:00-06')
SELECT h.zcta5, coalesce(c.area_name,'(not in coverage)') area, count(*) reports, count(DISTINCT day) storm_days,
       max(magnitude) max_in, count(*) FILTER (WHERE magnitude>=1.75) ge_175in, max(day) last_hit
FROM h LEFT JOIN coverage_zips c ON c.zcta5=h.zcta5 AND c.removed_at IS NULL
GROUP BY 1,2 ORDER BY storm_days DESC, reports DESC LIMIT 10;
\echo === zips, coverage only ===
WITH h AS (
  SELECT d.magnitude, (d.utc_datetime AT TIME ZONE 'America/Denver')::date AS day, z.zcta5
  FROM iem_data d JOIN report_zip_distances z ON z.iem_id=d.iem_id AND z.distance_m=0
  WHERE d.report_text='HAIL' AND d.magnitude>=1.00 AND d.utc_datetime >= '2021-10-24 00:00-06')
SELECT h.zcta5, c.area_name, count(*) reports, count(DISTINCT day) storm_days,
       max(magnitude) max_in, count(*) FILTER (WHERE magnitude>=1.75) ge_175in, max(day) last_hit
FROM h JOIN coverage_zips c ON c.zcta5=h.zcta5 AND c.removed_at IS NULL
GROUP BY 1,2 ORDER BY storm_days DESC, reports DESC LIMIT 10;
\echo === counties ===
SELECT cb.name county, count(*) reports, count(DISTINCT (d.utc_datetime AT TIME ZONE 'America/Denver')::date) storm_days, max(d.magnitude) max_in
FROM iem_data d JOIN county_boundaries cb ON ST_Contains(cb.geom, d.geom) AND cb.state_fips='08'
WHERE d.report_text='HAIL' AND d.magnitude>=1.00 AND d.utc_datetime >= '2021-10-24 00:00-06'
GROUP BY 1 ORDER BY reports DESC LIMIT 10;
