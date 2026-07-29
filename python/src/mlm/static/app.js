const menuButton = document.querySelector("[data-menu-toggle]");

if (menuButton) {
  menuButton.addEventListener("click", () => {
    const open = document.body.classList.toggle("nav-open");
    menuButton.setAttribute("aria-expanded", String(open));
  });
}

document.querySelectorAll(".nav-link").forEach((link) => {
  link.addEventListener("click", () => document.body.classList.remove("nav-open"));
});

function localizeTimes(root = document) {
  root.querySelectorAll("[data-local-time]").forEach((element) => {
    const value = element.getAttribute("data-local-time");
    if (!value) return;
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.valueOf())) {
      element.textContent = parsed.toLocaleString();
      element.title = value;
    }
  });
}

localizeTimes();

const liveDiagnostics = document.querySelector("[data-live-diagnostics]");
if (liveDiagnostics) {
  const refreshDiagnostics = async () => {
    if (document.querySelector(".activity-console details[open]")) return;
    try {
      const response = await fetch(window.location.href, {
        headers: { "X-HeavyMLM-Refresh": "diagnostics" },
      });
      if (!response.ok) return;
      const nextPage = new DOMParser().parseFromString(await response.text(), "text/html");
      [".debug-grid", ".activity-console"].forEach((selector) => {
        const current = document.querySelector(selector);
        const replacement = nextPage.querySelector(selector);
        if (current && replacement) {
          current.innerHTML = replacement.innerHTML;
          localizeTimes(current);
        }
      });
    } catch {
      // Keep the current diagnostic snapshot visible if a refresh is unavailable.
    }
  };
  window.setInterval(refreshDiagnostics, 5000);
}
