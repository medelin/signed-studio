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
let shownGoogle = null;
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
    if (!r.ok) { status("login-status", T("sign_in_failed", { error: E(r.data, r.status) }), true); return; }
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
  box.append(el("p", T("budget", { amount: plan.budget_daily, placements: plan.placements.join(", ") })));
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
    card.append(el("span", TV("cta", v.cta), "cta"));
    box.append(card);
  });
  if (copy.risks.length) box.append(el("p", T("risks", { list: copy.risks.join("; ") }), "hint"));
}

function samLine(check) {
  if (check.status === "not_recorded") return T("sam_not_recorded");
  if (check.status === "skipped") return T("sam_skipped");
  if (check.status === "failed") return T("sam_failed", { error: check.error || "-" });
  if (!check.matches || check.iou === null || check.iou === undefined) return T("sam_missing");
  let line = T("sam_ok", { iou: Number(check.iou).toFixed(2), area: (100 * check.area).toFixed(1) });
  if (check.edges && check.edges.length) line += T("sam_edges", { edges: check.edges.join(", ") });
  if (check.people) line += T("sam_people", { n: check.people });
  return line;
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
    if (im.placement === "video_9x16") continue;  // shown with its video below
    const fig = el("figure");
    const img = el("img");
    img.src = "/assets/" + runId + "/" + im.placement + ".png";
    img.alt = T("creative_alt", { placement: im.placement, w: im.width, h: im.height });
    img.loading = "lazy";
    const caption = el("figcaption", im.placement + " · " + im.width + "×" + im.height + " · sha256 " + im.sha256.slice(0, 12));
    const check = im.sam_check;
    if (check && check.status === "ok" && check.overlay) {
      // SAM mask overlay (an RGBA PNG made by the server), hidden until toggled
      const frame = el("div", null, "mask-frame");
      const mask = el("img", null, "mask-overlay");
      mask.src = "/assets/" + runId + "/" + im.placement + "_mask.png";
      mask.alt = T("sam_overlay_alt", { placement: im.placement });
      mask.loading = "lazy";
      mask.hidden = true;
      frame.append(img, mask);
      const toggle = el("button", T("sam_show"), "mask-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-pressed", "false");
      toggle.addEventListener("click", () => {
        mask.hidden = !mask.hidden;
        toggle.setAttribute("aria-pressed", String(!mask.hidden));
        toggle.textContent = T(mask.hidden ? "sam_show" : "sam_hide");
      });
      fig.append(frame, caption, el("p", samLine(check), "hint sam-line"), toggle);
    } else {
      fig.append(img, caption);
      if (check) fig.append(el("p", samLine(check), "hint sam-line"));
    }
    box.append(fig);
  }
  if (creatives.video) {
    const fig = el("figure");
    const v = el("video");
    v.src = "/assets/" + runId + "/video_9x16.mp4";
    v.poster = "/assets/" + runId + "/video_9x16.png";
    v.controls = true;
    v.preload = "none";
    v.setAttribute("aria-label", T("video_alt", { s: creatives.video.seconds }));
    fig.append(v, el("figcaption", T("video_caption", { s: creatives.video.seconds, model: creatives.video.storyboard })));
    box.append(fig);
  }
}

// The source a finding stands on (a Meta policy or spec page; "muse:grounding" for the
// rule that only lets the ad say what the owner confirmed). Only https links are made.
function sourceLink(source) {
  if (!source) return null;
  if (!/^https:\/\//.test(source)) {
    const label = source === "muse:grounding" ? T("source_muse_grounding") : T("source_muse_prompt");
    return el("span", "(" + label + ")", "hint");
  }
  const a = el("a", T("source_link"));
  a.href = source;
  a.rel = "noopener noreferrer";
  a.target = "_blank";
  return a;
}

