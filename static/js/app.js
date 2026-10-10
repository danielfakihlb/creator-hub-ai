// Creator Hub AI -- minimal client-side behavior. No frameworks, no CDN
// dependencies, so the app renders identically with zero network access.

(function () {
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
  }

  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem("chai-theme"); } catch (e) {}
    applyTheme(saved || "dark");
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute("data-theme") || "dark";
    var next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("chai-theme", next); } catch (e) {}
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", toggleTheme);
    });

    var navToggle = document.querySelector("[data-nav-toggle]");
    var mobileNav = document.querySelector("[data-mobile-nav]");
    if (navToggle && mobileNav) {
      navToggle.addEventListener("click", function () {
        mobileNav.classList.toggle("open");
      });
    }

    // FAQ accordions
    document.querySelectorAll("[data-faq-toggle]").forEach(function (q) {
      q.addEventListener("click", function () {
        var answer = q.nextElementSibling;
        if (answer) answer.style.display = answer.style.display === "block" ? "none" : "block";
      });
    });
  });
})();
