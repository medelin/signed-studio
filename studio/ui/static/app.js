// Muse Studio Lite — studio page: run a brief, follow receipts live, sign the approval.
// Keys and signing live in signer.js (non-extractable WebCrypto Ed25519). All model
// output is rendered with textContent, never as HTML.
"use strict";

const $ = (id) => document.getElementById(id);
const root = $("studio");
let loggedIn = root.dataset.loggedIn === "yes";
const readOnly = root.dataset.readOnly === "yes";
let source = null;
let totalCost = 0;
let shownPending = null;
// Set when the brief came from a voice recording: the run is then a transcript run
// (enveloped, Standard tier) even after the operator edits the text.
let voiceBriefId = null;

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

const postJSON = Signer.postJSON;

// -- login ----------------------------------------------------------------------------

const loginForm = $("login-form");
if (loginForm) {
  loginForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    status("login-status", T("signing_in"));
    const r = await Signer.login($("login-user").value, $("login-pass").value);
    if (!r.ok) { status("login-status", T("sign_in_failed", { error: r.data.error || r.status }), true); return; }
    loggedIn = true;
    try { await Signer.registerKey(); } catch (e) { status("login-status", e.message, true); return; }
    $("login-panel").hidden = true;
    if (!readOnly) { $("run-button").disabled = false; $("rec-button").disabled = false; }
    status("run-status", T("signed_in_key", { key: Signer.approver() }));
    $("brief").focus();
  });
}

// -- rendering ------------------------------------------------------------------------

function renderPlan(plan, reasoning) {
  const box = $("plan");
  if (!plan) return;
  box.replaceChildren();
  box.classList.remove("empty");
  box.append(el("p", T("audience", { value: plan.audience })));
  box.append(el("p", T("budget", { pln: plan.budget_pln_daily, placements: plan.placements.join(", ") })));
  const ol = el("ol");
  for (const a of plan.angles) ol.append(el("li", a.name + " — " + a.hook + " (" + a.persona + ")"));
  box.append(ol);
  if (reasoning && reasoning.length) $("thinking").textContent = T("reasoning", { text: reasoning[reasoning.length - 1] });
}

function renderCopy(copy, reviewedIndex) {
  const box = $("copy");
  if (!copy) return;
  box.replaceChildren();
  box.classList.remove("empty");
  copy.variants.forEach((v, i) => {
    const card = el("article", null, "card");
    card.append(el("h3", (i + 1) + ". " + v.headline + (i === reviewedIndex ? T("sent_for_review") : "")));
    card.append(el("p", v.primary_text));
    card.append(el("p", v.description, "hint"));
    card.append(el("span", v.cta.replace(/_/g, " "), "cta"));
    box.append(card);
  });
  if (copy.risks.length) box.append(el("p", T("risks", { list: copy.risks.join("; ") }), "hint"));
}