function renderReviewBlock(title, review) {
  const div = el("div");
  const h = el("h3", title + " ");
  h.append(el("span", review.verdict, "verdict " + review.verdict));
  div.append(h, el("p", review.summary));
  if (review.findings.length) {
    const ul = el("ul");
    for (const f of review.findings) {
      const li = el("li", f.rule + " (" + TV("severity", f.severity) + "): " + f.evidence, "finding finding-" + f.severity);
      const link = sourceLink(f.source);
      if (link) li.append(" ", link);
      ul.append(li);
    }
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

// The reviewer -> rewrite loop: what was blocked and why, the old text struck through, the
// rewritten text and the verdict of the second review.
function loopVariant(title, v, struck) {
  const card = el("article", null, "card loop-card" + (struck ? " loop-old" : " loop-new"));
  card.append(el("h4", title));
  const body = el("div", null, struck ? "struck" : "");
  body.append(el("p", v.headline, "loop-headline"), el("p", v.primary_text), el("p", v.description, "hint"));
  card.append(body);
  return card;
}

function renderLoop(rewrites) {
  const panel = $("loop-panel");
  const box = $("loop");
  panel.hidden = !(rewrites && rewrites.length);
  box.replaceChildren();
  for (const rw of rewrites || []) {
    const rules = [...new Set(rw.findings.map((f) => f.rule))].join(", ");
    const args = { n: rw.variant_index + 1, rules: rules, verdict: rw.after_verdict };
    const banner = el("p", T(rw.after_verdict ? "loop_banner" : "loop_banner_wait", args), "loop-banner");
    box.append(banner);
    const ul = el("ul", null, "loop-findings");
    for (const f of rw.findings) ul.append(el("li", T("loop_finding", { rule: f.rule, evidence: f.evidence })));
    box.append(ul);
    const pair = el("div", null, "loop-pair");
    pair.append(loopVariant(T("loop_before"), rw.before, true));
    if (rw.after) pair.append(loopVariant(T("loop_after"), rw.after, false));
    box.append(pair);
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
  renderLoop(state.rewrites);
  if (state.pending_id && state.pending_id !== shownPending && st === "awaiting_approval") {
    shownPending = state.pending_id;
    showApproval(state.pending_id);
  }
  if (state.google_pending_id && state.google_pending_id !== shownGoogle && st === "awaiting_approval") {
    shownGoogle = state.google_pending_id;
    showGoogle(state.google_pending_id);
  } else if (state.google_error && shownGoogle !== "error") {
    shownGoogle = "error";
    $("google-panel").hidden = false;
    $("google-spec").hidden = $("google-button").hidden = true;
    status("google-status", T("g_error", { error: E({ error: state.google_error }, state.google_error) }), true);
  }
}

// -- Google Search: a second approval for the same brief ---------------------------------

let googleApproval = null;
let googleCampaign = "";

function fillList(id, items) {
  const ul = $(id);
  ul.replaceChildren();
  for (const x of items) ul.append(el("li", x));
}

async function showGoogle(pendingId) {
  const panel = $("google-panel");
  panel.hidden = false;
  $("google-spec").hidden = false;
  $("google-button").hidden = false;
  if (!loggedIn) { status("google-status", T("sign_to_approve"), true); $("google-button").disabled = true; return; }
  try {
    const data = await Signer.loadPayload(pendingId);
    googleApproval = data;
    const g = data.content.google;
    $("gg-name").textContent = g.campaign.name;
    $("gg-budget").textContent = T("g_budget", { budget: (g.campaign.daily_budget_micros / 1e6).toFixed(2), currency: g.currency });
    $("gg-geo").textContent = g.geo.countries.join(", ");
    fillList("gg-keywords", g.keywords.map((k) => k.text));
    fillList("gg-headlines", g.ad.headlines);
    fillList("gg-descriptions", g.ad.descriptions);
    $("gg-spec").textContent = data.fields.spec_sha256 || "";
    $("google-button").disabled = data.status !== "pending";
    status("google-status", data.status === "pending" ? T("ready") : T("already", { status: data.status }));
  } catch (e) {
    status("google-status", e.message, true);
  }
}

$("google-button").addEventListener("click", async () => {
  if (!googleApproval) return;
  const btn = $("google-button");
  btn.disabled = true;
  status("google-status", T("signing"));
  try {
    const done = await Signer.sign(googleApproval);
    if (done.executed && done.result) {
      googleCampaign = done.result.campaign_item_id;
      status("google-status", T("g_created", { adapter: done.result.adapter, sig: done.signature.slice(0, 16) }));
      $("google-activate").hidden = readOnly;
    } else {
      status("google-status", T("g_failed", { error: E({ error: done.execution_error }, done.execution_error || "") }), true);
    }
  } catch (e) {
    status("google-status", e.message, true);
    btn.disabled = false;
  }
});

$("google-activate").addEventListener("click", async () => {
  const btn = $("google-activate");
  btn.disabled = true;
  try {
    const r = await postJSON("/api/google/" + encodeURIComponent(googleCampaign) + "/activate", {});
    if (!r.ok) throw new Error(T("activation_refused", { error: E(r.data, r.status) }));
    const payload = await Signer.loadPayload(r.data.approval_id);
    const c = payload.content;
    status("google-status", T("g_signing_activation", { budget: (c.daily_budget_micros / 1e6).toFixed(2), currency: c.currency }));
    const done = await Signer.sign(payload);
    if (!done.executed) throw new Error(T("not_activated", { error: done.execution_error || T("no_executor") }));
    status("google-status", T("g_activated", { adapter: done.result.adapter, sig: done.signature.slice(0, 16) }));
  } catch (e) {
    status("google-status", e.message, true);
    btn.disabled = false;
  }
});

// -- approval -------------------------------------------------------------------------

let currentPayload = null;

// The signed pixel check (website leads and sales): a badge and one line per check. A
// failed check cannot be signed (the server refuses it too: pixel_check_failed).
const PIXEL_BADGE = { ok: ["OK", "pixel_ok"], warn: ["WARN", "pixel_warn"], fail: ["BLOCK", "pixel_fail"] };

function showPixel(px) {
  const list = $("ap-pixel");
  const badge = $("ap-pixel-badge");
  list.replaceChildren();
  badge.replaceChildren();
  if (!px) return;
  const [cls, key] = PIXEL_BADGE[px.verdict] || PIXEL_BADGE.fail;
  badge.append(el("span", T(key), "verdict " + cls));
  const events = px.events_7d ? px.events_7d.total : T("pixel_unknown");
  list.append(el("li", T("pixel_head", { id: px.pixel_id, fired: px.last_fired_time || T("pixel_never"), events })));
  for (const c of px.checks || []) {
    const key = "pixel_c_" + c.code;  // a code without a line shows its English detail
    const line = T(key);
    const mark = { ok: "OK", warn: "WARN", fail: "BLOCK" }[c.level] || "BLOCK";
    const li = el("li");
    li.append(el("span", mark, "verdict " + mark), " ", line === key ? c.detail : line);
    list.append(li);
  }
}

// A2: what the owner asked for and the copy left out, with the rule in one sentence, the
// policy link and the phrasing used instead. Recordings made before the structured field
// only have free-text risks: those are shown as plain lines. The owner can overrule by
// editing the brief (the link goes back to it).
function ruleSentence(id) {
  const key = "rule_" + id;
  const text = T(key);
  return text === key ? "" : text;
}

function showLeftOut(content) {
  const box = $("ap-left-out");
  const list = $("ap-left-out-list");
  list.replaceChildren();
  const items = Array.isArray(content.left_out) ? content.left_out : [];
  const notes = Array.isArray(content.risks) ? content.risks : [];
  box.hidden = !items.length && !notes.length;
  if (box.hidden) return;
  for (const x of items) {
    const li = el("li", null, "left-out-item");
    li.append(el("p", T("left_out_asked", { asked: x.asked }), "left-out-asked"));
    const rule = el("p", T("left_out_rule", { rule: x.rule, sentence: ruleSentence(x.rule) }), "hint");
    const link = sourceLink(x.source);
    if (link) rule.append(" ", link);
    li.append(rule);
    if (x.instead) li.append(el("p", T("left_out_instead", { instead: x.instead })));
    list.append(li);
  }
  if (notes.length) {
    const li = el("li", null, "left-out-notes");
    li.append(el("p", T("left_out_notes"), "hint"));
    const ul = el("ul");
    for (const n of notes) ul.append(el("li", n));
    li.append(ul);
    list.append(li);
  }
  const back = el("li", null, "left-out-overrule");
  const a = el("a", T("left_out_edit"));
  a.href = "#brief-panel";
  back.append(el("span", T("left_out_overrule") + " "), a);
  list.append(back);
}

async function showApproval(pendingId) {
  const panel = $("approve-panel");
  panel.hidden = false;
  $("approve-h").focus();
  if (!loggedIn) { status("approve-status", T("sign_to_approve"), true); $("approve-button").disabled = true; return; }
  try {
    const data = await Signer.loadPayload(pendingId);
    currentPayload = data;
    $("ap-action").textContent = T("item", { action: data.fields.action, id: data.fields.item_id });
    showLeftOut(data.content || {});
    $("ap-sha").textContent = data.content_sha256;
    $("ap-head").textContent = data.chain_head;
    const briefSha = data.fields.brief_sha256 || "";
    $("ap-brief").textContent = briefSha;
    $("ap-brief-dt").hidden = $("ap-brief-dd").hidden = !briefSha;
    const spec = data.fields.spec_sha256 || "";
    $("ap-spec").textContent = spec;
    $("ap-spec-dt").hidden = $("ap-spec-dd").hidden = !spec;
    const d = data.content.delivery;
    if (d) {
      const promoted = Object.entries(d.promoted_object || {}).map(([k, v]) => k + "=" + v).join(", ");
      $("ap-delivery").textContent = T("delivery", { objective: d.objective, goal: d.optimization_goal,
        destination: d.destination_type ? " · " + d.destination_type : "", promoted: promoted || T("delivery_none") });
    }
    $("ap-delivery-dt").hidden = $("ap-delivery-dd").hidden = !d;
    const res = data.content.audience_resolution;
    const list = $("ap-audience");
    list.replaceChildren();
    if (res) {
      for (const x of res.interests || []) list.append(el("li", T("aud_interest", { hint: x.hint, name: x.name, id: x.id })));
      for (const x of res.excluded_interests || []) list.append(el("li", T("aud_excluded", { hint: x.hint, name: x.name, id: x.id })));
      for (const x of res.locales || []) list.append(el("li", T("aud_locale", { hint: x.hint, name: x.name, id: x.key })));
      for (const h of res.unresolved || []) list.append(el("li", T("aud_unresolved", { hint: h })));
      const tg = data.content.targeting || {};
      const auto = tg.targeting_automation && tg.targeting_automation.advantage_audience === 1;
      list.append(el("li", T(auto ? "aud_advantage_on" : "aud_advantage_off")));
      if (data.content.lookalike) list.append(el("li", T("aud_lookalike", { id: data.content.lookalike.origin_audience_id, pct: Math.round(data.content.lookalike.lookalike_spec.ratio * 100) })));
    }
    $("ap-audience-dt").hidden = $("ap-audience-dd").hidden = !res;
    const px = data.content.pixel_check;
    showPixel(px);
    $("ap-pixel-dt").hidden = $("ap-pixel-dd").hidden = !px;
    $("ap-key").textContent = Signer.approver();
    $("ap-exp").textContent = new Date(data.expires_ts * 1000).toLocaleTimeString();
    $("ap-content").textContent = JSON.stringify(data.content, null, 2);
    $("ap-message").textContent = atob(data.message_b64);
    const pixelFailed = !!px && px.verdict !== "ok" && px.verdict !== "warn";
    $("approve-button").disabled = data.status !== "pending" || pixelFailed;
    if (pixelFailed && data.status === "pending") status("approve-status", T("pixel_fail"), true);
    else status("approve-status", data.status === "pending" ? T("ready") : T("already", { status: data.status }));
  } catch (e) {
    status("approve-status", e.message, true);
  }
}

$("approve-button").addEventListener("click", async () => {
  if (!currentPayload) return;
  const btn = $("approve-button");
  btn.disabled = true;
  if (Date.now() / 1000 >= currentPayload.expires_ts - 5) {
    // No time limit for the person: an expired window is re-issued for the same content,
    // and they sign the fresh bytes when ready.
    const r = await postJSON("/api/approvals/" + encodeURIComponent(currentPayload.approval_id) + "/renew", {});
    if (r.ok && r.data.renewed) {
      shownPending = r.data.approval_id;
      await showApproval(r.data.approval_id);
      status("approve-status", T("brief_renewed"));
      return;
    }
  }
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
    status("run-status", runId + ": " + TV("run", d.status));
  });
  source.addEventListener("end", (ev) => {
    const d = JSON.parse(ev.data);
    status("run-status", runId + ": " + TV("run", d.status), d.status === "failed");
    source.close();
    if (!readOnly && loggedIn) $("run-button").disabled = false;
  });
  source.onerror = () => status("run-status", T("connection_lost"), true);
}

// The brief form itself (draft → confirm → run) lives in brief.js; it starts runs here.
window.Studio = {
  follow,
  voiceBriefId: () => voiceBriefId,
  clearVoiceBrief: () => { voiceBriefId = null; },
};

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
    if (recBtn) { recBtn.disabled = true; status("voice-status", T("no_recording") + " " + T("brief_no_mic")); }
    return;
  }
  const MAX_REC_S = 600;  // the server's limit per recording; record again to add more
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
    const lang = document.documentElement.lang === "pl" ? "pl" : "en";  // languageBias
    const r = await fetch("/api/briefs/voice?lang=" + lang, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": blob.type || "audio/webm" }, body: blob,
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) { status("voice-status", T("transcription_failed", { error: E(data, r.status) }), true); return; }
    $("brief").value = data.transcript;
    voiceBriefId = data.brief_id;
    const ol = $("turns");
    ol.replaceChildren();
    for (const t of data.turns) ol.append(el("li", fmt(t.start_ms) + "–" + fmt(t.end_ms) + "  " + t.text));
    status("voice-status", T("transcribed", { seconds: data.seconds.toFixed(1), cost: data.cost_usd.toFixed(5) })
      + " " + T(data.audio_kept ? "audio_kept" : "audio_deleted") + " " + T("brief_edit_then_draft"));
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
      status("voice-status", T("brief_no_mic"), true);
      $("brief").focus();
    }
  });
  stopBtn.addEventListener("click", () => { if (recorder && recorder.state === "recording") recorder.stop(); });
})();

