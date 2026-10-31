// Muse Studio Lite — step 2 of the brief as a short summary and an assistant. The summary
// card (#bv-summary) reads the draft back in a few sentences, each with a "Change" button
// that opens the field in the folded full form. The assistant (#bv-assistant) asks, one card
// at a time, every open [FACT] question (the list brief.js renders, reused) and every
// problem the server found in the draft, with one-click answers where it can. Answers
// are written into the form fields: the form and BriefForm.collect() stay the single
// source of truth. If anything here throws, the full form is shown open instead.
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const v1 = $("brief-v1-form");
  const F = window.BriefForm;
  if (!v1 || !F || !$("bv-summary") || !$("bv-assistant")) return;
  const lang = document.documentElement.lang === "pl" ? "pl" : "en";
  const locale = lang === "pl" ? "pl-PL" : "en-GB";
  const details = $("bv-details");

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }
  function button(text, cls) {
    const b = el("button", text, cls);
    b.type = "button";
    return b;
  }
  function fallback(err) {  // the plain form, everything visible
    if (details) details.open = true;
    for (const li of v1.querySelectorAll("#bv-question-list > li")) li.hidden = false;
    $("bv-questions").hidden = false;
    for (const id of ["bv-summary", "bv-ask-list", "bv-ask-nav", "bv-ask-h", "bv-ask-end"]) {
      const node = $(id);
      if (node) node.hidden = true;
    }
    if (window.console) window.console.error(err);
  }

  // -- countries: names or codes → ISO 3166-1 alpha-2 ---------------------------------
  const EU = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"];
  const KNOWN = EU.concat(["GB", "NO", "CH", "US", "CA", "AU"]);
  const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z ]/g, " ").replace(/\s+/g, " ").trim();
  const regionNames = {};
  function regionName(code, loc) {
    try {
      regionNames[loc] = regionNames[loc] || new Intl.DisplayNames([loc], { type: "region" });
      return regionNames[loc].of(code) || code;
    } catch (_) { return code; }
  }
  const byName = new Map();
  for (const code of KNOWN) {
    for (const loc of ["en", "pl"]) byName.set(fold(regionName(code, loc)), code);
  }
  for (const [alias, code] of [["uk", "GB"], ["great britain", "GB"], ["britain", "GB"],
    ["england", "GB"], ["anglia", "GB"], ["usa", "US"], ["america", "US"], ["holland", "NL"],
    ["czech republic", "CZ"], ["czechy", "CZ"], ["el", "GR"]]) byName.set(alias, code);
  function toIso(token) {
    const t = token.trim();
    if (/^[A-Za-z]{2}$/.test(t)) return t.toUpperCase() === "UK" ? "GB" : t.toUpperCase();
    return byName.get(fold(t)) || null;
  }
  const isoOk = (code) => /^[A-Z]{2}$/.test(code);
  const lines = (text) => String(text || "").split("\n").map((s) => s.trim()).filter(Boolean);

  // -- writing into the form ----------------------------------------------------------
  function setField(path, value) {
    const control = F.controlOf(path);
    if (!control) return;
    control.value = value;
    control.dispatchEvent(new Event("input", { bubbles: true }));
    control.dispatchEvent(new Event("change", { bubbles: true }));
    clearFieldError(path);
  }
  function clearFieldError(path) {
    const control = F.controlOf(path);
    if (control) control.removeAttribute("aria-invalid");
    const p = $("err-" + F.idOf(path));
    if (p) { p.hidden = true; p.textContent = ""; }
  }
  const valueOf = (path) => { const c = F.controlOf(path); return c ? c.value : ""; };

  // -- the summary card ---------------------------------------------------------------
  let fromProfile = new Set();
  function money(amount, currency) {
    try {
      return new Intl.NumberFormat(locale, { style: "currency", currency,
        minimumFractionDigits: Number.isInteger(Number(amount)) ? 0 : 2, maximumFractionDigits: 2 }).format(amount);
    } catch (_) { return amount + " " + currency; }
  }
  function place(item) {
    return /^[A-Z]{2}$/.test(item) ? regionName(item, lang) : item;
  }
  function language(code) {
    try { return new Intl.DisplayNames([lang], { type: "language" }).of(code) || code; } catch (_) { return code; }
  }
  function hostOf(url) {
    try { const u = new URL(url); return u.protocol === "https:" || u.protocol === "http:" ? u.host : ""; } catch (_) { return ""; }
  }
  function checkedLabel(name) {
    const r = v1.querySelector('input[name="' + name + '"]:checked');
    return r ? r.parentElement.textContent.trim() : "";
  }

  // the visible names of the goal and sub-choice, without the "needs your website" reason
  function goalText() {
    const s = $("bf-objective-goal");
    const o = s && s.selectedIndex >= 0 ? s.options[s.selectedIndex] : null;
    return o && s.value ? (o.dataset.label !== undefined ? o.dataset.label : o.text) : "";
  }
  function subText() {
    const s = $("bf-objective-sub");
    const o = s && s.value && s.selectedIndex >= 0 ? s.options[s.selectedIndex] : null;
    return o ? (o.dataset.label !== undefined ? o.dataset.label : o.text) : "";
  }

  // -- switching the objective when the owner has no website, or no pixel -------------
  // studio/brief.py destination() sends the switches; each one is a (goal, sub) row of
  // studio/meta/objectives.py. Traffic without a pixel is link clicks, never
  // landing-page views (Meta counts those only through a pixel).
  const SWITCH_LABELS = {
    "traffic:link_clicks": () => T("brief_switch_traffic"),
    "leads:instant_form": () => T("brief_switch_form"),
    "engagement:messenger": () => T("brief_switch_messenger"),
    "engagement:whatsapp": () => T("brief_switch_whatsapp"),
    "engagement:instagram_direct": () => T("brief_switch_instagram"),
    "engagement:post_engagement": () => T("brief_switch_engagement"),
  };
  // the summary offers one clear choice: an instant form or messages (no website), or
  // traffic or an instant form (no pixel)
  const SUMMARY_SWITCHES = ["leads:instant_form", "engagement:messenger", "traffic:link_clicks"];
  function switchesFor(issue) {
    return F.switches(issue).map(([g, s]) => g + ":" + s).filter((k) => SWITCH_LABELS[k]);
  }
  function summaryChoice(issue) {
    return switchesFor(issue).filter((k) => SUMMARY_SWITCHES.includes(k));
  }
  let lastData = {};
  function switchTo(key, from) {
    const [goal, sub] = key.split(":");
    const label = SWITCH_LABELS[key]();
    setField("objective.goal", goal);
    setField("objective.sub", sub);
    for (const p of ["objective.sub", "promotion.pixel_id", "promotion.product_catalog_id", "promotion.product_set_id", "advertiser.landing_url"]) clearFieldError(p);
    F.say(T("brief_switched", { choice: label }));
    renderSummary();
    buildAssistant(lastData, from === "assistant");
    if (from === "summary") {
      const change = $("bv-summary-list").querySelector('[data-topic="objective.goal"] .bv-sum-change');
      if (change) change.focus();
    }
  }

  function sentences() {
    const b = F.collect().brief;
    const out = [];
    const a = b.advertiser;
    const kind = checkedLabel("bf-kind");
    out.push({ topic: T("brief_sum_t_who"), path: "advertiser.display_name", paths: ["advertiser.display_name", "advertiser.kind"],
      missing: !a.display_name,
      text: a.display_name ? (kind ? T("brief_sum_who", { name: a.display_name, kind }) : T("brief_sum_who_name", { name: a.display_name }))
        : T("brief_sum_who_missing") });
    const o = b.offer;
    let what = o.product ? (o.offer ? T("brief_sum_what_offer", { product: o.product, offer: o.offer }) : T("brief_sum_what", { product: o.product }))
      : T("brief_sum_what_missing");
    if (o.product && o.valid_until) {
      let date = o.valid_until;
      try { date = new Date(o.valid_until + "T12:00:00").toLocaleDateString(locale, { day: "numeric", month: "long", year: "numeric" }); } catch (_) { /* as typed */ }
      what += " " + T("brief_sum_until", { date });
    }
    out.push({ topic: T("brief_sum_t_what"), path: "offer.product", paths: ["offer.product", "offer.offer", "offer.valid_until"], missing: !o.product, text: what });
    const au = b.audience;
    const places = (au.locations.length ? au.locations : a.countries).map(place).join(", ");
    const langs = au.languages.map(language).join(", ");
    const age = { min: au.age_min, max: au.age_max };
    out.push({ topic: T("brief_sum_t_whom"), path: "audience.locations", paths: ["audience.locations", "audience.age_min", "audience.age_max", "audience.languages", "advertiser.countries"],
      missing: !places,
      text: places ? (langs ? T("brief_sum_whom", { ...age, places, languages: langs }) : T("brief_sum_whom_nolang", { ...age, places }))
        : T("brief_sum_whom_noplace", age) });
    const goal = goalText();
    const sub = subText();
    const issue = F.siteIssue();
    out.push({ topic: T("brief_sum_t_goal"), path: "objective.goal", paths: ["objective.goal", "objective.sub", "objective.kpi"], missing: !goal || Boolean(issue),
      text: sub ? T("brief_sum_goal_sub", { goal, sub }) : T("brief_sum_goal", { goal }),
      note: issue === "no_website" ? T("brief_sum_goal_no_website") : issue === "no_pixel" ? T("brief_sum_goal_no_pixel") : "",
      choice: issue ? summaryChoice(issue) : [] });
    const bu = b.budget;
    let budget = T("brief_sum_budget_missing");
    if (bu.amount > 0) {
      const amount = money(bu.amount, bu.currency || (F.draft() || {}).currency || "PLN");
      budget = bu.kind === "lifetime" ? T("brief_sum_budget_total", { amount, start: bu.start || "…", end: bu.end || "…" })
        : T("brief_sum_budget_daily", { amount });
    }
    const advice = $("bf-budget-advice");
    out.push({ topic: T("brief_sum_t_budget"), path: "budget.amount", paths: ["budget.amount", "budget.kind", "budget.currency", "budget.start", "budget.end"],
      missing: !(bu.amount > 0), text: budget, note: advice && !advice.hidden ? advice.textContent : "" });
    const host = hostOf(a.landing_url);
    if (!F.hasWebsite()) {  // no website: the ad keeps people on Facebook and Instagram
      out.push({ topic: T("brief_sum_t_leads"), path: "advertiser.landing_url", paths: ["advertiser.landing_url"], missing: false,
        text: T("brief_sum_leads_nosite") });
      return out;
    }
    out.push({ topic: T("brief_sum_t_leads"), path: "advertiser.landing_url", paths: ["advertiser.landing_url"], missing: !host,
      text: host ? T("brief_sum_leads", { host }) : T("brief_sum_leads_missing") });
    return out;
  }

  function renderSummary() {
    if (!F.draft()) return;
    const list = $("bv-summary-list");
    const items = sentences().map((s) => {
      const li = el("li", null, "bv-sum" + (s.missing ? " is-missing" : ""));
      li.dataset.topic = s.path;
      const p = el("p", s.text, "bv-sum-text");
      if (s.paths.some((x) => fromProfile.has(x))) p.append(" ", el("span", T("brief_sum_profile"), "badge bv-sum-badge"));
      li.append(p);
      if (s.note) {
        const note = el("p", s.note, "hint");
        note.id = "bv-sum-note-" + F.idOf(s.path);
        li.append(note);
        if (s.choice && s.choice.length) {
          const group = el("div", null, "bv-chips");
          group.setAttribute("role", "group");
          group.setAttribute("aria-labelledby", note.id);
          for (const key of s.choice) {
            const b = button(SWITCH_LABELS[key](), "secondary bv-chip");
            b.dataset.switch = key;
            b.addEventListener("click", () => switchTo(key, "summary"));
            group.append(b);
          }
          li.append(group);
        }
      }
      const change = button(T("brief_sum_change"), "secondary bv-sum-change");
      change.setAttribute("aria-label", T("brief_sum_change_label", { what: s.topic }));
      change.addEventListener("click", () => F.focusField(s.path));
      li.append(change);
      return li;
    });
    list.replaceChildren(...items);
  }
  let timer = 0;
  function scheduleSummary() { clearTimeout(timer); timer = setTimeout(() => { try { renderSummary(); } catch (e) { fallback(e); } }, 150); }

  // -- the assistant ------------------------------------------------------------------
  // items: [{kind: "fact"|"ask", li, resolved(), handed}]
  let items = [];
  let cur = 0;
  let atEnd = false;
  const SPECIAL = ["advertiser.countries", "advertiser.landing_url", "budget.amount", "offer.product", "advertiser.display_name"];

  function factItem(q) {
    return { kind: "fact", li: q.li, resolved: () => q.strike.checked || (q.answer.checked && Boolean(q.input.value.trim())) };
  }

  function cardShell(path, code, question) {
    const li = el("li", null, "bv-item bv-ask");
    li.dataset.ask = path;
    const h = el("p", question, "bv-ask-q");
    h.id = "ask-" + F.idOf(path) + "-q";
    const why = el("p", F.errorText(code), "hint");
    why.id = "ask-" + F.idOf(path) + "-why";
    const err = el("p", null, "field-error");
    err.id = "ask-" + F.idOf(path) + "-err";
    err.hidden = true;
    li.append(h, why);
    return { li, h, why, err };
  }
  function input(shell, kind, labelText, value) {
    const id = "ask-" + F.idOf(shell.li.dataset.ask) + "-in";
    const lab = el("label", labelText);
    lab.htmlFor = id;
    const node = el(kind === "area" || kind === "lines" ? "textarea" : "input");
    node.id = id;
    if (node.tagName === "INPUT") node.type = kind === "url" ? "url" : kind === "number" ? "number" : "text";
    if (kind === "url") node.setAttribute("inputmode", "url");
    if (kind === "amount") node.setAttribute("inputmode", "decimal");
    if (kind === "number") { node.min = "0"; node.step = "any"; node.setAttribute("inputmode", "decimal"); }
    if (node.tagName === "TEXTAREA") node.rows = 2;
    node.value = value || "";
    node.setAttribute("aria-describedby", shell.h.id + " " + shell.why.id + " " + shell.err.id);
    shell.li.append(lab, node);
    return node;
  }
  function showErr(shell, text) {
    shell.err.textContent = text || "";
    shell.err.hidden = !text;
  }

  function countriesCard(code) {
    const path = "advertiser.countries";
    const shell = cardShell(path, code, T("brief_ask_countries"));
    const chips = el("div", null, "bv-chips");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-labelledby", shell.h.id);
    const current = () => lines(valueOf(path)).map((c) => c.toUpperCase());
    let placesWritten = null;  // the places this card wrote, while the owner keeps them
    const write = (codes) => {
      const uniq = [...new Set(codes)];
      setField(path, uniq.join("\n"));
      const places = valueOf("audience.locations");
      if (!lines(places).length || places === placesWritten) {
        placesWritten = uniq.map((c) => regionName(c, lang)).join("\n");
        setField("audience.locations", placesWritten);
      }
      sync();
    };
    const chipDefs = [["PL", regionName("PL", lang), ["PL"]], ["GB", regionName("GB", lang), ["GB"]],
      ["DE", regionName("DE", lang), ["DE"]], ["EU", T("brief_ask_eu"), EU]];
    const chipButtons = chipDefs.map(([key, text, codes]) => {
      const b = button(text, "secondary bv-chip");
      b.dataset.chip = key;
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => {
        const have = new Set(current());
        const on = codes.every((c) => have.has(c));
        if (on) codes.forEach((c) => have.delete(c)); else codes.forEach((c) => have.add(c));
        write([...have]);
        showErr(shell, "");
        F.say(T("brief_ask_saved", { label: labelText }));
      });
      chips.append(b);
      return { b, codes };
    });
    const labelText = F.labelFor(F.controlOf(path)) || path;
    shell.li.append(chips);
    const other = input(shell, "text", T("brief_ask_other"), "");
    other.addEventListener("change", () => {
      const tokens = other.value.split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean);
      const bad = tokens.filter((x) => !toIso(x));
      const codes = tokens.map(toIso).filter(Boolean);
      if (codes.length) write(current().concat(codes));
      showErr(shell, bad.length ? T("brief_ask_unknown_country", { names: bad.join(", ") }) : "");
    });
    shell.li.append(shell.err);
    function sync() {
      const have = new Set(current());
      for (const c of chipButtons) c.b.setAttribute("aria-pressed", String(c.codes.every((x) => have.has(x))));
    }
    sync();
    return { kind: "ask", li: shell.li, path, shell, sync,
      resolved: () => { const c = current(); return c.length > 0 && c.every(isoOk); },
      need: () => T("brief_err_bad_country"), focus: chipButtons[0].b };
  }

  function urlCard(code) {
    const path = "advertiser.landing_url";
    const shell = cardShell(path, code, T("brief_ask_url"));
    const node = input(shell, "url", F.labelFor(F.controlOf(path)) || path, valueOf(path));
    shell.li.append(shell.err);
    const ok = (v) => { try { const u = new URL(v.trim()); return u.protocol === "https:" && u.hostname.includes("."); } catch (_) { return false; } };
    node.addEventListener("input", () => { setField(path, node.value.trim()); showErr(shell, ""); });
    node.addEventListener("change", () => showErr(shell, node.value.trim() && !ok(node.value) ? T("brief_err_bad_url") : ""));
    return { kind: "ask", li: shell.li, path, shell, focus: node, sync: () => { if (document.activeElement !== node) node.value = valueOf(path); },
      resolved: () => ok(valueOf(path)), need: () => T("brief_err_bad_url") };
  }

  // "3", "3 GBP", "£3", "3,50 zł", "1 500": the number, and a currency named in the answer
  // when it is not the ad account's (the amount is never converted)
  const CURRENCY_WORDS = { "£": "GBP", "€": "EUR", "$": "USD", "zł": "PLN", "zl": "PLN", "złotych": "PLN" };
  let ISO = new Set(["PLN", "GBP", "EUR", "USD"]);
  try { ISO = new Set(Intl.supportedValuesOf("currency")); } catch (e) { /* older browser: the common four */ }
  function amountOf(text, currency) {
    const t = String(text || "").trim();
    const m = t.replace(/(\d)[\s\u00a0](?=\d{3}\b)/g, "$1").match(/\d+(?:[.,]\d+)?/);
    const n = m ? Number(m[0].replace(",", ".")) : NaN;
    const named = (t.match(/[A-Za-z]{3}\b|[£€$]|zł|zl/gi) || [])
      .map((w) => CURRENCY_WORDS[w.toLowerCase()] || w.toUpperCase())
      .find((c) => ISO.has(c));
    return { n, other: named && named !== currency ? named : "" };
  }

  function amountCard(code) {
    const path = "budget.amount";
    const d = F.draft() || {};
    const lifetime = (v1.querySelector('input[name="bf-budget-kind"]:checked') || {}).value === "lifetime";
    const shell = cardShell(path, code, lifetime ? T("brief_ask_amount_total") : T("brief_ask_amount"));
    const currency = valueOf("budget.currency") || d.currency || "PLN";
    const a = d.budget_advice;
    if (a && a.estimate !== false && !lifetime) {
      const chips = el("div", null, "bv-chips");
      chips.setAttribute("role", "group");
      chips.setAttribute("aria-labelledby", shell.h.id);
      const seen = new Set();
      for (const [key, n] of [["brief_ask_chip_suggested", a.suggested], ["brief_ask_chip_learning", a.learning], ["brief_ask_chip_minimum", a.minimum]]) {
        if (!(Number(n) > 0) || seen.has(Number(n))) continue;
        seen.add(Number(n));
        const label = key === "brief_ask_chip_suggested" ? T("brief_ask_chip_suggested", { amount: money(n, currency) })
          : key === "brief_ask_chip_learning" ? T("brief_ask_chip_learning", { amount: money(n, currency) })
            : T("brief_ask_chip_minimum", { amount: money(n, currency) });
        const b = button(label, "secondary bv-chip");
        b.addEventListener("click", () => {
          setField(path, String(n));
          box.value = String(n);
          showErr(shell, "");
          F.say(T("brief_ask_saved", { label: label }));
        });
        chips.append(b);
      }
      if (seen.size) shell.li.append(chips);
    }
    const box = input(shell, "amount", T("brief_ask_amount_label", { currency }), Number(valueOf(path)) > 0 ? valueOf(path) : "");
    shell.li.append(shell.err);
    box.addEventListener("input", () => {
      const { n, other } = amountOf(box.value, currency);
      setField(path, !other && n > 0 ? String(n) : "");
      showErr(shell, other ? T("brief_ask_amount_currency", { other, currency }) : "");
    });
    const ok = () => {
      const n = Number(valueOf(path));
      return n > 0 && n < 10000000 && (lifetime || !d.ceiling || n <= d.ceiling);
    };
    return { kind: "ask", li: shell.li, path, shell, focus: shell.li.querySelector(".bv-chip") || box,
      sync: () => { if (document.activeElement !== box) box.value = Number(valueOf(path)) > 0 ? valueOf(path) : ""; },
      resolved: ok, need: () => F.errorText(Number(valueOf(path)) > 0 ? "over_ceiling" : "amount") };
  }

  function textCard(path, code, question) {
    const control = F.controlOf(path);
    const kind = control.tagName === "TEXTAREA" ? (control.dataset.kind === "lines" ? "lines" : "area")
      : control.type === "url" ? "url" : control.type === "number" ? "number" : "text";
    const original = control.value;
    const shell = cardShell(path, code, question || T("brief_ask_generic", { label: F.labelFor(control) || path }));
    const node = input(shell, kind, F.labelFor(control) || T("brief_ask_your_answer"), control.value);
    shell.li.append(shell.err);
    node.addEventListener("input", () => { setField(path, kind === "lines" ? node.value : node.value.trim()); showErr(shell, ""); });
    return { kind: "ask", li: shell.li, path, shell, focus: node,
      sync: () => { if (document.activeElement !== node) node.value = control.value; },
      resolved: () => Boolean(control.value.trim()) && (code === "required" || control.value !== original),
      need: () => F.errorText(code) };
  }

  function formCard(path, code) {  // no single text control: send the owner to the field
    const control = F.controlOf(path);
    const holder = control && control.closest(".bf-field, fieldset");
    const name = (control && F.labelFor(control)) || (holder && holder.querySelector("legend") ? holder.querySelector("legend").textContent.trim() : "") || path;
    const shell = cardShell(path, code, T("brief_ask_generic", { label: name }));
    const go = button(T("brief_ask_show"), "secondary");
    const item = { kind: "ask", li: shell.li, path, shell, focus: go, handed: false, sync: () => {},
      resolved: () => item.handed, need: () => F.errorText(code) };
    go.addEventListener("click", () => { item.handed = true; F.focusField(path); });
    shell.li.append(go, shell.err);
    return item;
  }

  // one card instead of the website or pixel questions: switch the objective with one
  // click, or (no pixel) type the pixel's dataset id
  function switchCard(issue) {
    const path = "objective.switch";
    const noSite = issue === "no_website";
    const shell = cardShell(path, "required_for_objective", noSite ? T("brief_ask_no_website") : T("brief_ask_no_pixel"));
    const why = () => (noSite ? T("brief_ask_no_website_why") : T("brief_ask_no_pixel_why"));
    shell.why.textContent = why();
    const chips = el("div", null, "bv-chips");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-labelledby", shell.h.id);
    for (const key of switchesFor(issue)) {
      const b = button(SWITCH_LABELS[key](), "secondary bv-chip");
      b.dataset.switch = key;
      b.addEventListener("click", () => switchTo(key, "assistant"));
      chips.append(b);
    }
    shell.li.append(chips);
    let node = null;
    if (!noSite && F.controlOf("promotion.pixel_id")) {  // entering the id stays possible
      node = input(shell, "text", T("brief_ask_pixel_label"), valueOf("promotion.pixel_id"));
      node.setAttribute("inputmode", "numeric");
      node.setAttribute("autocomplete", "off");
      node.addEventListener("input", () => { setField("promotion.pixel_id", node.value.trim()); showErr(shell, ""); });
    }
    shell.li.append(shell.err);
    return { kind: "ask", li: shell.li, path, shell, focus: chips.querySelector("button") || node,
      sync: () => { if (node && document.activeElement !== node) node.value = valueOf("promotion.pixel_id"); },
      resolved: () => F.siteIssue() !== issue, need: why };
  }

  // a server problem still worth asking about: not an id the objective no longer uses
  // (after a switch), not the website or pixel the switch card stands for, and never a
  // landing page when the owner has no website
  function stillAsked(path, issue) {
    if (path === "advertiser.landing_url" && !F.hasWebsite()) return false;
    if (path.startsWith("promotion.")) {
      if (issue === "no_website" || (issue === "no_pixel" && path === "promotion.pixel_id")) return false;
      const c = F.controlOf(path);
      const box = c && c.closest("[data-needs]");
      if (box && !box.dataset.needs.split(" ").includes(F.rowKey())) return false;
    }
    return true;
  }
  // the ids the objective as it stands needs and the form still lacks (after a switch:
  // an instant form needs the privacy-policy page)
  function newlyNeeded(problems) {
    const out = {};
    for (const box of v1.querySelectorAll("[data-needs]")) {
      if (!box.dataset.needs.split(" ").includes(F.rowKey())) continue;
      const c = box.querySelector("input[data-path]:not([type=checkbox])");
      if (c && !c.value.trim() && !(c.dataset.path in problems)) out[c.dataset.path] = "required_for_objective";
    }
    return out;
  }

  function askItem(path, code) {
    if (path === "advertiser.countries") return countriesCard(code);
    if (path === "advertiser.landing_url") return urlCard(code);
    if (path === "budget.amount") return amountCard(code);
    const control = F.controlOf(path);
    const texty = control && (control.tagName === "TEXTAREA" || (control.tagName === "INPUT" && !["checkbox", "radio", "date"].includes(control.type)));
    if (!texty || control.closest("[hidden]")) return formCard(path, code);
    const q = path === "offer.product" ? T("brief_ask_product") : path === "advertiser.display_name" ? T("brief_ask_name") : "";
    return textCard(path, code, q);
  }

  function buildAssistant(data, focus) {
    lastData = data;
    items = F.questions().map(factItem);
    const asks = [];
    const issue = F.siteIssue();
    const problems = { ...(data.problems || {}) };
    Object.assign(problems, newlyNeeded(problems));
    const paths = Object.keys(problems).filter((p) => !p.startsWith("open_questions") && stillAsked(p, issue));
    paths.sort((x, y) => {  // the ones with a quick answer first, then form order
      const ix = SPECIAL.indexOf(x), iy = SPECIAL.indexOf(y);
      return (ix < 0 ? 99 : ix) - (iy < 0 ? 99 : iy);
    });
    if (issue) asks.push(switchCard(issue));
    for (const path of paths) asks.push(askItem(path, problems[path]));
    $("bv-ask-list").replaceChildren(...asks.map((x) => x.li));
    items = items.concat(asks);
    cur = 0;
    atEnd = false;
    if (!items.length) { finish(Boolean(focus)); return; }
    // start on the first card that still needs an answer
    const first = items.findIndex((x) => !x.resolved());
    if (first < 0) { finish(Boolean(focus)); return; }
    cur = first;
    $("bv-ask-next").hidden = false;
    render(Boolean(focus));
  }

  const left = () => items.filter((x) => !x.resolved()).length;

  function render(focus) {
    atEnd = false;
    items.forEach((x, i) => { x.li.hidden = i !== cur; if (x.sync) x.sync(); });
    const item = items[cur];
    $("bv-questions").hidden = item.kind !== "fact";
    $("bv-ask-end").hidden = true;
    $("bv-ask-nav").hidden = false;
    const h = $("bv-ask-h");
    h.hidden = false;
    const n = left();
    h.textContent = T("brief_ask_progress", { i: cur + 1, n: items.length }) + (n ? " · " + T("brief_ask_left", { n }) : "");
    $("bv-ask-back").disabled = cur === 0;
    $("bv-ask-skip").hidden = item.resolved();
    if (focus) h.focus();
  }

  function finish(focus) {
    atEnd = true;
    for (const x of items) x.li.hidden = true;
    $("bv-questions").hidden = true;
    const n = left();
    const end = $("bv-ask-end");
    end.textContent = n ? T("brief_ask_end_left", { n }) : T("brief_ask_done");
    end.hidden = false;
    $("bv-ask-h").hidden = true;
    $("bv-ask-nav").hidden = !items.length;
    $("bv-ask-back").disabled = !items.length;
    $("bv-ask-skip").hidden = true;
    $("bv-ask-next").hidden = true;
    if (!n && !items.length) $("bv-ask-nav").hidden = true;
    if (focus) {
      F.say(end.textContent);
      if (n) end.focus(); else $("confirm-button").focus();
    }
  }

  // the next card still open after the current one (wrapping round), or -1
  function nextOpen() {
    for (let k = 1; k < items.length; k++) {
      const i = (cur + k) % items.length;
      if (!items[i].resolved()) return i;
    }
    return -1;
  }
  function advance() {
    $("bv-ask-next").hidden = false;
    const i = nextOpen();
    if (i < 0) { finish(true); return; }
    cur = i;
    render(true);
  }

  $("bv-ask-next").addEventListener("click", () => {
    const item = items[cur];
    if (!item) return;
    if (!item.resolved() && item.kind === "ask") {
      showErr(item.shell, item.need());
      F.say(item.need());
      if (item.focus) item.focus.focus();
      return;
    }
    if (!item.resolved()) {  // a [FACT] question: answer it, or strike the claim
      F.say(T("brief_ask_need"));
      return;
    }
    advance();
  });
  $("bv-ask-skip").addEventListener("click", advance);
  $("bv-ask-back").addEventListener("click", () => {
    $("bv-ask-next").hidden = false;
    if (atEnd) { cur = items.length - 1; render(true); return; }
    if (cur > 0) { cur -= 1; render(true); }
  });

  // One click on "don't use it" (the mouse; arrow keys only move the choice) answers the
  // question: the next card follows.
  $("bv-question-list").addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLInputElement) || t.value !== "strike" || !ev.detail) return;
    const at = cur;
    setTimeout(() => { if (!atEnd && cur === at && t.checked && items[cur] && items[cur].resolved()) advance(); }, 450);
  });

  // keep the card state in step with edits anywhere in the form
  v1.addEventListener("input", () => {
    scheduleSummary();
    if (!atEnd && items[cur]) {
      const n = left();
      $("bv-ask-h").textContent = T("brief_ask_progress", { i: cur + 1, n: items.length }) + (n ? " · " + T("brief_ask_left", { n }) : "");
      $("bv-ask-skip").hidden = items[cur].resolved();
    }
  });
  v1.addEventListener("change", () => {
    scheduleSummary();
    if (!atEnd && items[cur]) $("bv-ask-skip").hidden = items[cur].resolved();
  });

  // a field brought into view (error link, Change button) that sits on a card: show it
  v1.addEventListener("brief:reveal", (ev) => {
    const node = ev.detail;
    const i = items.findIndex((x) => x.li.contains(node));
    if (i >= 0) { $("bv-ask-next").hidden = false; cur = i; render(false); }
  });
  v1.addEventListener("brief:errors", (ev) => {
    const fields = ev.detail || {};
    const q = Object.keys(fields).find((p) => /^open_questions\.\d+$/.test(p));
    if (q !== undefined) {
      const i = Number(q.split(".")[1]);
      if (items[i] && items[i].kind === "fact") { $("bv-ask-next").hidden = false; cur = i; render(false); }
    }
  });

  v1.addEventListener("brief:draft", (ev) => {
    try {
      const data = ev.detail || {};
      fromProfile = new Set(Array.isArray(data.from_profile) ? data.from_profile.filter((x) => typeof x === "string") : []);
      $("bv-summary").hidden = false;
      $("bv-ask-list").hidden = false;
      $("bv-ask-next").hidden = false;
      if (details) details.open = false;
      buildAssistant(data, false);
      renderSummary();
    } catch (e) { fallback(e); }
  });

  try {
    if (details) details.open = false;  // the summary and the assistant lead; details on demand
  } catch (e) { fallback(e); }
})();
