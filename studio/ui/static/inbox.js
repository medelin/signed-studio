// Muse Studio Lite — inbox: triage a comment (draft only), then sign the exact reply.
// Text from comments and models is rendered with textContent only.
"use strict";

(() => {
  const root = document.getElementById("inbox");
  const loggedIn = root.dataset.loggedIn === "yes";
  const say = (node, text, isError) => {
    node.textContent = text;
    node.classList.toggle("error", Boolean(isError));
  };
  const top = document.getElementById("inbox-status");

  async function signPending(card, approvalId, label) {
    const st = card.querySelector(".status");
    const payload = await Signer.loadPayload(approvalId);
    const details = card.querySelector("details");
    details.hidden = false;
    details.querySelector("pre").textContent = JSON.stringify(payload.content, null, 2) + "\n\n"
      + T("signed_dump", { sha: payload.content_sha256, bytes: atob(payload.message_b64) });
    say(st, T("signing_label", { label }));
    const done = await Signer.sign(payload);
    let text = T("label_signed", { label, sig: done.signature.slice(0, 16) });
    if (done.executed) text += " " + T("sent_via", { adapter: done.result && done.result.adapter || "" });
    else if (done.execution_error) text += " " + T("not_sent", { error: done.execution_error });
    else text += " " + T("no_sender");
    say(st, text, Boolean(done.execution_error));
  }

  async function act(card, what) {
    const id = card.dataset.id;
    const st = card.querySelector(".status");
    try {
      if (what === "triage") {
        say(st, T("triaging"));
        const r = await Signer.postJSON("/api/comments/" + encodeURIComponent(id) + "/triage", {});
        if (!r.ok) throw new Error(T("triage_failed", { error: E(r.data, r.status) }));
        location.reload();
        return;
      }
      const body = what === "reply" ? { message: card.querySelector("textarea").value } : {};
      const r = await Signer.postJSON("/api/comments/" + encodeURIComponent(id) + "/" + what, body);
      if (!r.ok) throw new Error(T("cannot_prepare", { error: E(r.data, r.status) }));
      await signPending(card, r.data.approval_id, what === "reply" ? T("reply") : T("hide"));
    } catch (e) {
      say(st, e.message, true);
    }
  }

  for (const card of document.querySelectorAll(".comment")) {
    for (const btn of card.querySelectorAll("button[data-act]")) {
      btn.addEventListener("click", () => act(card, btn.dataset.act));
    }
  }

  // A7: Meta disapproved an ad. "Propose" asks for one compliant variant (copywriter +
  // both reviewers) and a pending replacement; "sign" signs exactly that payload.
  for (const card of document.querySelectorAll(".disapproved")) {
    const st = card.querySelector(".status");
    for (const btn of card.querySelectorAll("button[data-dp]")) {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          if (btn.dataset.dp === "sign") {
            await signPending(card, btn.dataset.approval, T("dp_sign_label"));
            return;
          }
          say(st, T("dp_proposing"));
          const r = await Signer.postJSON("/api/disapprovals/" + encodeURIComponent(card.dataset.id) + "/propose", {});
          if (!r.ok) throw new Error(T("dp_failed", { error: E(r.data, r.status) }));
          if (r.data.status === "blocked") { say(st, T("dp_blocked"), true); btn.disabled = false; return; }
          location.reload();
        } catch (e) {
          say(st, e.message, true);
          btn.disabled = false;
        }
      });
    }
  }

  const sim = document.getElementById("simulate-form");
  if (sim) {
    sim.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const r = await Signer.postJSON("/api/comments/simulate", { text: document.getElementById("sim-text").value });
      if (!r.ok) { say(top, T("cannot_add", { error: E(r.data, r.status) }), true); return; }
      location.reload();
    });
  }

  if (loggedIn) {
    Signer.registerKey().then(
      (id) => say(top, T("your_key", { key: id })),
      (e) => say(top, e.message, true));
  }
})();
