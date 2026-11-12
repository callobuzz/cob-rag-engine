/* Light by default. A visitor's choice is kept in localStorage["cob.site.theme"]. Loaded in <head>
 * without defer, so the stored theme is applied before the first paint (no flash). */
(function () {
  "use strict";
  var KEY = "cob.site.theme";
  var root = document.documentElement;
  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function apply(theme) {
    if (theme === "dark") root.setAttribute("data-theme", "dark");
    else root.removeAttribute("data-theme");
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#07111d" : "#ffffff");
    var buttons = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    }
  }
  root.className = root.className.replace("no-js", "js");
  apply(stored() === "dark" ? "dark" : "light");
  document.addEventListener("DOMContentLoaded", function () {
    apply(root.getAttribute("data-theme") === "dark" ? "dark" : "light");
    document.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest("[data-theme-toggle]");
      if (!button) return;
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch (e) { /* private mode: this page only */ }
      apply(next);
    });
  });
})();
