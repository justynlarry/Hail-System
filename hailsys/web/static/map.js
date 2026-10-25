document.addEventListener('DOMContentLoaded', function () {
  const container = document.getElementById('map');
  if (!container) return;

  const map = L.map('map').setView([39.74, -104.99], 9);

  // Colorado county context, behind the coverage layer, a static fixture
  // (scripts/build_colorado_counties_geojson.py), not per-request data.
  const COUNTY_COLORS = [
    '#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#a65628',
    '#f781bf', '#999999', '#66c2a5', '#fc8d62', '#8da0cb', '#e78ac3',
  ];
  // Own pane below overlayPane (400), and not interactive: both layers load
  // by separate fetches, so without this whichever finished last drew on top
  // and its fill swallowed the coverage layer's hover tooltips.
  map.createPane('counties');
  map.getPane('counties').style.zIndex = 350;
  const counties = L.geoJSON(null, {
    pane: 'counties',
    interactive: false,
    style: function (feature) {
      return {
        color: COUNTY_COLORS[feature.properties.county_fips.charCodeAt(2) % COUNTY_COLORS.length],
        weight: .25,
        fillColor: '#999',      // overridden by .map-county in style.css, per theme
        fillOpacity: 0.35,
        className: 'map-county',
      };
    },
  }).addTo(map);

  fetch('/static/colorado_counties.geojson')
    .then(function (r) { return r.json(); })
    .then(function (data) { counties.addData(data); })
    .catch(function (err) { console.error('counties load failed', err); });


  // No tile layer: a basemap means a third-party tile service on every pan,
  // with its own terms of use. The coverage polygons are the basemap here.
  const coverage = L.geoJSON(null, {
    // colour and fill come from .map-coverage in style.css, per theme
    style: { color: '#888', weight: 1, fillColor: '#eee', fillOpacity: 0.4,
             className: 'map-coverage' },
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

  // Panes, bottom to top.  The radar layers sit above the coverage (400) and below the report circles
  // and points, and are not interactive, so the mouse reaches the points and the zip tooltips under them.
  const RADAR_ORDER = ['outer', 'radar_indicated', 'margin', 'core'];
  const RADAR_CLASS = {
    outer: 'radar-warnings', radar_indicated: 'radar-indicated', margin: 'radar-margin', core: 'radar-core',
  };
  const RADAR_LABEL = {
    outer: 'NWS warnings', radar_indicated: 'Radar-indicated', margin: 'Margin', core: 'Core',
  };
  RADAR_ORDER.forEach(function (name, i) {
    map.createPane('radar-' + name);
    map.getPane('radar-' + name).style.zIndex = 410 + i * 10;
  });
  const LIKELIHOOD_ORDER = ['context', 'one_source', 'agree'];
  const LIKELIHOOD_CLASS = { context: 'lk-context', one_source: 'lk-one', agree: 'lk-agree' };
  const LIKELIHOOD_LABEL = { context: 'Context', one_source: 'One source', agree: 'Agree' };
  LIKELIHOOD_ORDER.forEach(function (name, i) {
    map.createPane('likelihood-' + name);
    map.getPane('likelihood-' + name).style.zIndex = 402 + i * 2;
  });
  map.createPane('report-circles');
  map.getPane('report-circles').style.zIndex = 450;
  map.createPane('report-points');
  map.getPane('report-points').style.zIndex = 460;

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

  // The circle claims the match radius, which is a setting.  The server sends it in metres; with no
  // number the circles are left out rather than drawn at a guess (item 216).
  const matchRadiusM = parseFloat(container.dataset.matchRadiusM);
  if (!(matchRadiusM > 0)) console.error('map: no match radius from the server, circles not drawn');

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
        // which would be a lie about how far the radius is.
        if (matchRadiusM > 0) {
          circles.addLayer(L.circle([lat, lon], {
            pane: 'report-circles', radius: matchRadiusM,
            color: color, weight: 1, fillOpacity: 0.05,
            interactive: false,
          }));
        }

        // Formatted by hailsys/formatting.py; the raw p.magnitude stays in the
        // feature for anything that sorts or sizes by it.
        points.addLayer(L.circleMarker([lat, lon], {
          pane: 'report-points',
          radius: 5, color: color, fillColor: color, fillOpacity: 0.9, weight: 1,
        }).bindTooltip(p.local_time + '<br>' + p.report_text + ' ' + p.magnitude_display +
                       '<br>' + p.report_source));
      });
    })
    .catch(function (err) { console.error('points load failed', err); });

  // Radar layers (Phase 5b), staff only.  Colours come from style.css by class, per theme.
  const radar = {};
  // The NWS warnings start off:  the likelihood Context layer shows the same areas, less the parts near a hail report.
  RADAR_ORDER.forEach(function (name) {
    radar[name] = L.geoJSON(null, {
      pane: 'radar-' + name, interactive: false,
      style: { weight: 1, fillOpacity: 0.3, className: RADAR_CLASS[name] },
    });
    if (name !== 'outer') radar[name].addTo(map);
  });

  // Hail likelihood (Phase 5c), staff only.  Context covers whole warning areas, so it starts off.
  const likelihood = {};
  LIKELIHOOD_ORDER.forEach(function (name) {
    likelihood[name] = L.geoJSON(null, {
      pane: 'likelihood-' + name, interactive: false,
      style: { weight: 1, fillOpacity: 0.35, className: LIKELIHOOD_CLASS[name] },
    });
    if (name !== 'context') likelihood[name].addTo(map);
  });

  L.control.layers(null, {
    'Report circles': circles,
    'Report points': points,
    'Agree': likelihood.agree,
    'One source': likelihood.one_source,
    'Context (warnings only)': likelihood.context,
    'Core': radar.core,
    'Margin': radar.margin,
    'Radar-indicated': radar.radar_indicated,
    'NWS warnings': radar.outer,
  }, { collapsed: false }).addTo(map);

  const picker = document.getElementById('radar-day');
  const status = document.getElementById('radar-status');
  const findingsBox = document.getElementById('findings');
  const likelihoodStatus = document.getElementById('likelihood-status');
  let findingsRequest = 0;   // the newest request wins when the picker is changed quickly

  function km2(n) {
    return n.toLocaleString('en-US', { maximumFractionDigits: 0 }) + ' km²';
  }

  // One line for the layers on the map now:  largest claim first, the areas of the union.
  function describe(data) {
    const parts = [];
    ['core', 'margin', 'radar_indicated', 'outer'].forEach(function (name) {
      data.features.forEach(function (f) {
        if (f.properties.layer === name) parts.push(RADAR_LABEL[name] + ' ' + km2(f.properties.area_km2));
      });
    });
    let text = parts.length ? parts.join(' · ') : 'Nothing to draw for this selection.';
    if (data.days.length > 1) text += ' (' + data.days.length + ' days)';
    if (data.built_with_other_set.length) {
      text += ' ' + data.built_with_other_set.length + ' day(s) were built with an older parameter set and are not shown.';
    }
    return text;
  }

  function loadRadar() {
    if (!picker) return;
    const q = new URLSearchParams(window.location.search);
    q.delete('day');
    if (picker.value !== 'all') q.set('day', picker.value);
    fetch(container.dataset.radarUrl + '?' + q)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        RADAR_ORDER.forEach(function (name) { radar[name].clearLayers(); });
        data.features.forEach(function (f) { radar[f.properties.layer].addData(f); });
        if (status) status.textContent = describe(data);
      })
      .catch(function (err) {
        console.error('radar load failed', err);
        if (status) status.textContent = 'The radar layers could not be loaded.';
      });
  }

  function describeLikelihood(data) {
    const parts = [];
    ['agree', 'one_source', 'context'].forEach(function (name) {
      data.features.forEach(function (f) {
        if (f.properties.level === name) parts.push(LIKELIHOOD_LABEL[name] + ' ' + km2(f.properties.area_km2));
      });
    });
    let text = parts.length ? 'Likelihood: ' + parts.join(' · ') : 'Likelihood: nothing to draw for this selection.';
    if (data.built_with_other_set.length) {
      text += ' ' + data.built_with_other_set.length + ' day(s) were built with an older set and are not shown.';
    }
    return text;
  }

  function loadLikelihood() {
    if (!picker || !container.dataset.likelihoodUrl) return;
    const q = new URLSearchParams(window.location.search);
    q.delete('day');
    if (picker.value !== 'all') q.set('day', picker.value);
    fetch(container.dataset.likelihoodUrl + '?' + q)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        LIKELIHOOD_ORDER.forEach(function (name) { likelihood[name].clearLayers(); });
        data.features.forEach(function (f) { likelihood[f.properties.level].addData(f); });
        if (likelihoodStatus) likelihoodStatus.textContent = describeLikelihood(data);
      })
      .catch(function (err) {
        console.error('likelihood load failed', err);
        if (likelihoodStatus) likelihoodStatus.textContent = 'The hail-likelihood layers could not be loaded.';
      });
  }

  // The findings panel (slice 6) for the picked day.  The server sends HTML, so there is nothing to format here.
  // 'All days' has no findings and says so.
  function loadFindings() {
    if (!findingsBox || !picker) return;
    const mine = ++findingsRequest;
    if (picker.value === 'all') {
      findingsBox.textContent = 'Pick a single radar day to see its findings.';
      return;
    }
    findingsBox.textContent = 'Loading findings…';
    const q = new URLSearchParams(window.location.search);
    q.set('day', picker.value);
    fetch(container.dataset.findingsUrl + '?' + q)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (html) {
        if (mine === findingsRequest) findingsBox.innerHTML = html;
      })
      .catch(function (err) {
        console.error('findings load failed', err);
        if (mine === findingsRequest) findingsBox.textContent = 'The findings could not be loaded.';
      });
  }

  if (picker) {
    picker.addEventListener('change', function () { loadRadar(); loadLikelihood(); loadFindings(); });
    loadRadar();
    loadLikelihood();
    loadFindings();
  }
});
