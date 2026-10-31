// /account tabs: the page lists every section; with JavaScript one group of sections is
// shown at a time. The group follows the URL hash (links in mails point at a section),
// else the group holding a form error, else the first one.
"use strict";
(() => {
  const nav = document.querySelector("[data-account-tabs]");
  if (!nav) return;
  const tabs = [...nav.querySelectorAll("a[data-group]")];
  const groupOf = new Map();
  for (const tab of tabs) {
    for (const id of tab.dataset.members.split(" ")) groupOf.set(id, tab.dataset.group);
  }
  const sections = [...document.querySelectorAll("section.account-part[id]")]
    .filter((s) => groupOf.has(s.id));
  nav.classList.add("tabs");

  function show(group) {
    for (const s of sections) s.hidden = groupOf.get(s.id) !== group;
    for (const tab of tabs) {
      if (tab.dataset.group === group) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
  }

  // a fresh WhatsApp code (POST /account/whatsapp/code answers with this page): show it
  // in its tab and give the page its own address back, so a reload does not post again
  const waCode = document.getElementById("wa-code");
  if (waCode) history.replaceState(null, "", "/account#notifications");

  function fromPage() {
    const id = decodeURIComponent(location.hash.slice(1));
    if (groupOf.has(id)) return groupOf.get(id);
    const failed = sections.find((s) => s.querySelector(".status.error"));
    return failed ? groupOf.get(failed.id) : tabs[0].dataset.group;
  }

  window.addEventListener("hashchange", () => show(fromPage()));
  show(fromPage());
  if (waCode) waCode.scrollIntoView({ block: "center" });
})();

// Profile → Your business online: the Meta Pixel (a dataset id) matters only for ads that
// send people to the owner's website, so its field shows once a website is typed (a full
// https address) and hides again when the website is emptied. A pixel already chosen or
// typed stays visible, so it can be cleared. Without JavaScript the server decides the same
// way from the saved values.
(() => {
  const site = document.getElementById("pf-website");
  const field = document.getElementById("pf-pixel-field");
  const pixel = document.getElementById("pf-pixel");
  if (!site || !field || !pixel) return;
  function https(value) {
    try {
      const u = new URL(value.trim());
      return u.protocol === "https:" && u.hostname.includes(".");
    } catch (_) { return false; }
  }
  function sync() { field.hidden = !https(site.value) && !pixel.value.trim(); }
  site.addEventListener("input", sync);
  pixel.addEventListener("change", sync);
  sync();
})();
