// Click a part of the preview to open its block in the form (parking-lot item 294, slice 7).
//
// The editor preview is an <iframe sandbox="allow-same-origin" srcdoc=...>:  nothing inside it can
// run (no allow-scripts), but because it shares this page's origin this script can see it.  Its
// rows carry data-block="N" (blocks.compile_blocks(mark=True), the on-screen preview only), N being
// the form's block number b0, b1 ...  So:
//   - a click in the preview finds the nearest data-block and opens that block in the form, by
//     clicking its header (blockfold.js keeps one block open), and brings it into view;  a block
//     with nothing to edit (a storm row) is brought into view and flashed;
//   - a click never follows a link inside the preview (it would load that site inside the frame);
//   - hovering outlines the rows of the block a click would open.
// templates.js replaces the whole preview after each refresh, so every new frame is hooked when
// it loads.
(function () {
  var box = document.getElementById('preview');
  if (!box || !window.MutationObserver) { return; }

  var HOVER = 'tpl-hover';
  var STYLE = '[data-block]{cursor:pointer}' +
              'tr.' + HOVER + '{outline:2px dashed #2b7cd3; outline-offset:-2px}';

  function blockOf(node) {
    var el = node && node.nodeType === 1 ? node : node && node.parentElement;
    var row = el && el.closest ? el.closest('[data-block]') : null;
    return row ? row.getAttribute('data-block') : null;
  }

  function open(n) {
    var target = document.getElementById('b' + n);
    if (!target) { return; }
    if (target.classList.contains('tpl-block')) {
      if (target.classList.contains('is-closed')) {
        var toggle = target.querySelector('.tpl-toggle');
        if (toggle) { toggle.click(); }
      }
    } else {
      target.classList.add('tpl-flash');
      setTimeout(function () { target.classList.remove('tpl-flash'); }, 1200);
    }
    target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  // Hooked documents, not frames:  a srcdoc frame first holds an empty about:blank document that
  // already reads 'complete', then the email.  Keying on the frame would hook the empty one and
  // then skip the real one.  A document with no marked rows is not hooked at all.
  var hooked = typeof WeakSet === 'function' ? new WeakSet() : null;

  function hook(frame) {
    var doc;
    try { doc = frame.contentDocument; } catch (e) { return; }     // not same-origin: nothing to do
    if (!doc || !doc.body || (hooked && hooked.has(doc))) { return; }
    if (!doc.querySelectorAll('[data-block]').length) { return; }
    if (hooked) { hooked.add(doc); }
    frame.dataset.hooked = '1';
    var style = doc.createElement('style');
    style.textContent = STYLE;
    (doc.head || doc.body).appendChild(style);
    var lit = null;

    function light(n) {
      if (n === lit) { return; }
      var old = doc.querySelectorAll('.' + HOVER);
      for (var i = 0; i < old.length; i++) { old[i].classList.remove(HOVER); }
      lit = n;
      if (n === null) { return; }
      var rows = doc.querySelectorAll('tr[data-block="' + n + '"]');
      for (var j = 0; j < rows.length; j++) {
        // Only the block's outer rows:  the rows of a table inside it carry the same mark.
        var outer = rows[j].parentElement && rows[j].parentElement.closest('[data-block="' + n + '"]');
        if (!outer) { rows[j].classList.add(HOVER); }
      }
    }

    doc.addEventListener('click', function (event) {
      event.preventDefault();
      var n = blockOf(event.target);
      if (n !== null) { open(n); }
    });
    doc.addEventListener('mouseover', function (event) { light(blockOf(event.target)); });
    doc.addEventListener('mouseleave', function () { light(null); });
  }

  function watch() {
    var frame = box.querySelector('iframe.tpl-frame');
    if (!frame) { return; }
    frame.addEventListener('load', function () { hook(frame); });
    var doc = null;
    try { doc = frame.contentDocument; } catch (e) { doc = null; }   // unreadable: leave it alone
    if (doc && doc.readyState === 'complete') { hook(frame); }
  }

  new MutationObserver(watch).observe(box, { childList: true });
  watch();
})();
