// Help page enhancements; the page works without them (<details> accordion, anchors).
// - a link to #question opens that question;
// - opening/closing a question uses a View Transition where supported (not under
//   prefers-reduced-motion).
(() => {
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const openTarget = () => {
    const id = decodeURIComponent(location.hash.slice(1));
    const el = id && document.getElementById(id);
    if (el && el.tagName === "DETAILS") el.open = true;
  };
  openTarget();
  addEventListener("hashchange", openTarget);
  for (const summary of document.querySelectorAll(".faq-item > summary")) {
    summary.addEventListener("click", (ev) => {
      if (!document.startViewTransition || still.matches) return;
      ev.preventDefault();
      const item = summary.parentElement;
      document.startViewTransition(() => { item.open = !item.open; });
    });
  }
})();
