// Behaviour check for hailsys/web/static/map.js (Phase 5b, slice 5): a fake page, a fake Leaflet, the real script.
// There is no Node on the host, so run it in a throwaway container:
//   docker run --rm -v ./hailsys/web/static:/s:ro -v ./tests/js:/t:ro node:22-alpine node /t/map_check.js
// What it pins: the circles use the radius the server sent (item 216) and are left out with none; the panes stack
// counties < radar layers (outer, radar-indicated, margin, core) < circles < points; the picker asks for its day
// and keeps the page's own query string; a change replaces the layers; the status line says what is drawn; and a
// failed load says so instead of leaving a stale map.
const fs = require('fs');
const src = fs.readFileSync('/s/map.js', 'utf8');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }
const tick = (ms = 15) => new Promise(r => setTimeout(r, ms));

function makeEnv(o) {
  const st = {panes: {}, circles: [], markers: [], fetches: [], controls: [], geo: [], errors: [], groups: []};
  const L = {
    map() { const m = {setView() { return m; }, createPane(n) { st.panes[n] = {style: {}}; },
                       getPane(n) { return st.panes[n]; }, fitBounds() {}}; st.map = m; return m; },
    geoJSON(_, opts) {
      const g = {opts, features: [], cleared: 0, map: null,
                 addTo(m) { g.map = m; return g; }, addData(d) { g.features.push(d); },
                 clearLayers() { g.cleared++; g.features = []; }, getBounds() { return {}; }};
      st.geo.push(g); return g;
    },
    layerGroup() { const g = {layers: [], map: null, addTo(m) { g.map = m; return g; }, addLayer(l) { g.layers.push(l); }}; st.groups.push(g); return g; },
    circle(latlng, opts) { const c = {latlng, opts}; st.circles.push(c); return c; },
    circleMarker(latlng, opts) { const c = {latlng, opts, bindTooltip(t) { c.tooltip = t; return c; }}; st.markers.push(c); return c; },
    control: {layers(base, overlays, opts) { const c = {base, overlays, opts, addTo(m) { c.map = m; st.controls.push(c); return c; }}; return c; }},
  };
  const status = {textContent: ''};
  const picker = o.noPicker ? null : {value: o.pickerValue || 'all', handlers: {},
                                      addEventListener(t, f) { this.handlers[t] = f; }};
  const container = {dataset: o.dataset};
  let ready;
  const document = {
    addEventListener(t, f) { if (t === 'DOMContentLoaded') ready = f; },
    getElementById(id) { return {map: container, 'radar-day': picker, 'radar-status': o.noPicker ? null : status}[id] || null; },
  };
  const bodies = o.bodies || {};
  const fetchImpl = (url) => {
    st.fetches.push(url);
    if (url.startsWith('/static/')) return Promise.resolve({ok: true, json: () => Promise.resolve({features: []})});
    if (url.startsWith('/map/points.geojson')) return Promise.resolve({ok: true, json: () => Promise.resolve(bodies.points || {features: []})});
    if (url.startsWith('/map/radar.geojson')) {
      if (o.radarReject) return Promise.reject(new Error('network'));
      const r = o.radarSequence ? o.radarSequence.shift() : bodies.radar;
      return Promise.resolve({ok: o.radarOk !== false, json: () => Promise.resolve(r)});
    }
    return Promise.reject(new Error('unexpected ' + url));
  };
  const console_ = {error() { st.errors.push([...arguments].join(' ')); }, log: console.log};
  const window = {location: {search: o.search === undefined ? '' : o.search}};
  const run = async () => {
    new Function('L', 'document', 'window', 'fetch', 'console', 'URLSearchParams', src)(L, document, window, fetchImpl, console_, URLSearchParams);
    ready(); await tick();
  };
  return {st, picker, status, container, run};
}

const POINT = (lon, lat) => ({type: 'Feature', geometry: {type: 'Point', coordinates: [lon, lat]},
  properties: {report_source_norm: 'COCORAHS', local_time: '06-08-2026 17:19', report_text: 'HAIL', magnitude_display: '2.75"', report_source: 'CoCoRaHS'}});
