// Admin page tabs and confirm prompts (parking-lot item 201, 2026-11-03).
//
// Each <section class="tab-panel"> in admin.html is one tab.  This script builds
// the tab buttons from their data-tab-title and shows one panel at a time.
// Without it every section shows, one after another, as the page did before,
// so nothing is lost if it fails to load.
//
// Which tab opens, first match wins:
//   1. the #fragment, if it names a panel or anything inside one (the admin
//      redirects to #hail-zones and #email, the send page's #observer-wording);
//   2. data-open-tab on the page, set by the server when a re-rendered page must
//      show a tab (a hail-zone trial, an Add user form sent back with an error);
//   3. the tab last opened in this browser tab, from sessionStorage, so that
//      saving a form (every admin POST redirects back to /admin/) comes back to
//      the tab it was on;
//   4. the first tab.
// Storage can be blocked (private windows, site data off), so every access is
// wrapped and the page still works, just without remembering.
(function () {
  var KEY = 'hail-admin-tab';
  var page = document.querySelector('.admin-page');
  if (!page) return;
  var bar = page.querySelector('.tabs');
  var panels = Array.prototype.slice.call(page.querySelectorAll('.tab-panel'));
  if (!bar || !panels.length) return;

  function remember(id) { try { sessionStorage.setItem(KEY, id); } catch (e) {} }
  function remembered() { try { return sessionStorage.getItem(KEY); } catch (e) { return null; } }

  function panelFor(id) {
    if (!id) return null;
    var el = document.getElementById(id);
    return el ? el.closest('.tab-panel') : null;
  }

  var buttons = panels.map(function (panel) {
    var b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-controls', panel.id);
    b.textContent = panel.getAttribute('data-tab-title');
    b.addEventListener('click', function () { show(panel); remember(panel.id); });
    bar.appendChild(b);
    return b;
  });

  function show(panel) {
    panels.forEach(function (p, i) {
      var on = p === panel;
      p.hidden = !on;
      buttons[i].setAttribute('aria-selected', on ? 'true' : 'false');
      buttons[i].classList.toggle('active', on);
    });
  }

  var hash = '';
  try { hash = decodeURIComponent(location.hash.slice(1)); } catch (e) {}
  var fromHash = panelFor(hash);
  var start = fromHash || panelFor(page.getAttribute('data-open-tab')) ||
              panelFor(remembered()) || panels[0];

  page.classList.add('tabbed');
  show(start);
  remember(start.id);

  if (fromHash && hash !== fromHash.id) {
    // A fragment inside a tab (#observer-wording): the browser could not scroll
    // to it while its tab was hidden, so scroll now.
    document.getElementById(hash).scrollIntoView();
  } else if (fromHash) {
    // A fragment naming a whole tab: stay at the top, where the "saved" message is.
    window.scrollTo(0, 0);
  }

  // Disruptive buttons ask first: a form with data-confirm submits only on OK.
  page.addEventListener('submit', function (e) {
    var message = e.target.getAttribute && e.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) e.preventDefault();
  });
})();
