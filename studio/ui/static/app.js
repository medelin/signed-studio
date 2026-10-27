// Muse Studio Lite — studio page: run a brief, follow receipts live, sign the approval.
// The approver key is a WebCrypto Ed25519 key pair generated per session with
// extractable=false: the private key never leaves the browser. Only the public key is
// sent (POST /api/keys). All model output is rendered with textContent, never as HTML.
"use strict";

const $ = (id) => document.getElementById(id);
const root = $("studio");
let loggedIn = root.dataset.loggedIn === "yes";
const readOnly = root.dataset.readOnly === "yes";
let approverId = null;
let keyPair = null;
let source = null;
let totalCost = 0;
let shownPending = null;

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function status(id, text, isError) {
  const node = $(id);
  node.textContent = text;
  node.classList.toggle("error", Boolean(isError));
}

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function hex(bytes) {
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch (_) { /* empty body */ }
  return { ok: r.ok, status: r.status, data };
}

// -- approver key ---------------------------------------------------------------------

async function newKey() {
  return crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
}

// The CryptoKey pair is stored in IndexedDB as an opaque, non-extractable object, so a
// reload in the same session keeps the same registered key. Storage may be unavailable
// (private windows): then the key lives for this page only.
function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("muse-studio-approver", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("keys");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbKey(action, value) {
  try {
    const db = await idb();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("keys", action === "get" ? "readonly" : "readwrite");
      const store = tx.objectStore("keys");
      const req = action === "get" ? store.get("approver")
        : action === "put" ? store.put(value, "approver") : store.delete("approver");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  } catch (_) {
    return undefined;
  }
}

async function registerKey() {
  if (!loggedIn) return null;
  if (!window.crypto || !crypto.subtle) throw new Error("WebCrypto is not available");
  if (!keyPair) keyPair = (await idbKey("get")) || null;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (!keyPair) { keyPair = await newKey(); await idbKey("put", keyPair); }
    const raw = await crypto.subtle.exportKey("raw", keyPair.publicKey);  // public half only
    const r = await postJSON("/api/keys", { public_key: b64url(raw) });
    if (r.ok) { approverId = r.data.approver; return approverId; }
    if (r.status === 409) { keyPair = null; await idbKey("delete"); continue; }  // new pair
    throw new Error("key registration failed (" + (r.data.error || r.status) + ")");
  }
  throw new Error("key registration failed");
}

// -- login ----------------------------------------------------------------------------

const loginForm = $("login-form");
if (loginForm) {
  loginForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    status("login-status", "Signing in…");
    const r = await postJSON("/login", {
      username: $("login-user").value, password: $("login-pass").value,
    });
    if (!r.ok) { status("login-status", "Sign-in failed (" + (r.data.error || r.status) + ")", true); return; }
    loggedIn = true;
    try { await registerKey(); } catch (e) { status("login-status", e.message, true); return; }
    $("login-panel").hidden = true;
    if (!readOnly) $("run-button").disabled = false;
    status("run-status", "Signed in. Your approver key is " + approverId + ".");
    $("brief").focus();
  });
}

// -- rendering ------------------------------------------------------------------------

function renderPlan(plan, reasoning) {
  const box = $("plan");
  if (!plan) return;
  box.replaceChildren();
  box.classList.remove("empty");
  box.append(el("p", "Audience: " + plan.audience));
  box.append(el("p", "Budget: " + plan.budget_pln_daily + " PLN / day · placements: " + plan.placements.join(", ")));
  const ol = el("ol");
  for (const a of plan.angles) ol.append(el("li", a.name + " — " + a.hook + " (" + a.persona + ")"));
  box.append(ol);
  if (reasoning && reasoning.length) $("thinking").textContent = "Reasoning summary: " + reasoning[reasoning.length - 1];
}

function renderCopy(copy, reviewedIndex) {
  const box = $("copy");
  if (!copy) return;
  box.replaceChildren();
  box.classList.remove("empty");
  copy.variants.forEach((v, i) => {
    const card = el("article", null, "card");
    card.append(el("h3", (i + 1) + ". " + v.headline + (i === reviewedIndex ? " (sent for review)" : "")));
    card.append(el("p", v.primary_text));
    card.append(el("p", v.description, "hint"));
    card.append(el("span", v.cta.replace(/_/g, " "), "cta"));
    box.append(card);
  });
  if (copy.risks.length) box.append(el("p", "Risks: " + copy.risks.join("; "), "hint"));
}

