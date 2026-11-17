// The Address Search map (item 313).  Read only:  it draws what search.py put in the page (the house, the match
// radius, the reports in the table, the city and county lines near the house) and fetches the radar and hail-likelihood
// layers for the picked day from the Territory map's own URLs.  Nothing here reaches a pull, a match, an email or an
// export.  Not map.js:  that one is the Territory page's, with its window-wide points, storm legend and findings panel.
document.addEventListener('DOMContentLoaded', function () {
  const container = document.getElementById('map');
  const dataEl = document.getElementById('search-map-data');
  if (!container || !dataEl) return;
  const d = JSON.parse(dataEl.textContent);

  const map = L.map('map').setView([d.lat, d.lon], 12);

  // As on Territory (map.js, item 112):  on a touch screen one finger scrolls the page until "Move map" is on.
  const touch = ('ontouchstart' in window) ||
    (typeof navigator !== 'undefined' && navigator.maxTouchPoints > 0);
  if (touch && map.dragging && L.Control) {
    map.dragging.disable();
    const MoveControl = L.Control.extend({
      options: { position: 'topright' },
      onAdd: function () {
        const box = L.DomUtil.create('div', 'leaflet-bar map-move');
        const button = L.DomUtil.create('button', '', box);
        button.type = 'button';
        button.textContent = 'Move map';
        button.setAttribute('aria-pressed', 'false');
        button.title = 'One finger moves the map while this is on; off, it scrolls the page';
        L.DomEvent.disableClickPropagation(box);
        L.DomEvent.on(button, 'click', function () {
          const on = !map.dragging.enabled();
          if (on) { map.dragging.enable(); } else { map.dragging.disable(); }
          button.setAttribute('aria-pressed', on ? 'true' : 'false');
          button.textContent = on ? 'Moving map (tap to stop)' : 'Move map';
        });
        return box;
      },
    });
    new MoveControl().addTo(map);
  }

  // Panes, bottom to top.  The same order as Territory's for the shared layers, then the lines, the circle, the
  // reports and the house on top, so the house is never hidden under a dot.
  map.createPane('counties');
  map.getPane('counties').style.zIndex = 350;
  const LIKELIHOOD_ORDER = ['context', 'one_source', 'agree'];
  const LIKELIHOOD_CLASS = { context: 'lk-context', one_source: 'lk-one', agree: 'lk-agree' };
  LIKELIHOOD_ORDER.forEach(function (name, i) {
    map.createPane('likelihood-' + name);
    map.getPane('likelihood-' + name).style.zIndex = 402 + i * 2;
  });
  const RADAR_ORDER = ['outer', 'radar_indicated', 'margin', 'core'];
  const RADAR_CLASS = {
    outer: 'radar-warnings', radar_indicated: 'radar-indicated', margin: 'radar-margin', core: 'radar-core',
  };
  RADAR_ORDER.forEach(function (name, i) {
    map.createPane('radar-' + name);
    map.getPane('radar-' + name).style.zIndex = 410 + i * 10;
  });
  [['lines', 445], ['search-radius', 450], ['report-points', 460], ['house', 470]].forEach(function (p) {
    map.createPane(p[0]);
    map.getPane(p[0]).style.zIndex = p[1];
  });

  // The background, as on Territory:  no tile layer (decision log 2026-09-16), the county and zip shapes instead.
  const counties = L.geoJSON(null, {
    pane: 'counties', interactive: false,
    style: { weight: 0.25, fillOpacity: 0.35, className: 'map-county' },
  }).addTo(map);
  fetch('/static/colorado_counties.geojson')
    .then(function (r) { return r.json(); })
    .then(function (data) { counties.addData(data); })
    .catch(function (err) { console.error('counties load failed', err); });

  const coverage = L.geoJSON(null, {
    style: { weight: 1, fillOpacity: 0.4, className: 'map-coverage' },
    onEachFeature: function (feature, layer) {
      const p = feature.properties;
      layer.bindTooltip(p.zcta5 + ' — ' + p.area_name, { sticky: true });
    },
  }).addTo(map);
  fetch('/static/coverage.geojson')
    .then(function (r) { return r.json(); })
    .then(function (data) { coverage.addData(data); })
    .catch(function (err) { console.error('coverage load failed', err); });

  // The city and county lines from the database, the ones the jurisdiction panel measured against.  Outlines only:
  // a fill would hide which side of the line the house is on.
  const lines = L.geoJSON(d.boundaries, {
    pane: 'lines',
    style: function (feature) {
      const county = feature.properties.kind === 'county';
      return { weight: county ? 3 : 2, fill: false, className: county ? 'map-line-county' : 'map-line-city' };
    },
    onEachFeature: function (feature, layer) {
      const p = feature.properties;
      layer.bindTooltip(p.kind === 'county' ? p.name + ' County line' : p.name + ' city line', { sticky: true });
    },
  }).addTo(map);

  // L.circle is metres on the ground, so it is the real radius at every zoom.
  const radius = L.circle([d.lat, d.lon], {
    pane: 'search-radius', radius: d.radius_m, weight: 2, fillOpacity: 0.04,
    className: 'search-radius', interactive: false,
  }).addTo(map);
  map.fitBounds(radius.getBounds(), { padding: [16, 16] });

  // Reports by hail size.  The band is worked out in search.py, so the edges live in one place; bigger hail is
  // also a bigger dot, so the size never rests on colour alone.
  const DOT_RADIUS = { h1: 4, h2: 5, h3: 6.5, h4: 8, other: 4 };
  const reports = L.layerGroup().addTo(map);
  d.reports.forEach(function (r) {
    const dot = L.circleMarker([r.lat, r.lon], {
      pane: 'report-points', radius: DOT_RADIUS[r.band] || 4, weight: 1, fillOpacity: 0.9,
      className: 'hail-dot hail-' + r.band,
    });
    // Built as text nodes, not an HTML string:  the source is free text from IEM.
    const tip = document.createElement('div');
    [r.local_time, r.report_text + ' ' + r.magnitude_display, r.distance + ' away',
      r.report_source ? 'Reported by ' + r.report_source : null].forEach(function (line) {
      if (!line) return;
      const div = document.createElement('div');
      div.textContent = line;
      tip.appendChild(div);
    });
    dot.bindTooltip(tip);
    reports.addLayer(dot);
  });

  const house = L.marker([d.lat, d.lon], {
    pane: 'house', keyboard: false,
    icon: L.divIcon({ className: 'search-house', iconSize: [14, 14], html: '<span></span>' }),
  }).bindTooltip(document.createTextNode(d.address)).addTo(map);

  // Radar (Phase 5b) and hail likelihood (Phase 5c) for one day, from the Territory URLs, which take a window and a
  // day inside it:  a one-day window is that day.  The NWS warnings and Context start off, as on Territory.
  const radar = {};
  RADAR_ORDER.forEach(function (name) {
    radar[name] = L.geoJSON(null, {
      pane: 'radar-' + name, interactive: false,
      style: { weight: 1, fillOpacity: 0.3, className: RADAR_CLASS[name] },
    });
    if (name !== 'outer') radar[name].addTo(map);
  });
  const likelihood = {};
  LIKELIHOOD_ORDER.forEach(function (name) {
    likelihood[name] = L.geoJSON(null, {
      pane: 'likelihood-' + name, interactive: false,
      style: { weight: 1, fillOpacity: 0.35, className: LIKELIHOOD_CLASS[name] },
    });
    if (name !== 'context') likelihood[name].addTo(map);
  });

  L.control.layers(null, {
    'City and county lines': lines,
    'Match radius': radius,
    'Reports': reports,
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
  let request = 0;     // the newest pick wins when the picker is changed quickly

  function clearZones() {
    RADAR_ORDER.forEach(function (name) { radar[name].clearLayers(); });
    LIKELIHOOD_ORDER.forEach(function (name) { likelihood[name].clearLayers(); });
  }

  function getJson(url, day) {
    const q = new URLSearchParams({ start: day, end: day, day: day });
    return fetch(url + '?' + q).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function loadDay() {
    if (!picker) return;
    const mine = ++request;
    const day = picker.value;
    clearZones();
    if (!day) {
      if (status) status.textContent = 'No radar day shown.';
      return;
    }
    if (status) status.textContent = 'Loading ' + day + '…';
    Promise.all([getJson(container.dataset.radarUrl, day), getJson(container.dataset.likelihoodUrl, day)])
      .then(function (both) {
        if (mine !== request) return;
        const r = both[0], lk = both[1];
        r.features.forEach(function (f) { radar[f.properties.layer].addData(f); });
        lk.features.forEach(function (f) { likelihood[f.properties.level].addData(f); });
        let text = 'Showing radar and hail likelihood for ' + day + '.';
        if (r.built_with_other_set.length || lk.built_with_other_set.length) {
          text += ' Some of it was built with an older parameter set and is not shown.';
        }
        if (status) status.textContent = text;
      })
      .catch(function (err) {
        console.error('radar or likelihood load failed', err);
        if (mine === request && status) status.textContent = 'The radar and hail-likelihood layers could not be loaded.';
      });
  }

  if (picker) {
    picker.addEventListener('change', loadDay);
    loadDay();
  }
});
