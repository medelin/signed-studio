# Signed Studio: the PayPal edition

**An AI ad agency for one-person businesses that cannot spend a cent you did not sign
for.** PayPal holds the budget, your signature releases it, and PayPal's own signed
webhooks prove it. Your own AI assistant can also pay for campaigns from your saved
PayPal, but only within a spending mandate you signed, and `studio verify` proves offline
that it stayed within it.

Everything here runs against the **PayPal sandbox** only. The base URL is a constant
(`https://api-m.sandbox.paypal.com`), and any `PAYPAL_ENV` other than `sandbox` stops the
app at start.

## What was updated during the Submission Period

The submission period runs from 1 Oct to 12 Nov 2026. All of this project's history
falls inside it, and the git history shows every date:

- **The base** (agents, Ed25519 signed approvals, the hash-chained receipt ledger, the
  Meta adapter that only creates PAUSED campaigns, mandates for assistants) was written
  from **27 Oct 2026** for a different hackathon. Its last commit is tagged
  `base-meta-final` (`5f8ffb6`, 3 Nov 2026 13:10 CET).
- **Everything PayPal was written from 3 Nov 2026**, after that tag:
  - the PayPal client, plus Orders v2 authorize, capture, void and refund;
  - the signed `funding/v1` block in the owner's approval;
  - webhooks verified offline against PayPal's RSA signature and re-read from PayPal's
    API;
  - Vault v3, and customer-not-present charges under a signed spending mandate;
  - the `studio verify` rules P-V1 to P-V6 and 12 attack classes;
  - the AI treasurer;
  - the removal of the base's Stripe billing.

Check it yourself:

```bash
git log -1 --format='%h %ad' --date=iso base-meta-final
git log --format='%ad %s' --date=iso base-meta-final..main
git diff --stat base-meta-final..main | tail -1
git rev-list --count base-meta-final..main
git log --diff-filter=A --format='%ad' --date=short -- studio/paypal/vault.py
```

[docs/BUILD-LOG.md](docs/BUILD-LOG.md) records what was built on each day.

## How the money moves

### The owner pays and signs (buyer present)

1. The agents turn a brief into an ad, and the run ends with a campaign approval waiting
   for the owner.
2. On the approval screen, code computes the amount from the signed payload (daily
   budget × days + the 5 % studio fee) and creates a PayPal order with
   `intent: AUTHORIZE`. The order's `custom_id` is the ad's `content_sha256`, so PayPal's
   own record names the exact bytes the owner signs.
3. The owner approves the order in PayPal and then signs. The signed message carries
   `funding_sha256`, the hash of the closed `funding/v1` block: order id, amount,
   currency, payee and the ad's hash.
4. The campaign is created PAUSED, and **only then** does the order get authorized. The
   money is held, not taken.
5. A second signature activates the campaign. The capture runs first: no capture, no
   activation.
6. A hold that is never captured is voided after PayPal's 3-day honour period. A refund
   needs its own signed `funding.refund` approval.

### The assistant pays under a signed mandate (buyer not present)

1. **Save PayPal once** (`/account` → "Saved PayPal", buyer present). The studio creates
   a Vault v3 setup token, and the owner approves it in PayPal. On return, the setup
   token is exchanged for a payment token (`POST /v3/vault/payment-tokens`). The studio
   stores only PayPal's token id and its SHA-256: no e-mail, name or payer id.
2. **Sign a mandate** with "Let the assistant pay from my saved PayPal". Besides the base
   limits (goals, countries, landing sites, daily budget, number of campaigns, expiry,
   the assistant tokens), the owner signs a `funding` block:
   - the saved PayPal's SHA-256 (never the token id itself);
   - `max_charge_minor`, the cap per campaign with the fee included;
   - `funding_cap_minor`, the cap for each period of `period_days` from `period_start`;
   - the campaign kinds it may pay for (`link`, `message`, `lead_form`).