function renderImages(runId, creatives) {
  const box = $("images");
  if (!creatives) return;
  box.replaceChildren();
  box.classList.remove("empty");
  const cut = el("figure");
  const ci = el("img");
  ci.src = "/assets/" + runId + "/cutout.png";
  ci.alt = T("cutout_alt", { px: creatives.foreground_pixels });
  cut.append(ci, el("figcaption", T("cutout_caption", { px: creatives.foreground_pixels })));
  box.append(cut);
  for (const im of creatives.images) {
    const fig = el("figure");
    const img = el("img");
    img.src = "/assets/" + runId + "/" + im.placement + ".png";
    img.alt = T("creative_alt", { placement: im.placement, w: im.width, h: im.height });
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
  box.replaceChildren();
  if (!review) {
    box.classList.add("empty");
    box.textContent = T("no_review");
    return;
  }
  box.classList.remove("empty");
  const head = el("p", T("combined"));
  head.append(el("span", review.verdict, "verdict " + review.verdict));
  box.append(head);
  for (const f of review.formats || []) {
    const fh = el("h3", f.placement + " ");
    fh.append(el("span", f.verdict, "verdict " + f.verdict));
    box.append(fh);
    box.append(renderReviewBlock(T("spark"), f.primary));
    if (f.second) {
      box.append(renderReviewBlock(T("spark_open"), f.second));
    } else {
      box.append(el("p", T("second_opinion", { state: f.second_opinion }), "hint"));
    }
  }
  if (review.disagreements.length) {
    const ul = el("ul", null, "disagree");
    for (const d of review.disagreements) ul.append(el("li", T("disagreement", { text: d })));
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
  if (state.pending_id && state.pending_id !== shownPending && st === "awaiting_approval") {
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
  if (!loggedIn) { status("approve-status", T("sign_to_approve"), true); $("approve-button").disabled = true; return; }
  try {
    const data = await Signer.loadPayload(pendingId);
    currentPayload = data;
    $("ap-action").textContent = T("item", { action: data.fields.action, id: data.fields.item_id });
    $("ap-sha").textContent = data.content_sha256;
    $("ap-head").textContent = data.chain_head;
    $("ap-key").textContent = Signer.approver();
    $("ap-exp").textContent = new Date(data.expires_ts * 1000).toLocaleTimeString();
    $("ap-content").textContent = JSON.stringify(data.content, null, 2);
    $("ap-message").textContent = atob(data.message_b64);
    $("approve-button").disabled = data.status !== "pending";
    status("approve-status", data.status === "pending" ? T("ready") : T("already", { status: data.status }));
  } catch (e) {
    status("approve-status", e.message, true);
  }
}

$("approve-button").addEventListener("click", async () => {
  if (!currentPayload) return;
  const btn = $("approve-button");
  btn.disabled = true;
  status("approve-status", T("signing"));
  try {
    const data = await Signer.sign(currentPayload);
    const r = { data };
    $("signature").hidden = false;
    $("sig-value").textContent = r.data.signature;
    $("sig-receipt").textContent = r.data.receipt_id + " · " + r.data.receipt_hash.slice(0, 16);
    if (r.data.executed && r.data.result) {
      status("approve-status", T("signed_executed", { adapter: r.data.result.adapter }));
      showCampaign(currentPayload.content.run_id, r.data.result.campaign_item_id);
    } else if (r.data.execution_error) {
      status("approve-status", T("signed_failed", { error: r.data.execution_error }), true);
    } else {
      status("approve-status", T("signed_only"));
    }
    status("run-status", T("approved", { run: currentPayload.content.run_id }));
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
  status("run-status", T("following", { run: runId }));
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
  source.onerror = () => status("run-status", T("connection_lost"), true);
}

$("brief-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("run-button").disabled = true;
  status("run-status", T("starting"));
  const body = { brief: $("brief").value };
  if (voiceBriefId) body.brief_id = voiceBriefId;
  const r = await postJSON("/api/runs", body);
  if (r.ok) voiceBriefId = null;
  if (r.status === 409 && r.data.run_id) { follow(r.data.run_id); return; }
  if (!r.ok) { status("run-status", T("cannot_start", { error: r.data.error || r.status }), true); $("run-button").disabled = false; return; }
  follow(r.data.run_id);
});

$("open-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const id = $("open-run").value.trim();
  if (/^[A-Za-z0-9_-]{1,40}$/.test(id)) follow(id);
});

(async () => {
  if (loggedIn) {
    try { await Signer.registerKey(); status("run-status", T("your_key", { key: Signer.approver() })); }
    catch (e) { status("run-status", e.message, true); }
  }
  const run = new URLSearchParams(location.search).get("run");
  if (run && /^[A-Za-z0-9_-]{1,40}$/.test(run)) follow(run);
})();

// -- voice brief ----------------------------------------------------------------------
// MediaRecorder → raw audio POST /api/briefs/voice → ffmpeg + Muse Voice Transcribe.

(() => {
  const recBtn = $("rec-button");
  const stopBtn = $("stop-button");
  if (!recBtn || !window.MediaRecorder) {
    if (recBtn) { recBtn.disabled = true; status("voice-status", T("no_recording")); }
    return;
  }
  const MAX_REC_S = 60;
  const MAX_BYTES = 32 * 1024 * 1024;
  let recorder = null;
  let chunks = [];
  let timer = null;
  let started = 0;

  function fmt(ms) {
    const s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") + "." + String(Math.floor((ms % 1000) / 100));
  }

  async function upload(blob) {
    if (blob.size > MAX_BYTES) { status("voice-status", T("too_big"), true); return; }
    status("voice-status", T("transcribing"));
    const r = await fetch("/api/briefs/voice", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": blob.type || "audio/webm" }, body: blob,
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) { status("voice-status", T("transcription_failed", { error: data.error || r.status }), true); return; }
    $("brief").value = data.transcript;
    voiceBriefId = data.brief_id;
    const ol = $("turns");
    ol.replaceChildren();
    for (const t of data.turns) ol.append(el("li", fmt(t.start_ms) + "–" + fmt(t.end_ms) + "  " + t.text));
    status("voice-status", T("transcribed", { seconds: data.seconds.toFixed(1), cost: data.cost_usd.toFixed(5) })
      + " " + T(data.audio_kept ? "audio_kept" : "audio_deleted") + " " + T("edit_then_run"));
    $("brief").focus();
  }

  recBtn.addEventListener("click", async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        clearInterval(timer);
        stopBtn.hidden = true;
        recBtn.hidden = false;
        upload(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
      };
      recorder.start();
      started = Date.now();
      recBtn.hidden = true;
      stopBtn.hidden = false;
      stopBtn.focus();
      status("voice-status", T("recording"));
      timer = setInterval(() => {
        const ms = Date.now() - started;
        $("rec-timer").textContent = fmt(ms);
        if (ms >= MAX_REC_S * 1000 && recorder.state === "recording") recorder.stop();
      }, 200);
    } catch (e) {
      status("voice-status", T("no_mic", { error: e.message }), true);
    }
  });
  stopBtn.addEventListener("click", () => { if (recorder && recorder.state === "recording") recorder.stop(); });
})();

// -- campaign + activation ------------------------------------------------------------

let currentCampaign = null;

async function showCampaign(runId, itemId) {
  const r = await fetch("/api/campaigns/" + encodeURIComponent(itemId), { credentials: "same-origin" });
  if (!r.ok) return;
  const c = await r.json();
  currentCampaign = c;
  $("campaign-panel").hidden = false;
  $("campaign-summary").textContent = T("campaign", { name: c.name, status: c.status,
    budget: (c.daily_budget_minor / 100).toFixed(2), adapter: c.adapter, ceiling: c.ceiling_pln });
  const box = $("preview");
  box.replaceChildren();
  const p = c.preview || {};
  if (p.kind === "iframe" && /^https:\/\/www\.facebook\.com\/ads\/api\/preview_iframe\.php\?/.test(p.src)) {
    const f = el("iframe");
    f.src = p.src; f.width = "320"; f.height = "560"; f.title = T("preview_title");
    f.setAttribute("sandbox", "allow-scripts allow-same-origin");
    box.append(f);
  } else if (p.kind === "inline") {
    const card = el("article", null, "card feed");
    card.append(el("p", p.fields.message));
    const img = el("img");
    img.src = "/assets/" + runId + "/feed_1x1.png";
    img.alt = T("preview_alt");
    card.append(img, el("h3", p.fields.name), el("p", p.fields.description, "hint"),
                el("span", p.fields.cta.replace(/_/g, " "), "cta"));
    card.append(el("p", T("mock_preview"), "hint"));
    box.append(card);
  } else {
    box.append(el("p", T("no_preview", { error: c.preview_error || "no preview" }), "hint"));
  }
  $("activate-button").disabled = c.status !== "PAUSED" || !loggedIn || readOnly;
  $("campaign-h").focus();
}

$("activate-button").addEventListener("click", async () => {
  if (!currentCampaign) return;
  const btn = $("activate-button");
  btn.disabled = true;
  try {
    const r = await postJSON("/api/campaigns/" + encodeURIComponent(currentCampaign.id) + "/activate", {});
    if (!r.ok) throw new Error(T("activation_refused", { error: r.data.error || r.status }));
    const payload = await Signer.loadPayload(r.data.approval_id);
    status("activate-status", T("signing_activation", { budget: (payload.content.daily_budget_minor / 100).toFixed(2) }));
    const done = await Signer.sign(payload);
    if (!done.executed) throw new Error(T("not_activated", { error: done.execution_error || "no executor" }));
    status("activate-status", T("activated", { adapter: done.result.adapter, sig: done.signature.slice(0, 16) }));
    showCampaign(currentCampaign.run_id, currentCampaign.id);
  } catch (e) {
    status("activate-status", e.message, true);
    btn.disabled = false;
  }
});
