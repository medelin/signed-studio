// /account#billing: the payment consent names the chosen plan, and the 14-day withdrawal
// acknowledgement is shown to a private person only. Without JavaScript the page shows a
// general consent sentence and both parts; the server checks what each buyer must tick.
"use strict";
(() => {
  const form = document.querySelector("[data-billing-form]");
  if (!form) return;
  const texts = [...form.querySelectorAll("[data-plan-text]")];
  const parts = [...form.querySelectorAll("[data-buyer-only]")];

  function sync() {
    const plan = form.querySelector('input[name="plan"]:checked');
    const chosen = plan ? plan.value : "";
    const specific = texts.some((s) => s.dataset.planText === chosen && chosen !== "");
    for (const s of texts) {
      s.hidden = specific ? s.dataset.planText !== chosen : s.dataset.planText !== "";
    }
    const buyer = form.querySelector('input[name="buyer"]:checked');
    for (const p of parts) p.hidden = buyer ? p.dataset.buyerOnly !== buyer.value : false;
  }

  form.addEventListener("change", sync);
  sync();
})();
