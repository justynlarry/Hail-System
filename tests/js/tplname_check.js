// Behaviour check for hailsys/web/static/tplname.js (item 294, slice 4).  Run by
// tests/test_js_checks.py in the test service.  It pins that the name shown is made with the same
// rules as templatestore.name_from_title (the Python test of that function uses the same examples).
const fs = require('fs');
const STATIC = process.argv[2] || '/s';
const src = fs.readFileSync(STATIC + '/tplname.js', 'utf8');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }

const handlers = {};
const field = {value: '', addEventListener(t, f) { handlers[t] = f; }};
const shown = {textContent: ''};
global.document = {getElementById: (id) => (id === 'friendly_title' ? field : null),
                   querySelector: (sel) => (sel === '[data-name-preview]' ? shown : null)};
new Function(src)();

check(shown.textContent === '…', 'with no title it shows …');
const cases = [
  ['Spring storm follow-up', 'spring_storm_follow_up'],
  ['  Hail!!  Alert 2026 ', 'hail_alert_2026'],
  ['Café crème', 'caf_cr_me'],
  ['!!!', '…'],
  ['A'.repeat(39) + ' b', 'a'.repeat(39)],
  ['x'.repeat(60), 'x'.repeat(40)],
];
cases.forEach(([title, want]) => {
  field.value = title; handlers.input();
  check(shown.textContent === want, JSON.stringify(title) + ' shows ' + want);
});

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
