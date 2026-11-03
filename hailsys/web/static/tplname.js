// The internal name shown under "Name this template" (parking-lot item 294, slice 4).
//
// The server makes the name when the template is created (templatestore.name_from_title):
// lowercase letters and digits joined by underscores, at most 40 characters, with _2, _3 ...
// added if it is taken.  This shows the same thing as you type, without the number, which only
// the database can know.  It is a preview:  nothing typed here is posted as the name.
(function () {
  var field = document.getElementById('friendly_title');
  var shown = document.querySelector('[data-name-preview]');
  if (!field || !shown) { return; }

  function nameFrom(title) {
    return String(title).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
      .slice(0, 40).replace(/_+$/, '');
  }

  function update() { shown.textContent = nameFrom(field.value) || '…'; }
  field.addEventListener('input', update);
  update();
})();
