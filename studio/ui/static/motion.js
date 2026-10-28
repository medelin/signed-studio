// Motion for the studio page, layered on top of app.js without touching its logic: it
// watches the DOM app.js renders (MutationObserver) and animates it with the Web
// Animations API. Under prefers-reduced-motion every effect has a static equivalent: the
// run HUD still marks steps, the stamp still appears, the counters show final values.
"use strict";

(() => {
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  document.documentElement.classList.add("motion-ready");
  const EASE = "cubic-bezier(.2, .8, .2, 1)";
  const animate = (node, frames, opts) =>
    still.matches || !node.animate ? null : node.animate(frames, { duration: 200, easing: EASE, fill: "backwards", ...opts });

  // -- run HUD: producer → copywriter → art director → reviewers → approval ------------
  const hud = document.getElementById("run-hud");
  const STEP = { producer: 0, copywriter: 1, art_director: 2, reviewer: 3, reviewer_open: 3, human: 4 };
  let reached = -1;
  function markStep(agent, kind) {
    if (!hud) return;
    const i = kind === "approval" ? 4 : STEP[agent];
    if (i === undefined) return;
    const steps = hud.querySelectorAll("li");
    if (i > reached) reached = i;
    steps.forEach((li, n) => {
      const was = li.dataset.state;
      const now = n < reached || reached === 4 ? "done" : n === reached ? "active" : "";
      li.dataset.state = now;
      if (now === "active") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
      if (now && now !== was) animate(li.querySelector(".tick"), [{ transform: "scale(.2) rotate(45deg)" }, { transform: "scale(1) rotate(45deg)" }], { duration: 320 });
    });
    const ticks = hud.querySelector(".hud-ticks");
    if (ticks) ticks.dataset.progress = String(Math.min(reached + 1, 5));
  }
  function resetHud() {
    reached = -1;
    if (!hud) return;
    hud.querySelectorAll("li").forEach((li) => { li.dataset.state = ""; li.removeAttribute("aria-current"); });
  }

  // -- counters: cost and tokens roll with @property (CSS) ----------------------------
  const costOut = document.getElementById("cost-counter");
  const tokOut = document.getElementById("tokens-counter");
  let tokens = 0;
  let cost = 0;
  function setCounters() {
    const micro = Math.round(cost * 100000);
    if (costOut) {
      costOut.style.setProperty("--whole", String(Math.floor(micro / 100000)));
      costOut.style.setProperty("--frac", String(micro % 100000));
    }
    if (tokOut) tokOut.style.setProperty("--n", String(tokens));
  }

  // -- receipt tape: each new receipt slides in, a link line draws to the previous one --
  const tape = document.getElementById("ticker");
  if (tape) {
    new MutationObserver((records) => {
      for (const rec of records) {
        if (rec.type === "childList" && rec.addedNodes.length === 0 && tape.children.length === 0) {
          resetHud(); tokens = 0; cost = 0; setCounters();
        }
        for (const row of rec.addedNodes) {
          if (row.nodeType !== 1 || row.tagName !== "TR") continue;
          const cells = row.children;
          const agent = cells[0] ? cells[0].textContent : "";
          const kind = cells[1] ? cells[1].textContent : "";
          markStep(agent, kind);
          const [tin, tout] = (cells[3] ? cells[3].textContent : "0/0").split("/").map(Number);
          tokens += (tin || 0) + (tout || 0);
          const total = document.getElementById("total-cost");  // app.js keeps the exact sum
          cost = total ? Number(total.textContent.replace("$", "")) || 0 : cost;
          setCounters();
          if (row.previousElementSibling) row.classList.add("linked");
          animate(row, [{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }], { duration: 260 });
          if (row.classList.contains("linked") && cells[0]) {
            animate(cells[0], [{ transform: "scaleY(0)" }, { transform: "scaleY(1)" }], { duration: 320, delay: 120, pseudoElement: "::before" });
          }
        }
      }
    }).observe(tape, { childList: true });
  }

  // -- creatives fade in as Muse Image results arrive --------------------------------
  const images = document.getElementById("images");
  if (images) {
    new MutationObserver((records) => {
      for (const rec of records) {
        rec.addedNodes.forEach((fig, n) => {
          if (fig.nodeType !== 1 || fig.tagName !== "FIGURE") return;
          const img = fig.querySelector("img");
          const show = () => animate(fig, [{ opacity: 0, transform: "translateY(8px) scale(.98)" }, { opacity: 1, transform: "none" }], { duration: 420, delay: n * 90 });
          if (img && !img.complete) { fig.classList.add("arriving"); img.addEventListener("load", () => { fig.classList.remove("arriving"); show(); }, { once: true }); }
          else show();
        });
      }
    }).observe(images, { childList: true });
  }

  // -- approval: the signature hash stamps onto the payload ---------------------------
  const signature = document.getElementById("signature");
  const stamp = document.getElementById("stamp");
  if (signature && stamp) {
    new MutationObserver(() => {
      if (signature.hidden) { stamp.hidden = true; return; }
      const value = document.getElementById("sig-value").textContent;
      stamp.querySelector(".stamp-hash").textContent = value.slice(0, 16);
      stamp.hidden = false;
      markStep("human", "approval");
      animate(stamp, [
        { opacity: 0, transform: "scale(2.4) rotate(-14deg)" },
        { opacity: 1, transform: "scale(.94) rotate(-6deg)", offset: 0.7 },
        { opacity: 1, transform: "scale(1) rotate(-6deg)" },
      ], { duration: 520 });
    }).observe(signature, { attributes: true, attributeFilter: ["hidden"] });
  }
})();