const FEATURE = (layer, km2) => ({type: 'Feature', properties: {layer, area_km2: km2, parts: 1}, geometry: {type: 'Polygon', coordinates: []}});
const RADAR = (layers, days = ['2026-06-08'], other = []) => ({type: 'FeatureCollection', features: layers.map(([l, a]) => FEATURE(l, a)),
  days, built_with_other_set: other, set: {set_id: 1}});
const DS = {matchRadiusM: '8046.7', radarUrl: '/map/radar.geojson'};

(async () => {
  check(!/8046|1609\.344|5 miles/.test(src), 'no hard-coded radius left in map.js');

  // ---- a normal page
  let e = makeEnv({dataset: DS, pickerValue: '2026-06-08', search: '?days=90&type=HAIL&day=stale',
                   bodies: {points: {features: [POINT(-104.9, 39.7), POINT(-104.8, 39.6)]},
                            radar: RADAR([['outer', 14894.5], ['core', 1802.5]])}});
  await e.run();
  check(e.st.circles.length === 2 && e.st.circles.every(c => c.opts.radius === 8046.7), 'circles drawn at the radius the server sent');
  check(e.st.circles.every(c => c.opts.pane === 'report-circles' && c.opts.interactive === false), 'circles are in their pane and not interactive');
  check(e.st.markers.length === 2 && e.st.markers.every(m => m.opts.pane === 'report-points'), 'points are in their pane');
  check(e.st.markers[0].tooltip.includes('HAIL') && e.st.markers[0].tooltip.includes('2.75"'), 'points keep their tooltip');
  const z = (n) => e.st.panes[n].style.zIndex;
  check(z('counties') < z('radar-outer') && z('radar-outer') < z('radar-radar_indicated') && z('radar-radar_indicated') < z('radar-margin')
        && z('radar-margin') < z('radar-core') && z('radar-core') < z('report-circles') && z('report-circles') < z('report-points'),
        'panes stack counties < outer < radar-indicated < margin < core < circles < points');
  check(z('radar-outer') > 400, 'radar layers sit above the coverage layer (400)');
  check(e.st.fetches.includes('/map/points.geojson?days=90&type=HAIL&day=stale'), 'points are fetched with the page query string');
  check(e.st.fetches.includes('/map/radar.geojson?days=90&type=HAIL&day=2026-06-08'), 'radar asks for the picked day, replacing a stale day= and keeping the rest');
  const radarGroups = e.st.geo.filter(g => g.opts.pane && g.opts.pane.startsWith('radar-'));
  check(radarGroups.length === 4 && radarGroups.every(g => g.opts.interactive === false && g.map), 'four radar layers, non-interactive, on the map');
  const by = (p) => radarGroups.find(g => g.opts.pane === 'radar-' + p);
  check(by('core').features.length === 1 && by('outer').features.length === 1 && by('margin').features.length === 0, 'features go to their own layers');
  check(by('radar_indicated').opts.style.className === 'radar-indicated' && by('outer').opts.style.className === 'radar-warnings', 'layer classes match style.css');
  check(e.status.textContent === 'Core 1,803 km² · NWS warnings 14,895 km²', 'status line lists the areas, core first: ' + e.status.textContent);
  const names = Object.keys(e.st.controls[0].overlays);
  check(JSON.stringify(names) === JSON.stringify(['Report circles', 'Report points', 'Core', 'Margin', 'Radar-indicated', 'NWS warnings']), 'layer control offers the six toggles');
  check(e.st.controls[0].overlays['Core'] === by('core') && e.st.controls[0].overlays['NWS warnings'] === by('outer'), 'toggles are wired to the right layers');
  check(e.st.controls[0].opts.collapsed === false, 'layer control starts open');
  check(e.st.groups.every(g => g.map), 'circles and points are on by default');

  // ---- changing the day replaces the layers
  e.picker.value = '2026-06-01';
  e.st.fetches.length = 0;
  e.picker.handlers.change();
  await tick();
  check(e.st.fetches.length === 1 && e.st.fetches[0] === '/map/radar.geojson?days=90&type=HAIL&day=2026-06-01', 'a change asks for the new day only');
  check(radarGroups.every(g => g.cleared >= 1), 'a change clears every radar layer first');
  check(by('core').features.length === 1, 'and does not pile features up');

  // ---- all days
  e = makeEnv({dataset: DS, pickerValue: 'all', search: '?days=30',
               bodies: {radar: RADAR([['core', 100]], ['2026-06-01', '2026-06-02', '2026-06-08'], ['2026-05-01'])}});
  await e.run();
  check(e.st.fetches.includes('/map/radar.geojson?days=30'), '"all" sends no day= at all');
  check(e.status.textContent === 'Core 100 km² (3 days) 1 day(s) were built with an older parameter set and are not shown.', 'status counts days and names the older-set days: ' + e.status.textContent);

  e = makeEnv({dataset: DS, pickerValue: 'all', search: '?days=30&day=2026-06-01', bodies: {radar: RADAR([['core', 100]])}});
  await e.run();
  check(e.st.fetches.includes('/map/radar.geojson?days=30'), '"all" drops a day= left in the page URL');

  // ---- colours by source
  e = makeEnv({dataset: DS, pickerValue: 'all', bodies: {radar: RADAR([]), points: {features: [
    POINT(-104.9, 39.7), {...POINT(-104.8, 39.6), properties: {...POINT(0, 0).properties, report_source_norm: 'SOMEONE NEW'}}]}}});
  await e.run();
  check(e.st.markers[0].opts.color === '#2ca02c' && e.st.circles[0].opts.color === '#2ca02c', 'a known source keeps its colour on point and circle');
  check(e.st.markers[1].opts.color === '#666' && e.st.circles[1].opts.color === '#666', 'an unknown source is grey');

  // ---- nothing to draw
  e = makeEnv({dataset: DS, pickerValue: 'all', bodies: {radar: RADAR([])}});
  await e.run();
  check(e.status.textContent === 'Nothing to draw for this selection.', 'an empty answer says so');

  // ---- failures
  e = makeEnv({dataset: DS, pickerValue: 'all', radarOk: false, bodies: {radar: {}}});
  await e.run();
  check(e.status.textContent === 'The radar layers could not be loaded.' && e.st.errors.some(s => s.includes('radar load failed')), 'an HTTP error is shown and logged');
  e = makeEnv({dataset: DS, pickerValue: 'all', radarReject: true});
  await e.run();
  check(e.status.textContent === 'The radar layers could not be loaded.', 'a network failure is shown');

  // ---- no radar days on the page: no picker
  e = makeEnv({dataset: DS, noPicker: true, bodies: {points: {features: [POINT(-104.9, 39.7)]}}});
  await e.run();
  check(!e.st.fetches.some(u => u.startsWith('/map/radar.geojson')), 'with no picker the radar is never requested');
  check(e.st.markers.length === 1 && e.st.circles.length === 1, 'and the points still draw');

  // ---- no radius from the server
  for (const bad of [undefined, '', 'abc', '0', '-5']) {
    e = makeEnv({dataset: bad === undefined ? {radarUrl: '/map/radar.geojson'} : {matchRadiusM: bad, radarUrl: '/map/radar.geojson'},
                 pickerValue: 'all', bodies: {points: {features: [POINT(-104.9, 39.7)]}, radar: RADAR([])}});
    await e.run();
    check(e.st.circles.length === 0 && e.st.markers.length === 1 && e.st.errors.some(s => s.includes('no match radius')),
          'radius ' + JSON.stringify(bad) + ': no circles guessed, error logged, points still drawn');
  }

  console.log(failures ? failures + ' FAILED' : 'all ok');
  process.exit(failures ? 1 : 0);
})();
