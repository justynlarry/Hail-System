// "+ Add a section here" in the template editor (parking-lot item 294, slice 8).
//
// Between the blocks are buttons (.tpl-insert, data-at="N": add before block N).  A click opens
// the picker (#tpl-picker, one button per block type with a small drawing) beside it; choosing a
// type writes the type into the "Add a block" menu (add_kind) and the place into the add_at field,
// then clicks the form's own Add button, so the add goes through templates.js like any other block
// action and blockfold.js opens the new block where it landed.  Escape, or a click elsewhere,
// closes the picker.  Without JavaScript the insert buttons are hidden (.picker-on) and the menu
// at the foot of the blocks adds just above the footer, as before.
(function () {
  var form = document.getElementById('editor');
  var picker = document.getElementById('tpl-picker');
  if (!form || !picker) { return; }
  form.classList.add('picker-on');

  var at = null;          // where the open picker will add, or null when it is closed

  function close() {
    picker.hidden = true;
    at = null;
    var open = form.querySelector('.tpl-insert[aria-expanded="true"]');
    if (open) { open.setAttribute('aria-expanded', 'false'); }
  }

  function openAt(button) {
    close();
    at = button.getAttribute('data-at');
    button.setAttribute('aria-expanded', 'true');
    button.parentNode.insertBefore(picker, button.nextSibling);   // the picker sits under its button
    picker.hidden = false;
    var first = picker.querySelector('button[data-kind]');
    if (first) { first.focus(); }
  }

  function add(kind) {
    var menu = form.querySelector('select[name="add_kind"]');
    var place = form.querySelector('input[name="add_at"]');
    var go = form.querySelector('button[name="action"][value="add"]');
    if (!menu || !place || !go || at === null) { close(); return; }
    menu.value = kind;
    place.value = at;
    close();
    picker.parentNode.removeChild(picker);        // out of the block list before it is redrawn
    form.appendChild(picker);
    go.click();
    place.value = '';                             // the menu at the foot adds above the footer again
  }

  form.addEventListener('click', function (event) {
    var insert = event.target.closest('.tpl-insert');
    if (insert) {
      event.preventDefault();
      if (insert.getAttribute('aria-expanded') === 'true') { close(); } else { openAt(insert); }
      return;
    }
    var choice = event.target.closest('#tpl-picker button[data-kind]');
    if (choice) {
      event.preventDefault();
      add(choice.getAttribute('data-kind'));
      return;
    }
    if (!picker.hidden && !event.target.closest('#tpl-picker')) { close(); }
  }, true);

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !picker.hidden) { close(); }
  });
})();
