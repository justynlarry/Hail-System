// Behaviour check for hailsys/web/static/tplpresets.js (item 294, slice 3): a fake page, the real
// script, and the events a browser would send.  Run by tests/test_js_checks.py in the test service.
// What it pins: the menus are switched on; picking a step writes its number into the exact box,
// Custom leaves the box alone; typing a step's number picks it, any other number picks Custom and
// shows it; the handlers run in the capture phase (before templates.js reads the form).
const fs = require('fs');
const STATIC = process.argv[2] || '/s';
const src = fs.readFileSync(STATIC + '/tplpresets.js', 'utf8');
let failures = 0;
function check(cond, what) { if (!cond) { failures++; console.log('FAIL: ' + what); } else { console.log('ok:   ' + what); } }

function makeMenu(name, selectedValue) {
  const menu = {classList: {contains: (c) => c === 'tpl-preset'}, name: undefined,
                getAttribute: (n) => (n === 'data-preset-for' ? name : null), options: []};
  [['0', 'None'], ['8', 'Tight'], ['20', 'Normal'], ['32', 'Roomy'], ['', 'Custom']].forEach(([v, t]) => {
    const option = {value: v, textContent: t, _sel: false};
    Object.defineProperty(option, 'selected', {
      get() { return this._sel; },
      set(on) { if (on) menu.options.forEach(o => { o._sel = false; }); this._sel = !!on; }});
    menu.options.push(option);
  });
  (menu.options.find(o => o.value === selectedValue) || menu.options[4]).selected = true;
  Object.defineProperty(menu, 'value', {get() { const o = menu.options.find(x => x.selected); return o ? o.value : ''; }});
  return menu;
}

const exact = {name: 'b6_space_after', value: '26', classList: {contains: () => false}};
const menu = makeMenu('b6_space_after', '');
const handlers = {};
const form = {classes: [], classList: {add(c) { form.classes.push(c); }},
  addEventListener(t, f, capture) { handlers[t] = {f, capture}; },
  querySelector: (sel) => (sel === 'select.tpl-preset[data-preset-for="b6_space_after"]' ? menu : null)};
global.document = {getElementById: (id) => ({editor: form, b6_space_after: exact}[id] || null)};
new Function(src)();

check(form.classes.includes('presets-on'), 'switches the menus on');
check(handlers.change && handlers.change.capture === true && handlers.input && handlers.input.capture === true,
      'listens to change and input in the capture phase, before templates.js');

menu.options[1].selected = true;                      // Tight
handlers.change.f({target: menu});
check(exact.value === '8', 'picking Tight writes 8 into the exact box');
menu.options[4].selected = true;                      // Custom
handlers.change.f({target: menu});
check(exact.value === '8', 'picking Custom leaves the exact box as it is');

exact.value = '20';
handlers.input.f({target: exact});
check(menu.value === '20' && menu.options[4].textContent === 'Custom', 'typing 20 picks Normal');
exact.value = '26';
handlers.input.f({target: exact});
check(menu.value === '' && menu.options[4].textContent === 'Custom (26 px)', 'typing 26 picks Custom and shows the number');
exact.value = ' 32 ';
handlers.change.f({target: exact});
check(menu.value === '32', 'spaces around the number do not matter');

const other = {name: 'b6_text', value: 'hello', classList: {contains: () => false}};
handlers.input.f({target: other});
check(menu.value === '32', 'typing in another field leaves the menus alone');
handlers.input.f({target: {classList: {contains: () => false}}});
check(true, 'a target with no name is ignored without an error');

console.log(failures ? failures + ' FAILED' : 'all passed');
process.exit(failures ? 1 : 0);
