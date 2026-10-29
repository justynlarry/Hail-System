// Behaviour check for hailsys/web/static/templates.js (item 203, piece 4.6): a fake page, the real
// script, and the events a browser would send.  There is no Node on the host, so run it in a throwaway container:
//   docker run --rm -v ./hailsys/web/static:/s:ro -v ./tests/js:/t:ro node:22-alpine node /t/templates_check.js
// Throwaway behaviour check: a fake page, the real templates.js, and the events the browser would send.
const fs = require('fs');
const STATIC = process.argv[2] || '/s';  // the static folder; tests/test_js_checks.py passes it, the docker run above uses /s
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }
const tick = (ms) => new Promise(r => setTimeout(r, ms));

function makePage(fetchImpl, unsaved = '0') {
  const handlers = {}, winHandlers = {}, ids = {};
  const mk = (id) => (ids[id] = {id, innerHTML: '', scrolled: false,
    scrollIntoView() { this.scrolled = true; }});
  const form = {id: 'editor', dataset: {unsaved}, getAttribute: (n) => ({action: '/templates/edit'}[n] || null),
    addEventListener: (t, f) => (handlers[t] = f), action: {tagName: 'BUTTON'} /* the clobbering trap */,
    classList: {classes: [], add(c) { this.classes.push(c); }}};
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
  return {target: {closest: (sel) => (sel === 'button[name="action"]' ? b : null)},
    preventDefault() { this.prevented = true; }, _b: b};
}

(async () => {
  const src = fs.readFileSync(STATIC + '/templates.js', 'utf8');  
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

  // 8. the toolbar: the two pure edits
  const tools = (() => {
    global.document = {getElementById: () => null}; global.window = {fetch: true};
    const m = {exports: {}}; new Function('module', src)(m); return m.exports;
  })();
  let r = tools.wrapText('hello world', 0, 5, '[b]', '[/b]');
  check(r.text === '[b]hello[/b] world' && r.start === 3 && r.end === 8, 'wraps the selection and keeps it selected');
  r = tools.wrapText('ab', 1, 1, '[b]', '[/b]');
  check(r.text === 'a[b][/b]b' && r.start === 4 && r.end === 4, 'with nothing selected, inserts a pair with the cursor between');
  r = tools.wrapText('hi', 2, 2, '[i]', '[/i]');
  check(r.text === 'hi[i][/i]' && r.start === 5, 'works at the end of the text');
  r = tools.insertText('Hi ', 3, 3, '{total}');
  check(r.text === 'Hi {total}' && r.start === 10 && r.end === 10, 'inserts a field and puts the cursor after it');
  r = tools.insertText('Hi XX!', 3, 5, '{total}');
  check(r.text === 'Hi {total}!', 'an inserted field replaces the selection');

  // 8b. the toolbar: clicks and menus on a fake page
  const bar = {getAttribute: (n) => (n === 'data-for' ? 'f1' : null)};
  const control = (attrs, extra = {}) => Object.assign({closest: (s) => (s === '.tpl-toolbar' ? bar : null),
    getAttribute: (n) => (n in attrs ? attrs[n] : null)}, extra);
  const clickOn = (ctl) => ({target: {closest: (s) => (s === '[data-wrap],[data-link]' ? ctl : null)},
    preventDefault() { this.prevented = true; }});
  function toolbarPage(a, b, value = 'hello world') {
    const t = makePage(() => reply({preview: 'P', problems: []}));
    t.field = t.ids.f1 = {value, selectionStart: a, selectionEnd: b, focused: 0, focus() { this.focused++; },
      setSelectionRange(x, y) { this.sel = [x, y]; }};
    new Function(src)(); return t;
  }
  let t = toolbarPage(0, 5);
  check(t.form.classList.classes.includes('js'), 'switches the toolbars on');
  ev = clickOn(control({'data-wrap': 'b'})); t.handlers.click(ev);
  check(t.field.value === '[b]hello[/b] world' && t.field.sel[0] === 3 && t.field.sel[1] === 8 && t.field.focused === 1, 'Bold wraps the selection and refocuses');
  check(!ev.prevented && t.sent.length === 0, 'a toolbar click is not a block action and sends nothing at once');
  leave = {preventDefault() { this.stopped = true; }}; t.winHandlers.beforeunload(leave);
  check(leave.stopped === true, 'a toolbar edit counts as unsaved');
  await tick(700); check(t.sent.length === 1 && t.sent[0].opts.body.data.action === 'preview', 'and refreshes the preview');
  t = toolbarPage(5, 5); t.handlers.click(clickOn(control({'data-wrap': 'i'})));
  check(t.field.value === 'hello[i][/i] world' && t.field.sel[0] === 8 && t.field.sel[1] === 8, 'Italic with nothing selected leaves the cursor between the tags');
  t = toolbarPage(0, 5); global.window.prompt = () => ' https://x.example ';
  t.handlers.click(clickOn(control({'data-link': ''})));
  check(t.field.value === '[link=https://x.example]hello[/link] world', 'Link uses the typed address');
  t = toolbarPage(0, 5); global.window.prompt = () => '   ';
  t.handlers.click(clickOn(control({'data-link': ''})));
  check(t.field.value === '[link={schedule_url}]hello[/link] world', 'an empty address links to the schedule page');
  t = toolbarPage(0, 5); global.window.prompt = () => null;
  t.handlers.click(clickOn(control({'data-link': ''})));
  check(t.field.value === 'hello world' && t.field.focused === 0, 'cancelling the prompt changes nothing');
  t = toolbarPage(0, 5); let menu = control({}, {dataset: {colour: ''}, value: 'red'});
  t.handlers.change({target: menu});
  check(t.field.value === '[color=red]hello[/color] world' && menu.value === '', 'the Colour menu wraps the selection and resets');
  t = toolbarPage(5, 5); menu = control({}, {dataset: {merge: ''}, value: '{total}'});
  t.handlers.change({target: menu});
  check(t.field.value === 'hello{total} world' && menu.value === '', 'the Insert field menu inserts the token and resets');
  t = toolbarPage(0, 5); menu = control({}, {dataset: {colour: ''}, value: ''});
  t.handlers.change({target: menu});
  check(t.field.value === 'hello world', 'choosing the menu label changes nothing');

  // 7. it does nothing on a page without the editor
  global.document = {getElementById: () => null}; global.window = {fetch: true};
  let threw = false; try { new Function(src)(); } catch (e) { threw = true; }
  check(!threw, 'harmless on a page with no editor');

  console.log(failures ? failures + ' FAILED' : 'ALL PASSED');
  process.exit(failures ? 1 : 0);
})();
