// Muse Studio Lite — onboarding: the six steps of /start as a looping sequence (about 48 s,
// 8 s a step), and the one-time "how it works" banner on /studio. Reads and sends nothing.
//
// The player: Play / Pause, Previous / Next, one dot per step, arrow keys, swipe on touch.
// The step advances when the progress bar's CSS animation ends, so pausing the bar pauses
// the clock. Any manual step change pauses the loop (the viewer has taken over). Under
// prefers-reduced-motion nothing runs: every step stays listed and every drawing is still.
"use strict";

(() => {
  // -- the first-visit banner (per account; remembered in this browser only) -----------
  const banner = document.getElementById("ob-banner");
  if (banner) {
    const key = "muse.onboarding.seen." + (banner.dataset.key || "anon");
    let seen = false;
    try { seen = window.localStorage.getItem(key) === "1"; } catch (e) { seen = false; }
    const remember = () => {
      try { window.localStorage.setItem(key, "1"); } catch (e) { /* storage blocked: shown again next visit */ }
    };
    if (!seen) {
      banner.hidden = false;
      const close = document.getElementById("ob-banner-close");
      if (close) close.addEventListener("click", () => { remember(); banner.hidden = true; });
      const link = banner.querySelector("a");
      if (link) link.addEventListener("click", remember);
    }
  }

  // -- the player on /start -------------------------------------------------------------
  const root = document.getElementById("ob");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!root || reduce) return;
  const steps = Array.from(root.querySelectorAll(".ob-step"));
  const dots = Array.from(root.querySelectorAll(".ob-dot"));
  const controls = root.querySelector(".ob-controls");
  const play = document.getElementById("ob-play");
  const pos = document.getElementById("ob-pos");
  const bar = root.querySelector(".ob-bar");
  const player = root.querySelector(".ob-player");
  const total = steps.length;
  if (!total || !controls || !play || !bar) return;
  let current = 1;
  let playing = false;
  let hiddenWhilePlaying = false;

  function show(n) {
    current = ((n - 1 + total) % total) + 1;
    steps.forEach((el, i) => el.classList.toggle("is-active", i === current - 1));
    dots.forEach((d, i) => {
      if (i === current - 1) d.setAttribute("aria-current", "step");
      else d.removeAttribute("aria-current");
    });
    if (pos) pos.textContent = T("ob_step_of", { n: current, total: total });
    bar.classList.remove("run");
    void bar.offsetWidth;  // restart the progress animation
    bar.classList.add("run");
  }

  function setPlaying(on) {
    playing = on;
    root.classList.toggle("playing", on);
    play.textContent = T(on ? "ob_pause" : "ob_play");
    if (pos) pos.setAttribute("aria-live", on ? "off" : "polite");  // no chatter while it plays
  }

  function go(n) { setPlaying(false); show(n); }

  bar.addEventListener("animationend", (e) => {
    if (e.animationName === "ob-progress" && playing) show(current + 1);
  });
  play.addEventListener("click", () => {
    setPlaying(!playing);
  });
  document.getElementById("ob-prev").addEventListener("click", () => go(current - 1));
  document.getElementById("ob-next").addEventListener("click", () => go(current + 1));
  dots.forEach((d) => d.addEventListener("click", () => go(Number(d.dataset.step))));

  document.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const tag = e.target && e.target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || (e.target && e.target.isContentEditable)) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); go(current - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(current + 1); }
  });

  let startX = null;
  let startY = 0;
  player.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1) { startX = null; return; }
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
  }, { passive: true });
  player.addEventListener("touchend", (e) => {
    if (startX === null || !e.changedTouches.length) return;
    const dx = e.changedTouches[0].clientX - startX;
    const dy = e.changedTouches[0].clientY - startY;
    startX = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) go(current + (dx < 0 ? 1 : -1));
  }, { passive: true });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && playing) { hiddenWhilePlaying = true; setPlaying(false); }
    else if (!document.hidden && hiddenWhilePlaying) { hiddenWhilePlaying = false; setPlaying(true); }
  });

  root.classList.add("live");
  controls.hidden = false;
  setPlaying(true);
  show(1);
})();
