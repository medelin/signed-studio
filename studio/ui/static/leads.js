// Muse Studio Lite — leads: (re-)score a lead; the page reloads with the new card.
"use strict";

(() => {
  const st = document.getElementById("leads-status");
  for (const card of document.querySelectorAll(".lead")) {
    const btn = card.querySelector("button[data-act=score]");
    if (!btn) continue;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      st.textContent = "Scoring (rules + one Muse Spark rationale call)…";
      const r = await Signer.postJSON("/api/leads/" + encodeURIComponent(card.dataset.id) + "/score", {});
      if (!r.ok) { st.textContent = "Scoring failed: " + (r.data.error || r.status); btn.disabled = false; return; }
      location.reload();
    });
  }
})();
