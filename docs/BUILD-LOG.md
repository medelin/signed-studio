# Signed Studio (PayPal edition): build log

A dated record of what was built for the PayPal AI Hackathon, and when. Everything here
came after the base, which is tagged `base-meta-final` (commit `8d5f854`, 3 Nov 2026 13:10
CET). The base was written for a different hackathon, from 27 Oct 2026. Check the dates
yourself with `git log --format='%ad %s' --date=iso base-meta-final..main`.

On 5 Nov 2026 the history was rewritten once before publishing: one author identity
(`Piotr Kaczmarski`, GitHub noreply address), assistant co-author trailers removed, every
commit re-signed with the same SSH key. Trees, parents, messages otherwise and all author
and committer dates are unchanged, so every date below still holds; only hashes changed
(the base tag moved from `5f8ffb6` to `8d5f854`; `5f8ffb6` is still the same commit in the
base repository).

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
- *The offline re-check matches one to one.* `studio verify --paypal-events` now pairs
  each export line with exactly one `paypal.webhook` receipt on (transmission, body
  hash), never by body alone. Unpaired items on either side fail, so a tampered receipt
  cannot hide behind a valid one with the same body.

## 2026-11-03 (Tue, night): Day 3, agentic commerce (Vault v3 and signed spending mandates)

The owner's own assistant can now pay for campaigns from the owner's saved PayPal, but
only within a spending mandate the owner signed, and `studio verify` proves it stayed
within it.

**Vault v3** (`studio/paypal/vault.py`)
- Save PayPal on `/account`, with the buyer present. The studio creates a setup token
  (`usage_type: MERCHANT`, `customer_type: CONSUMER`). The buyer approves it at PayPal's
  `approve` link, and on return the studio exchanges it for a payment token
  (`POST /v3/vault/payment-tokens`).
- Stored: the studio's reference (`vlt_…`), the account, the setup token id, PayPal's
  vault id and its SHA-256. No payer data is stored: PayPal's answers carry the buyer's
  e-mail and payer id, and both are dropped.
- The vault id never enters a receipt, a prompt or an API answer; receipts carry its
  SHA-256.
- Removing a saved PayPal marks it deleted locally first (no charge can use it from that
  moment), then calls `DELETE /v3/vault/payment-tokens/{id}`.
- In mock mode the fake PayPal implements the same setup → payment-token → charge →
  delete flow.

**The mandate signs the money.**
- A mandate body may now carry a closed `funding` block (`mandate-funding/v1`):
  `vault_sha256`, `max_charge_minor` (per campaign, fee included), `funding_cap_minor`
  (per period), `period_days`, `period_start` and `kinds` (campaign kinds: `link`,
  `message`, `lead_form`).
- It is part of the content the owner signs on the signing screen, which lists it in
  plain words.
- Creating the mandate refuses a saved PayPal that is not the account's own and active, a
  backdated period, and a per-charge cap above the period cap.

**Charges under the mandate** (`studio.mandates.execute`, `Funder.charge`)
- With PayPal on, a token's run that fits a mandate is paid from the saved PayPal. One
  transaction consumes the approval, declares the charge (`charge/v1`) in the approval
  receipt, and reserves it in `mandate_charges`.
- The reservation counts charges still in flight against the period cap, so two
  concurrent runs cannot pass the cap together.
- PayPal is then sent an `AUTHORIZE` order with `payment_source.paypal.vault_id` and
  `custom_id` = the ad's hash. The money is held, and only then is the campaign created
  PAUSED. The capture still needs the owner's activation signature.
- The temporary Day-2 `funding_required` refusal of mandate campaigns is gone. A mandate
  without a `funding` block now gets the explicit reason `no_funding`, and the owner pays
  and signs.
- Refusals reach the assistant as reasons: `max_charge`, `funding_cap`, `kind`, `vault`,
  `charge_currency`, `period`, plus the base reasons (`revoked`, `expired`, …).
- Fail closed:
  - On a definite PayPal refusal, the reserved amount is freed and the run is reopened
    for the owner with a fresh approval (the pay-and-sign path).
  - On an unknown outcome (a timeout or a 5xx), the amount stays reserved, and only the
    same request id can settle it.
  - If the mandate was revoked, or the saved PayPal removed, while PayPal was answering,
    the hold is voided at once.
  - A campaign that is then not created has its hold voided.
- `Funder.for_create` accepts a mandate's held money only for exactly its approval, item,
  ad hash and amount. A buyer's signed block can never point at a vault charge.

**`studio verify` P-V5** (`receipts.verify`)
- A `paypal.vault_charge` must name a by-mandate approval that declared it, under an
  owner-signed `mandate.grant` with a `funding` block.
