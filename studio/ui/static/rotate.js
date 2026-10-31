// Owners taking turns in the illustrations built by cast.rotation: every few seconds one
// slot (in turn, never all at once) cross-fades to its next person. Nothing moves under
// prefers-reduced-motion or while the tab is hidden; the first person of each slot is
// what a reader without script sees.
"use strict";

(() => {
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const EVERY_MS = 3500;
  const FADE_MS = 900;  // components.css: .il-rotate .turn transition
  for (const art of document.querySelectorAll("svg.il-rotate")) {
    const slots = [...art.querySelectorAll(".slot")].filter((s) => s.children.length > 1);
    if (!slots.length) continue;
    let next = 0;
    setInterval(() => {
      if (still.matches || document.hidden) return;
      const slot = slots[next];
      next = (next + 1) % slots.length;
      const turns = [...slot.children];
      const at = turns.findIndex((g) => g.classList.contains("on"));
      const old = turns[at];
      const incoming = turns[(at + 1) % turns.length];
      // no dip through the background: the next person fades in on top of the one
      // shown (moved last, so it paints over), which fades out only afterwards
      old.classList.add("was");
      slot.appendChild(incoming);
      incoming.classList.add("on");
      setTimeout(() => old.classList.remove("on", "was"), FADE_MS);
    }, EVERY_MS);
  }
})();
