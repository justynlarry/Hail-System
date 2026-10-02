// Light/dark theme.  Loaded in <head>, not deferred, so the theme is set
// before the first paint (a deferred script would flash the light page).
//
// The choice is saved per browser in localStorage under 'hail-theme'; with no
// saved choice the OS setting decides, and follows it if it changes.  Storage
// can be blocked (private windows, site data off), so every access is
// wrapped and the page still works, just without remembering.
(function () {
  var KEY = 'hail-theme';
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function saved() {
    try {
      var v = localStorage.getItem(KEY);
      return (v === 'dark' || v === 'light') ? v : null;
    } catch (e) { return null; }
  }

  function apply(theme) {
    root.setAttribute('data-theme', theme);
    var sw = document.getElementById('theme-toggle');
    if (sw) sw.setAttribute('aria-checked', theme === 'dark' ? 'true' : 'false');
  }

  apply(saved() || (media && media.matches ? 'dark' : 'light'));

  if (media && media.addEventListener) {
    media.addEventListener('change', function (e) {
      if (!saved()) apply(e.matches ? 'dark' : 'light');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var sw = document.getElementById('theme-toggle');
    if (!sw) return;                    // signed-out pages have no header
    apply(root.getAttribute('data-theme'));
    sw.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(KEY, next); } catch (e) { /* not remembered */ }
      apply(next);
    });
  });
})();