- The charge must be under the same mandate and the same saved PayPal (SHA-256), for the
  declared amount (at most the per-charge cap), with PayPal's `custom_id` equal to the
  approved ad.
- Cumulative charges of one period may not exceed the cap, and one PayPal order may be
  charged only once in the log.
- A charge recorded after the mandate's revocation must be voided and never captured.
- P-V1 (capture) and P-V3 (webhooks) accept vault charges as the funding of their order.

**Attack classes 10 to 12** (`tests/attack/test_paypal_vault.py`). Each class is refused
at run time (nothing reaches PayPal, or the hold is voided) and caught offline when the
server re-signs a forged log with its own key:
- 10: a charge over the per-charge cap, over the period cap, over what the approval
  declared, or never declared;
- 11: an approval and charge forged after the revocation, or a charge landing after it
  and left held;
- 12: a charge relabelled to another mandate, a charge of another saved PayPal, or one
  PayPal order counted under a second mandate.

**MCP and API: `run_brief`.**
- A new assistant operation, exposed as an MCP tool and as
  `POST /api/v1/briefs/{brief_id}/run`.
- It runs a confirmed brief, waits up to `wait_s` and returns one of these outcomes:
  - `mandate_charge`, with the receipt of the vault charge (receipt id and hash, order,
    authorization, amount) and the PAUSED campaign;
  - `pay_and_sign`, with the `pay_and_sign_url` for the owner and the mandate's reasons;
  - `running`, while the run is still going.
- `run_status` has the same `payment` section.

**Treasurer (light)** (`studio/agents/treasurer.py`)
- Muse Spark 1.3 with strict JSON and no tools proposes a budget split and explains a
  funding's reconciliation. The prompt holds only code's numbers.
- Code recomputes everything: a split that does not add up or breaks the cap, figures
  that differ from `funding.reconcile`, or an amount in the text that code did not
  compute is replaced by code's version, with the reasons.
- Route: `POST /api/funding/{id}/treasurer` (owner only).
- The test fixture is hand-written and labelled synthetic: no Model API key is configured
  for this edition yet.

**Deploy (prepared, not done).**
- `deploy/muse-pay.service` runs on 127.0.0.1:8100 with its own user, data and env.
- `deploy/pay-install.sh` installs the app only: it never writes a web-server config and
  is not `remote-install.sh`, which replaces Caddy's config.
- `deploy/apache/…` and `deploy/nginx/…` hold the proxy sites, and
  `deploy/PAY-DEPLOY.md` covers both targets (an Apache shared host, a fresh VM).

**Docs.** [PAYPAL.md](../PAYPAL.md): how it works, the sandbox buyer flow, the verify
rules and the honest "significant update" narrative. All PayPal work is dated 3 Nov 2026
onwards. The base began on 27 Oct 2026, also inside the submission period.

**Not yet.**
- The Vault sandbox cassette needs the sandbox buyer to approve saving PayPal in a
  browser (`scripts/paypal_record_vault.py --start` prints the URL, `--finish` records).
- Webhook registration, deploy, the video and the Devpost text are Day 4.

## 2026-11-05 (Wed): PayPal tools everywhere

- **Agent Toolkit read tools** (vendored `paypal_agent_toolkit/shared` 1.11.0): four read
  tools on the receipted sandbox client, payer data stripped, own PayPal ids only, keyed-hash
  receipts bound to the account's chain.
- **Reconciliation and assistant ops:** `paypal_transactions`, `paypal_tool`,
  `propose_refund` (a pending `funding.refund` approval; never moves money).
- **Sandbox negative testing:** `PayPal-Mock-Response` on capture only, a decline control at
  activation, PayPal's reason shown, `debug_id` receipted.
- **Money page** in AG Grid Community 34.3.1 (noStyle build, vendored CSS, strict CSP).
- **Postman collection** (`make postman`), base_url per deployment.
- **Tools used while building (controller, 5 Nov 2026):** PayPal's `/paypal:doctor` (PayPal
  AI Toolkit plugin 1.2.0 for Claude Code) on the repo: 0 critical, 4 warnings, all fixed on
  this branch (`.env.example` PayPal block; declined authorize kept with a retry path;
  webhook lookup without the full retry budget; `debug_id` receipted). The APIMatic Context
  Plugin for PayPal was installed in the coding agent. Both helped while writing code; neither
  runs in the app.
- **Transaction Search** was enabled for the sandbox app on 5 Nov 2026 (scope propagation
  was still pending then).
- **Docs:** README, PAYPAL.md, assistants (optional PayPal MCP setup), demo script, Devpost text.
