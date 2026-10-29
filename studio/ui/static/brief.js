// Muse Studio Lite — Brief v1 on the studio page: owner's words (typed, or a voice
// transcript they can edit) → producer draft → editable form → answer or strike every
// [FACT] question → confirm → the run. Voice works for every text field (Dictate), the
// draft can be read aloud with the browser's own speechSynthesis (no network), and every
// step is announced in the #brief-live region. No step has a time limit. All text is set
// with textContent; nothing here builds HTML from strings.
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("brief-form");
  const v1 = $("brief-v1-form");
  if (!form || !v1 || !window.Studio) return;
  const lang = document.documentElement.lang === "pl" ? "pl" : "en";
  const live = $("brief-live");
  let draft = null;  // {brief_id, draft_run_id, brief, ceiling, currency}
  let dictated = false;  // any voice input makes the run a transcript run (stricter)
  let facts = [];  // [{id, input}]
  let factSeq = 0;
  let reasons = [];  // [{text input, refs: Set of fact ids, box}]

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }
  function say(text) { live.textContent = ""; setTimeout(() => { live.textContent = text; }, 50); }
  function status(id, text, isError) {
    const node = $(id);
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  }
  const STEP_NAMES = ["step1", "step2", "step3", "step4"];
  function setStep(n, announce) {
    for (const li of $("brief-steps").querySelectorAll("li")) {
      const k = Number(li.dataset.bstep);
      li.dataset.state = k < n ? "done" : k === n ? "active" : "";
      if (k === n) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    }
    if (announce) say(T("brief_step", { n, name: $("brief-steps").children[n - 1].textContent }));
    return STEP_NAMES[n - 1];
  }

  // -- path helpers -------------------------------------------------------------------
  const ERR_PREFIX = "brief_err_";  // + a code from studio/brief.py problems() or the API
  const idOf = (path) => "bf-" + path.replace(/\./g, "-");
  function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
  function setPath(obj, path, value) {
    const keys = path.split(".");
    let o = obj;
    for (const k of keys.slice(0, -1)) { if (o[k] == null) o[k] = {}; o = o[k]; }
    o[keys[keys.length - 1]] = value;
  }
  const lines = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);

  // -- dictation: any text field, answers and facts -----------------------------------
  const canRecord = Boolean(window.MediaRecorder && navigator.mediaDevices);
  let active = null;  // {recorder, button, target}

  function labelFor(target) {
    const lab = target.id && document.querySelector('label[for="' + target.id + '"]');
    return lab ? lab.textContent.trim() : "";
  }

  async function stopDictation() {
    if (active && active.recorder.state === "recording") active.recorder.stop();
  }

  async function startDictation(button, target) {
    if (active) { await stopDictation(); return; }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (_) {
      status("confirm-status", T("brief_no_mic"), true);
      say(T("brief_no_mic"));
      target.focus();
      return;
    }
    const recorder = new MediaRecorder(stream);
    const chunks = [];
    active = { recorder, button, target };
    button.setAttribute("aria-pressed", "true");
    button.textContent = T("brief_dictate_stop");
    recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      button.setAttribute("aria-pressed", "false");
      button.textContent = T("brief_dictate");
      active = null;
      status("confirm-status", T("transcribing"));
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      const r = await fetch("/api/briefs/voice?purpose=field&lang=" + lang, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": blob.type || "audio/webm" }, body: blob,
      });
      let data = {};
      try { data = await r.json(); } catch (_) { /* empty */ }
      if (!r.ok) { status("confirm-status", T("transcription_failed", { error: E(data, r.status) }), true); target.focus(); return; }
      target.value = target.value.trim() ? target.value.trimEnd() + (target.tagName === "TEXTAREA" && target.dataset.kind === "lines" ? "\n" : " ") + data.transcript : data.transcript;
      target.dispatchEvent(new Event("input", { bubbles: true }));
      dictated = true;
      const msg = T("brief_dictated", { label: labelFor(target) });
      status("confirm-status", msg);
      say(msg);
      target.focus();
    };
    recorder.start();
    status("confirm-status", T("brief_dictating"));
    say(T("brief_dictating"));
  }

  function addDictate(target) {
    if (!canRecord || target.dataset.dictateReady) return;
    target.dataset.dictateReady = "1";
    const b = el("button", T("brief_dictate"), "secondary dictate");
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    b.setAttribute("aria-label", T("brief_dictate_into", { label: labelFor(target) }));
    b.addEventListener("click", () => startDictation(b, target));
    target.after(b);
  }

  // -- facts and reasons --------------------------------------------------------------
  function renumber() {
    facts.forEach((f, i) => {
      f.label.textContent = T("brief_fact_label", { n: i + 1 });
      f.remove.setAttribute("aria-label", T("brief_remove_fact", { n: i + 1 }));
    });
    renderReasonRefs();
  }

  function addFact(text, focus) {
    const id = "fact-" + (++factSeq);
    const li = el("li", null, "bv-item");
    const label = el("label");
    label.htmlFor = id + "-text";
    const input = el("input");
    input.id = id + "-text";
    input.value = text || "";
    input.setAttribute("aria-describedby", "hint-facts");
    input.addEventListener("input", renderReasonRefs);
    const remove = el("button", T("brief_remove"), "secondary");
    remove.type = "button";
    const f = { id, input, label, remove, li };
    remove.addEventListener("click", () => {
      facts = facts.filter((x) => x !== f);
      for (const r of reasons) r.refs.delete(id);
      li.remove();
      renumber();
      (facts.length ? facts[facts.length - 1].input : $("bv-add-fact")).focus();
    });
    li.append(label, input, remove);
    $("bv-fact-list").append(li);
    facts.push(f);
    addDictate(input);
    renumber();
    if (focus) input.focus();
    return f;
  }

  function renderReasonRefs() {
    reasons.forEach((r, i) => {
      r.label.textContent = T("brief_reason_label", { n: i + 1 });
      r.remove.setAttribute("aria-label", T("brief_remove_reason", { n: i + 1 }));
      r.box.replaceChildren(el("legend", T("brief_reason_facts")));
      if (!facts.length) r.box.append(el("p", T("brief_no_facts"), "hint"));
      facts.forEach((f, n) => {
        const lab = el("label");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = r.refs.has(f.id);
        cb.addEventListener("change", () => { if (cb.checked) r.refs.add(f.id); else r.refs.delete(f.id); });
        lab.append(cb, " " + (n + 1) + ". " + (f.input.value || "…"));
        r.box.append(lab);
      });
    });
  }

  function addReason(text, refIds, focus) {
    const i = reasons.length;
    const li = el("li", null, "bv-item");
    const label = el("label");
    const input = el("input");
    input.id = "reason-" + i + "-" + Date.now();
    label.htmlFor = input.id;
    input.value = text || "";
    input.setAttribute("aria-describedby", "hint-reasons err-bf-offer-reasons_to_believe-" + i);
    const box = el("fieldset", null, "radios");
    const remove = el("button", T("brief_remove"), "secondary");
    remove.type = "button";
    const err = el("p", null, "field-error");
    err.id = "err-bf-offer-reasons_to_believe-" + i;
    err.hidden = true;
    const r = { input, label, box, remove, li, refs: new Set(refIds || []) };
    remove.addEventListener("click", () => {
      reasons = reasons.filter((x) => x !== r);
      li.remove();
      renderReasonRefs();
      $("bv-add-reason").focus();
    });
    li.append(label, input, box, remove, err);
    $("bv-reason-list").append(li);
    reasons.push(r);
    addDictate(input);
    renderReasonRefs();
    if (focus) input.focus();
  }

  // -- [FACT] questions ---------------------------------------------------------------
  let questions = [];  // [{answer radio, strike radio, input, li}]

  function updateQuestions(announce) {
    const left = questions.filter((q) => !(q.strike.checked || (q.answer.checked && q.input.value.trim()))).length;
    if (!questions.length) return;
    setStep(left ? 3 : 4, false);
    if (announce) say(left ? T("brief_questions_left", { n: left }) : T("brief_questions_done"));
  }

  function renderQuestions(list) {
    const ol = $("bv-question-list");
    ol.replaceChildren();
    questions = [];
    $("bv-no-questions").hidden = list.length > 0;
    list.forEach((q, i) => {
      const n = i + 1;
      const li = el("li", null, "bv-item bv-question");
      const fs = el("fieldset");
      const errId = "err-bf-open_questions-" + i;
      fs.setAttribute("aria-describedby", "q-" + i + "-claim " + errId);
      fs.append(el("legend", T("brief_question", { n }) + ": " + q.question));
      const claim = el("p", q.claim ? T("brief_claim", { text: q.claim }) : "", "hint");
      claim.id = "q-" + i + "-claim";
      claim.hidden = !q.claim;
      const choice = el("div", null, "radios");
      choice.setAttribute("role", "radiogroup");
      choice.setAttribute("aria-label", T("brief_choice", { n }));
      const mk = (value, text) => {
        const lab = el("label");
        const r = el("input");
        r.type = "radio";
        r.name = "q-" + i;
        r.value = value;
        lab.append(r, " " + text);
        choice.append(lab);
        return r;
      };
      const answer = mk("answer", T("brief_answer"));
      const strike = mk("strike", T("brief_strike"));
      const tl = el("label", T("brief_answer_label", { n }));
      const input = el("input");
      input.id = "q-" + i + "-text";
      tl.htmlFor = input.id;
      input.setAttribute("aria-describedby", errId);
      const struck = el("p", T("brief_struck"), "hint struck");
      struck.hidden = true;
      const err = el("p", null, "field-error");
      err.id = errId;
      err.hidden = true;
      const entry = { answer, strike, input, li };
      const sync = (announce) => {
        input.disabled = strike.checked;
        const dictate = input.nextElementSibling;
        if (dictate && dictate.classList.contains("dictate")) dictate.disabled = strike.checked;
        struck.hidden = !strike.checked;
        li.classList.toggle("is-struck", strike.checked);
        updateQuestions(announce);
      };
      answer.addEventListener("change", () => { sync(true); input.focus(); });
      strike.addEventListener("change", () => sync(true));
      input.addEventListener("input", () => { if (input.value.trim()) answer.checked = true; sync(false); });
      input.addEventListener("change", () => updateQuestions(true));
      fs.append(claim, choice, tl, input, struck, err);
      li.append(fs);
      ol.append(li);
      questions.push(entry);
      addDictate(input);
    });
  }

  // -- objective: sub-choices of the chosen goal, and only the ids that row needs -----
  function syncObjective() {
    const goal = $("bf-objective-goal").value;
    const sub = $("bf-objective-sub");
    for (const opt of sub.querySelectorAll("option[data-goal]")) opt.disabled = opt.dataset.goal !== goal;
    for (const grp of sub.querySelectorAll("optgroup")) grp.hidden = grp.dataset.goal !== goal;
    if (sub.selectedOptions.length && sub.selectedOptions[0].disabled) sub.value = "";
    const first = sub.querySelector('option[data-goal="' + goal + '"]');
    const row = goal + ":" + (sub.value || (first ? first.value : ""));
    for (const box of v1.querySelectorAll("[data-needs]")) {
      const used = box.dataset.needs.split(" ").includes(row);
      const input = box.querySelector("input:not([type=checkbox]), select");
      box.hidden = !used && !(input && input.value);
    }
  }
  $("bf-objective-goal").addEventListener("change", syncObjective);
  $("bf-objective-sub").addEventListener("change", syncObjective);

  // -- fill and collect ---------------------------------------------------------------
  function fill(b) {
    for (const node of v1.querySelectorAll("[data-path]")) {
      const path = node.dataset.path;
      const value = getPath(b, path);
      if (path === "assets") { node.value = (b.assets || []).map((a) => a.note).join("\n"); continue; }
      if (node.type === "radio") node.checked = value === node.value;
      else if (node.type === "checkbox" && "multi" in node.dataset) node.checked = (value || []).includes(node.value);
      else if (node.type === "checkbox") node.checked = Boolean(value);
      else if (node.dataset.kind === "lines") node.value = (value || []).join("\n");
      else if (node.type === "number") node.value = value || value === 0 ? String(value) : "";
      else node.value = value == null ? "" : String(value);
    }
    syncObjective();
    facts = []; reasons = [];
    $("bv-fact-list").replaceChildren();
    $("bv-reason-list").replaceChildren();
    const ids = b.confirmed_facts.map((f) => addFact(f, false).id);
    for (const r of b.offer.reasons_to_believe) addReason(r.text, r.facts.map((n) => ids[n]).filter(Boolean), false);
    renderQuestions(b.open_questions);
    for (const node of v1.querySelectorAll("[data-dictate]")) addDictate(node);
  }

  function collect() {
    const b = JSON.parse(JSON.stringify(draft.brief));
    const multi = {};  // path -> checked values (checkbox groups marked data-multi)
    for (const node of v1.querySelectorAll("[data-path]")) {
      const path = node.dataset.path;
      if (path === "assets") {
        const kinds = new Map((draft.brief.assets || []).map((a) => [a.note, a.kind]));
        b.assets = lines(node.value).map((note) => ({ kind: kinds.get(note) || "other", note }));
      } else if (node.type === "radio") { if (node.checked) setPath(b, path, node.value); }
      else if (node.type === "checkbox" && "multi" in node.dataset) { multi[path] = multi[path] || []; if (node.checked) multi[path].push(node.value); }
      else if (node.type === "checkbox") setPath(b, path, node.checked);
      else if (node.dataset.kind === "lines") {
        const items = lines(node.value);
        setPath(b, path, path === "advertiser.countries" || path === "audience.excluded_countries" ? items.map((c) => c.toUpperCase()) : items);
      } else if (node.type === "number") {
        const n = node.value.trim() === "" ? 0 : Number(node.value);
        setPath(b, path, path.startsWith("audience.age") || ["audience.lookalike_percent", "placements.carousel_cards", "placements.copy_variants"].includes(path) ? Math.trunc(n) : n);
      } else setPath(b, path, path === "budget.currency" ? node.value.trim().toUpperCase() : node.value.trim());
    }
    for (const [path, values] of Object.entries(multi)) setPath(b, path, values);
    const keep = facts.filter((f) => f.input.value.trim());
    b.confirmed_facts = keep.map((f) => f.input.value.trim());
    const index = new Map(keep.map((f, i) => [f.id, i]));
    b.offer.reasons_to_believe = reasons.map((r) => ({
      text: r.input.value.trim(), facts: [...r.refs].filter((id) => index.has(id)).map((id) => index.get(id)).sort((x, y) => x - y),
    }));
    b.open_questions = draft.brief.open_questions;
    const resolutions = questions.map((q) => (q.strike.checked ? { action: "strike" }
      : q.answer.checked ? { action: "answer", text: q.input.value.trim() } : null));
    return { brief: b, resolutions };
  }

  // -- errors -------------------------------------------------------------------------
  function clearErrors() {
    for (const p of v1.querySelectorAll(".field-error")) { p.hidden = true; p.textContent = ""; }
    for (const n of v1.querySelectorAll("[aria-invalid]")) n.removeAttribute("aria-invalid");
    $("bv-errors").hidden = true;
    $("bv-error-list").replaceChildren();
  }

  function errTarget(path) {
    const parts = path.split(".");
    while (parts.length) {
      const id = idOf(parts.join("."));
      const p = $("err-" + id);
      if (p) return { p, id };
      parts.pop();
    }
    return { p: $("err-bf-confirmed_facts"), id: "bf-confirmed_facts" };
  }

  function showErrors(fields) {
    clearErrors();
    const list = $("bv-error-list");
    const entries = Object.entries(fields);
    for (const [path, code] of entries) {
      const { p, id } = errTarget(path);
      const msg = T(ERR_PREFIX + code, { ceiling: draft.ceiling, currency: draft.currency, google_currency: draft.google_currency || "" });
      p.textContent = msg;
      p.hidden = false;
      const holder = p.closest(".bf-field, .bv-item, fieldset");
      const control = $(id) || (holder && holder.querySelector("input, textarea, select"));
      if (control) control.setAttribute("aria-invalid", "true");
      const li = el("li");
      const a = el("a", (holder && holder.querySelector("legend, label") ? holder.querySelector("legend, label").textContent.trim() + ": " : "") + msg);
      a.href = "#" + (control ? control.id : p.id);
      a.addEventListener("click", (ev) => { ev.preventDefault(); if (control) control.focus(); });
      li.append(a);
      list.append(li);
    }
    $("bv-errors").hidden = false;
    $("bv-errors").focus();
    say(T("brief_errors", { n: entries.length }));
  }

  // -- read-back (browser speech, no network) -----------------------------------------
  if ("speechSynthesis" in window && window.SpeechSynthesisUtterance) {
    $("read-aloud").hidden = false;
    $("read-aloud").addEventListener("click", () => {
      const parts = [];
      for (const fs of v1.querySelectorAll(":scope > fieldset")) {
        parts.push(fs.querySelector("legend").textContent + ".");
        for (const node of fs.querySelectorAll("input:not([type=radio]):not([type=checkbox]), textarea, select")) {
          const value = node.tagName === "SELECT" ? node.options[node.selectedIndex].text : node.value.trim();
          if (value) parts.push((labelFor(node) || "") + ": " + value.replace(/\n/g, ", ") + ".");
        }
        for (const node of fs.querySelectorAll("input[type=radio]:checked, input[type=checkbox]:checked")) {
          parts.push(node.parentElement.textContent.trim() + ".");
        }
      }
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(parts.join(" "));
      u.lang = lang === "pl" ? "pl-PL" : "en-GB";
      u.onend = () => { $("read-stop").hidden = true; };
      speechSynthesis.speak(u);
      $("read-stop").hidden = false;
      status("confirm-status", T("brief_reading"));
    });
    $("read-stop").addEventListener("click", () => { speechSynthesis.cancel(); $("read-stop").hidden = true; $("read-aloud").focus(); });
  }

  // -- the flow -----------------------------------------------------------------------
  const example = $("brief-example");
  if (example) {
    example.addEventListener("change", () => {
      $("brief").value = example.selectedOptions[0].dataset.text;
      say(example.selectedOptions[0].textContent);
    });
  }
  $("bv-add-fact").addEventListener("click", () => addFact("", true));
  $("bv-add-reason").addEventListener("click", () => addReason("", [], true));
  $("redraft-button").addEventListener("click", () => {
    v1.hidden = true;
    setStep(1, true);
    $("brief").focus();
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = $("run-button");
    btn.disabled = true;
    status("run-status", T("brief_drafting"));
    say(T("brief_drafting"));
    const body = { text: $("brief").value, lang, dictated };
    const voiceId = Studio.voiceBriefId();
    if (voiceId) body.brief_id = voiceId;
    const r = await Signer.postJSON("/api/briefs/draft", body);
    btn.disabled = false;
    if (!r.ok) {
      const msg = T("brief_draft_failed", { error: E(r.data, r.status) });
      status("run-status", msg, true);
      say(msg);
      return;
    }
    Studio.clearVoiceBrief();
    if (r.data.source === "transcript") dictated = true;
    draft = r.data;
    clearErrors();
    v1.querySelectorAll("input, textarea, select, button").forEach((n) => { n.disabled = false; });
    fill(draft.brief);
    v1.hidden = false;
    setStep(2, false);
    const n = draft.brief.open_questions.length;
    const msg = n ? T("brief_drafted", { n }) : T("brief_drafted_none");
    status("run-status", msg);
    say(msg);
    if (n) setStep(3, false);
    $("bv-h").focus();
  });

  v1.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!draft) return;
    const { brief, resolutions } = collect();
    const missing = {};
    resolutions.forEach((x, i) => {
      if (x === null) missing["open_questions." + i] = "unresolved";
      else if (x.action === "answer" && !x.text) missing["open_questions." + i] = "answer_required";
    });
    if (Object.keys(missing).length) { showErrors(missing); return; }
    const btn = $("confirm-button");
    btn.disabled = true;
    status("confirm-status", T("brief_confirming"));
    const ow = $("open-weights-run");
    const r = await Signer.postJSON("/api/runs", { brief_id: draft.brief_id, brief_v1: brief, resolutions, dictated, open_weights: !!(ow && ow.checked) });
    btn.disabled = false;
    if (r.status === 422 && r.data.fields) { showErrors(r.data.fields); status("confirm-status", T("brief_errors", { n: Object.keys(r.data.fields).length }), true); return; }
    if (r.status === 409 && r.data.run_id) { Studio.follow(r.data.run_id); return; }
    if (!r.ok) { status("confirm-status", T("cannot_start", { error: E(r.data, r.status) }), true); return; }
    clearErrors();
    for (const li of $("brief-steps").querySelectorAll("li")) { li.dataset.state = "done"; li.removeAttribute("aria-current"); }
    const msg = T("brief_confirmed", { sha: r.data.brief_sha256.slice(0, 12), facts: r.data.facts });
    status("confirm-status", msg);
    say(msg);
    v1.querySelectorAll("input, textarea, select, button").forEach((n) => { n.disabled = true; });
    Studio.follow(r.data.run_id);
  });
})();
