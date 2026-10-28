// Muse Studio Lite — receipts page: copy one receipt as JSON.
"use strict";

(() => {
  const st = document.getElementById("copy-status");
  document.querySelectorAll("button.copy-json").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const pre = document.getElementById(btn.dataset.target);
      if (!pre) return;
      try {
        await navigator.clipboard.writeText(pre.textContent);
        st.textContent = "Receipt copied as JSON.";
      } catch (_) {
        pre.closest("details").open = true;
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        st.textContent = "Clipboard unavailable: the JSON is selected, press Ctrl+C.";
      }
    });
  });
})();
