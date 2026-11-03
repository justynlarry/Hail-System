// Behaviour check for hailsys/web/static/tplpicker.js (item 294, slice 8): a fake page, the real
// script, and the events a browser would send.  Run by tests/test_js_checks.py in the test service.
// What it pins: the insert lines are switched on; one opens the picker under itself and a second
// click, Escape or a click elsewhere closes it; choosing a kind sets add_kind and add_at and clicks
// the form's own Add button, then clears add_at and puts the picker back outside the block list.
const fs = require('fs');
const STATIC = process.argv[2] || '/s';
const src = fs.readFileSync(STATIC + '/tplpicker.js', 'utf8');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }

const st = {clicks: [], focused: 0, keydown: null, capture: null};
const classes = [];
const addAt = {value: ''};
const addKind = {value: 'text'};
const addButton = {click() { st.clicks.push({kind: addKind.value, at: addAt.value}); }};
const blocksList = {inserted: [], insertBefore(node, ref) { this.inserted.push(node); node.parentNode = this; }, removeChild(n) { n.parentNode = null; }};
const form = {
  classList: {add(c) { classes.push(c); }},
  addEventListener(t, f, capture) { if (t === 'click' && capture) st.capture = f; },
  appendChild(n) { n.parentNode = form; st.backInForm = true; },
  removeChild(n) { n.parentNode = null; },
  querySelector(sel) {
    if (sel === 'select[name="add_kind"]') return addKind;
    if (sel === 'input[name="add_at"]') return addAt;
    if (sel === 'button[name="action"][value="add"]') return addButton;
    if (sel === '.tpl-insert[aria-expanded="true"]') return inserts.find(i => i.attrs['aria-expanded'] === 'true') || null;
    return null;
  }};
const choice = (kind) => ({attrs: {'data-kind': kind}, getAttribute(n) { return this.attrs[n]; }, focus() { st.focused++; }});
const picker = {hidden: true, parentNode: form, querySelector: (sel) => (sel === 'button[data-kind]' ? choice('text') : null)};
picker.parentNode = form;
const makeInsert = (at) => ({attrs: {'data-at': String(at), 'aria-expanded': 'false'}, parentNode: blocksList, nextSibling: {},
  getAttribute(n) { return this.attrs[n]; }, setAttribute(n, v) { this.attrs[n] = v; }});
const inserts = [makeInsert(0), makeInsert(4)];
global.document = {getElementById: (id) => ({editor: form, 'tpl-picker': picker}[id] || null),
                   addEventListener(t, f) { if (t === 'keydown') st.keydown = f; }};
new Function(src)();

// An event whose target is inside `el`, matched by the selectors the script asks for.
function on(el, kind) {
  return {target: {closest(sel) {
    if (kind === 'insert' && sel === '.tpl-insert') return el;
    if (kind === 'choice' && sel === '#tpl-picker button[data-kind]') return el;
    if (kind === 'choice' && sel === '#tpl-picker') return picker;
    if (kind === 'inside' && sel === '#tpl-picker') return picker;
    return null;
  }}, preventDefault() { this.prevented = true; }};
}

check(classes.includes('picker-on'), 'switches the insert lines on');
let ev = on(inserts[1], 'insert');
st.capture(ev);
check(ev.prevented && picker.hidden === false && inserts[1].attrs['aria-expanded'] === 'true', 'an insert line opens the picker');
check(blocksList.inserted.includes(picker) && st.focused === 1, 'under itself, with the first choice focused');
st.capture(on(inserts[1], 'insert'));
check(picker.hidden === true && inserts[1].attrs['aria-expanded'] === 'false', 'a second click closes it');

st.capture(on(inserts[1], 'insert'));
st.keydown({key: 'Escape'});
check(picker.hidden === true, 'Escape closes it');
st.capture(on(inserts[1], 'insert'));
st.capture(on({}, 'inside'));
check(picker.hidden === false, 'a click inside the picker leaves it open');
st.capture({target: {closest: () => null}});
check(picker.hidden === true, 'a click elsewhere closes it');

st.capture(on(inserts[0], 'insert'));
st.capture(on(inserts[1], 'insert'));
check(inserts[0].attrs['aria-expanded'] === 'false' && inserts[1].attrs['aria-expanded'] === 'true', 'opening another moves it there');
ev = on(choice('band'), 'choice');
st.capture(ev);
check(ev.prevented && st.clicks.length === 1 && st.clicks[0].kind === 'band' && st.clicks[0].at === '4',
      'choosing a kind adds it at that place through the Add button');
check(addAt.value === '' && picker.hidden === true && st.backInForm === true,
      'then clears the place and puts the picker back outside the block list');

st.capture(on(choice('text'), 'choice'));
check(st.clicks.length === 1, 'a choice with no open place adds nothing');

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
