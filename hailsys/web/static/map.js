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

  // Each storm -- one local date and one report type, as a Storm Days row -- gets its own colour, so
  // every circle and dot of 2026-08-13 HAIL matches (developer, 2026-11-03; item 201).  The colours are
  // ten CSS slots, storm-1 to storm-10 in style.css, given newest storm first; older storms share the
  // grey storm-older, because cycling the ten would give two storms one colour.  Who reported a point
  // (it was the colour until 2026-11-03, with no legend) is in its tooltip.
  const STORM_SLOTS = 10;

  const circles = L.layerGroup().addTo(map);
  const points = L.layerGroup().addTo(map);
  const legendList = document.getElementById('storm-legend');
  const legendNote = document.getElementById('storm-legend-note');

  // The circle claims the match radius, which is a setting.  The server sends it in metres; with no
  // number the circles are left out rather than drawn at a guess (item 216).
  const matchRadiusM = parseFloat(container.dataset.matchRadiusM);
  if (!(matchRadiusM > 0)) console.error('map: no match radius from the server, circles not drawn');

  // local_time arrives as MM-DD-YYYY HH:MM (views.py), in Denver time; the storm's date is its day.
  function stormDate(localTime) {
    const d = localTime.slice(0, 10).split('-');
    return d[2] + '-' + d[0] + '-' + d[1];
  }

  // Click a storm in the legend to fade the others; click it again, or "Show all", to undo.
  const storms = {};          // key -> { slot, date, type, layers: [{layer, kind}] }
  let highlighted = null;
  const FULL = { ring: { opacity: 1, fillOpacity: 0.05 }, dot: { opacity: 1, fillOpacity: 0.9 } };
  const FADED = { ring: { opacity: 0.12, fillOpacity: 0 }, dot: { opacity: 0.15, fillOpacity: 0.12 } };

  function highlight(key) {
    highlighted = (highlighted === key) ? null : key;
    Object.keys(storms).forEach(function (k) {
      const on = highlighted === null || k === highlighted;
      storms[k].layers.forEach(function (l) {
        l.layer.setStyle((on ? FULL : FADED)[l.kind]);
        if (on && highlighted !== null && l.kind === 'dot') l.layer.bringToFront();
      });
    });
    if (legendList) {
      legendList.querySelectorAll('button[data-key]').forEach(function (b) {
        b.setAttribute('aria-pressed', b.dataset.key === highlighted ? 'true' : 'false');
      });
      legendList.classList.toggle('has-highlight', highlighted !== null);
    }
  }

  function legendRow(key, slotClass, text) {
    const li = document.createElement('li');
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.key = key;
    b.setAttribute('aria-pressed', 'false');
    const sw = document.createElement('span');
    sw.className = 'swatch ' + slotClass;
    b.appendChild(sw);
    b.appendChild(document.createTextNode(text));
    b.addEventListener('click', function () { highlight(key); });
    li.appendChild(b);
    return li;
  }

  function plural(n, word) { return n.toLocaleString('en-US') + ' ' + word + (n === 1 ? '' : 's'); }

  const params = new URLSearchParams(window.location.search);
  fetch('/map/points.geojson?' + params)
    .then(function (r) { return r.json(); })
    .then(function (data) {
      // Number the storms newest first, so the colours go to the storms most likely to be worked.
      const counts = {};
      data.features.forEach(function (f) {
        const key = stormDate(f.properties.local_time) + '|' + f.properties.report_text;
        counts[key] = (counts[key] || 0) + 1;
      });
      const keys = Object.keys(counts).sort(function (a, b) {
        const [da, ta] = a.split('|'), [db, tb] = b.split('|');
        return da < db ? 1 : da > db ? -1 : (ta < tb ? -1 : ta > tb ? 1 : 0);
      });
      keys.forEach(function (key, i) {
        const [date, type] = key.split('|');
        storms[i < STORM_SLOTS ? key : 'older'] = storms[i < STORM_SLOTS ? key : 'older'] ||
          { slot: i < STORM_SLOTS ? 'storm-' + (i + 1) : 'storm-older', date: date, type: type, layers: [] };
        if (i >= STORM_SLOTS) storms.older.count = (storms.older.count || 0) + 1;
      });

      data.features.forEach(function (f) {
        const [lon, lat] = f.geometry.coordinates;
        const p = f.properties;
        const key = stormDate(p.local_time) + '|' + p.report_text;
        const storm = storms[key] || storms.older;

        // L.circle takes metres and draws a true circle on the ground.
        // L.circleMarker takes pixels and would grow as you zoom out,
        // which would be a lie about how far the radius is.
        // The colour comes from the storm-N class in style.css, per theme.
        if (matchRadiusM > 0) {
          const ring = L.circle([lat, lon], {
            pane: 'report-circles', radius: matchRadiusM,
            weight: 1, fillOpacity: 0.05, className: 'storm-ring ' + storm.slot,
            interactive: false,
          });
          circles.addLayer(ring);
          storm.layers.push({ layer: ring, kind: 'ring' });
        }

        // Formatted by hailsys/formatting.py; the raw p.magnitude stays in the
        // feature for anything that sorts or sizes by it.
        const dot = L.circleMarker([lat, lon], {
          pane: 'report-points', radius: 5, fillOpacity: 0.9, weight: 1,
          className: 'storm-dot ' + storm.slot,
        }).bindTooltip(p.local_time + '<br>' + p.report_text + ' ' + p.magnitude_display +
                       '<br>Reported by ' + p.report_source);
        points.addLayer(dot);
        storm.layers.push({ layer: dot, kind: 'dot' });
      });

      if (legendList) {
        keys.slice(0, STORM_SLOTS).forEach(function (key) {
          const s = storms[key];
          legendList.appendChild(legendRow(key, s.slot, s.date + ' · ' + s.type + ' · ' + plural(counts[key], 'report')));
        });
        if (storms.older) {
          const n = storms.older.layers.filter(function (l) { return l.kind === 'dot'; }).length;
          legendList.appendChild(legendRow('older', 'storm-older',
            'Older storms in grey · ' + plural(storms.older.count, 'storm') + ', ' + plural(n, 'report')));
        }
        if (legendNote) {
          legendNote.textContent = keys.length
            ? 'Each colour is one storm: one day and one report type. Click a storm to fade the others; click it again to show all.'
            : 'No storm reports in this view.';
        }
      }

      // The server keeps the newest reports when a view has more than it sends (item 33).
      const pointsStatus = document.getElementById('points-status');
      if (pointsStatus && data.total > data.features.length) {
        pointsStatus.textContent = 'Showing the newest ' + data.features.length.toLocaleString('en-US') +
          ' of ' + data.total.toLocaleString('en-US') + ' reports. Narrow the dates or pick a type to see them all.';
      }
    })
    .catch(function (err) {
      console.error('points load failed', err);
      if (legendNote) legendNote.textContent = 'The storm reports could not be loaded.';
    });

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
