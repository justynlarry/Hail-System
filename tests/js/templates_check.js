// Behaviour check for hailsys/web/static/templates.js (item 203, piece 4.6): a fake page, the real
// script, and the events a browser would send.  There is no Node on the host, so run it in a throwaway container:
//   docker run --rm -v ./hailsys/web/static:/s:ro -v ./tests/js:/t:ro node:22-alpine node /t/templates_check.js
// Throwaway behaviour check: a fake page, the real templates.js, and the events the browser would send.
const fs = require('fs');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }
const tick = (ms) => new Promise(r => setTimeout(r, ms));

function makePage(fetchImpl, unsaved = '0') {
  const handlers = {}, winHandlers = {}, ids = {};
  const mk = (id) => (ids[id] = {id, innerHTML: '', scrolled: false,
    scrollIntoView() { this.scrolled = true; }});
  const form = {id: 'editor', dataset: {unsaved}, getAttribute: (n) => ({action: '/templates/edit'}[n] || null),
    addEventListener: (t, f) => (handlers[t] = f), action: {tagName: 'BUTTON'} /* the clobbering trap */};
  const errorSpots = {0: {children: [], appendChild(p) { this.children.push(p); }}};
  const blocks = mk('blocks'); blocks.errorsRemoved = 0;
  blocks.querySelectorAll = (sel) => { check(sel === 'p.error', 'clears p.error before redrawing'); return [{remove() { blocks.errorsRemoved++; }}]; };
  blocks.querySelector = (sel) => { const m = /^\.block-errors\[data-block="(\d+)"\]$/.exec(sel); check(!!m, 'looks up ' + sel); return m ? errorSpots[m[1]] || null : null; };
  const preview = mk('preview'); mk('b3'); ids.editor = form;
  const sent = [];
  global.document = {getElementById: (id) => ids[id] || null,
    createElement: () => ({className: '', textContent: ''})};
  global.FormData = function (f) { this.f = f; this.data = {csrf_token: 'T', title: 'x'}; this.set = (k, v) => (this.data[k] = v); };
  global.window = {fetch: true, addEventListener: (t, f) => (winHandlers[t] = f)};
  global.fetch = (url, opts) => { sent.push({url, opts}); return fetchImpl(url, opts); };
  global.setTimeout = setTimeout;
  return {form, handlers, winHandlers, blocks, preview, sent, errorSpots, ids};
}
const reply = (body, ok = true) => Promise.resolve({ok, json: () => Promise.resolve(body)});
function button(value, extra = {}) {
  const b = Object.assign({value, disabled: false, clicked: 0,
    getAttribute: (n) => (n === 'formaction' ? '/templates/edit#b3' : null),
    click() { this.clicked++; }}, extra);
  return {target: {closest: (sel) => { check(sel === 'button[name="action"]', 'finds the clicked button'); return b; }},
    preventDefault() { this.prevented = true; }, _b: b};
}