// -- campaign + activation ------------------------------------------------------------

let currentCampaign = null;
// Meta's currency offsets: these currencies have no subdivisions in the API (offset 1).
const OFFSET_ONE = ["CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"];
function minor(value, currency) {
  return OFFSET_ONE.includes(currency) ? String(value) : (value / 100).toFixed(2);
}

async function showCampaign(runId, itemId) {
  const r = await fetch("/api/campaigns/" + encodeURIComponent(itemId), { credentials: "same-origin" });
  if (!r.ok) return;
  const c = await r.json();
  currentCampaign = c;
  $("campaign-panel").hidden = false;
  $("campaign-summary").textContent = T("campaign", { name: c.name, status: c.status,
    budget: minor(c.daily_budget_minor, c.currency), currency: c.currency, adapter: c.adapter, ceiling: c.ceiling });
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
                el("span", TV("cta", p.fields.cta), "cta"));
    card.append(el("p", T("mock_preview"), "hint"));
    box.append(card);
  } else {
    box.append(el("p", T("no_preview", { error: c.preview_error || T("no_preview_default") }), "hint"));
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
    if (!r.ok) throw new Error(T("activation_refused", { error: E(r.data, r.status) }));
    const payload = await Signer.loadPayload(r.data.approval_id);
    status("activate-status", T("signing_activation", { budget: minor(payload.content.daily_budget_minor, payload.content.currency), currency: payload.content.currency }));
    const done = await Signer.sign(payload);
    if (!done.executed) throw new Error(T("not_activated", { error: done.execution_error || T("no_executor") }));
    status("activate-status", T("activated", { adapter: done.result.adapter, sig: done.signature.slice(0, 16) }));
    showCampaign(currentCampaign.run_id, currentCampaign.id);
  } catch (e) {
    status("activate-status", e.message, true);
    btn.disabled = false;
  }
});
