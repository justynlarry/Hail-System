// Merge fields as labelled chips in the template editor (parking-lot item 294, slice 9).
//
// A field marked data-chips is shown as an editable box in which {largest_hail} reads as a chip,
// "Largest hail size":  "line" for the subject, the preview line and a heading (one line), "multi"
// for a text block, the blue box's intro and its checklist (line breaks kept; one checklist item a
// line).  The real field stays in the form, hidden, and is what is posted:  it is rewritten only
// when you edit the box, so a template opened and saved with no edits posts exactly what it loaded.
//   - A chip cannot be half-edited; Backspace removes it whole.
//   - A paste is plain text (one line in a one-line box).  Enter does nothing in a one-line box and
//     starts a new line in a multi-line one.  Dropping dragged content is refused.
//   - The "Insert storm detail" menu (subject, preview line) and the toolbar's Insert field put a
//     chip at the cursor, or at the end.
//   - The toolbar's B, I, Colour and Link wrap the selected text in the same [b]...[/b] tags as
//     before (the tags still show as text; hiding them is slice 10).  Link asks for an address;
//     empty links to {schedule_url}, Cancel does nothing.  The toolbar is handled here for a chip
//     box and kept from templates.js, which would edit the hidden field instead.
// templates.js redraws the block list after Move, Add and Remove, so new boxes are made for the
// redrawn fields.  The labels come from blockform.CHIP_LABELS, in <script id="chip-labels">.  A
// {name} that is not a merge field is left as typed, for the server to point out.  Without this
// script the plain fields are used, as before.
//
// Slice 10, option A (developer, 2026-11-04), in the "tags" section below:  in a box for a field
// that takes formatting (data-rich), each tag is a marker that cannot be edited ("Bold ▸" ...
// "◂ Bold") and keeps its own text, so [b=navy] is read back as [b=navy], and the text between a
// pair is shown bold, in the colour, or as a link.  Nothing outside that section depends on how a
// box draws formatting, so a library editor (Quill) could replace it later behind the same hidden
// field.  The server still checks every tag at save.
(function () {
  var form = document.getElementById('editor');
  var source = document.getElementById('chip-labels');
  if (!form || !source) { return; }
  var LABELS;
  try { LABELS = JSON.parse(source.textContent); } catch (e) { return; }
  var TOKEN = /\{([a-z_]+)\}/g;
  var has = function (name) { return Object.prototype.hasOwnProperty.call(LABELS, name); };
  var PALETTE = {};
  try { PALETTE = JSON.parse((document.getElementById('tag-palette') || {}).textContent || '{}'); } catch (e) { PALETTE = {}; }
  var boxes = {};          // field name -> its chip box, for the toolbar

  function chip(name) {
    var el = document.createElement('span');
    el.className = 'tpl-chip';
    el.setAttribute('contenteditable', 'false');
    el.setAttribute('data-token', name);
    el.textContent = LABELS[name];
    return el;
  }

  // A <br> that only lets the cursor sit on an empty last line (a browser shows no line after a
  // final line break).  It is never read back.
  function filler() {
    var br = document.createElement('br');
    br.setAttribute('data-filler', '1');
    return br;
  }

  function endFiller(box) {
    var last = box.lastChild;
    if (last && last.nodeType === 1 && last.getAttribute('data-filler')) { box.removeChild(last); }
    var text = serialize(box, true);
    if (/\n$/.test(text)) { box.appendChild(filler()); }
  }

  // Text -> the box's contents:  text nodes, a chip for each known {name}, and in a rich box a
  // marker for each tag the server knows ([b], [b=colour], [i], [color=...], [link=...] and closes).
  function render(box, text, multi, rich) {
    while (box.firstChild) { box.removeChild(box.firstChild); }
    var pattern = rich ? ANY : TOKEN;
    var last = 0, m;
    pattern.lastIndex = 0;
    while ((m = pattern.exec(text)) !== null) {
      var node = null;
      if (m[1] !== undefined) { if (has(m[1])) { node = chip(m[1]); } }
      else { node = marker(m[0]); }
      if (!node) { continue; }
      if (m.index > last) { box.appendChild(document.createTextNode(text.slice(last, m.index))); }
      box.appendChild(node);
      last = m.index + m[0].length;
    }
    if (last < text.length) { box.appendChild(document.createTextNode(text.slice(last))); }
    if (multi && /\n$/.test(text)) { box.appendChild(filler()); }
    if (rich) { linkPairs(box); restyle(box); }
  }

  // The box's contents -> text:  a chip back to {name}.  In a multi-line box a <br> is a line break
  // and a block a browser makes (<div>, <p>) starts a new line; a one-line box has no line breaks.
  // A no-break space a browser puts in for a typed space becomes a plain space.
  function serialize(node, multi) {
    var out = '';
    for (var i = 0; i < node.childNodes.length; i++) {
      var n = node.childNodes[i];
      if (n.nodeType === 3) {
        out += n.nodeValue.replace(/ /g, ' ');
      } else if (n.nodeType === 1) {
        var token = n.getAttribute('data-token');
        var tag = n.getAttribute('data-src');
        if (token) {
          out += '{' + token + '}';
        } else if (tag) {
          out += tag;                                // a marker:  its tag exactly as it was
        } else if (n.tagName === 'BR') {
          if (multi && !n.getAttribute('data-filler')) { out += '\n'; }
        } else {
          if (multi && (n.tagName === 'DIV' || n.tagName === 'P') && out && !/\n$/.test(out)) { out += '\n'; }
          out += serialize(n, multi);
        }
      }
    }
    return multi ? out.replace(/\r\n?/g, '\n') : out.replace(/[\r\n]+/g, ' ');
  }

  // ---- tags (slice 10, option A) ----------------------------------------------------------------
  // The same tags as blocks._TAG on the server.  Any other square-bracket text stays as typed.
  var ANY = /\{([a-z_]+)\}|\[(\/?)(b|i|color|link)(?:=([^\[\]]*))?\]/g;
  var TAG_ONE = /^\[(\/?)(b|i|color|link)(?:=([^\[\]]*))?\]$/;
  var WORDS = { b: 'Bold', i: 'Italic', color: 'Colour', link: 'Link' };

  // A marker for one tag, keeping the tag's own text (data-src) to be read back exactly.
  function marker(src) {
    var m = TAG_ONE.exec(src);
    var el = document.createElement('span');
    el.setAttribute('contenteditable', 'false');
    el.setAttribute('data-src', src);
    if (!m) { el.textContent = src; return el; }
    var closing = m[1] === '/', name = m[2], value = m[3];
    var word = name === 'color' && value ? value.charAt(0).toUpperCase() + value.slice(1)
             : name === 'b' && value ? 'Bold ' + value : WORDS[name];
    el.className = 'tpl-mark ' + (closing ? 'tpl-mark-close' : 'tpl-mark-open');
    el.setAttribute('data-tag', name);
    el.textContent = closing ? '\u25C2 ' + WORDS[name] : word + ' \u25B8';
    if (name === 'link' && value) { el.setAttribute('title', value); }
    return el;
  }

  // Opening and closing markers are linked in pairs (data-pair), matched as the server matches tags:
  // a close pairs with the latest open of its kind.  Deleting either one of a pair then removes the
  // other (prunePairs), so a delete means "remove this formatting" and cannot leave a lone tag.  A
  // marker with no partner in the saved text is not linked and is left alone, for the server to
  // point out.  Links are made when a box is drawn and by the toolbar, never redone on an edit, so
  // a pair keeps its partner.  The browser's undo does not know about the partner it removes
  // (developer, 2026-11-04:  accepted).
  var nextPair = 1;

  function linkPairs(box) {
    var open = {};
    var marks = [];
    for (var i = 0; i < box.childNodes.length; i++) {
      var n = box.childNodes[i];
      if (n.nodeType === 1 && n.getAttribute('data-tag')) { marks.push(n); }
      else if (n.nodeType === 1 && n.getAttribute('data-fmt')) {
        for (var j = 0; j < n.childNodes.length; j++) {
          var c = n.childNodes[j];
          if (c.nodeType === 1 && c.getAttribute('data-tag')) { marks.push(c); }
        }
      }
    }
    marks.forEach(function (m) {
      var name = m.getAttribute('data-tag');
      if (m.getAttribute('data-src').charAt(1) === '/') {
        var stack = open[name] || [];
        var partner = stack.pop();
        if (partner) {
          var id = String(nextPair++);
          partner.setAttribute('data-pair', id);
          m.setAttribute('data-pair', id);
        }
      } else {
        (open[name] = open[name] || []).push(m);
      }
    });
  }

  function markersIn(box) {
    var out = [];
    (function walk(node) {
      for (var i = 0; i < node.childNodes.length; i++) {
        var n = node.childNodes[i];
        if (n.nodeType !== 1) { continue; }
        if (n.getAttribute('data-tag')) { out.push(n); } else { walk(n); }
      }
    })(box);
    return out;
  }

  // After an edit:  a linked marker whose partner is gone goes too.
  function prunePairs(box) {
    var count = {};
    var marks = markersIn(box);
    marks.forEach(function (m) { var id = m.getAttribute('data-pair'); if (id) { count[id] = (count[id] || 0) + 1; } });
    marks.forEach(function (m) {
      var id = m.getAttribute('data-pair');
      if (id && count[id] === 1 && m.parentNode) { m.parentNode.removeChild(m); }
    });
  }

  // How the text inside a tag looks:  as the email draws it (blocks.py), near enough to read.
  function look(stack) {
    var css = [];
    stack.forEach(function (t) {
      if (t.name === 'b') { css.push('font-weight:bold'); if (t.value && PALETTE[t.value]) { css.push('color:' + PALETTE[t.value]); } }
      if (t.name === 'i') { css.push('font-style:italic'); }
      if (t.name === 'color' && PALETTE[t.value]) { css.push('color:' + PALETTE[t.value]); }
      if (t.name === 'link') { css.push('color:#1f5c9e'); css.push('font-weight:bold'); css.push('text-decoration:underline'); }
    });
    return css.join(';');
  }

  // Redraw the formatting:  unwrap the old styling spans and wrap each run of text and chips that
  // sits inside open tags in one span styled for them.  Only the top level of the box is styled
  // (the script makes no other blocks), and the cursor is put back on the same text node.
  function restyle(box) {
    var sel = window.getSelection ? window.getSelection() : null;
    var keep = null;
    if (sel && sel.rangeCount && within(box, sel.anchorNode)) {
      keep = { a: sel.anchorNode, ao: sel.anchorOffset, f: sel.focusNode, fo: sel.focusOffset };
    }
    var spans = [];
    for (var i = 0; i < box.childNodes.length; i++) {
      var n0 = box.childNodes[i];
      if (n0.nodeType === 1 && n0.getAttribute('data-fmt')) { spans.push(n0); }
    }
    spans.forEach(function (span) {
      while (span.firstChild) { box.insertBefore(span.firstChild, span); }
      box.removeChild(span);
    });
    var stack = [], run = [];
    function flush() {
      if (run.length && stack.length) {
        var span = document.createElement('span');
        span.setAttribute('data-fmt', '1');
        span.setAttribute('style', look(stack));
        box.insertBefore(span, run[0]);
        run.forEach(function (n) { span.appendChild(n); });
      }
      run = [];
    }
    var nodes = Array.prototype.slice.call(box.childNodes);
    nodes.forEach(function (n) {
      var tag = n.nodeType === 1 ? n.getAttribute('data-tag') : null;
      if (tag) {
        flush();
        if (n.getAttribute('data-src').charAt(1) === '/') {
          if (stack.length && stack[stack.length - 1].name === tag) { stack.pop(); }
        } else {
          var m = TAG_ONE.exec(n.getAttribute('data-src'));
          stack.push({ name: tag, value: m ? m[3] : undefined });
        }
      } else if (n.nodeType === 1 && (n.tagName === 'BR' || n.tagName === 'DIV' || n.tagName === 'P')) {
        flush();
      } else {
        run.push(n);
      }
    });
    flush();
    if (keep && sel) {
      try { sel.setBaseAndExtent(keep.a, keep.ao, keep.f, keep.fo); } catch (e) { /* the cursor stays where the browser put it */ }
    }
  }
  // ---- end of tags --------------------------------------------------------------------------------

  function within(box, node) { return !!node && (node === box || box.contains(node)); }

  // The chip a node is inside, if any (a range must never start or end inside one).
  function chipOf(box, node) {
    for (var n = node; n && n !== box; n = n.parentNode) {
      if (n.nodeType === 1 && (n.getAttribute('data-token') || n.getAttribute('data-src'))) { return n; }
    }
    return null;
  }

  // The cursor or selection in this box:  the live one, else the one remembered, else none.
  function rangeIn(box, saved) {
    var sel = window.getSelection ? window.getSelection() : null;
    var range = null;
    if (sel && sel.rangeCount && within(box, sel.getRangeAt(0).startContainer)) {
      range = sel.getRangeAt(0);
    } else if (saved && within(box, saved.startContainer)) {
      range = saved;
    }
    if (range) {
      var start = chipOf(box, range.startContainer), end = chipOf(box, range.endContainer);
      if (start) { range.setStartBefore(start); }
      if (end) { range.setEndAfter(end); }
    }
    return range;
  }

  function select(range) {
    var sel = window.getSelection ? window.getSelection() : null;
    if (sel) { sel.removeAllRanges(); sel.addRange(range); }
  }

  // Put a node at the cursor (replacing a selection), or at the end.
  function place(box, node, saved) {
    var range = rangeIn(box, saved);
    if (!range) {
      var last = box.lastChild;
      if (last && last.nodeType === 1 && last.getAttribute('data-filler')) { box.insertBefore(node, last); }
      else { box.appendChild(node); }
      return;
    }
    range.deleteContents();
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    select(range);
  }

  // Wrap the selection in open...close (the toolbar):  with nothing selected, the pair goes in with
  // the cursor between;  with no cursor in the box, at the end.
  function wrap(box, open, close, saved) {
    var rich = box.getAttribute('data-rich-box') === '1';
    var o = rich ? marker(open) : document.createTextNode(open);
    var c = rich ? marker(close) : document.createTextNode(close);
    if (rich) { var id = String(nextPair++); o.setAttribute('data-pair', id); c.setAttribute('data-pair', id); }
    var range = rangeIn(box, saved);
    if (!range) {
      place(box, o, null);
      place(box, c, null);
      if (rich) { restyle(box); }
      return;
    }
    var chosen = range.extractContents();
    range.insertNode(c);
    range.insertNode(chosen);
    range.insertNode(o);
    range.setStartAfter(o);
    range.setEndBefore(c);
    select(range);
    if (rich) { restyle(box); }
  }

  function enhance(input) {
    if (input.getAttribute('data-chips-on')) { return null; }
    input.setAttribute('data-chips-on', '1');
    var multi = input.getAttribute('data-chips') === 'multi';
    var rich = input.getAttribute('data-rich') === '1';
    var label = input.id ? document.querySelector('label[for="' + input.id + '"]') : null;
    var box = document.createElement('div');
    box.className = 'tpl-chipbox' + (multi ? ' tpl-chipbox-multi' : '') + (rich ? ' tpl-chipbox-rich' : '');
    if (rich) { box.setAttribute('data-rich-box', '1'); }
    box.id = (input.id || input.name) + '-chips';
    box.setAttribute('contenteditable', 'true');
    box.setAttribute('role', 'textbox');
    if (multi) { box.setAttribute('aria-multiline', 'true'); }
    box.setAttribute('aria-label', input.getAttribute('aria-label') || (label ? label.textContent : '') || input.name);
    box.setAttribute('spellcheck', 'true');
    render(box, input.value, multi, rich);
    input.parentNode.insertBefore(box, input.nextSibling);
    input.hidden = true;
    if (label) { label.setAttribute('for', box.id); }
    var saved = null;      // the cursor, kept for the menus (choosing from one moves focus away)

    function remember() {
      var sel = window.getSelection ? window.getSelection() : null;
      if (sel && sel.rangeCount && within(box, sel.getRangeAt(0).startContainer)) { saved = sel.getRangeAt(0).cloneRange(); }
    }
    function sync() {
      if (rich) { prunePairs(box); restyle(box); }
      if (multi) { endFiller(box); }
      input.value = serialize(box, multi);
      // templates.js refreshes the preview on a form change:  the box itself sends none.
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    box.addEventListener('input', function () { remember(); sync(); });
    box.addEventListener('keyup', remember);
    box.addEventListener('mouseup', remember);
    box.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') { return; }
      event.preventDefault();
      if (!multi) { return; }
      place(box, document.createTextNode('\n'), saved);   // a line break as text, not a browser <div>
      remember();
      sync();
    });
    box.addEventListener('paste', function (event) {
      event.preventDefault();
      var text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
      text = multi ? text.replace(/\r\n?/g, '\n') : text.replace(/\s*[\r\n]+\s*/g, ' ');
      place(box, document.createTextNode(text), saved);
      remember();
      sync();
    });
    box.addEventListener('drop', function (event) { event.preventDefault(); });

    boxes[input.name] = {box: box, input: input, multi: multi,
                         saved: function () { return saved; }, remember: remember, sync: sync};

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

  function enhanceAll() {
    var inputs = form.querySelectorAll('[data-chips]');
    for (var i = 0; i < inputs.length; i++) { enhance(inputs[i]); }
  }

  // ---- the toolbar, for a field shown as a chip box ----
  function boxFor(control) {
    var bar = control.closest ? control.closest('.tpl-toolbar') : null;
    return bar ? boxes[bar.getAttribute('data-for')] || null : null;
  }

  // Keep the cursor in the box while a toolbar button is pressed.
  form.addEventListener('mousedown', function (event) {
    var control = event.target.closest ? event.target.closest('.tpl-toolbar button') : null;
    if (control && boxFor(control)) { event.preventDefault(); }
  }, true);

  form.addEventListener('click', function (event) {
    var control = event.target.closest ? event.target.closest('[data-wrap],[data-link]') : null;
    var entry = control && boxFor(control);
    if (!entry) { return; }
    event.preventDefault();
    event.stopPropagation();                         // templates.js would edit the hidden field
    var tag = control.getAttribute('data-wrap');
    var open, close;
    if (tag) {
      open = '[' + tag + ']';
      close = '[/' + tag + ']';
    } else {
      var address = window.prompt('Where should the link go? Type a web address starting with ' +
        'https://, or leave this empty to link to your schedule page.');
      if (address === null) { return; }
      open = '[link=' + (address.trim() || '{schedule_url}') + ']';
      close = '[/link]';
    }
    wrap(entry.box, open, close, entry.saved());
    entry.remember();
    entry.sync();
  }, true);

  form.addEventListener('change', function (event) {
    var target = event.target;
    var data = target.dataset;
    if (!data || (data.colour === undefined && data.merge === undefined)) { return; }
    var entry = boxFor(target);
    if (!entry) { return; }
    event.stopPropagation();                         // templates.js would edit the hidden field
    var value = target.value;
    target.value = '';                               // back to its label, ready for the next use
    if (!value) { return; }
    if (data.colour !== undefined) {
      wrap(entry.box, '[color=' + value + ']', '[/color]', entry.saved());
    } else {
      var name = value.replace(/^\{|\}$/g, '');      // the menu's values are {name}
      if (!has(name)) { return; }
      place(entry.box, chip(name), entry.saved());
    }
    entry.remember();
    entry.sync();
  }, true);

  // The block list is redrawn after Move, Add and Remove:  make boxes for its new fields.
  var blocksBox = document.getElementById('blocks');
  if (blocksBox && window.MutationObserver) {
    new MutationObserver(function () {
      Object.keys(boxes).forEach(function (name) { if (!form.contains(boxes[name].box)) { delete boxes[name]; } });
      enhanceAll();
    }).observe(blocksBox, { childList: true });
  }

  form.classList.add('chips-on');
  enhanceAll();

  // For the behaviour check (tests/js/tplchips_check.js):  the two conversions, nothing else.
  window.tplChips = { render: render, serialize: serialize };
})();
