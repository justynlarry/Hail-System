// Behaviour check for hailsys/web/static/tplpreview.js (item 294, slice 7): a fake page with a
// fake preview frame, the real script.  Run by tests/test_js_checks.py in the test service.
// What it pins: a frame is hooked once it has loaded, and again after each refresh replaces it;
// a click opens the block it lands in (by clicking that block's header, only if it is closed),
// brings it into view, flashes a one-line row, and never follows a link; a click outside any
// block does nothing; hovering outlines only the block's outer rows; a frame it cannot read is
// left alone.
const fs = require('fs');
const STATIC = process.argv[2] || '/s';
const src = fs.readFileSync(STATIC + '/tplpreview.js', 'utf8');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }

function classList() {
  const set = new Set();
  return {set, add(c) { set.add(c); }, remove(c) { set.delete(c); }, contains(c) { return set.has(c); }};
}

// The frame's document:  rows with data-block, the outer row of block 2 holding an inner row of block 2.
function makeFrameDoc() {
  const handlers = {}, appended = [];
  const mk = (n, parent) => {
    const el = {n, parentElement: parent, nodeType: 1, classList: classList(),
      getAttribute: (a) => (a === 'data-block' && n !== null ? String(n) : null),
      closest(sel) {
        let cur = this;
        while (cur) {
          if (cur.n !== null && (sel === '[data-block]' || sel === '[data-block="' + cur.n + '"]')) return cur;
          cur = cur.parentElement;
        }
        return null;
      }};
    return el;
  };
  const top = mk(null, null);
  const row0 = mk(0, top), row2 = mk(2, top), inner2 = mk(2, row2), outside = mk(null, top);
  const rows = [row0, row2, inner2];
  const doc = {body: {appendChild: (s) => appended.push(s)}, head: {appendChild: (s) => appended.push(s)}, readyState: 'complete',
    createElement: () => ({textContent: ''}),
    addEventListener(t, f) { handlers[t] = f; },
    querySelectorAll(sel) {
      if (sel === '.tpl-hover') return rows.filter(r => r.classList.contains('tpl-hover'));
      if (sel === '[data-block]') return doc.empty ? [] : rows;
      const m = /^tr\[data-block="(\d+)"\]$/.exec(sel);
      return m ? rows.filter(r => String(r.n) === m[1]) : [];
    }};
  return {doc, handlers, appended, row0, row2, inner2, outside};
}

function makePage({frameReadable = true} = {}) {
  const st = {observer: null, toggles: 0, scrolled: [], timers: []};
  let frameDoc = makeFrameDoc();
  const makeFrame = (fd) => {
    const f = {dataset: {}, listeners: {}, addEventListener(t, fn) { this.listeners[t] = fn; }};
    Object.defineProperty(f, 'contentDocument', {configurable: true,
      get() { if (!frameReadable) throw new Error('cross-origin'); return fd.doc; }});
    return f;
  };
  let frame = makeFrame(frameDoc);
  const box = {querySelector: (sel) => (sel === 'iframe.tpl-frame' ? frame : null)};
  const blockFields = {cls: classList(), toggle: {click() { st.toggles++; blockFields.cls.delete ? 0 : 0; blockFields.cls.remove('is-closed'); }},
                       scrollIntoView(o) { st.scrolled.push(['b2', o]); }};
  blockFields.classList = blockFields.cls;
  blockFields.cls.add('tpl-block'); blockFields.cls.add('is-closed');
  blockFields.querySelector = (sel) => (sel === '.tpl-toggle' ? blockFields.toggle : null);
  const row0 = {classList: classList(), scrollIntoView(o) { st.scrolled.push(['b0', o]); }};
  global.document = {getElementById: (id) => ({preview: box, b2: blockFields, b0: row0}[id] || null)};
  global.window = {MutationObserver: true};
  global.MutationObserver = function (fn) { st.observer = fn; this.observe = () => {}; };
  global.setTimeout = (fn, ms) => { st.timers.push(fn); };
  new Function(src)();
  return {st, get frameDoc() { return frameDoc; }, get frame() { return frame; }, blockFields, row0,
          refresh() { frameDoc = makeFrameDoc(); frame = makeFrame(frameDoc); st.observer(); }};
}

const click = (target) => ({target, preventDefault() { this.prevented = true; }});

let p = makePage();
check(p.frame.dataset.hooked === '1' && p.frameDoc.appended.length === 1, 'a loaded frame is hooked, with its hover style');
let ev = click(p.frameDoc.inner2);
p.frameDoc.handlers.click(ev);
check(ev.prevented === true, 'a click in the preview never follows a link');
check(p.st.toggles === 1 && !p.blockFields.classList.contains('is-closed'), 'a click on a row inside block 2 opens block 2 by its header');
check(p.st.scrolled.some(s => s[0] === 'b2'), 'and brings it into view');
p.frameDoc.handlers.click(click(p.frameDoc.row2));
check(p.st.toggles === 1, 'a click on an open block does not close it');
p.frameDoc.handlers.click(click(p.frameDoc.row0));
check(p.row0.classList.contains('tpl-flash') && p.st.scrolled.some(s => s[0] === 'b0'), 'a one-line block is brought into view and flashed');
p.st.timers.forEach(fn => fn());
check(!p.row0.classList.contains('tpl-flash'), 'and the flash goes again');
const before = p.st.scrolled.length;
ev = click(p.frameDoc.outside);
p.frameDoc.handlers.click(ev);
check(ev.prevented === true && p.st.scrolled.length === before, 'a click outside any block does nothing (and follows no link)');

p.frameDoc.handlers.mouseover({target: p.frameDoc.inner2});
check(p.frameDoc.row2.classList.contains('tpl-hover') && !p.frameDoc.inner2.classList.contains('tpl-hover'),
      'hovering outlines the outer rows of the block only');
p.frameDoc.handlers.mouseover({target: p.frameDoc.row0});
check(p.frameDoc.row0.classList.contains('tpl-hover') && !p.frameDoc.row2.classList.contains('tpl-hover'), 'moving to another block moves the outline');
p.frameDoc.handlers.mouseleave({});
check(!p.frameDoc.row0.classList.contains('tpl-hover'), 'leaving the preview clears it');

p.refresh();
check(p.frame.dataset.hooked === '1', 'a refreshed preview (a new frame) is hooked too');

// A srcdoc frame first holds an empty about:blank document that already reads 'complete'.
p = makePage();
const blank = makeFrameDoc(); blank.doc.empty = true;
p.refresh();                                          // a new frame ...
const fresh = p.frame;
Object.defineProperty(fresh, 'contentDocument', {get: () => blank.doc, configurable: true});
p.st.observer();                                      // ... seen while it still holds about:blank
check(blank.appended.length === 0, 'the empty about:blank document is not hooked');
const email = makeFrameDoc();
Object.defineProperty(fresh, 'contentDocument', {get: () => email.doc, configurable: true});
fresh.listeners.load();                               // then the email loads
check(email.appended.length === 1, 'the email document that follows is hooked');
fresh.listeners.load();
check(email.appended.length === 1, 'and only once');

p = makePage({frameReadable: false});
check(p.frame.dataset.hooked === undefined, 'a frame it cannot read is left alone, without an error');

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
