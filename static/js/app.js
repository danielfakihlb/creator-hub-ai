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

    // Every agent's "it's running" feedback: the instant ANY form in the
    // app is submitted -- running an agent, uploading a clip, creating a
    // campaign, anything -- disable its submit button and swap in a
    // spinner + "Working..." label. This is site-wide (one place, every
    // form, every page) rather than something added per-agent, so it
    // never gets missed on a future agent either. It's honest, simple
    // feedback that something is happening -- never a fake progress
    // percentage. The two agents with a real multi-step pipeline (AI
    // Video Editor, AI Audio & Voice Agent) redirect to their own live,
    // measured-progress page right after this; for the rest (a single
    // request/response call), this spinner is the whole "please wait."
    document.querySelectorAll("form").forEach(function (form) {
      form.addEventListener("submit", function (evt) {
        if (evt.defaultPrevented) return; // e.g. a confirm() dialog on this form was cancelled
        var btn = form.querySelector('button[type="submit"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        btn.classList.add("is-loading");
        btn.innerHTML = '<span class="btn-spinner"></span> Working...';
      });
    });
  });
})();