3. **The assistant runs a brief** (`run_brief` over MCP, or
   `POST /api/v1/briefs/{id}/run`). When the run's ad fits the mandate, one database
   transaction does all of the following:
   - consumes the approval as a by-mandate approval;
   - declares the charge in the approval receipt (`charge/v1`: amount, currency, period,
     saved PayPal);
   - reserves the amount against the period cap, counting charges still in flight.

   Then the saved PayPal is charged: an `AUTHORIZE` order with
   `payment_source.paypal.vault_id`, the same items and `custom_id` = the ad's hash.
   PayPal holds the money at once, and only then is the campaign created PAUSED. The
   assistant gets back the receipt of the charge.
4. **Outside the mandate**, nothing reaches PayPal. This covers a charge over either cap,
   a kind the mandate does not pay for, a revoked or expired mandate, a removed saved
   PayPal, or a mandate without a `funding` block. The assistant gets machine-readable
   reasons (`max_charge`, `funding_cap`, `kind`, `revoked`, `vault`, `no_funding`, …) and
   a `pay_and_sign_url` for the owner.
5. **When the charge fails**, the run is reopened for the owner with a fresh approval:
   - On a definite PayPal refusal, the reserved amount is freed.
   - On an unknown outcome (a timeout or a 5xx), the amount stays reserved.
   - If the mandate was revoked while PayPal was answering, the hold is voided at once.
   - If the campaign is then not created, its hold is voided too.
6. **Turning the campaign on still needs the owner's signature.** That signature captures
   the held money.

## PayPal APIs used

| API | Calls | Where |
|---|---|---|
| OAuth 2.0 | `POST /v1/oauth2/token` (client credentials, cached) | `studio/paypal/client.py` |
| Orders v2 | `POST /v2/checkout/orders` (`AUTHORIZE`; with `vault_id` for mandate charges), `GET …/{id}`, `POST …/{id}/authorize` | `studio/paypal/orders.py` |
| Payments v2 | `POST /v2/payments/authorizations/{id}/capture`, `…/void`, `…/reauthorize`, `POST /v2/payments/captures/{id}/refund`, `GET` of each | `studio/paypal/orders.py`, `money.py` |
| Vault v3 | `POST /v3/vault/setup-tokens`, `GET /v3/vault/setup-tokens/{id}`, `POST /v3/vault/payment-tokens`, `DELETE /v3/vault/payment-tokens/{id}` | `studio/paypal/vault.py` |
| Webhooks | `POST /webhooks/paypal`: RSA `SHA256withRSA` over `transmission_id\|time\|webhook_id\|crc32(body)`, cert chain and host checked, cert pinned by SHA-256 | `studio/paypal/webhooks.py` |
| JS SDK | v5 `paypal.Buttons` (`intent=authorize`) on the approval screen | `studio/ui/static/paypal.js` |

Every POST carries a `PayPal-Request-Id` derived from what authorises the step (the
funding, the step and the approval id). A retry, a restart or a double click therefore
cannot move money twice. An ambiguous outcome stays pending, with its amount reserved,
until the same request id settles it.

## The sandbox, step by step (for judges)

**Buyer present:**
1. Sign in and run a brief.
2. Open the campaign approval, click the PayPal button and log in as the **sandbox
   buyer**. Approve the order.
3. Click "Approve and sign". The campaign is created PAUSED with the money held.
4. Activate it and sign again: the money is captured.

**Assistant with a mandate:**
1. Go to `/account` → Assistants & API and make a draft token.
2. Under "Saved PayPal", click **Save my PayPal**, approve it as the sandbox buyer in
   PayPal, and come back.
3. Under Mandates, tick "Let the assistant pay from my saved PayPal". Set the caps (for
   example 300 per campaign and 500 per 7 days) and the kinds, then sign. The signing
   screen lists every limit in words.
4. From any MCP client (or curl), call `run_brief` with the token and a confirmed brief.
   The answer is either `outcome: mandate_charge` with the charge's receipt, or
   `outcome: pay_and_sign` with the link and the reasons.
5. Export the receipts (footer link) and run `studio verify receipts.jsonl --server
   <host>`.

