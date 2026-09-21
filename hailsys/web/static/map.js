document.addEventListener('DOMContentLoaded', function () {
  const container = document.getElementById('map');
  if (!container) return;

  const map = L.map('map').setView([39.74, -104.99], 9);

  // No tile layer: a basemap means a third-party tile service on every pan,
  // with its own terms of use. The coverage polygons are the basemap here.
  const coverage = L.geoJSON(null, {
    style: { color: '#888', weight: 1, fillColor: '#eee', fillOpacity: 0.4 },
    onEachFeature: function (feature, layer) {
      const p = feature.properties;
      layer.bindTooltip(p.zcta5 + ' — ' + p.area_name, { sticky: true });
    },
  }).addTo(map);

  fetch('/static/coverage.geojson')
    .then(function (r) { return r.json(); })
    .then(function (data) {
      coverage.addData(data);
      map.fitBounds(coverage.getBounds());
    })
    .catch(function (err) { console.error('coverage load failed', err); });

  // Keys are report_source_norm values (upper/trim), the top 5 sources by
  // report_count in reference/sources.csv. report_source itself is free
  // text with inconsistent case, so it is never the match key.
  const SOURCE_COLORS = {
    'COCORAHS': '#2ca02c',
    'MESONET': '#ff7f0e',
    'TRAINED SPOTTER': '#d62728',
    'PUBLIC': '#1f77b4',
    'CO-OP OBSERVER': '#9467bd',
  };

  const circles = L.layerGroup().addTo(map);
  const points = L.layerGroup().addTo(map);

  const params = new URLSearchParams(window.location.search);
  fetch('/map/points.geojson?' + params)
    .then(function (r) { return r.json(); })
    .then(function (data) {
      data.features.forEach(function (f) {
        const [lon, lat] = f.geometry.coordinates;
        const p = f.properties;
        const color = SOURCE_COLORS[p.report_source_norm] || '#666';

        // L.circle takes metres and draws a true circle on the ground.
        // L.circleMarker takes pixels and would grow as you zoom out,
        // which would be a lie about how far 5 miles is.
        circles.addLayer(L.circle([lat, lon], {
          radius: 8046.72,
          color: color, weight: 1, fillOpacity: 0.05,
          interactive: false,
        }));

        // Formatted by hailsys/formatting.py; the raw p.magnitude stays in the
        // feature for anything that sorts or sizes by it.
        points.addLayer(L.circleMarker([lat, lon], {
          radius: 5, color: color, fillColor: color, fillOpacity: 0.9, weight: 1,
        }).bindTooltip(p.local_time + '<br>' + p.report_text + ' ' + p.magnitude_display +
                       '<br>' + p.report_source));
      });
    })
    .catch(function (err) { console.error('points load failed', err); });
});