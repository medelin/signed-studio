// Muse Studio Lite — the public tour (/try): reveal the recorded run step by step and
// verify its receipt chain in the browser. Reads only /try/data.json; sends nothing.
//
// Verification: every receipt's hash is the SHA-256 of its canonical JSON. Python writes
// floats such as 6.0 in a way JavaScript cannot reproduce, so the server ships those exact
// bytes as text (data.canonical); the browser hashes them, checks that they parse to the
// receipt shown, that each one names the previous hash, and the ledger's Ed25519 signature.
"use strict";

(() => {
  const root = document.getElementById("tour");
  if (!root) return;
  const steps = Array.from(root.querySelectorAll(".tour-step"));
  const links = Array.from(root.querySelectorAll(".tour-nav a[data-step]"));
  const total = steps.length;
  const play = document.getElementById("tour-play");
  const pos = document.getElementById("tour-pos");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STEP_MS = 2600;
  let current = 1;
  let timer = null;

  function show(n, scroll) {
    current = Math.max(1, Math.min(total, n));
    steps.forEach((el, i) => {
      const on = i < current;
      if (on && !el.classList.contains("shown")) el.classList.add("enter");
      el.classList.toggle("shown", on);
    });
    links.forEach((a, i) => {
      a.classList.toggle("seen", i < current);
      if (i === current - 1) a.setAttribute("aria-current", "step");
      else a.removeAttribute("aria-current");
    });
    pos.textContent = T("tour_step_of", { n: current, total: total });
    if (scroll) steps[current - 1].scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
    play.setAttribute("aria-pressed", "false");
    play.textContent = T("tour_play");
  }

  function start() {
    if (current >= total) show(1, true);
    play.setAttribute("aria-pressed", "true");
    play.textContent = T("tour_pause");
    timer = setInterval(() => {
      if (current >= total) { stop(); return; }
      show(current + 1, true);
      if (current >= total) stop();
    }, reduce ? STEP_MS * 1.5 : STEP_MS);
  }

  root.classList.add("live");
  root.querySelector(".tour-buttons").hidden = false;
  play.addEventListener("click", () => (timer ? stop() : start()));
  document.getElementById("tour-all").addEventListener("click", () => { stop(); show(total, false); });
  document.getElementById("tour-restart").addEventListener("click", () => {
    stop();
    steps.forEach((el) => el.classList.remove("shown", "enter"));
    show(1, true);
  });
  links.forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    stop();
    show(Number(a.dataset.step), true);
    history.replaceState(null, "", a.getAttribute("href"));
  }));
  const fromHash = links.findIndex((a) => a.getAttribute("href") === location.hash);
  show(fromHash >= 0 ? fromHash + 1 : 1, fromHash >= 0);

  // -- verification --------------------------------------------------------------------

  const verifyBtn = document.getElementById("tour-verify");
  const tamperBtn = document.getElementById("tour-tamper");
  const verdict = document.getElementById("tour-verdict");
  const list = document.getElementById("tour-checks");
  const rows = Array.from(root.querySelectorAll(".tour-receipts tbody tr"));
  document.getElementById("tour-verify-buttons").hidden = false;
  let data = null;

  const enc = new TextEncoder();
  const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
  const unhex = (s) => new Uint8Array((s.match(/../g) || []).map((h) => parseInt(h, 16)));
  const sha256 = async (text) => hex(await crypto.subtle.digest("SHA-256", enc.encode(text)));

  function same(a, b) {
    if (a === b) return true;
    if (typeof a !== typeof b || a === null || b === null || typeof a !== "object") return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    const ka = Object.keys(a).sort();
    const kb = Object.keys(b).sort();
    return ka.length === kb.length && ka.every((k, i) => k === kb[i] && same(a[k], b[k]));
  }

  function withoutSig(r) {
    const out = Object.assign({}, r);
    delete out.hash;
    delete out.sig;
    return out;
  }

  async function ledgerKey(raw) {
    try {
      return await crypto.subtle.importKey("raw", unhex(raw), { name: "Ed25519" }, false, ["verify"]);
    } catch (e) {
      return null;
    }
  }

  async function check() {
    const results = [];
    const add = (ok, key, values) => results.push({ ok: ok, text: T(key + (ok ? "" : "_bad"), values) });
    const key = await ledgerKey(data.ledger_key);
    let prev = data.genesis;
    let bad = 0;
    for (let i = 0; i < data.receipts.length; i++) {
      const r = data.receipts[i];
      const body = data.canonical.receipts[i];
      const n = i + 1;
      let ok = true;
      const hashOk = (await sha256(body)) === r.hash;
      add(hashOk, "tour_c_hash", { i: n });
      let parsed = null;
      try { parsed = JSON.parse(body); } catch (e) { parsed = null; }
      if (!parsed || !same(parsed, withoutSig(r))) {
        results.push({ ok: false, text: T("tour_c_fields_bad", { i: n }) });
        ok = false;
      }
      const linkOk = !!parsed && parsed.prev === prev;
      add(linkOk, "tour_c_link", { i: n });
      if (key) {
        const sigOk = await crypto.subtle.verify({ name: "Ed25519" }, key, unhex(r.sig), unhex(r.hash));
        add(sigOk, "tour_c_sig", { i: n });
        ok = ok && sigOk;
      }
      ok = ok && hashOk && linkOk;
      if (!ok) bad++;
      if (rows[i]) {
        rows[i].classList.toggle("ok", ok);
        rows[i].classList.toggle("bad", !ok);
        rows[i].querySelector(".tour-mark").textContent = ok ? "✓" : "✗";
      }
      prev = r.hash;
    }
    const last = data.receipts.length ? data.receipts[data.receipts.length - 1].hash : data.genesis;
    let m = null;
    try { m = JSON.parse(data.canonical.manifest); } catch (e) { m = null; }
    const run = m && m.runs ? m.runs[data.run_id] : null;
    let manOk = !!run && run.count === data.receipts.length && run.head === last
      && same(m.runs, data.manifest.runs) && m.seq === data.manifest.seq;
    if (key && manOk) {
      manOk = await crypto.subtle.verify({ name: "Ed25519" }, key, unhex(data.manifest.sig),
        enc.encode(data.canonical.manifest));
    }
    add(manOk, "tour_c_manifest", { count: data.receipts.length });
    const f = data.approval.fields;
    const briefOk = (await sha256(data.canonical.brief)) === f.brief_sha256
      && same(JSON.parse(data.canonical.brief), data.brief.confirmed);
    add(briefOk, "tour_c_brief", {});
    let msg = null;
    try { msg = JSON.parse(data.canonical.message); } catch (e) { msg = null; }
    const contentOk = (await sha256(data.canonical.content)) === f.content_sha256
      && same(JSON.parse(data.canonical.content), data.approval.content)
      && !!msg && Object.keys(f).every((k) => msg[k] === f[k]);
    add(contentOk, "tour_c_content", {});
    // the approval pins the chain as it stood when it was issued (later steps add to it)
    const pinned = data.receipts.findIndex((r) => r.hash === f.chain_head);
    add(pinned >= 0, "tour_c_head", { i: pinned + 1 });
    if (!key) results.push({ ok: null, text: T("tour_no_ed25519") });
    bad += [manOk, briefOk, contentOk, pinned >= 0].filter((x) => !x).length;
    return { results: results, bad: bad };
  }

  async function run() {
    if (!window.crypto || !crypto.subtle) {
      verdict.className = "status bad";
      verdict.textContent = T("tour_no_crypto");
      return;
    }
    verifyBtn.disabled = true;
    verdict.className = "status";
    verdict.textContent = T("tour_checking");
    try {
      if (!data) {
        const res = await fetch("/try/data.json", { credentials: "omit" });
        if (!res.ok) throw new Error(String(res.status));
        data = await res.json();
      }
      const out = await check();
      list.replaceChildren(...out.results.map((x) => {
        const li = document.createElement("li");
        li.className = x.ok === null ? "note" : (x.ok ? "ok" : "bad");
        li.textContent = x.text;
        return li;
      }));
      verdict.className = "status " + (out.bad ? "bad" : "ok");
      verdict.textContent = out.bad ? T("tour_fail", { n: out.bad }) : T("tour_ok", { n: data.receipts.length });
      if (!tamperBtn.dataset.done) tamperBtn.disabled = false;
    } catch (e) {
      verdict.className = "status bad";
      verdict.textContent = T("tour_load_failed");
    } finally {
      verifyBtn.disabled = false;
    }
  }

  // Change one receipt the way a forger would: its cost, in the shown fields and the bytes.
  function tamper() {
    if (!data) return;
    const i = Math.min(4, data.receipts.length - 1);
    const r = data.receipts[i];
    const was = r.cost_usd;
    r.cost_usd = Math.round((was + 0.01) * 1e6) / 1e6;
    data.canonical.receipts[i] = data.canonical.receipts[i].replace(
      /"cost_usd":[0-9.eE+-]+/, '"cost_usd":' + String(r.cost_usd));
    if (rows[i]) rows[i].querySelector(".tour-cost").textContent = "$" + r.cost_usd.toFixed(5);
    tamperBtn.disabled = true;
    tamperBtn.dataset.done = "1";
    verdict.className = "status";
    verdict.textContent = T("tour_tampered", { i: i + 1 });
    list.replaceChildren();
  }

  verifyBtn.addEventListener("click", run);
  tamperBtn.addEventListener("click", tamper);
})();