In mock mode (`PAYPAL_ADAPTER=mock`), a fake PayPal plays the buyer, so the same flows
run without any PayPal account.

## What `studio verify` proves (offline)

The server holds the ledger key, so it could re-sign an edited log. These rules check
the log against what the **owner** signed:

| Rule | Checks |
|---|---|
| P-V1 | A capture rides on a `campaign.activate` approval of a campaign whose creation signed a funding block for the same order, or that a mandate charge funded |
| P-V2 | captured ≤ authorized ≤ signed, in the same currency |
| P-V3 | A webhook receipt is RSA-verified, re-read from PayPal's API, one per attested state, for an order this run funded, with `custom_id` = the signed ad and an amount ≤ the signed amount |
| P-V4 | A refund matches its signed `refund/v1` body, and refunds never exceed the capture |
| **P-V5** | A vault charge names a valid by-mandate approval that declared it (`charge/v1`), under the same mandate and saved PayPal (SHA-256), for the declared amount ≤ `max_charge_minor`, with PayPal's `custom_id` = the approved ad. Cumulative charges of one mandate period never exceed `funding_cap_minor`. A PayPal order is charged once in the whole log. A charge recorded after the mandate's revocation must be voided and never captured. The mandate itself must be an owner-signed `mandate.grant` earlier in the log, not revoked or expired at the approval |
| P-V6 | Every PayPal money step names a valid approval of the right action, used once |

`studio verify --paypal-events … --paypal-certs … --paypal-webhook-id …` also re-checks
PayPal's RSA signature of every exported webhook offline.

**Attack classes** (`tests/attack/test_paypal_money.py`, `tests/attack/test_paypal_vault.py`).
In each class the attacker is the server itself, re-signing its own log:
1. an amount edited after signing;
2. an order id swapped;
3. a capture without activation;
4. a capture above the authorization;
5. a refund without approval, or done twice;
6. a forged webhook signature;
7. a replayed or stale webhook;
8. a webhook for an unknown order;
9. a `custom_id` mismatch;
10. **a charge over the mandate's caps**, per charge or per period, or over what the
    approval declared;
11. **a charge after the mandate's revocation**: an approval forged after the revoke, or
    a hold left after it;
12. **a charge replayed against another mandate**: relabelled, of another saved PayPal,
    or one PayPal order counted twice.

Each class is refused at run time and caught offline.

## AI, and what it never touches

- The base's agents (Muse Spark 1.3 and its contributor variant, Muse Image, SAM 3.1,
  Muse Voice) plan, write, draw and review the ad.
- **The treasurer** (`studio/agents/treasurer.py`, Muse Spark 1.3, strict JSON) proposes
  how to split an amount across campaigns and explains a funding's reconciliation in
  plain words. It receives only code's numbers, has no tools, and never sees a PayPal id,
  credential or vault reference. Code recomputes every amount: a split that does not add
  up, figures that differ from code's statement, or any amount in the text that code did
  not compute is replaced by code's own version, and the reasons are shown. It moves no
  money; a refund still needs a signed approval.
- No model is given PayPal credentials or a PayPal write tool. Money moves only through
  code paths that check an owner's signature or a mandate the owner signed.

## Honest limits (as of 3 Nov 2026)

- The treasurer's test fixture is hand-written and labelled synthetic
  (`fixtures/cassettes/treasurer.SYNTHETIC.json`). It will be re-recorded once the Model
  API key for judging is confirmed.
- The Vault sandbox cassette (`fixtures/cassettes/paypal/sandbox_vault.json`) needs the
  sandbox buyer to approve saving PayPal in a browser. The recorder stops at that step
  (`scripts/paypal_record_vault.py --start`). Until it is recorded, the vault flow is
  tested against the fake PayPal, which follows PayPal's documented shapes.
- The webhook is not registered with PayPal yet, and the hosted demo is not deployed yet
  (see [deploy/PAY-DEPLOY.md](deploy/PAY-DEPLOY.md)).
