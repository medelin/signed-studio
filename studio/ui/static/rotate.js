// Owners taking turns in the illustrations built by cast.rotation: every few seconds one
// slot (in turn, never all at once) cross-fades to its next person. The incoming person
// never looks like anyone shown beside them (pronoun and skin tone both differ, from
// data-look); a slot with no such person waits for the next turn. Nothing moves under
// prefers-reduced-motion or while the tab is hidden; the first person of each slot is
// what a reader without script sees.
"use strict";

(() => {
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const EVERY_MS = 7000;
  const FADE_MS = 900;  // components.css: .il-rotate .turn transition
  const look = (g) => (g.dataset.look || "").split(" ");
  const unlike = (a, b) => a[0] !== b[0] && a[1] !== b[1];
  for (const art of document.querySelectorAll("svg.il-rotate")) {
    const slots = [...art.querySelectorAll(".slot")].filter((s) => s.children.length > 1);
    if (!slots.length) continue;
    let turn = 0;
    setInterval(() => {
      if (still.matches || document.hidden) return;
      const slot = slots[turn];
      turn = (turn + 1) % slots.length;
      const beside = slots.filter((s) => s !== slot)
        .map((s) => s.querySelector(".turn.on")).filter(Boolean).map(look);
      const turns = [...slot.children];
      const at = turns.findIndex((g) => g.classList.contains("on"));
      const old = turns[at];
      let incoming = null;
      for (let i = 1; i < turns.length && !incoming; i += 1) {
        const g = turns[(at + i) % turns.length];
        if (beside.every((b) => unlike(look(g), b))) incoming = g;
      }
      if (!incoming) return;
      // no dip through the background: the next person fades in on top of the one
      // shown (moved last, so it paints over), which fades out only afterwards
      old.classList.add("was");
      slot.appendChild(incoming);
      incoming.classList.add("on");
      setTimeout(() => old.classList.remove("on", "was"), FADE_MS);
    }, EVERY_MS);
  }
})();
