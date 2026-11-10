// Muse Studio Lite — /money: the account's PayPal fundings in an AG Grid Community table.
// Reads /api/money (no PayPal call); the check button asks for ?reconcile=1.
// Community-only features: column filters (text/number/date, floating), quick filter, CSV export,
// pinned totals row, cell renderers, remembered column state; AG Charts Community for the daily chart.
"use strict";

(() => {
  const el = document.getElementById("money-grid");
  if (!el || !window.agGrid) return;
  const status = document.getElementById("money-status");
  const empty = document.getElementById("money-empty");
  const advice = document.getElementById("money-advice");
  const check = document.getElementById("money-check");
  const exampleNote = document.getElementById("money-examples");
  const csvButton = document.getElementById("money-csv");

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
  // the full sentence for badges whose visible text is short (shown as the tooltip)
  const reconTips = {
    pending: () => T("money_recon_pending_tip"), held: () => T("money_recon_held_tip"),
    unknown: () => T("money_recon_unknown_tip"),
    amount_mismatch: () => T("money_recon_amount_mismatch_tip"),
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

  // ---- status badges: a glyph (plain text, no icon font) + the label, coloured by tone ----
  const tones = {
    created: ["idle", "○"], payer_approved: ["idle", "◐"], authorized: ["warn", "◔"],
    captured: ["ok", "✓"], partially_refunded: ["warn", "↺"], refunded: ["idle", "↺"],
    voided: ["idle", "∅"], expired: ["idle", "∅"], failed: ["bad", "✕"],
  };
  const reconTones = {
    matched: ["ok", "✓"], pending: ["warn", "…"], held: ["warn", "◔"], missing: ["bad", "✕"],
    amount_mismatch: ["bad", "≠"], unknown: ["idle", "?"],
  };
  const badge = (map, labels, tips) => (p) => {
    if (!p.value || !p.data || p.data.total) return "";
    const [tone, glyph] = map[p.value] || ["idle", "•"];
    const b = document.createElement("span");
    b.className = "money-badge money-badge-" + tone;
    const g = document.createElement("span");
    g.className = "money-badge-glyph";
    g.setAttribute("aria-hidden", "true");
    g.textContent = glyph;
    const text = document.createElement("span");
    text.className = "money-badge-text";
    text.textContent = label(labels)(p);
    // the tooltip carries the full sentence when the visible text is short
    b.title = tips && tips[p.value] ? tips[p.value]() : text.textContent;
    b.append(g, text);
    return b;
  };
  // filter and quick-filter text for coded columns is the label the user sees
  const coded = (labels) => ({
    filterValueGetter: (p) => (p.data && p.data.total ? "" : label(labels)({ value: p.data && p.data[p.colDef.field] })),
    getQuickFilterText: (p) => (p.value ? label(labels)(p) : ""),
  });

  // ---- numbers: the *_n twins filter and sort; the display strings come from the server ----
  const money = (key) => ({
    field: key + "_n", type: "rightAligned", width: 92, minWidth: 92, maxWidth: 130, flex: 1, filter: "agNumberColumnFilter",
    valueFormatter: (p) => (p.data ? p.data[key] : ""),
    filterParams: { buttons: ["reset"], maxNumDecimalPlaces: 2 },
  });
  // created: a Date at local midnight of the (UTC) day shown, for the date filter and sorting
  // shown compactly as YYYY-MM-DD HH:MM (the server may send seconds or a T / Z form)
  const stamp = (s) => (s ? String(s).replace("T", " ").slice(0, 16) : "");
  const day = (s) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };

  const columns = [
    { colId: "created", headerName: T("money_col_created"), width: 180, minWidth: 180, sort: "desc",
      valueGetter: (p) => (p.data && p.data.created ? day(p.data.created) : null),
      valueFormatter: (p) => (p.data ? stamp(p.data.created) : ""),
      getQuickFilterText: (p) => (p.data ? stamp(p.data.created) : ""),
      comparator: (a, b, na, nb) => ((na.data && na.data.created_ts) || 0) - ((nb.data && nb.data.created_ts) || 0),
      filter: "agDateColumnFilter",
      filterParams: { buttons: ["reset"], comparator: (f, c) => (c < f ? -1 : c > f ? 1 : 0) } },
    { field: "funding_id", headerName: T("money_col_funding"), minWidth: 130, flex: 2, filter: "agTextColumnFilter",
      tooltipField: "funding_id",
      cellRenderer: (p) => {
        if (!p.data || !p.data.example) return p.value;
        const box = document.createElement("span");
        box.className = "money-funding";
        const id = document.createElement("span");
        id.className = "money-funding-id";
        id.textContent = p.value;
        const b = document.createElement("span");
        b.className = "money-badge money-badge-example";
        b.textContent = T("money_example_badge");
        box.append(id, b);
        return box;
      } },
    { field: "run_id", headerName: T("money_col_campaign"), minWidth: 100, flex: 1, filter: "agTextColumnFilter", tooltipField: "run_id",
      valueFormatter: (p) => (p.data && p.data.total ? "" : (p.value || "")) },
    { field: "state", headerName: T("money_col_state"), minWidth: 160, flex: 1, filter: "agTextColumnFilter",
      valueFormatter: label(states), cellRenderer: badge(tones, states), ...coded(states) },
    { colId: "amount", headerName: T("money_col_amount"), ...money("amount") },
    { colId: "authorized", headerName: T("money_col_authorized"), ...money("authorized") },
    { colId: "captured", headerName: T("money_col_captured"), ...money("captured") },
    { colId: "refunded", headerName: T("money_col_refunded"), ...money("refunded") },
    { field: "via", headerName: T("money_col_via"), minWidth: 92, flex: 1, filter: "agTextColumnFilter",
      valueFormatter: label(vias), ...coded(vias) },
    { colId: "receipts", headerName: T("money_col_receipts"), minWidth: 90, flex: 1, tooltipValueGetter: (p) => p.value, filter: "agTextColumnFilter",
      valueGetter: (p) => (p.data && !p.data.total
        ? ((p.data.receipt_ids || []).length ? p.data.receipt_ids.join(", ") : p.data.run_id) : ""),
      cellRenderer: (p) => {
        if (!p.data || p.data.total) return "";
        if (p.data.example) return "—";  // invented rows have no receipts to open
        const a = document.createElement("a");
        a.href = "/receipts?run=" + encodeURIComponent(p.data.run_id);
        a.textContent = p.value;
        return a;
      } },
    { field: "recon", headerName: T("money_col_recon"), minWidth: 125, flex: 1.5, filter: "agTextColumnFilter",
      valueFormatter: (p) => (p.value ? label(recons)(p) : (p.data && p.data.total ? "" : "—")),
      cellRenderer: badge(reconTones, recons, reconTips),
      filterValueGetter: (p) => (p.data && p.data.recon ? label(recons)({ value: p.data.recon }) : ""),
      getQuickFilterText: (p) => (p.value ? label(recons)(p) : "") },
    { colId: "explain", headerName: "", width: 92, minWidth: 92, sortable: false, filter: false,
      floatingFilter: false, resizable: false, cellRenderer: (p) => (p.data && !p.data.total && !p.data.example ? explain(p) : "") },
  ];
  const CSV_COLUMNS = columns.map((c) => c.colId || c.field).filter((id) => id !== "explain");

  // ---- column state (order, widths, sort, filters) remembered per account ----
  let storeKey = null;
  const read = () => {
    try {
      const v = JSON.parse(localStorage.getItem(storeKey) || "null");
      return v && Array.isArray(v.columns) ? v : null;
    } catch (_) { return null; }
  };
  let saveTimer = 0;
  const save = () => {
    if (api === null || storeKey === null) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try {
        localStorage.setItem(storeKey, JSON.stringify({
          columns: api.getColumnState(), filters: api.getFilterModel() || {},
        }));
      } catch (_) { /* private window or blocked storage: the grid just starts fresh next time */ }
    }, 200);
  };
  const restore = () => {
    const saved = read();
    if (!saved) return false;
    try {
      api.applyColumnState({ state: saved.columns, applyOrder: true });
      api.setFilterModel(saved.filters || null);
      return true;
    } catch (_) { return false; }
  };

  let api = null;  // created when there are rows, so it measures a visible element
  const options = {
    theme: "legacy",
    columnDefs: columns,
    // AG Grid's icon font is not vendored (CSP: 'self' only): plain glyphs instead
    defaultColDef: {
      sortable: true, filter: true, floatingFilter: true, resizable: true,
      suppressHeaderMenuButton: true, suppressHeaderFilterButton: true,
    },
    icons: {
      sortAscending: "↑", sortDescending: "↓", sortUnSort: "", filter: "▾", filterActive: "▾",
      smallDown: "▾", smallUp: "▴", smallLeft: "◂", smallRight: "▸", cancel: "×", clear: "×",
    },
    rowData: [],
    pinnedBottomRowData: [],
    getRowId: (p) => p.data.funding_id,
    ensureDomOrder: true,
    suppressCellFocus: false,
    domLayout: "normal",
    onColumnMoved: save, onColumnResized: (e) => { if (e.finished) save(); },
    onSortChanged: save, onFilterChanged: () => { save(); count(); },
    onColumnVisible: save, onColumnPinned: save, onModelUpdated: () => count(),
  };

  function count() {
    if (api === null) return;
    tally.textContent = T("money_rows", { shown: api.getDisplayedRowCount(), all: total });
  }
  let total = 0;
  const tally = document.getElementById("money-count");
  const tools = document.getElementById("money-tools");
  const keys = document.getElementById("money-keys");
  const search = document.getElementById("money-search");

  // ---- chart: held / taken / refunded per day (AG Charts Community, canvas, no tooltip DOM) ----
  const chartBox = document.getElementById("money-chart-box");
  const chartEl = document.getElementById("money-chart");
  let chart = null;
  let lastDaily = [];
  let lastCurrency = "";
  // a theme token as rgb(): the chart parser knows no oklch/light-dark(), the canvas does
  function rgb(token) {
    const probe = document.createElement("span");
    probe.style.color = "var(" + token + ")";
    document.body.appendChild(probe);
    const css = getComputedStyle(probe).color;
    probe.remove();
    try {
      const c = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
      c.fillStyle = css;
      c.fillRect(0, 0, 1, 1);
      const d = c.getImageData(0, 0, 1, 1).data;
      return "rgb(" + d[0] + "," + d[1] + "," + d[2] + ")";
    } catch (_) { return css; }
  }
  function drawChart() {
    if (!window.agCharts || !chartEl) return;
    const rows = lastDaily.filter((d) => d.currency === lastCurrency);
    chartBox.hidden = rows.length === 0;
    if (rows.length === 0) { if (chart) { chart.destroy(); chart = null; } return; }
    chartEl.setAttribute("aria-label", T("money_chart_summary", { currency: lastCurrency, days: rows.length }));
    const ink = rgb("--ink-2"), rule = rgb("--rule-soft"), bg = rgb("--bg-raised");
    const series = (key, name, token) => ({
      type: "bar", xKey: "day", yKey: key, yName: name, fill: rgb(token), stroke: rgb(token),
      tooltip: { enabled: false },
    });
    const opts = {
      container: chartEl, data: rows, background: { fill: bg },
      animation: { enabled: false }, contextMenu: { enabled: false },
      series: [
        series("held", T("money_state_authorized"), "--partial"),
        series("taken", T("money_state_captured"), "--ok"),
        series("refunded", T("money_state_refunded"), "--down"),
      ],
      axes: [
        { type: "category", position: "bottom", label: { color: ink }, line: { stroke: rule } },
        { type: "number", position: "left", label: { color: ink }, line: { stroke: rule },
          gridLine: { style: [{ stroke: rule }] },
          title: { text: lastCurrency, color: ink } },
      ],
      legend: { position: "bottom", item: { label: { color: ink } } },
    };
    if (chart) chart.update(opts); else chart = agCharts.AgCharts.create(opts);
  }
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { drawChart(); });
  }
  new MutationObserver(() => { drawChart(); }).observe(document.documentElement, { attributes: true });

  function pinnedOf(totals) {
    return (totals || []).map((t) => ({
      total: true, funding_id: T("money_total", { currency: t.currency }),
      amount: t.amount, authorized: t.authorized, captured: t.captured, refunded: t.refunded,
      amount_n: t.amount_n, authorized_n: t.authorized_n, captured_n: t.captured_n,
      refunded_n: t.refunded_n, currency: t.currency,
    }));
  }

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
    const examples = data.examples === true;
    exampleNote.hidden = !examples;
    // example rows are shown, never exported and never checked with PayPal
    csvButton.disabled = examples;
    check.disabled = examples;
    empty.hidden = data.rows.length > 0;
    el.hidden = data.rows.length === 0;
    tools.hidden = keys.hidden = data.rows.length === 0;
    total = data.rows.length;
    lastDaily = data.daily || [];
    lastCurrency = data.currency || "";
    if (data.rows.length) {
      if (api === null) {
        storeKey = "studio.money.grid.v4." + (data.account || "");
        api = agGrid.createGrid(el, options);
        restore();
      }
      api.setGridOption("rowData", data.rows);
      api.setGridOption("pinnedBottomRowData", pinnedOf(data.totals));
    } else if (api !== null) {
      api.setGridOption("rowData", []);
      api.setGridOption("pinnedBottomRowData", []);
    }
    count();
    drawChart();
    status.textContent = reconcile ? T("money_checked") : "";
  }

  search.addEventListener("input", () => {
    if (api !== null) api.setGridOption("quickFilterText", search.value);
    count();
  });
  csvButton.addEventListener("click", () => {
    if (api === null || csvButton.disabled) return;
    api.exportDataAsCsv({
      fileName: "money-" + new Date().toISOString().slice(0, 10) + ".csv",
      columnKeys: CSV_COLUMNS,
    });
  });
  document.getElementById("money-reset").addEventListener("click", () => {
    if (api === null) return;
    api.resetColumnState();
    api.setFilterModel(null);
    api.applyColumnState({ state: [{ colId: "created", sort: "desc" }], defaultState: { sort: null } });
    search.value = "";
    api.setGridOption("quickFilterText", "");
    try { localStorage.removeItem(storeKey); } catch (_) { /* nothing stored */ }
  });

  check.addEventListener("click", async () => {
    check.disabled = true;
    await load(true);
    if (!exampleNote.hidden) return;  // examples: the button stays off
    check.disabled = false;
  });
  load(false);
})();
