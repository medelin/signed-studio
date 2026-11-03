# Signed Studio (PayPal edition): build log

A dated record of what was built for the PayPal AI Hackathon, and when. Everything here
came after the base, which is tagged `base-meta-final` (commit `5f8ffb6`, 3 Nov 2026 13:10
CET). The base was written for a different hackathon, from 27 Oct 2026. Check the dates
yourself with `git log --format='%ad %s' --date=iso base-meta-final..main`.

## 2026-11-03 (Tue): repo setup and Day 1, the money core

**Setup**

- A fresh local clone of the base at `5f8ffb6`. All inherited remotes were removed, and
  the base was tagged with the signed tag `base-meta-final`. A local `pre-push` hook
  refuses any remote URL that names the base repo, which stays frozen for its own judges.
- Origin is a private Forgejo repo. No public repo yet.
- Product name in user-facing PayPal material: **Signed Studio**. Module names keep their
  old prefix; the README and UI rename comes later.
- Studio fee: **5 %** of the ad budget (`STUDIO_FEE_PCT`, default 5, allowed 0–30). It is
  added to the PayPal order as its own line item. At reconciliation the fee is due only
  on what Meta actually spent, so unspent budget comes back with its share of the fee.

**Stripe removed.** The Stripe subscription, the metered ad-spend fee, the price cache, the
`/account/billing/*` routes, `/webhooks/stripe`, the `studio billing` CLI, the billing
timer units, and their tests and strings are gone. `/pricing` and `/terms` now describe the
per-campaign PayPal funding. The money in this edition is the campaign budget, funded
through PayPal. There is no plan billing (decision D5).

**Money core (new)**

- `studio/paypal/__init__.py`: `PayPalConfig` (`PAYPAL_ADAPTER=mock|rest`). Any
  `PAYPAL_ENV` other than `sandbox` stops the app at start (`SandboxOnly`). The base URL is
  the constant `https://api-m.sandbox.paypal.com`, and no parameter can change it.
- `studio/paypal/client.py`: the REST client over `httpx` (no SDK). It has an OAuth2
  client-credentials token cache (refreshed 60 s before expiry; a 401 drops it once).
  Every POST carries `PayPal-Request-Id`, derived from the funding id, the step and the
  approval id. Every call sends `Prefer: return=representation`. 429/5xx and network
  errors are retried with the same request id. Errors carry PayPal's error name, the
  issue code, a readable reason and `debug_id`, never the body or a secret.
- `studio/paypal/orders.py`: Orders v2 create/get/authorize and Payments v2
  capture/reauthorize/void/refund. The order body is built only from the code-computed
  quote, with `intent: AUTHORIZE`, `custom_id` = the ad's `content_sha256`,
  `invoice_id` = the funding id, and items "Ad budget" and "Studio fee". Answers are
  parsed strictly.
- `studio/paypal/mock.py`: a deterministic fake PayPal (an `httpx` transport) with the same
  state machine, idempotent request ids and PayPal's error shapes.
- `studio/funding.py`: the money model. Amounts are integers in minor units, and amounts
  with too many decimals are refused, never rounded. It holds the quote (budget × days +
  fee), the closed `funding/v1` block and its `funding_sha256` (what the owner will sign),
  the state machine `created → payer_approved → authorized → captured →
  partially_refunded/refunded` (plus `voided` and `expired`), and the reconciliation
  arithmetic. The `fundings` table has a CHECK that captured ≤ authorized ≤ total and
  refunded ≤ captured. `funding_moves` is the money ledger, one row per PayPal movement,
  each naming its receipt.
- `studio/paypal/money.py`: each money step runs in this order: state check → PayPal
  call → check PayPal's answer (the ad hash in `custom_id`, the amount, the status) → one
  transaction with the receipt, the new state and the money move. Failures leave an
  `ok: false` receipt.
- `studio/receipts.py`: a new receipt kind `paypal.call`. PayPal's own ids are kept exact
  when they have PayPal's shape, because the PII redaction would otherwise read their
  digit runs as phone numbers. Receipts hold ids, codes, integers and digests only: no
  payer name, e-mail or payer id.

**Tests.** `tests/test_paypal_client.py`, `tests/test_funding.py` and
`tests/test_paypal_money.py` run fully offline. Fixtures are `httpx.MockTransport` and
`fixtures/cassettes/paypal/orders.SYNTHETIC.json`. That file is hand-written from PayPal's
API reference and labelled synthetic; it is kept beside the real recording below. `tests/test_paypal_live.py` is a live sandbox smoke test (token,
an `AUTHORIZE` order keeping `custom_id`, idempotent create, the `ORDER_NOT_APPROVED`
refusal). It is skipped unless `~/.config/muse-pay/paypal-sandbox.env` exists.

**Security review fixes (same day)**

- *Refund race and replays.* Every money step first takes a claim in `funding_claims`,
  keyed by its approval id, so one signed approval moves money once (the primary key
  enforces it). A pending refund claim reserves its amount, so a concurrent refund cannot
  pass the "at most what is left" check. Refunds add relatively under a version guard
  read inside the commit transaction. `funding_moves` has `UNIQUE(move, paypal_id)`, so a
  replayed PayPal answer rolls back and is receipted as `NOT_RECORDED`. Capture and refund
  request ids include the amount.
- *Fail closed.* Claims are never deleted. Their statuses are `pending`, `done`,
  `refused` and `released`. The client carries "sent" and "unknown" across its retries.
  An ambiguous outcome stays pending, with its amount reserved: a timeout, a 5xx, a 409,
  or a duplicate or in-progress issue. Only a retry with the same approval and amount
  (hence the same request id) can settle it. A definite refusal spends the approval.
  Only a request that never left the studio is released, and only for the same
  parameters.
- *A success that cannot be read is not a refusal.* A claim becomes `refused` only on a
  definite 4xx. Any error for an answer below 400 (a body that does not parse or check) is
  `unknown`, so its claim stays pending. `Money.reconcile_claim` settles a pending claim
  by sending the same step again with the same `PayPal-Request-Id`: PayPal returns the
  first result instead of acting twice.
- *No stale paid tiers.* `db.init` refuses a database that carries any Stripe billing
  state, so a copy of the base deployment's database can never start here. After the
  removal, only operator commands write `accounts.tier`.

**Live sandbox.** The sandbox REST app "Signed Studio" now exists.
`tests/test_paypal_live.py` passed against `api-m.sandbox.paypal.com`: a token, an
`AUTHORIZE` order that keeps `custom_id` = the content hash, an idempotent re-create
(same id), read-back, and the `ORDER_NOT_APPROVED` refusal. `scripts/paypal_record.py`
recorded those answers into `fixtures/cassettes/paypal/sandbox.json` (scrubbed: no OAuth
exchange, payer or payee; `debug_id`s replaced). No money moved: an authorize, capture or
void needs a buyer approval in the browser, which is Day 2. `kingfisher scan` (tree and
full history) found nothing.

**Not yet** (next days): `funding_sha256` signed into approvals, the PayPal button, the
executor hooks (authorize after create, capture on activate, void on lapse), webhooks with
offline RSA verification, `studio verify` rules P-V1…P-V6, vault and mandates, the
treasurer, and the AG Grid money page.