function renderImages(runId, creatives) {
  const box = $("images");
  if (!creatives) return;
  box.replaceChildren();
  box.classList.remove("empty");
  const cut = el("figure");
  const ci = el("img");
  ci.src = "/assets/" + runId + "/cutout.png";
  ci.alt = "SAM 3.1 cut-out of the product (" + creatives.foreground_pixels + " foreground pixels)";
  cut.append(ci, el("figcaption", "SAM 3.1 cut-out · " + creatives.foreground_pixels + " px"));
  box.append(cut);
  for (const im of creatives.images) {
    const fig = el("figure");
    const img = el("img");
    img.src = "/assets/" + runId + "/" + im.placement + ".png";
    img.alt = "Ad creative for " + im.placement + " (" + im.width + "×" + im.height + ")";
    img.loading = "lazy";
    fig.append(img, el("figcaption", im.placement + " · " + im.width + "×" + im.height + " · sha256 " + im.sha256.slice(0, 12)));
    box.append(fig);
  }
}

function renderReviewBlock(title, review) {
  const div = el("div");
  const h = el("h3", title + " ");
  h.append(el("span", review.verdict, "verdict " + review.verdict));
  div.append(h, el("p", review.summary));
  if (review.findings.length) {
    const ul = el("ul");
    for (const f of review.findings) ul.append(el("li", f.rule + " (" + f.severity + "): " + f.evidence));
    div.append(ul);
  }
  return div;
}

function renderReview(review) {
  const box = $("review");
  if (!review) return;
  box.replaceChildren();
  box.classList.remove("empty");
  const head = el("p", "Combined verdict ");
  head.append(el("span", review.verdict, "verdict " + review.verdict));
  box.append(head);
  box.append(renderReviewBlock("Muse Spark 1.3 (vision)", review.primary));
  if (review.second) {
    box.append(renderReviewBlock("Muse Spark Open on Baseten (open weights)", review.second));
  } else {
    box.append(el("p", "Second opinion " + review.second_opinion + " (Muse Spark Open on Baseten).", "hint"));
  }
  if (review.disagreements.length) {
    const ul = el("ul", null, "disagree");
    for (const d of review.disagreements) ul.append(el("li", "Disagreement: " + d));
    box.append(ul);
  }
}

function addReceipt(r) {
  const tr = el("tr");
  const what = r.tool ? r.tool + (r.status ? " · " + r.status : "") : (r.model || "—");
  const calls = r.tool_calls && r.tool_calls.length ? " → " + r.tool_calls.join(", ") : "";
  tr.append(el("td", r.agent), el("td", r.kind, "kind " + r.kind), el("td", what + calls),
            el("td", r.tokens_in + "/" + r.tokens_out), el("td", "$" + r.cost_usd.toFixed(5)));
  const h = el("td");
  const code = el("code", r.hash.slice(0, 12));
  code.title = r.hash;
  h.append(code);
  tr.append(h);
  $("ticker").append(tr);
  totalCost += r.cost_usd;
  $("total-cost").textContent = "$" + totalCost.toFixed(5);
}

function renderState(runId, st, state) {
  renderPlan(state.plan, state.reasoning);
  renderCopy(state.copy, state.review ? state.review.variant_index : -1);
  renderImages(runId, state.creatives);
  renderReview(state.review);
  if (state.pending_id && state.pending_id !== shownPending && st !== "approved") {
    shownPending = state.pending_id;
    showApproval(state.pending_id);
  }
}

// -- approval -------------------------------------------------------------------------

let currentPayload = null;

