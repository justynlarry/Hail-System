// Template editor (item 203, piece 4.6).  The page works without this file: every button is a
// real submit.  This only stops the page reloading.  It posts the form with fetch, swaps in the
// block list and preview the server sends back, refreshes the preview after a change, and warns
// before leaving with unsaved edits.  Every rule lives on the server.
(function () {
  'use strict';

  // The two edits the toolbar makes.  Each takes the field's text and selection (start, end)
  // and returns the new text and the selection to leave behind.
  function wrapText(text, start, end, open, close) {
    var chosen = text.slice(start, end);
    return {
      text: text.slice(0, start) + open + chosen + close + text.slice(end),
      start: start + open.length,
      end: start + open.length + chosen.length
    };
  }

  function insertText(text, start, end, token) {
    var at = start + token.length;
    return {text: text.slice(0, start) + token + text.slice(end), start: at, end: at};
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {wrapText: wrapText, insertText: insertText};
  }

  var form = document.getElementById('editor');
  if (!form || !window.fetch) { return; }
  form.classList.add('js');

  var blocksBox = document.getElementById('blocks');
  var previewBox = document.getElementById('preview');
  var busy = false;      // a button's request is running
  var skip = false;      // a button is being clicked again to submit the page normally
  var dirty = form.dataset.unsaved === '1';   // edits not saved; a page that comes back with
                                              // your draft (a test send) starts out unsaved
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

  // A change the script made itself: the browser sends no input event for it.
  function edited() {
    dirty = true;
    clearTimeout(timer);
    timer = setTimeout(refreshPreview, 500);
  }

  // ---- the formatting toolbar above the rich text boxes ----
  function toolbarField(control) {
    var bar = control.closest('.tpl-toolbar');
    return bar ? document.getElementById(bar.getAttribute('data-for')) : null;
  }

  function applyTo(field, make) {
    var result = make(field.value, field.selectionStart, field.selectionEnd);
    field.value = result.text;
    field.focus();
    field.setSelectionRange(result.start, result.end);
    edited();
  }

  // The B, I and Link buttons.  Returns true if the click was one of them.
  function toolbarClick(event) {
    var control = event.target.closest('[data-wrap],[data-link]');
    var field = control && toolbarField(control);
    if (!field) { return false; }
    var tag = control.getAttribute('data-wrap');
    var open, close;
    if (tag) {
      open = '[' + tag + ']';
      close = '[/' + tag + ']';
    } else {
      var address = window.prompt('Where should the link go? Type a web address starting with ' +
        'https://, or leave this empty to link to your schedule page.');
      if (address === null) { return true; }
      open = '[link=' + (address.trim() || '{schedule_url}') + ']';
      close = '[/link]';
    }
    applyTo(field, function (text, a, b) { return wrapText(text, a, b, open, close); });
    return true;
  }

  // The Colour and Insert field menus.
  function toolbarChange(target) {
    var data = target.dataset;
    if (!data || (data.colour === undefined && data.merge === undefined) || !target.value) {
      return;
    }
    var value = target.value;
    var field = toolbarField(target);
    target.value = '';              // back to its label, ready for the next use
    if (!field) { return; }
    applyTo(field, function (text, a, b) {
      return data.colour !== undefined
        ? wrapText(text, a, b, '[color=' + value + ']', '[/color]')
        : insertText(text, a, b, value);
    });
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
    if (toolbarClick(event)) { return; }
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
    toolbarChange(event.target);
    if (event.target.id === 'add_kind') { return; }
    if (event.target.id !== 'situation') { dirty = true; }
    clearTimeout(timer);
    timer = setTimeout(refreshPreview, 500);
  });

  form.addEventListener('input', function () { dirty = true; });

  // Only Save clears the warning.  A test send also submits the page, but the draft stays unsaved.
  form.addEventListener('submit', function (event) {
    if (event.submitter && event.submitter.value === 'save') { dirty = false; }
  });

  window.addEventListener('beforeunload', function (event) {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
})();
