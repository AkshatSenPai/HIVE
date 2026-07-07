/* HIVE page transitions — a lightweight cross-fade for the multi-page shell.
 *
 * The pages render client-side (dc-runtime + React), so each load has a brief
 * blank moment. This turns that into polish: a dark backdrop paints instantly
 * (no white flash), content fades in once it mounts, and internal link clicks
 * fade out before navigating — together reading as a cross-fade between pages.
 *
 * Self-contained, no dependencies. Respects prefers-reduced-motion. Degrades
 * safely: with JS off, nothing is hidden and pages render as normal.
 * Load this BEFORE support.js so the hidden-until-ready rule is in place
 * before first paint.
 */
(function () {
  'use strict';

  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    return; // honor the OS setting — no motion at all
  }

  var DUR = 260; // ms

  // Paint a dark backdrop on <html> immediately and hide <body> until content
  // is ready. The rule is injected before <body> parses, so there is no flash
  // of unstyled/empty content, and no white gap during the client render.
  var css = document.createElement('style');
  css.textContent =
    'html{background:#06070c}' +
    'body{opacity:0}' +
    'body.hive-in{opacity:1;transition:opacity ' + DUR + 'ms ease}';
  (document.head || document.documentElement).appendChild(css);

  var shown = false;
  function reveal() {
    if (shown) return;
    shown = true;
    if (document.body) document.body.classList.add('hive-in');
  }

  // "Ready" = the app shell has actually mounted (sidebar / nav / header),
  // not just an empty body.
  function ready() {
    return !!document.querySelector('aside, nav, header') ||
      (document.body && document.body.childElementCount > 1);
  }

  function watch() {
    if (ready()) { reveal(); return; }
    var obs = new MutationObserver(function () {
      if (ready()) { obs.disconnect(); reveal(); }
    });
    obs.observe(document.body, { childList: true, subtree: true });
    setTimeout(reveal, 1500); // safety net — never leave the page hidden
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', watch);
  } else {
    watch();
  }

  // Fade out before same-origin, in-app navigations.
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest ? e.target.closest('a[href]') : null;
    if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;

    var url;
    try { url = new URL(href, location.href); } catch (_) { return; }
    if (url.origin !== location.origin) return;                 // external — let it go
    if (url.pathname === location.pathname && url.search === location.search) return; // same page

    e.preventDefault();
    document.body.style.transition = 'opacity ' + DUR + 'ms ease';
    document.body.style.opacity = '0';
    setTimeout(function () { location.href = url.href; }, DUR);
  }, true);

  // Restored from the back/forward cache — make sure we're visible.
  window.addEventListener('pageshow', function (e) {
    if (e.persisted && document.body) {
      shown = true;
      document.body.style.opacity = '1';
    }
  });
})();
