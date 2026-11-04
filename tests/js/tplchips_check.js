// Behaviour check for hailsys/web/static/tplchips.js (item 294, slice 9a): a small working DOM, the
// real script, and the events a browser would send.  Run by tests/test_js_checks.py in the test service.
// What it pins: text -> chips -> text gives back every stored text exactly (the round trip the brief
// asks for); loading the page never writes the posted field; an edit writes it and sends one change;
// Enter does nothing in a one-line box; a paste comes in as one line of plain text; the menu adds a
// chip and is not itself an edit; an unknown {name} stays as typed; a browser's no-break space is read
// as a space.
const fs = require('fs');
const STATIC = process.argv[2] || '/s';
const src = fs.readFileSync(STATIC + '/tplchips.js', 'utf8');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }

// ---- a small DOM: enough nodes, attributes, events and selection for the script ----
class Node_ {
  constructor(type, tag) { this.nodeType = type; this.tagName = tag; this.childNodes = []; this.parentNode = null;
                           this.attrs = {}; this.handlers = {}; this.classes = new Set(); }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() { const s = this.parentNode && this.parentNode.childNodes; return s ? s[s.indexOf(this) + 1] || null : null; }
  appendChild(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.childNodes.push(n); return n; }
  removeChild(n) { this.childNodes.splice(this.childNodes.indexOf(n), 1); n.parentNode = null; return n; }
  insertBefore(n, ref) { if (!ref) return this.appendChild(n); if (n.parentNode) n.parentNode.removeChild(n);
                         n.parentNode = this; this.childNodes.splice(this.childNodes.indexOf(ref), 0, n); return n; }
  contains(n) { for (let c = n; c; c = c.parentNode) if (c === this) return true; return false; }
  set textContent(t) { this.childNodes = []; this.appendChild(new Text_(t)); }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(c => c.textContent).join(''); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  set className(v) { this.classes = new Set(v.split(' ')); }
  get classList() { const c = this.classes; return {add: (x) => c.add(x), contains: (x) => c.has(x)}; }
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); }
  fire(t, extra = {}) { const ev = Object.assign({type: t, target: this, prevented: false, stopped: false,
    preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }}, extra);
    (this.handlers[t] || []).forEach(f => f(ev)); return ev; }
  dispatchEvent(ev) { st.dispatched.push({node: this, type: ev.type, bubbles: ev.bubbles}); return true; }
}
class Text_ extends Node_ { constructor(t) { super(3); this.nodeValue = t; } }
const st = {dispatched: [], writes: 0};

function makeInput(name, value, label) {
  const input = new Node_(1, 'INPUT');
  input.name = name; input.id = name; let v = value;
  Object.defineProperty(input, 'value', {get: () => v, set: (x) => { st.writes++; v = x; }});
  input.attrs['aria-label'] = label;
  return input;
}
const field = new Node_(1, 'P');
const subject = makeInput('subject_text', 'Storms near {total} homes', 'Subject text');
const preheader = makeInput('preheader', '{largest_hail} hail, {nearest_miles} away, on {storm_dates_label}.', '');
const menuSpot = new Node_(1, 'SPAN');
field.appendChild(subject); field.appendChild(preheader); field.appendChild(menuSpot);
const label = new Node_(1, 'LABEL'); label.attrs.for = 'preheader'; label.textContent = 'Preview line';
const LABELS = {total: 'Number of houses', storm_dates_label: 'Storm date(s)', largest_hail: 'Largest hail size',
                nearest_miles: 'Distance to nearest report'};
const form = new Node_(1, 'FORM');
form.querySelectorAll = (sel) => (sel === 'input[data-chips="line"]' ? [subject, preheader] : []);
form.querySelector = (sel) => (sel === '.tpl-chip-menu[data-chips-for="preheader"]' ? menuSpot : null);
global.document = {
  getElementById: (id) => ({editor: form, 'chip-labels': {textContent: JSON.stringify(LABELS)}}[id] || null),
  createElement: (tag) => new Node_(1, tag.toUpperCase()),
  createTextNode: (t) => new Text_(t),
  querySelector: (sel) => (sel === 'label[for="preheader"]' ? label : null),
};
global.window = {getSelection: () => null};
global.Event = function (type, opts) { this.type = type; this.bubbles = !!(opts && opts.bubbles); };
new Function(src)();

