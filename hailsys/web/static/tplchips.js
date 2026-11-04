// Merge fields as labelled chips in the template editor (parking-lot item 294, slice 9a).
//
// An <input data-chips="line"> (the subject and the preview line) is shown as an editable box in
// which {largest_hail} reads as a chip, "Largest hail size".  The input stays in the form, hidden,
// and is the field that is posted:  it is rewritten only when you edit the box, so a template opened
// and saved with no edits posts exactly what it loaded.  A chip cannot be half-edited; Backspace
// removes it whole.  Pasting brings in plain text only, Enter does nothing in a one-line box, and
// dropping dragged content is refused (it could bring formatting).  The menu beside each box
// ("Insert storm detail") puts a chip where the cursor was, or at the end.  The labels come from
// blockform.CHIP_LABELS, in <script id="chip-labels">.  A {name} that is not a merge field is left
// as typed, for the server to point out.  Without this script the plain boxes are used, as before.
(function () {
  var form = document.getElementById('editor');
  var source = document.getElementById('chip-labels');
  if (!form || !source) { return; }
  var LABELS;
  try { LABELS = JSON.parse(source.textContent); } catch (e) { return; }
  var TOKEN = /\{([a-z_]+)\}/g;
  var has = function (name) { return Object.prototype.hasOwnProperty.call(LABELS, name); };

  function chip(name) {
    var el = document.createElement('span');
    el.className = 'tpl-chip';
    el.setAttribute('contenteditable', 'false');
    el.setAttribute('data-token', name);
    el.textContent = LABELS[name];
    return el;
  }

  // Text -> the box's contents:  text nodes, and a chip for each known {name}.
  function render(box, text) {
    while (box.firstChild) { box.removeChild(box.firstChild); }
    var last = 0, m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(text)) !== null) {
      if (!has(m[1])) { continue; }
      if (m.index > last) { box.appendChild(document.createTextNode(text.slice(last, m.index))); }
      box.appendChild(chip(m[1]));
      last = m.index + m[0].length;
    }
    if (last < text.length) { box.appendChild(document.createTextNode(text.slice(last))); }
  }

  // The box's contents -> text:  a chip back to {name}; anything a browser adds (a <br>, a wrapping
  // element) is read through.  A one-line box has no line breaks.  A no-break space a browser puts in
  // for a typed space becomes a plain space.
  function serialize(node) {
    var out = '';
    for (var i = 0; i < node.childNodes.length; i++) {
      var n = node.childNodes[i];
      if (n.nodeType === 3) {
        out += n.nodeValue.replace(/ /g, ' ');
      } else if (n.nodeType === 1) {
        var token = n.getAttribute('data-token');
        if (token) { out += '{' + token + '}'; } else if (n.tagName !== 'BR') { out += serialize(n); }
      }
    }
    return out.replace(/[\r\n]+/g, ' ');
  }

  function within(box, node) { return !!node && (node === box || box.contains(node)); }

  // Put a node where the cursor is in this box (replacing a selection), or at the end.
  function place(box, node, saved) {
    var sel = window.getSelection ? window.getSelection() : null;
    var range = null;
    if (sel && sel.rangeCount && within(box, sel.getRangeAt(0).startContainer)) {
      range = sel.getRangeAt(0);
    } else if (saved && within(box, saved.startContainer)) {
      range = saved;
    }
    if (!range) { box.appendChild(node); return; }
    range.deleteContents();
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    if (sel) { sel.removeAllRanges(); sel.addRange(range); }
  }

  function enhance(input) {
    var box = document.createElement('div');
    box.className = 'tpl-chipbox';
    box.id = (input.id || input.name) + '-chips';
    box.setAttribute('contenteditable', 'true');
    box.setAttribute('role', 'textbox');
    box.setAttribute('aria-label', input.getAttribute('aria-label') ||
                     (document.querySelector('label[for="' + input.id + '"]') || {}).textContent || input.name);
    box.setAttribute('spellcheck', 'true');
    render(box, input.value);
    input.parentNode.insertBefore(box, input.nextSibling);
    input.hidden = true;
    var label = input.id ? document.querySelector('label[for="' + input.id + '"]') : null;
    if (label) { label.setAttribute('for', box.id); }
    var saved = null;      // the cursor, kept for the menu (choosing from it moves focus away)

    function remember() {
      var sel = window.getSelection ? window.getSelection() : null;
      if (sel && sel.rangeCount && within(box, sel.getRangeAt(0).startContainer)) { saved = sel.getRangeAt(0).cloneRange(); }
    }
    function sync() {
      input.value = serialize(box);
      // templates.js refreshes the preview on a form change:  the box itself sends none.
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    box.addEventListener('input', function () { remember(); sync(); });
    box.addEventListener('keyup', remember);
    box.addEventListener('mouseup', remember);
    box.addEventListener('keydown', function (event) { if (event.key === 'Enter') { event.preventDefault(); } });
    box.addEventListener('paste', function (event) {
      event.preventDefault();
      var text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
      place(box, document.createTextNode(text.replace(/\s*[\r\n]+\s*/g, ' ')), saved);
      remember();
      sync();
    });
    box.addEventListener('drop', function (event) { event.preventDefault(); });

    var spot = form.querySelector('.tpl-chip-menu[data-chips-for="' + input.name + '"]');
    if (spot) {
      var menu = document.createElement('select');
      menu.className = 'tpl-chip-picker';
      menu.setAttribute('aria-label', 'Insert a storm detail into ' + box.getAttribute('aria-label'));
      var first = document.createElement('option');
      first.value = '';
      first.textContent = 'Insert storm detail';
      menu.appendChild(first);
      Object.keys(LABELS).forEach(function (name) {
        var option = document.createElement('option');
        option.value = name;
        option.textContent = LABELS[name];
        menu.appendChild(option);
      });
      menu.addEventListener('change', function (event) {
        // Kept from templates.js:  the menu has no name and is not part of the template.  The edit
        // it makes reaches templates.js through sync(), as a change of the real field.
        event.stopPropagation();
        if (!has(menu.value)) { return; }
        place(box, chip(menu.value), saved);
        menu.value = '';
        remember();
        sync();
      });
      spot.appendChild(menu);
    }
    return box;
  }

  form.classList.add('chips-on');
  var inputs = form.querySelectorAll('input[data-chips="line"]');
  for (var i = 0; i < inputs.length; i++) { enhance(inputs[i]); }

  // For the behaviour check (tests/js/tplchips_check.js):  the two conversions, nothing else.
  window.tplChips = { render: render, serialize: serialize };
})();
