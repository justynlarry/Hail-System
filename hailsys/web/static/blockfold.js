// Folding blocks in the template editor (parking-lot item 294, slice 2).
//
// Each block with settings is a <fieldset class="tpl-block" data-fold="N"> whose legend holds a
// .tpl-toggle button.  This script closes them all and opens one at a time, so a template of
// seventeen blocks fits on about one screen.  Without it every block stays open, as before.
//
// Which block is open:
//   - a click on a header opens that block and closes the others (a second click closes it);
//   - templates.js redraws the whole list after Move, Add and Remove.  The click is seen here
//     first (a capture listener), so after the redraw the open block follows a moved block, a
//     new block opens where it was added (add_at, from "+ Add a section here"), and a removed
//     one is forgotten;
//   - on a full page load, the #bN the block buttons put in the address opens that block;
//   - after a redraw or a load, a block the server marked with a problem opens, so its message
//     is never hidden.  A problem found while you type (the live preview) only marks its block,
//     so the block you are typing in is not closed under you.
//
// The Remove button of a block with settings carries data-confirm and asks first.
(function () {
  var form = document.getElementById('editor');
  var box = document.getElementById('blocks');
  if (!form || !box || !window.MutationObserver) { return; }
  form.classList.add('folding');

  var open = null;        // data-fold of the open block, as a number, or null
  var pending = null;     // the block action clicked, until its redraw arrives

  function folds() { return Array.prototype.slice.call(box.querySelectorAll('fieldset.tpl-block[data-fold]')); }
  function indexOf(el) { return parseInt(el.getAttribute('data-fold'), 10); }

  function hasProblem(el) {
    if (el.querySelector('p.error')) { return true; }
    var spot = el.querySelector('.block-errors');
    return !!(spot && spot.children.length);
  }

  function show() {
    folds().forEach(function (el) {
      var on = indexOf(el) === open;
      el.classList.toggle('is-closed', !on);
      el.classList.toggle('has-problem', hasProblem(el));
      var toggle = el.querySelector('.tpl-toggle');
      if (toggle) { toggle.setAttribute('aria-expanded', on ? 'true' : 'false'); }
    });
  }

  // After a load or a redraw:  a block with a problem wins, then whatever the caller chose.
  function settle(chosen) {
    var bad = folds().filter(hasProblem);
    open = bad.length ? indexOf(bad[0]) : chosen;
    if (open !== null && !folds().some(function (el) { return indexOf(el) === open; })) { open = null; }
    show();
  }

  // Where the open block is after an action on block i.  n is the number of blocks after the redraw.
  function follow(action, n) {
    var m = /^(up|down|remove):(\d+)$/.exec(action || '');
    if (action === 'add') { return n - 2; }               // a new block goes just above the footer
    var at = /^add:(\d+)$/.exec(action || '');
    if (at) { return Math.min(parseInt(at[1], 10), n - 2); }   // or where "+ Add a section here" was
    if (!m || open === null) { return open; }
    var i = parseInt(m[2], 10);
    if (m[1] === 'up') { return open === i ? i - 1 : (open === i - 1 ? i : open); }
    if (m[1] === 'down') { return open === i ? i + 1 : (open === i + 1 ? i : open); }
    return open === i ? null : (open > i ? open - 1 : open);
  }

  function blockCount() {
    var field = box.querySelector('input[name="n_blocks"]');
    return field ? parseInt(field.value, 10) : 0;
  }

  // Headers open and close; Remove asks first.  Capture, so this runs before templates.js.
  form.addEventListener('click', function (event) {
    var toggle = event.target.closest('.tpl-toggle');
    if (toggle && box.contains(toggle)) {
      event.preventDefault();
      var i = indexOf(toggle.closest('fieldset.tpl-block'));
      open = open === i ? null : i;
      show();
      return;
    }
    var button = event.target.closest('button[name="action"]');
    if (!button || button.disabled) { return; }
    var message = button.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopPropagation();          // templates.js listens on the form too:  it must not post
      return;
    }
    if (button.value === 'add') {
      // "+ Add a section here" fills add_at (tplpicker.js); empty means just above the footer.
      var place = form.querySelector('input[name="add_at"]');
      pending = place && /^\d+$/.test(place.value) ? 'add:' + place.value : 'add';
    } else if (button.value !== 'preview' && button.value !== 'save') {
      pending = button.value;
    }
  }, true);

  // templates.js replaces the list after a block action, and adds problem lines while you type.
  new MutationObserver(function () {
    if (pending !== null) {
      var action = pending;
      pending = null;
      settle(follow(action, blockCount()));
    } else {
      show();                            // only the problem marks can have changed
    }
  }).observe(box, { childList: true, subtree: true });

  var hash = /^#b(\d+)$/.exec(window.location.hash || '');
  settle(hash ? parseInt(hash[1], 10) : null);
})();
