// Muse Studio Lite — report: one long-context analyst call, optional screenshot upload.
"use strict";

(() => {
  const form = document.getElementById("report-form");
  if (!form) return;
  const st = document.getElementById("report-status");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = document.getElementById("report-button");
    const file = document.getElementById("shot").files[0];
    if (file && file.size > 5 * 1024 * 1024) { st.textContent = T("shot_too_big"); return; }
    btn.disabled = true;
    st.textContent = T("analyst_reading");
    const r = await fetch("/api/reports", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": file ? file.type : "application/json" },
      body: file ? file : "{}",
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) { st.textContent = T("report_failed", { error: data.error || r.status }); btn.disabled = false; return; }
    location.reload();
  });
})();