const {render, serialize} = window.tplChips;
const boxOf = (input) => input.nextSibling;

// ---- the round trip ----
const STORED = [
  '', 'plain words', '{total}', 'Storms near {total} homes', '{largest_hail}{nearest_miles}',
  '{largest_hail} hail, {nearest_miles} away, on {storm_dates_label}.', '  two  spaces  ', 'Café ☂ 1¾" hail',
  'unknown {zip_code} stays', '{Total} is not a field', 'braces { alone } and {}', '[b]bold {total}[/b]',
  '{total}{total}{total}', 'ends with a field {storm_dates_label}',
];
STORED.forEach((text) => {
  const box = document.createElement('div');
  render(box, text);
  check(serialize(box) === text, 'round trip: ' + JSON.stringify(text));
});
const box1 = document.createElement('div');
render(box1, 'a {largest_hail} b {zip} c');
check(box1.childNodes.filter(n => n.getAttribute && n.getAttribute('data-token')).length === 1, 'only a real merge field becomes a chip');
check(box1.childNodes[1].textContent === 'Largest hail size' && box1.childNodes[1].getAttribute('contenteditable') === 'false',
      'a chip shows its label and cannot be edited');

// ---- loading the page ----
check(st.writes === 0, 'loading the page writes neither posted field (an unedited save posts what it loaded)');
check(form.classList.contains('chips-on'), 'switches chips on');
check(subject.hidden === true && boxOf(subject).getAttribute('contenteditable') === 'true', 'the field is hidden and an editable box follows it');
check(boxOf(subject).getAttribute('aria-label') === 'Subject text', 'the box keeps the field\'s name for a screen reader');
check(label.getAttribute('for') === 'preheader-chips', 'the label now points at the box');
check(serialize(boxOf(preheader)) === preheader.value, 'the box shows exactly the stored text');

// ---- editing ----
const pbox = boxOf(preheader);
pbox.appendChild(new Text_(' Soon now'));
pbox.fire('input');
check(preheader.value === '{largest_hail} hail, {nearest_miles} away, on {storm_dates_label}. Soon now',
      'an edit writes the field, a no-break space read as a space');
const sent = st.dispatched.filter(d => d.node === preheader && d.type === 'change');
check(sent.length === 1 && sent[0].bubbles === true, 'and sends one change, so the preview refreshes');
check(pbox.fire('keydown', {key: 'Enter'}).prevented === true, 'Enter does nothing in a one-line box');
check(pbox.fire('keydown', {key: 'a'}).prevented === false, 'other keys type');
pbox.fire('paste', {clipboardData: {getData: (t) => (t === 'text/plain' ? 'line one\r\n  line two\n' : '<b>x</b>')}});
check(preheader.value.endsWith('Soon nowline one line two '), 'a paste comes in as one line of plain text (at the end, with no cursor)');
check(pbox.fire('drop').prevented === true, 'dropping dragged content is refused');

// ---- the menu ----
const menu = menuSpot.childNodes[0];
check(menu && menu.tagName === 'SELECT' && !menu.name, 'the preview line has a storm-detail menu with no name');
check(menu.childNodes.length === 5 && menu.childNodes[3].textContent === 'Largest hail size', 'it offers every field by its label');
menu.value = 'nearest_miles';
const before = st.dispatched.length;
const ev = menu.fire('change');
check(ev.stopped === true, 'the menu itself is not passed on as an edit');
check(preheader.value.endsWith('{nearest_miles}') && menu.value === '', 'it adds the chip (at the end, with no cursor) and resets');
check(st.dispatched.length === before + 1, 'and the field\'s own change goes out');
menu.value = 'not_a_field';
menu.fire('change');
check(preheader.value.endsWith('{nearest_miles}'), 'an unknown choice adds nothing');

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
