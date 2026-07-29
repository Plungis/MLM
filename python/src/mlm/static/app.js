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

document.querySelectorAll("[data-local-time]").forEach((element) => {
  const value = element.getAttribute("data-local-time");
  if (!value) return;
  const parsed = new Date(value);
  if (!Number.isNaN(parsed.valueOf())) {
    element.textContent = parsed.toLocaleString();
    element.title = value;
  }
});
