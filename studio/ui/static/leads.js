// Muse Studio Lite — leads: (re-)score a lead; the page reloads with the new card.
"use strict";

(() => {
  const st = document.getElementById("leads-status");
  const poll = document.getElementById("poll-leads");
  if (poll) {
    poll.addEventListener("click", async () => {
      poll.disabled = true;
      st.textContent = T("polling");
      const r = await Signer.postJSON("/api/leadforms/poll", {});
      poll.disabled = false;
      if (!r.ok) { st.textContent = T("polling_failed", { error: E(r.data, r.status) }); return; }
      st.textContent = T("polled", { forms: r.data.forms, n: r.data.new });
      if (r.data.new) setTimeout(() => location.reload(), 800);
    });
  }
  for (const card of document.querySelectorAll(".lead")) {
    const btn = card.querySelector("button[data-act=score]");
    if (!btn) continue;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      st.textContent = T("scoring");
      const r = await Signer.postJSON("/api/leads/" + encodeURIComponent(card.dataset.id) + "/score", {});
      if (!r.ok) { st.textContent = T("scoring_failed", { error: E(r.data, r.status) }); btn.disabled = false; return; }
      location.reload();
    });
  }
})();
