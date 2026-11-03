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

## 2026-11-03 (Tue, evening): Day 2, signed funding

The owner's signature now covers the money. The order of events:

1. The run ends with a pending `campaign.create` approval, as before.
2. On the approval screen, the studio creates the PayPal order
   (`POST /api/funding/{approval}/order`). Code computes the amount from the payload: the
   daily budget × days plus the 5 % fee. `custom_id` is the ad's hash.
3. The buyer approves it in PayPal. In mock mode a button plays the buyer.
4. The server reads the order back. Only when PayPal says APPROVED is the approval
   replaced by one whose payload carries the closed `funding/v1` block. The signed
   message adds `funding_sha256`, and the approval receipt carries the block.
5. Signing creates the campaign PAUSED, then **authorizes** the order under the same
   approval: the money is held, not taken. With PayPal on, a creation without an
   approved, signed funding creates nothing.
6. The activation payload names the held funding, so the second signature covers the
   **capture**. The capture runs before Meta activates anything: no capture, no
   activation.
7. A hold not captured within the 3-day honour period is voided
   (`studio paypal void-lapsed`).
8. A refund is its own signed `funding.refund` approval over a closed `refund/v1` body.

**Webhooks.** `POST /webhooks/paypal` verifies before parsing:

- the transmission headers, and a transmission time within 5 minutes;
- a cert URL on an allowlist (`api.paypal.com` and `api.sandbox.paypal.com` over HTTPS
  only, so the header cannot point the server at an internal address);
- a cert chain that is valid now and ends at a trusted root, with a PayPal host name,
  pinned by SHA-256;
- RSA/SHA-256 over `transmission_id|time|webhook_id|crc32(body)`.

Each event is applied once. An event for an order the studio funded becomes a
`paypal.webhook` receipt in that funding's run, with hashes and ids only and no payer
data. Raw events go only into the owner export. `studio verify --paypal-events
--paypal-certs` re-checks PayPal's signatures offline. In mock mode the fake PayPal signs
its events with a test CA, and they go through the same verification.

**`studio verify` rules.**

- P-V1: a capture rides on a `campaign.activate` approval of a campaign whose creation
  signed a funding block for the same order.
- P-V2: captured ≤ authorized ≤ signed.
- P-V3: a webhook names a funded order; its `custom_id` is the signed ad and its amount
  at most the signed amount.
- P-V4: a refund matches its signed body, and refunds never exceed the capture.
- P-V6: every money step names a valid approval of the right action, used once.

**Attack classes 1–9** are in `tests/attack/test_paypal_money.py`:

1. an amount edited after signing;
2. an order id swapped;
3. a capture without activation;
4. a capture above the authorization;
5. a refund without approval, or twice;
6. a forged webhook signature (another key, a changed body, an untrusted CA);
7. a replayed webhook, or a stale one;
8. a webhook for an unknown order;
9. a `custom_id` mismatch.

The attacker is the server itself, holding the ledger key: it re-signs the edited logs,
so only the owner's signature and the P-V rules can catch them.

**UI.** `paypal.js` puts the PayPal step on the approval screen, and signing stays
disabled until PayPal reports the payer approved.
- **JS SDK:** I used the v5 `paypal.Buttons` flow (`intent=authorize`), which is
  documented in depth. I could not test v6 in a browser here; it remains an option.
- **CSP:** the PayPal origins are added only when `PAYPAL_ADAPTER=rest`.

**Recorded sandbox money loop.** The sandbox buyer approved two real `AUTHORIZE`
orders in a browser (the coordinator did it; the agent never had the buyer's password).
`scripts/paypal_record.py --full` then:
- order A: authorized, captured (47.25 GBP) and partly refunded (14.49 GBP);
- order B: authorized and voided.

All of it is recorded, scrubbed, in `fixtures/cassettes/paypal/sandbox_full.json`. The
client parses every answer (`tests/test_paypal_client.py`). The first recorder run
crashed on a transient non-JSON page while polling; it was hardened and resumed with
the same two orders.

**Not yet.**
- With PayPal on, a campaign created under a signed mandate is refused
  (`funding_required`) until Day 3 adds Vault charges under the mandate.

**Security review fixes (Day 2).**
- *Who sees the money.* `GET /api/funding/item/…` and the refund request now apply the
  same ownership as the campaign's approval (`approval_refusal`). Another account's
  items are 404; order creation and payer approval already went through it.
- *Webhook bodies are only a trigger.* PayPal's signature binds the body through CRC32
  only, so the webhook no longer attests anything by itself. The resource it names
  (capture, refund, authorization or order) is re-fetched from PayPal's API with the
  studio's own credentials, and only the API's ids, status, amount and `custom_id` go
  into the receipt (`resource_source: api`, required by `studio verify`). Without that
  read the event is stored `unchecked`, with no receipt. A transmission id is accepted
  once.
- *Offline re-check anchored.* `studio verify --paypal-events` now:
  - takes the webhook id from the verifier (`--paypal-webhook-id`), never from the
    export;
  - requires the cert file's leaf DER hash to equal its name, the event's and the
    receipt's `cert_sha256`;
  - walks the chain to a trusted root (certifi, or `--paypal-roots`) as of the
    transmission time, with a PayPal leaf and CA-only intermediates.

  A self-signed leaf, a swapped cert file, a malformed digest, a reused transmission id
  and a wrong webhook id are each refused in tests.
- *The event's class is PayPal's, not the body's.*
  - A webhook receipt's `event_class` (`capture.completed`, `refund.completed`,
    `authorization.voided` …) is derived from the resource kind and the status PayPal's
    API reports. The body's `event_type` is kept only as `body_event_type`. `studio
    verify` checks that the class matches the recorded status.
  - When the API read fails, nothing is stored and the route answers 503, so PayPal's
    retry is checked afresh instead of being dropped as a duplicate. A resource the
    API does not know is refused (400).
- *Dedupe on what PayPal attests, not on the body.* PayPal's signature binds the body only
  through CRC32, which is easy to collide, so no state is keyed on a body. (A first
  attempt kept tombstones per transmission id; that would let a colliding body poison a
  genuine delivery, so it was replaced the same evening.) Refusals store nothing.
  - An event is recorded once per state PayPal's API attests for one of our resources:
    (kind, id, API status). Its order, authorization or capture must be in `fundings`,
    and its `custom_id` must be that funding's ad hash.
  - A replayed or colliding body that points at a recorded state is a no-op. A new
    genuine state, such as a capture becoming partially refunded, is recorded.
  - A recorded delivery is also a no-op on redelivery (transmission id + body hash).
  - `studio verify` checks one receipt per attested state. The body's event id is
    only a label (`body_event_id`).
