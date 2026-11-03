// Spacing in named steps in the template editor (parking-lot item 294, slice 3).
//
// Each "Space above" / "Space below" shows a menu (None, Tight, Normal, Roomy, Custom) with no
// name, so it is never posted.  The field that is saved is the exact number box under "More
// options", named as before.  This script keeps the two in step:
//   - picking a step writes its number into the exact box (Custom leaves the box as it is);
//   - typing in the exact box picks the matching step, or Custom (n px) when none matches.
// So a stored value that is none of the steps shows as Custom and is saved unchanged, and an
// unedited save posts exactly what was loaded.  The menus are switched on here (.presets-on on the
// form); without this script they stay hidden and the exact boxes work as before.
//
// templates.js redraws the block list after Move, Add and Remove; the menus in the new list are
// drawn by the server already in step, so nothing needs redoing here.  Events are caught on the
// form (capture), so the exact box is updated before templates.js reads the form for its preview.
(function () {
  var form = document.getElementById('editor');
  if (!form) { return; }
  form.classList.add('presets-on');

  function exactFor(menu) { return document.getElementById(menu.getAttribute('data-preset-for')); }

  function customOption(menu) {
    var options = menu.options;
    for (var i = 0; i < options.length; i++) { if (options[i].value === '') { return options[i]; } }
    return null;
  }

  // The exact box changed:  pick the step with that number, or Custom with the number shown.
  function matchMenu(menu, value) {
    var found = false;
    for (var i = 0; i < menu.options.length; i++) {
      var option = menu.options[i];
      if (option.value !== '' && option.value === value) { option.selected = true; found = true; }
    }
    var custom = customOption(menu);
    if (custom) {
      custom.textContent = found || value === '' ? 'Custom' : 'Custom (' + value + ' px)';
      if (!found) { custom.selected = true; }
    }
  }

  function onEdit(event) {
    var target = event.target;
    if (target.classList && target.classList.contains('tpl-preset')) {
      var exact = exactFor(target);
      if (exact && target.value !== '') { exact.value = target.value; }
      return;
    }
    if (target.name) {
      var menu = form.querySelector('select.tpl-preset[data-preset-for="' + target.name + '"]');
      if (menu) { matchMenu(menu, String(target.value).trim()); }
    }
  }

  form.addEventListener('change', onEdit, true);
  form.addEventListener('input', onEdit, true);
})();
