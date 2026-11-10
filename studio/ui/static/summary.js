// The approval screen's plain-language card: what the owner is about to approve, in words a
// non-technical person understands, above the proof (hashes, chain head, key).
// Three kinds get one: a campaign (what runs, what PayPal holds, PAUSED, second signature),
// a mandate (its limits in words) and a refund (the amount). Any other action gets no card.
// The amounts are never computed here: they are the signed content's own numbers (the
// campaign's daily budget, the mandate's limits, the refund body) and, for the hold, the
// server's quote (paypal.js reports it, or the signed funding block carries it).
"use strict";

window.ApprovalSummary = (() => {
  const ZERO = ["HUF", "JPY", "TWD"];  // currencies without minor units (as the server's table)
  const major = (minor, currency) => (ZERO.includes(currency) ? String(minor) : (minor / 100).toFixed(2));
  const money = (minor, currency) => major(minor, currency) + " " + currency;
  let current = null;
  let hold = null;

  // The hold PayPal places: from the order the studio just opened (paypal.js), else from the
  // funding block the approval already carries. Null while neither exists.
  function holdOf(payload, asked) {
    if (asked) return asked;
    const block = (payload.content || {}).funding;
    const campaign = (payload.content || {}).campaign || {};
    if (!block || !campaign.daily_budget_minor) return null;
    return {
      total: major(block.amount_minor, block.currency), budget: major(block.budget_minor, block.currency),
      fee: major(block.fee_minor, block.currency), currency: block.currency,
      days: Math.round(block.budget_minor / campaign.daily_budget_minor),
    };
  }

  function scheduleDays(content) {
    const s = content.schedule;
    if (!s || !s.start || !s.end) return 0;
    const ms = Date.parse(s.end) - Date.parse(s.start);
    return Number.isFinite(ms) ? Math.max(1, Math.round(ms / 86400000) + 1) : 0;
  }

  function campaign(payload, asked) {
    const c = payload.content || {};
    const k = c.campaign || {};
    const h = holdOf(payload, asked);
    const days = (h && h.days) || scheduleDays(c);
    const values = { name: k.angle || T("sum_unnamed"), daily: money(k.daily_budget_minor || 0, k.currency),
                     days };
    const out = [T(!days ? "sum_campaign_nodays" : days === 1 ? "sum_campaign_one" : "sum_campaign", values)];
    if (h) out.push(T("sum_hold", { total: h.total + " " + h.currency, budget: h.budget + " " + h.currency,
                                    fee: h.fee + " " + h.currency }));
    out.push(T("sum_paused"), T("sum_second"));
    return out;
  }

  function mandate(payload) {
    const m = (payload.content || {}).mandate;
    if (!m) return [];
    const out = [T("sum_mandate", { n: m.max_campaigns, amount: m.max_daily_budget, currency: m.currency,
                                    date: new Date(m.valid_until * 1000).toLocaleDateString() })];
    const f = m.funding;
    if (f) out.push(T("sum_mandate_pay", { charge: major(f.max_charge_minor, m.currency),
      cap: major(f.funding_cap_minor, m.currency), currency: m.currency, days: f.period_days }));
    out.push(T("sum_mandate_signature"));
    return out;
  }

  function refund(payload) {
    const r = (payload.content || {}).refund;
    if (!r) return [];
    return [T("sum_refund", { amount: money(r.amount_minor, r.currency) }), T("sum_refund_final")];
  }

  // The sentences of the card for this payload (pure; the page and the tests call this).
  function lines(payload, asked) {
    const action = ((payload || {}).fields || {}).action;
    if (action === "campaign.create") return campaign(payload, asked || null);
    if (action === "mandate.grant") return mandate(payload);
    if (action === "funding.refund") return refund(payload);
    return [];
  }

  function render() {
    const box = document.getElementById("ap-summary");
    const list = document.getElementById("ap-summary-list");
    if (!box || !list) return;
    const sentences = current ? lines(current, hold) : [];
    list.replaceChildren(...sentences.map((s) => {
      const li = document.createElement("li");
      li.textContent = s;
      return li;
    }));
    box.hidden = sentences.length === 0;
  }

  // A new approval is on screen: draw its card. The hold is added when the order is known.
  function show(payload) {
    current = payload;
    hold = null;
    render();
  }

  // paypal.js: the order the studio opened ({total, budget, fee, currency, days}, display strings).
  function setHold(h) {
    hold = h;
    render();
  }

  return { lines, show, setHold };
})();
