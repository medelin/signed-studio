// UI strings for scripts, in the page's language: the server renders them as a JSON data
// block (<script type="application/json" id="i18n">, never executed) from studio/ui/i18n.py.
// T("key", {name: value}) fills {name} placeholders; a missing key returns the key itself.
"use strict";
(() => {
  let strings = {};
  try { strings = JSON.parse(document.getElementById("i18n").textContent || "{}"); } catch (e) { strings = {}; }
  window.T = (key, values) => {
    const raw = Object.prototype.hasOwnProperty.call(strings, key) ? strings[key] : key;
    return values ? raw.replace(/\{(\w+)\}/g, (m, name) => (name in values ? String(values[name]) : m)) : raw;
  };
})();
