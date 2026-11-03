// Behaviour check for hailsys/web/static/blockfold.js (item 294, slice 2): a fake page, the real
// script, and the events a browser would send.  Run by tests/test_js_checks.py in the test service:
//   docker compose --profile test run --rm -T test
// What it pins: every block starts closed; a header click opens one and closes the rest, and a
// second click closes it; after a redraw the open block follows a Move, a new block opens after
// Add, a removed one is forgotten; #bN opens a block on load; a block with a server problem opens;
// a live problem only marks its block; Remove with data-confirm asks, and a No stops the post.
const fs = require('fs');
const STATIC = process.argv[2] || '/s';
const src = fs.readFileSync(STATIC + '/blockfold.js', 'utf8');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }

function classList() {
  const set = new Set();
  return {set, add(c) { set.add(c); }, contains(c) { return set.has(c); },
          toggle(c, on) { if (on === undefined) on = !set.has(c); if (on) set.add(c); else set.delete(c); return on; }};
}

// A block list like _editor_blocks.html draws:  `kinds` is one letter per block, F a block with
// settings (a fieldset with a toggle), R a one-line row; `problems` the indexes with a p.error.
function makePage({kinds, problems = [], hash = '', confirmAnswer = true}) {
  const st = {observer: null, confirms: [], captureClick: null, otherClicks: 0};
  const box = {children: [], n: kinds.length};
  function build(kindsNow, problemsNow) {
    box.n = kindsNow.length;
    box.folds = [];
    kindsNow.split('').forEach((k, i) => {
      if (k !== 'F') return;
      const el = {fold: String(i), classList: classList(), problem: problemsNow.includes(i), live: [],
                  getAttribute(n) { return n === 'data-fold' ? this.fold : null; }};
      el.toggle = {attrs: {}, setAttribute(n, v) { this.attrs[n] = v; },
                   closest(sel) { return sel === 'fieldset.tpl-block' ? el : (sel === '.tpl-toggle' ? this : null); }};
      el.querySelector = (sel) => {
        if (sel === 'p.error') return el.problem ? {} : null;
        if (sel === '.block-errors') return {children: el.live};
        if (sel === '.tpl-toggle') return el.toggle;
        return null;
      };
      box.folds.push(el);
    });
  }
  build(kinds, problems);
  box.querySelectorAll = (sel) => (sel === 'fieldset.tpl-block[data-fold]' ? box.folds : []);
  box.querySelector = (sel) => (sel === 'input[name="n_blocks"]' ? {value: String(box.n)} : null);
  box.contains = () => true;
  const form = {classList: classList(), addEventListener(t, f, capture) { if (t === 'click' && capture) st.captureClick = f; }};
  global.document = {getElementById: (id) => ({editor: form, blocks: box}[id] || null)};
  global.window = {MutationObserver: true, location: {hash}, confirm(m) { st.confirms.push(m); return confirmAnswer; }};
  global.MutationObserver = function (fn) { st.observer = fn; this.observe = () => {}; };
  new Function(src)();
  const openOnes = () => box.folds.filter(el => !el.classList.contains('is-closed')).map(el => Number(el.fold));
  const clickToggle = (i) => {
    const el = box.folds.find(f => f.fold === String(i));
    const ev = {target: {closest: (sel) => (sel === '.tpl-toggle' ? el.toggle : null)}, preventDefault() { this.prevented = true; }};
    st.captureClick(ev); return ev;
  };
  const clickAction = (value, extra = {}) => {
    const b = Object.assign({value, disabled: false, getAttribute: (n) => extra[n] || null}, extra);
    const ev = {target: {closest: (sel) => (sel === 'button[name="action"]' ? b : null)},
                preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }};
    st.captureClick(ev); return ev;
  };
  const redraw = (kindsNow, problemsNow = []) => { build(kindsNow, problemsNow); st.observer(); };
  return {st, box, form, openOnes, clickToggle, clickAction, redraw};
}

// Blocks:  0 R, 1 F, 2 F, 3 R, 4 F, 5 F (the footer).
const KINDS = 'RFFRFF';

