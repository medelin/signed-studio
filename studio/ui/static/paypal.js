// PayPal on the approval screen (studio.routes_paypal). Before the sign button on a
// campaign: the studio creates the PayPal order for the budget (amounts computed by the
// server from the signed payload), the owner approves it in PayPal, and the server
// replaces the approval with one that carries the funding block; the owner signs that
// one. Signing stays disabled until PayPal reports the payer approved. Sandbox only.
//
// Rest mode loads the PayPal JS SDK (v5 "Buttons", intent=authorize) from PayPal's own
// host, allowed by the CSP only when PAYPAL_ADAPTER=rest. Mock mode shows a button that
// plays the buyer's part (POST /mock-approve); nothing leaves the studio.
"use strict";

window.StudioPayPal = (() => {
  const $ = (id) => document.getElementById(id);
  let config = null;
  let sdk = null;

  function say(text, isError) {
    const node = $("paypal-status");
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  }

  async function loadConfig() {
    if (config !== null) return config;
    try {
      const r = await fetch("/api/paypal/config", { credentials: "same-origin" });
      config = r.ok ? await r.json() : { enabled: false };
    } catch (e) {
      config = { enabled: false };
    }
    return config;
  }

  function money(minor, currency) {
    const zero = ["HUF", "JPY", "TWD"].includes(currency);
    return (zero ? String(minor) : (minor / 100).toFixed(2)) + " " + currency;
  }

  function loadSdk(currency) {
    if (sdk) return sdk;
    sdk = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://www.paypal.com/sdk/js?client-id=" + encodeURIComponent(config.client_id)
        + "&intent=authorize&components=buttons&currency=" + encodeURIComponent(currency);
      s.onload = () => resolve(window.paypal);
      s.onerror = () => reject(new Error(T("paypal_sdk_failed")));
      document.head.append(s);
    });
    return sdk;
  }

  async function approved(approvalId, path, onFunded) {
    say(T("paypal_checking"));
    const r = await Signer.postJSON("/api/funding/" + encodeURIComponent(approvalId) + path, {});
    if (!r.ok) {
      say(E(r.data, r.status), true);
      return;
    }
    say(T("paypal_approved", { order: r.data.order_id }));
    onFunded(r.data.approval_id);
  }

  // Returns true while the owner still has to pay (the sign button stays disabled).
  async function prepare(data, onFunded) {
    const box = $("ap-paypal");
    if (!box) return false;
    box.hidden = true;
    if (data.fields.action !== "campaign.create") return false;
    const cfg = await loadConfig();
    if (!cfg.enabled) return false;
    box.hidden = false;
    const block = (data.content || {}).funding;
    $("paypal-button").replaceChildren();
    $("paypal-mock").hidden = true;
    if (block) {  // already funded: the block is part of what is signed
      $("ap-paypal-amount").textContent = T("paypal_held_on_sign", {
        amount: money(block.amount_minor, block.currency), order: block.order_id });
      say(T("paypal_ready"));
      return false;
    }
    say(T("paypal_creating"));
    const r = await Signer.postJSON("/api/funding/" + encodeURIComponent(data.approval_id) + "/order", {});
    if (!r.ok) {
      say(E(r.data, r.status), true);
      return true;
    }
    const o = r.data;
    $("ap-paypal-amount").textContent = T("paypal_amount", {
      total: o.total + " " + o.currency, budget: o.budget, fee: o.fee, pct: o.fee_pct,
      days: o.days, order: o.order_id });
    if (cfg.adapter === "mock") {
      const btn = $("paypal-mock");
      btn.hidden = false;
      btn.onclick = () => { btn.disabled = true; approved(data.approval_id, "/mock-approve", onFunded); };
      btn.disabled = false;
      say(T("paypal_mock_hint"));
      return true;
    }
    try {
      const paypal = await loadSdk(o.currency);
      say(T("paypal_pay_first"));
      await paypal.Buttons({
        style: { label: "pay", layout: "vertical" },
        createOrder: () => o.order_id,
        onApprove: () => approved(data.approval_id, "/payer-approved", onFunded),
        onCancel: () => say(T("paypal_cancelled"), true),
        onError: () => say(T("paypal_sdk_failed"), true),
      }).render("#paypal-button");
    } catch (e) {
      say(e.message, true);
    }
    return true;
  }

  return { prepare };
})();
