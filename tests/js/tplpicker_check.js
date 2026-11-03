// Behaviour check for hailsys/web/static/tplpicker.js (item 294, slice 8): a fake page, the real
// script, and the events a browser would send.  Run by tests/test_js_checks.py in the test service.
// What it pins: the + buttons are switched on; one opens the picker above its block and a second
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
const blocksList = {inserted: [], before: [], insertBefore(node, ref) { this.inserted.push(node); this.before.push(ref); node.parentNode = this; },
                    removeChild(n) { n.parentNode = null; }};
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
// Each + sits in a block's line; the picker goes just above that block.
const makeInsert = (at) => {
  const block = {at, parentNode: blocksList};
  return {attrs: {'data-at': String(at), 'aria-expanded': 'false'}, block,
    closest: (sel) => (sel === '.tpl-block, .tpl-row' ? block : null),
    getAttribute(n) { return this.attrs[n]; }, setAttribute(n, v) { this.attrs[n] = v; }};
};
const inserts = [makeInsert(0), makeInsert(4)];
// The block list templates.js redraws:  contains() answers whether the picker is still in it.
const blocksBox = {contains: (n) => n.parentNode === blocksList};
global.document = {getElementById: (id) => ({editor: form, 'tpl-picker': picker, blocks: blocksBox}[id] || null),
                   addEventListener(t, f) { if (t === 'keydown') st.keydown = f; }};
global.window = {MutationObserver: true};
global.MutationObserver = function (fn) { st.redrawn = fn; this.observe = () => {}; };
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

check(classes.includes('picker-on'), 'switches the + buttons on');
let ev = on(inserts[1], 'insert');
st.capture(ev);
check(ev.prevented && picker.hidden === false && inserts[1].attrs['aria-expanded'] === 'true', 'an insert line opens the picker');
check(blocksList.inserted.includes(picker) && blocksList.before[blocksList.before.length - 1] === inserts[1].block && st.focused === 1,
      'just above its block, with the first choice focused');
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

// Open, then another block's arrow redraws the list and drops the picker with it.
st.capture(on(inserts[0], 'insert'));
check(picker.parentNode === blocksList && picker.hidden === false, 'open in the list again');
picker.parentNode = null;                             // the old list, and the picker in it, are gone
st.backInForm = false;
st.redrawn();
check(picker.hidden === true && st.backInForm === true && inserts[0].attrs['aria-expanded'] === 'false',
      'a redraw that drops the open picker closes it and brings it home');
st.capture(on(inserts[1], 'insert'));
st.capture(on(choice('heading'), 'choice'));
check(st.clicks.length === 2 && st.clicks[1].kind === 'heading', 'and it still works afterwards');

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