async function showApproval(pendingId) {
  const panel = $("approve-panel");
  panel.hidden = false;
  $("approve-h").focus();
  if (!loggedIn) { status("approve-status", "Sign in to approve this run.", true); $("approve-button").disabled = true; return; }
  try {
    if (!approverId) await registerKey();
    const r = await fetch("/api/approvals/" + encodeURIComponent(pendingId) + "/payload?approver=" + encodeURIComponent(approverId), { credentials: "same-origin" });
    const data = await r.json();
    if (!r.ok) throw new Error("cannot load the approval (" + (data.error || r.status) + ")");
    currentPayload = data;
    $("ap-action").textContent = data.fields.action + " (item " + data.fields.item_id + ")";
    $("ap-sha").textContent = data.content_sha256;
    $("ap-head").textContent = data.chain_head;
    $("ap-key").textContent = approverId;
    $("ap-exp").textContent = new Date(data.expires_ts * 1000).toLocaleTimeString();
    $("ap-content").textContent = JSON.stringify(data.content, null, 2);
    $("ap-message").textContent = atob(data.message_b64);
    $("approve-button").disabled = data.status !== "pending";
    status("approve-status", data.status === "pending" ? "Ready to sign." : "Already " + data.status + ".");
  } catch (e) {
    status("approve-status", e.message, true);
  }
}

$("approve-button").addEventListener("click", async () => {
  if (!currentPayload || !keyPair) return;
  const btn = $("approve-button");
  btn.disabled = true;
  status("approve-status", "Signing in the browser…");
  try {
    const bytes = Uint8Array.from(atob(currentPayload.message_b64), (c) => c.charCodeAt(0));
    const sig = await crypto.subtle.sign({ name: "Ed25519" }, keyPair.privateKey, bytes);
    const r = await postJSON("/api/approvals/" + encodeURIComponent(currentPayload.approval_id),
                             { approver: approverId, signature: hex(sig) });
    if (!r.ok) throw new Error("approval refused: " + (r.data.error || r.status));
    $("signature").hidden = false;
    $("sig-value").textContent = r.data.signature;
    $("sig-receipt").textContent = r.data.receipt_id + " · " + r.data.receipt_hash.slice(0, 16);
    status("approve-status", "Signed and verified by the server. The campaign is approved (PAUSED); nothing went live.");
    status("run-status", currentPayload.content.run_id + ": approved");
  } catch (e) {
    status("approve-status", e.message, true);
    btn.disabled = false;
  }
});

// -- runs -----------------------------------------------------------------------------

function follow(runId) {
  if (source) source.close();
  $("ticker").replaceChildren();
  totalCost = 0;
  shownPending = null;
  history.replaceState(null, "", "/studio?run=" + encodeURIComponent(runId));
  status("run-status", "Following " + runId + "…");
  source = new EventSource("/api/runs/" + encodeURIComponent(runId) + "/events");
  source.addEventListener("receipt", (ev) => addReceipt(JSON.parse(ev.data)));
  source.addEventListener("state", (ev) => {
    const d = JSON.parse(ev.data);
    renderState(runId, d.status, d.state || {});
    status("run-status", runId + ": " + d.status.replace(/_/g, " "));
  });
  source.addEventListener("end", (ev) => {
    const d = JSON.parse(ev.data);
    status("run-status", runId + ": " + d.status.replace(/_/g, " "), d.status === "failed");
    source.close();
    if (!readOnly && loggedIn) $("run-button").disabled = false;
  });
  source.onerror = () => status("run-status", "Connection lost; reload to resume.", true);
}

$("brief-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("run-button").disabled = true;
  status("run-status", "Starting…");
  const r = await postJSON("/api/runs", { brief: $("brief").value });
  if (r.status === 409 && r.data.run_id) { follow(r.data.run_id); return; }
  if (!r.ok) { status("run-status", "Cannot start: " + (r.data.error || r.status), true); $("run-button").disabled = false; return; }
  follow(r.data.run_id);
});

$("open-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const id = $("open-run").value.trim();
  if (/^[A-Za-z0-9_-]{1,40}$/.test(id)) follow(id);
});

(async () => {
  if (loggedIn) {
    try { await registerKey(); status("run-status", "Your approver key is " + approverId + "."); }
    catch (e) { status("run-status", e.message, true); }
  }
  const run = new URLSearchParams(location.search).get("run");
  if (run && /^[A-Za-z0-9_-]{1,40}$/.test(run)) follow(run);
})();
