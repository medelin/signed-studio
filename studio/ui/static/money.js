// Muse Studio Lite — /money: the account's PayPal fundings in an AG Grid Community table.
// Reads /api/money (no PayPal call); the check button asks for ?reconcile=1.
"use strict";

(() => {
  const el = document.getElementById("money-grid");
  if (!el || !window.agGrid) return;
  const status = document.getElementById("money-status");
  const empty = document.getElementById("money-empty");
  const advice = document.getElementById("money-advice");
  const check = document.getElementById("money-check");

  agGrid.ModuleRegistry.registerModules([agGrid.AllCommunityModule]);

  // literal keys (the i18n test finds them); unknown values are shown as they are
  const label = (map) => (p) => (p.value && map[p.value] ? map[p.value]() : (p.value || ""));
  const states = {
    created: () => T("money_state_created"), payer_approved: () => T("money_state_payer_approved"),
    authorized: () => T("money_state_authorized"), captured: () => T("money_state_captured"),
    partially_refunded: () => T("money_state_partially_refunded"),
    refunded: () => T("money_state_refunded"), voided: () => T("money_state_voided"),
    expired: () => T("money_state_expired"), failed: () => T("money_state_failed"),
  };
  const vias = { checkout: () => T("money_via_checkout"), mandate: () => T("money_via_mandate") };
  const recons = {
    matched: () => T("money_recon_matched"), pending: () => T("money_recon_pending"),
    held: () => T("money_recon_held"), missing: () => T("money_recon_missing"),
    amount_mismatch: () => T("money_recon_amount_mismatch"),
    unknown: () => T("money_recon_unknown"),
  };
  const explain = (p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = T("money_explain");
    b.addEventListener("click", async () => {
      b.disabled = true;
      advice.hidden = false;
      advice.textContent = T("money_explaining");
      const r = await postJSON("/api/funding/" + encodeURIComponent(p.data.funding_id) + "/treasurer", {});
      b.disabled = false;
      advice.textContent = r.ok ? (r.data.explanation || "") : T("money_failed", { error: E(r.data, r.status) });
    });
    return b;
  };

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty body */ }
    return { ok: r.ok, status: r.status, data };
  }

  const num = { type: "rightAligned", width: 120 };
  let api = null;  // created when there are rows, so it measures a visible element
  const options = {
    theme: "legacy",
    columnDefs: [
      { field: "created", headerName: T("money_col_created"), width: 150, sort: "desc" },
      { field: "funding_id", headerName: T("money_col_funding"), width: 190 },
      { field: "state", headerName: T("money_col_state"), width: 130, valueFormatter: label(states) },
      { field: "amount", headerName: T("money_col_amount"), ...num },
      { field: "authorized", headerName: T("money_col_authorized"), ...num },
      { field: "captured", headerName: T("money_col_captured"), ...num },
      { field: "refunded", headerName: T("money_col_refunded"), ...num },
      { field: "via", headerName: T("money_col_via"), width: 120, valueFormatter: label(vias) },
      { field: "receipt_ids", headerName: T("money_col_receipts"), width: 190,
        valueFormatter: (p) => (p.value || []).join(", "),
        cellRenderer: (p) => {
          const a = document.createElement("a");
          a.href = "/receipts?run=" + encodeURIComponent(p.data.run_id);
          a.textContent = (p.value || []).length ? p.value.join(", ") : p.data.run_id;
          return a;
        } },
      { field: "recon", headerName: T("money_col_recon"), width: 150,
        valueFormatter: (p) => (p.value ? label(recons)(p) : "—") },
      { headerName: "", width: 110, sortable: false, filter: false, cellRenderer: explain },
    ],
    // AG Grid's icon font is not vendored (CSP: 'self' only): plain arrows, no header menus
    defaultColDef: { sortable: true, filter: false, resizable: true, suppressHeaderMenuButton: true },
    icons: { sortAscending: "↑", sortDescending: "↓", sortUnSort: "" },
    autoSizeStrategy: { type: "fitGridWidth", defaultMinWidth: 80 },
    rowData: [],
    getRowId: (p) => p.data.funding_id,
    domLayout: "normal",
  };

  async function load(reconcile) {
    status.textContent = T(reconcile ? "money_checking" : "money_loading");
    let r;
    try {
      r = await fetch("/api/money" + (reconcile ? "?reconcile=1" : ""), { credentials: "same-origin" });
    } catch (_) {
      status.textContent = T("money_failed", { error: "" });
      return;
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { status.textContent = T("money_failed", { error: E(data, r.status) }); return; }
    empty.hidden = data.rows.length > 0;
    el.hidden = data.rows.length === 0;
    if (data.rows.length) {
      if (api === null) api = agGrid.createGrid(el, options);
      api.setGridOption("rowData", data.rows);
    } else if (api !== null) {
      api.setGridOption("rowData", []);
    }
    status.textContent = reconcile ? T("money_checked") : "";
  }

  check.addEventListener("click", async () => {
    check.disabled = true;
    await load(true);
    check.disabled = false;
  });
  load(false);
})();
