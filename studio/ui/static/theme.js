// Display preferences: the header form works without JavaScript (GET, the server sets the
// cookies and renders <html data-theme data-palette>). This only submits on change, so
// the Apply button is not needed, and closes the open panel on Escape or a click outside.
"use strict";
(() => {
  const form = document.querySelector(".theme-form");
  if (!form) return;
  form.classList.add("auto");
  for (const select of form.querySelectorAll("select")) {
    select.addEventListener("change", () => form.requestSubmit());
  }
  const panel = form.closest("details");
  if (!panel) return;
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !panel.open) return;
    panel.removeAttribute("open");
    panel.querySelector("summary").focus();
  });
  document.addEventListener("click", (event) => {
    if (panel.open && !panel.contains(event.target)) panel.removeAttribute("open");
  });
})();
