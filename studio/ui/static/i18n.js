// UI strings for scripts, in the page's language: the server renders them as a JSON data
// block (<script type="application/json" id="i18n">, never executed) from studio/ui/i18n.py.
// T("key", {name: value}) fills {name} placeholders; a missing key returns the key itself.
// TV("run", status) is the label of an enum value (falls back to the value, underscores as
// spaces); E(data, status) is the sentence for an API error body ({error, detail_code, ...}).
"use strict";
(() => {
  let strings = {};
  try { strings = JSON.parse(document.getElementById("i18n").textContent || "{}"); } catch (e) { strings = {}; }
  window.T = (key, values) => {
    const raw = Object.prototype.hasOwnProperty.call(strings, key) ? strings[key] : key;
    return values ? raw.replace(/\{(\w+)\}/g, (m, name) => (name in values ? String(values[name]) : m)) : raw;
  };
  const has = (key) => Object.prototype.hasOwnProperty.call(strings, key);
  window.TV = (group, value) => {
    const key = "enum." + group + "." + value;
    return has(key) ? strings[key] : String(value).replace(/_/g, " ");
  };
  window.E = (data, status) => {
    data = data || {};
    if (data.detail_code && has("err." + data.detail_code)) return window.T("err." + data.detail_code, data.detail_params || {});
    if (data.error && has("err." + data.error)) return strings["err." + data.error];
    return String(data.detail || data.error || status);
  };
})();
