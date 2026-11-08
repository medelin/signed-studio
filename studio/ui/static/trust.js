// Copy buttons on /trust and the print button on a receipt; both pages read fine without it.
(() => {
  for (const btn of document.querySelectorAll("button[data-copy]")) {
    btn.addEventListener("click", async () => {
      const el = document.getElementById(btn.dataset.copy);
      if (!el) return;
      try {
        await navigator.clipboard.writeText(el.textContent.trim());
      } catch {
        const range = document.createRange();
        range.selectNodeContents(el);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        return;
      }
      const label = btn.textContent;
      btn.textContent = btn.dataset.done || label;
      setTimeout(() => { btn.textContent = label; }, 1500);
    });
  }
  const print = document.getElementById("print-btn");
  if (print) print.addEventListener("click", () => window.print());
})();
