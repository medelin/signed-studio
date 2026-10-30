# Changes for after the hackathon

Decided by the operator on 2026-10-30; not live on the judge sandbox (muse.udun.eu, which
is deleted after 16 November 2026).

## Commitment plans (built, switched off on the sandbox)
- 3 months billed upfront (3 × monthly), 6 months for the price of 5, 12 months for the
  price of 10 — code, pricing page, Terms and renewal reminders are in place
  (`STRIPE_PRICE_3M`, `STRIPE_PRICE_6M`, `STRIPE_PRICE_12M`; an unset id hides the plan).
- Before switching them on: a production host that outlives the hackathon (the sandbox's
  Privacy page promises deletion after 16 November, which contradicts a 12-month term),
  live Stripe prices, the Stripe dashboard set to send `invoice.upcoming` 7 days ahead and
  to allow plan switching in the customer portal.

## Open from the Terms review
- A model withdrawal form for consumers (today: by e-mail).
- What happens above the fair-use line (60 runs a month): today nothing is enforced.
- Suspension for misuse and jurisdiction wording.

## Other
- Rename the plan schema field `persona` to `moment` (the recorded replay sets use the old
  name, so it waits for re-recorded sets).
- Daily rows in the spend statement need an insights pull with `time_increment=1`.
- Signed mandates (`docs/mandates.md`): AP2-compatible format; optional settlement by a bank
  payment with the bank's own strong customer authentication (PSD2 payment initiation).
- Mandate approvals: `studio verify` checks the `checked` values against the signed
  mandate, but those values are the server's (ledger-signed) statement; carry the redacted
  delivery fields (objective, countries, hosts, budget, account, token) in the receipt so
  the verifier recomputes the fit, or report mandate-made campaigns separately.
- `GET /api/approvals/{id}/payload` lets any signed-in session read any pending payload
  (including a pending mandate's limits); restrict it to the account that owns the run.
