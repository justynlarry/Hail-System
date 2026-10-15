// Template editor (item 203, piece 4.6).  The page works without this file: every button is a
// real submit.  This only stops the page reloading.  It posts the form with fetch, swaps in the
// block list and preview the server sends back, refreshes the preview after a change, and warns
// before leaving with unsaved edits.  Every rule lives on the server.
(function () {
  'use strict';

  var form = document.getElementById('editor');
  if (!form || !window.fetch) { return; }

  var blocksBox = document.getElementById('blocks');
  var previewBox = document.getElementById('preview');
  var busy = false;      // a button's request is running
  var skip = false;      // a button is being clicked again to submit the page normally
  var dirty = false;     // edits that have not been saved
  var latest = 0;        // number of the newest request: an older, slower reply is ignored
  var timer = null;

  // Post the form with one action.  Resolves to the reply, or null if a newer request has begun.
  // The address comes from getAttribute: the buttons are named "action", so form.action would be
  // the button, not the address.
  function send(action) {
    var data = new FormData(form);
    data.set('action', action);
    var mine = ++latest;
    return fetch(form.getAttribute('action'), {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      headers: {'X-Requested-With': 'fetch'}
    }).then(function (response) {
      if (!response.ok) { throw new Error('status ' + response.status); }
      return response.json();
    }).then(function (reply) {
      return mine === latest ? reply : null;
    });
  }

  // Put each problem under its block.  The server draws them too, on a full page; clear those
  // first so none is shown twice.
  function showProblems(problems) {
    blocksBox.querySelectorAll('p.error').forEach(function (old) { old.remove(); });
    problems.forEach(function (problem) {
      var spot = problem[0] === null ? null :
        blocksBox.querySelector('.block-errors[data-block="' + (problem[0] - 1) + '"]');
      if (spot) {
        var line = document.createElement('p');
        line.className = 'error';
        line.textContent = problem[1];
        spot.appendChild(line);
      }
    });
  }

  function scrollTo(button) {
    var hash = (button.getAttribute('formaction') || '').split('#')[1];
    var target = hash ? document.getElementById(hash) : null;
    if (target) { target.scrollIntoView({block: 'nearest'}); }
  }

  // Add, move, remove and preview buttons.
  function act(button) {
    if (busy) { return; }
    busy = true;
    var action = button.value;
    send(action).then(function (reply) {
      if (reply === null) { return; }
      blocksBox.innerHTML = reply.blocks;
      previewBox.innerHTML = reply.preview;
      if (action !== 'preview') { dirty = true; }
      scrollTo(button);
    }).catch(function () {
      skip = true;
      button.click();                 // anything went wrong: let the browser submit the page
    }).then(function () {
      busy = false;
    });
  }

  form.addEventListener('click', function (event) {
    var button = event.target.closest('button[name="action"]');
    if (!button || button.disabled || button.value === 'save') { return; }
    if (skip) { skip = false; return; }
    event.preventDefault();
    act(button);
  });

  // A changed field refreshes the preview after a short pause, without touching the fields.
  function refreshPreview() {
    if (busy) { timer = setTimeout(refreshPreview, 300); return; }
    send('preview').then(function (reply) {
      if (reply === null) { return; }
      previewBox.innerHTML = reply.preview;
      showProblems(reply.problems);
    }).catch(function () { /* the Show preview button still works */ });
  }

  form.addEventListener('change', function (event) {
    if (event.target.id === 'add_kind') { return; }
    if (event.target.id !== 'situation') { dirty = true; }
    clearTimeout(timer);
    timer = setTimeout(refreshPreview, 500);
  });

  form.addEventListener('input', function () { dirty = true; });

  // Only a real page submit gets here (Save): the edits are being saved.
  form.addEventListener('submit', function () { dirty = false; });

  window.addEventListener('beforeunload', function (event) {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
})();