(async () => {
  const src = fs.readFileSync('/s/templates.js', 'utf8');

  // 1. a structural click posts the form with fetch and swaps the parts
  let p = makePage(() => reply({blocks: '<b>BLOCKS</b>', preview: '<i>PREVIEW</i>', problems: []}));
  new Function(src)();
  let ev = button('up:3');
  p.handlers.click(ev);
  await tick(20);
  check(ev.prevented === true, 'a block button is intercepted');
  check(p.sent.length === 1 && p.sent[0].url === '/templates/edit', 'posts to the form address (not form.action)');
  check(p.sent[0].opts.method === 'POST' && p.sent[0].opts.headers['X-Requested-With'] === 'fetch', 'POST with the fetch header');
  check(p.sent[0].opts.body.data.action === 'up:3' && p.sent[0].opts.body.data.csrf_token === 'T', 'sends the action and the token');
  check(p.blocks.innerHTML === '<b>BLOCKS</b>' && p.preview.innerHTML === '<i>PREVIEW</i>', 'swaps in the blocks and the preview');
  check(p.ids.b3.scrolled === true, 'scrolls to the block named in the button anchor');

  // 2. save is never intercepted; disabled buttons are ignored
  ev = button('save'); p.handlers.click(ev); check(!ev.prevented, 'save is a normal submit');
  ev = button('up:0', {disabled: true}); p.handlers.click(ev); check(!ev.prevented, 'a disabled button is ignored');

  // 3. a failing request falls back to a normal submit of the same button
  p = makePage(() => reply({}, false)); new Function(src)();
  ev = button('add'); p.handlers.click(ev); await tick(20);
  check(ev._b.clicked === 1, 'a failed request clicks the button again');
  const again = button('add'); again._b.click = () => {}; p.handlers.click(again);
  check(again.prevented !== true, 'and that second click is let through (not intercepted again)');

  // 4. a field change refreshes the preview after a pause and shows problems under their block
  p = makePage(() => reply({blocks: 'NO', preview: '<i>P2</i>', problems: [[1, 'bad one'], [null, 'general'], [9, 'nowhere']]}));
  new Function(src)();
  p.handlers.change({target: {id: 'b0_text'}});
  await tick(100); check(p.sent.length === 0, 'waits before refreshing');
  await tick(600);
  check(p.sent.length === 1 && p.sent[0].opts.body.data.action === 'preview', 'refreshes with the preview action');
  check(p.preview.innerHTML === '<i>P2</i>', 'updates the preview');
  check(p.blocks.innerHTML === '', 'leaves the block fields alone');
  check(p.errorSpots[0].children.length === 1 && p.errorSpots[0].children[0].textContent === 'bad one', 'puts the problem under block 1');
  check(p.blocks.errorsRemoved === 1, 'removes the old error lines');

  // 5. rapid changes make one request; add_kind changes make none
  p = makePage(() => reply({preview: 'x', problems: []})); new Function(src)();
  for (let i = 0; i < 4; i++) { p.handlers.change({target: {id: 'title'}}); await tick(50); }
  await tick(700); check(p.sent.length === 1, 'four quick changes send one preview');
  p.handlers.change({target: {id: 'add_kind'}}); await tick(700); check(p.sent.length === 1, 'choosing a block kind sends nothing');

  // 6. unsaved edits warn on leaving; saving clears the warning
  p = makePage(() => reply({})); new Function(src)();
  let leave = {preventDefault() { this.stopped = true; }};
  p.winHandlers.beforeunload(leave); check(!leave.stopped, 'no warning before any edit');
  p.handlers.input({}); leave = {preventDefault() { this.stopped = true; }};
  p.winHandlers.beforeunload(leave); check(leave.stopped === true && leave.returnValue === '', 'warns after an edit');
  p.handlers.submit({submitter: {value: 'save'}}); leave = {preventDefault() { this.stopped = true; }};
  p.winHandlers.beforeunload(leave); check(!leave.stopped, 'saving clears the warning');
  p.handlers.change({target: {id: 'situation'}}); leave = {preventDefault() { this.stopped = true; }};
  p.winHandlers.beforeunload(leave); check(!leave.stopped, 'changing the situation is not an edit');

  // 6b. a structural action marks the page as edited, a plain preview does not
  p = makePage(() => reply({blocks: 'B', preview: 'P', problems: []})); new Function(src)();
  p.handlers.click(button('preview')); await tick(20);
  leave = {preventDefault() { this.stopped = true; }}; p.winHandlers.beforeunload(leave);
  check(!leave.stopped, 'showing the preview is not an edit');
  p.handlers.click(button('remove:2')); await tick(20);
  leave = {preventDefault() { this.stopped = true; }}; p.winHandlers.beforeunload(leave);
  check(leave.stopped === true, 'removing a block is an edit');

  // 6c. a slow reply to an older request does not overwrite a newer one
  p = makePage((url, opts) => opts.body.data.action === 'preview'
    ? new Promise(r => setTimeout(() => r({ok: true, json: () => Promise.resolve({preview: 'OLD', problems: []})}), 300))
    : reply({blocks: 'NEW-BLOCKS', preview: 'NEW', problems: []}));
  new Function(src)();
  p.handlers.change({target: {id: 'title'}});
  await tick(550);                                      // the slow preview request is now in flight
  p.handlers.click(button('add')); await tick(30);      // a newer request finishes first
  check(p.preview.innerHTML === 'NEW', 'the newer reply is shown');
  await tick(400);
  check(p.preview.innerHTML === 'NEW', 'the older, slower reply is ignored');

  // 6d. a second click while a request is running does nothing
  p = makePage(() => new Promise(r => setTimeout(() => r({ok: true, json: () => Promise.resolve({blocks: 'B', preview: 'P', problems: []})}), 100)));
  new Function(src)();
  p.handlers.click(button('add')); p.handlers.click(button('add')); p.handlers.click(button('add'));
  await tick(250);
  check(p.sent.length === 1, 'three quick clicks send one request');
  p.handlers.click(button('add')); await tick(250);
  check(p.sent.length === 2, 'and the next click works once the first is done');

  // 6e. a page that comes back with an unsaved draft starts out unsaved; only Save clears it
  p = makePage(() => reply({}), '1'); new Function(src)();
  leave = {preventDefault() { this.stopped = true; }}; p.winHandlers.beforeunload(leave);
  check(leave.stopped === true, 'a page returned with your draft warns before any new edit');
  p.handlers.submit({submitter: {value: '1', name: 'test'}});
  leave = {preventDefault() { this.stopped = true; }}; p.winHandlers.beforeunload(leave);
  check(leave.stopped === true, 'a test send submit does not clear the warning');
  p.handlers.submit({});
  leave = {preventDefault() { this.stopped = true; }}; p.winHandlers.beforeunload(leave);
  check(leave.stopped === true, 'a submit with no known submitter does not clear it either');
  p.handlers.submit({submitter: {value: 'save'}});
  leave = {preventDefault() { this.stopped = true; }}; p.winHandlers.beforeunload(leave);
  check(!leave.stopped, 'Save clears it');
  p = makePage(() => reply({}), '0'); new Function(src)();
  leave = {preventDefault() { this.stopped = true; }}; p.winHandlers.beforeunload(leave);
  check(!leave.stopped, 'a fresh page does not warn');

  // 7. it does nothing on a page without the editor
  global.document = {getElementById: () => null}; global.window = {fetch: true};
  let threw = false; try { new Function(src)(); } catch (e) { threw = true; }
  check(!threw, 'harmless on a page with no editor');

  console.log(failures ? failures + ' FAILED' : 'ALL PASSED');
  process.exit(failures ? 1 : 0);
})();