let p = makePage({kinds: KINDS});
check(p.form.classList.contains('folding'), 'switches folding on');
check(p.openOnes().length === 0, 'every block starts closed');
check(p.box.folds.every(el => el.toggle.attrs['aria-expanded'] === 'false'), 'and says so to a screen reader');
let ev = p.clickToggle(2);
check(ev.prevented === true && JSON.stringify(p.openOnes()) === '[2]', 'a header click opens that block');
p.clickToggle(4);
check(JSON.stringify(p.openOnes()) === '[4]', 'opening another closes the first: one open at a time');
p.clickToggle(4);
check(p.openOnes().length === 0, 'a second click closes it');

p.clickToggle(2); p.clickAction('up:2'); p.redraw(KINDS.replace('FF', 'FF'));   // block 2 is now at 1
check(JSON.stringify(p.openOnes()) === '[1]', 'the open block follows a Move up');
p.clickAction('down:1'); p.redraw(KINDS);
check(JSON.stringify(p.openOnes()) === '[2]', 'and a Move down');
p.clickAction('up:2'); p.redraw(KINDS);
check(JSON.stringify(p.openOnes()) === '[1]', 'and up again');
p.clickAction('up:2'); p.redraw(KINDS);                                          // block 2 jumps over the open block 1
check(JSON.stringify(p.openOnes()) === '[2]', 'another block moved up past the open one pushes it down');
p.clickToggle(4); p.clickAction('up:4'); p.redraw('RFFFRF');
check(JSON.stringify(p.openOnes()) === '[3]', 'a block moved up over a row lands where it went');
p.clickAction('remove:1'); p.redraw('RFFRF');
check(JSON.stringify(p.openOnes()) === '[2]', 'removing a block above the open one shifts it up');
p.clickAction('remove:2'); p.redraw('RFRF');
check(p.openOnes().length === 0, 'removing the open block forgets it');
p.clickAction('add'); p.redraw('RFRFF');
check(JSON.stringify(p.openOnes()) === '[3]', 'a new block (just above the footer) opens');
p.clickAction('preview'); p.redraw('RFRFF');
check(JSON.stringify(p.openOnes()) === '[3]', 'a preview changes nothing');

p = makePage({kinds: KINDS, hash: '#b4'});
check(JSON.stringify(p.openOnes()) === '[4]', '#b4 in the address opens block 4 on load');
p = makePage({kinds: KINDS, hash: '#b3'});
check(p.openOnes().length === 0, '#b3, a one-line row, opens nothing');
p = makePage({kinds: KINDS, problems: [5], hash: '#b1'});
check(JSON.stringify(p.openOnes()) === '[5]', 'a block with a problem from the server opens, over the address');
check(p.box.folds.find(f => f.fold === '5').classList.contains('has-problem'), 'and is marked');

p = makePage({kinds: KINDS});
p.clickToggle(1);
p.box.folds.find(f => f.fold === '4').live.push({});   // templates.js puts a live problem under block 4
p.st.observer();
check(JSON.stringify(p.openOnes()) === '[1]', 'a live problem does not close the block being edited');
check(p.box.folds.find(f => f.fold === '4').classList.contains('has-problem'), 'it marks its own block instead');

p = makePage({kinds: KINDS, confirmAnswer: false});
ev = p.clickAction('remove:2', {'data-confirm': 'Remove this text block?'});
check(p.st.confirms.length === 1 && ev.prevented === true && ev.stopped === true, 'Remove asks, and No stops it before templates.js posts');
p = makePage({kinds: KINDS, confirmAnswer: true});
ev = p.clickAction('remove:2', {'data-confirm': 'Remove this text block?'});
check(ev.prevented !== true && ev.stopped !== true, 'Yes lets it through');
ev = p.clickAction('remove:0');
check(p.st.confirms.length === 1 && ev.prevented !== true, 'a row with nothing typed in it does not ask');
ev = p.clickAction('up:2', {disabled: true});
p.redraw(KINDS);
check(p.openOnes().length === 0, 'a disabled button is ignored');

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
