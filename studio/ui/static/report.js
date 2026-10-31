// Muse Studio Lite — report: fresh numbers from Meta's insights API, then one long-context
// analyst call. No screenshots: the report reads the platform's own data.
"use strict";

(() => {
  const st = document.getElementById("ms-status");
  const pull = document.getElementById("pull-insights");
  if (pull) {
    pull.addEventListener("click", async () => {
      pull.disabled = true;
      st.textContent = T("ms_pulling");
      const r = await Signer.postJSON("/api/insights/pull", {});
      if (!r.ok) { st.textContent = T("ms_pull_failed", { error: E(r.data, r.status) }); pull.disabled = false; return; }
      location.reload();
    });
  }
  // A recommendation changes nothing by itself: it becomes a campaign.update approval that
  // the owner signs here, in the browser, like every other external write.
  for (const btn of document.querySelectorAll("button[data-act=execute]")) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      st.textContent = T("ms_signing");
      try {
        await Signer.registerKey();
        const r = await Signer.postJSON("/api/recommendations/" + encodeURIComponent(btn.dataset.id) + "/approve", {});
        if (!r.ok) throw new Error(T("ms_refused", { error: E(r.data, r.status) }));
        const payload = await Signer.loadPayload(r.data.approval_id);
        const done = await Signer.sign(payload);
        if (!done.executed) throw new Error(T("ms_not_executed", { error: done.execution_error || T("no_executor") }));
        st.textContent = T("ms_executed", { sig: done.signature.slice(0, 16) });
        setTimeout(() => location.reload(), 900);
      } catch (e) {
        st.textContent = e.message;
        btn.disabled = false;
      }
    });
  }
})();

(() => {
  const form = document.getElementById("report-form");
  if (!form) return;
  const st = document.getElementById("report-status");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = document.getElementById("report-button");
    btn.disabled = true;
    // the latest numbers first (read-only Meta insights); an outage there still lets the
    // analyst write from the snapshots already stored
    st.textContent = T("ms_pulling");
    try { await Signer.postJSON("/api/insights/pull", {}); } catch (_) { /* stored data */ }
    st.textContent = T("analyst_reading");
    const r = await Signer.postJSON("/api/reports", {});
    if (!r.ok) { st.textContent = T("report_failed", { error: E(r.data, r.status) }); btn.disabled = false; return; }
    location.reload();
  });
})();
