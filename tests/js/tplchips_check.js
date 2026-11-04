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
const st = {dispatched: [], writes: 0, textWrites: 0};

function makeInput(name, value, label) {
  const input = new Node_(1, 'INPUT');
  input.name = name; input.id = name; let v = value;
  Object.defineProperty(input, 'value', {get: () => v, set: (x) => { st.writes++; if (name === 'b6_text') st.textWrites++; v = x; }});
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
// A text block's field (multi-line) and its toolbar, as _editor_blocks.html draws them.
const textField = makeInput('b6_text', 'Hello {total} homes\nSecond line', '');
textField.tagName = 'TEXTAREA';
field.appendChild(textField);
const toolbar = new Node_(1, 'SPAN'); toolbar.attrs['data-for'] = 'b6_text';
const chipAttr = (node, kind) => { node.attrs['data-chips'] = kind; };
chipAttr(subject, 'line'); chipAttr(preheader, 'line'); chipAttr(textField, 'multi');
form.querySelectorAll = (sel) => (sel === '[data-chips]' ? [subject, preheader, textField] : []);
form.contains = (n) => true;
form.querySelector = (sel) => (sel === '.tpl-chip-menu[data-chips-for="preheader"]' ? menuSpot : null);
global.document = {
  getElementById: (id) => ({editor: form, 'chip-labels': {textContent: JSON.stringify(LABELS)}}[id] || null),
  createElement: (tag) => new Node_(1, tag.toUpperCase()),
  createTextNode: (t) => new Text_(t),
  querySelector: (sel) => (sel === 'label[for="preheader"]' ? label : null),
};
global.window = {getSelection: () => null, prompt: () => st.promptAnswer};
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

// ---- slice 9b:  a multi-line box and the toolbar ----
const tbox = boxOf(textField);
check(tbox.getAttribute('aria-multiline') === 'true', 'a text block\'s field becomes a multi-line box');
check(serialize(tbox, true) === 'Hello {total} homes\nSecond line' && st.textWrites === 0, 'showing its line breaks, without writing it');
const MULTI = ['one\ntwo', 'ends with a break\n', '\nstarts with one', 'a\n\n\nb', '[b]Item {largest_hail}[/b]\nItem two\n'];
MULTI.forEach((text) => {
  const box = document.createElement('div');
  render(box, text, true);
  check(serialize(box, true) === text, 'multi-line round trip: ' + JSON.stringify(text));
});
const fill = document.createElement('div');
render(fill, 'last line\n', true);
check(fill.childNodes[fill.childNodes.length - 1].getAttribute('data-filler') === '1',
      'a final line break gets a filler so the cursor can sit on the empty line, and it is not read back');
check(serialize(fill, false) === 'last line ', 'a one-line read turns line breaks into spaces');

const enter = tbox.fire('keydown', {key: 'Enter'});
check(enter.prevented === true && textField.value === 'Hello {total} homes\nSecond line\n',
      'Enter in a multi-line box adds a line break as text (no browser <div>)');
tbox.fire('paste', {clipboardData: {getData: () => 'pasted one\r\npasted two'}});
check(textField.value === 'Hello {total} homes\nSecond line\npasted one\npasted two',
      'a paste keeps its lines in a multi-line box (the filler stays last)');

// The toolbar acts on the box, and templates.js does not see it.
const button = (attrs) => { const b = new Node_(1, 'BUTTON'); Object.assign(b.attrs, attrs);
  b.closest = (sel) => (sel.includes('.tpl-toolbar') || sel.includes('[data-') ? (sel === '.tpl-toolbar' ? toolbar : b) : null); return b; };
const formClick = form.handlers.click[0], formChange = form.handlers.change[0], formDown = form.handlers.mousedown[0];
const clickEv = (target) => ({target, prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }});
let e = clickEv(button({'data-wrap': 'b'}));
formDown(e);
check(e.prevented === true, 'pressing a toolbar button keeps the cursor in the box');
e = clickEv(button({'data-wrap': 'b'}));
formClick(e);
check(e.stopped === true && textField.value.endsWith('pasted two[b][/b]'), 'Bold with no cursor adds the pair at the end, and templates.js is kept out');
st.promptAnswer = '';
formClick(clickEv(button({'data-link': ''})));
check(textField.value.endsWith('[link={schedule_url}][/link]'), 'Link with an empty address links to the schedule page');
st.promptAnswer = null;
const len = textField.value.length;
formClick(clickEv(button({'data-link': ''})));
check(textField.value.length === len, 'Cancel adds no link');
const colour = new Node_(1, 'SELECT'); colour.dataset = {colour: ''}; colour.value = 'navy';
colour.closest = (sel) => (sel === '.tpl-toolbar' ? toolbar : null);
e = clickEv(colour); formChange(e);
check(e.stopped === true && textField.value.endsWith('[color=navy][/color]') && colour.value === '', 'Colour wraps, and the menu resets');
const merge = new Node_(1, 'SELECT'); merge.dataset = {merge: ''}; merge.value = '{storm_dates_label}';
merge.closest = (sel) => (sel === '.tpl-toolbar' ? toolbar : null);
formChange(clickEv(merge));
check(textField.value.endsWith('{storm_dates_label}') && tbox.childNodes.some(n => n.getAttribute && n.getAttribute('data-token') === 'storm_dates_label'),
      'Insert field puts in a chip, stored as {storm_dates_label}');
const otherBar = new Node_(1, 'SPAN'); otherBar.attrs['data-for'] = 'b9_label';
const plainButton = button({'data-wrap': 'b'}); plainButton.closest = (sel) => (sel === '.tpl-toolbar' ? otherBar : plainButton);
e = clickEv(plainButton); formClick(e);
check(e.stopped === false, 'a toolbar for a field with no chip box is left to templates.js');

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
