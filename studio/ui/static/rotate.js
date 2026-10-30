// Owners taking turns in the illustrations built by cast.rotation: every few seconds one
// slot (in turn, never all at once) cross-fades to its next person. Nothing moves under
// prefers-reduced-motion or while the tab is hidden; the first person of each slot is
// what a reader without script sees.
"use strict";

(() => {
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const EVERY_MS = 3500;
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
      turns[at].classList.remove("on");
      turns[(at + 1) % turns.length].classList.add("on");
    }, EVERY_MS);
  }
})();
