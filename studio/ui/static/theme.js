// Display preferences: the header form works without JavaScript (GET, the server sets the
// cookies and renders <html data-theme data-palette>). This only submits on change, so
// the Apply button is not needed.
"use strict";
(() => {
  const form = document.querySelector(".theme-form");
  if (!form) return;
  form.classList.add("auto");
  for (const select of form.querySelectorAll("select")) {
    select.addEventListener("change", () => form.requestSubmit());
  }
})();
