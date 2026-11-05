# Devpost submission text (copy into the form)

**Project name:** Signed Studio

**Tagline (≤ 60 chars):** An AI ad agency that can't spend a cent you didn't sign

**Links:**
- Try it: https://pay.udun.eu (user `judge`, password in the private judges field below)
- Code: <public repository URL> (Apache-2.0)
- Video: <YouTube URL> (2:55)

**Private field for judges (never in the public text):** judge password; PayPal sandbox
buyer login (Personal account e-mail and password) for checkout and "Save my PayPal".

---

## Inspiration

I run ads for my own one-person firm. An AI could do the evening of work a campaign takes,
but I would never hand an AI my card or my ad account. The question became: can an agent do
all the work while the money moves only on a human signature, and can anyone prove
afterwards that it did?

## What it does

Signed Studio turns a short brief into a Meta campaign: AI agents plan, write and draw, two
reviewers check every ad against the facts the owner confirmed and the platform's rules.
**PayPal holds the budget and the owner's signature releases it:**

- **Buyer present.** The approval screen computes the amount in code and creates a PayPal
  order (Orders v2, `AUTHORIZE`) whose `custom_id` is the ad's content hash. Signing is
  disabled until PayPal reports the payer approved; the signature then covers the order
  too. Signing authorizes the money and creates the campaign **PAUSED**; a second
  signature turns it on and only that one **captures**.
- **Buyer not present (agentic commerce).** The owner saves PayPal once (Vault v3) and
  signs a spending mandate in plain words: goals, countries, hosts, caps per campaign and
  per period, which assistant tokens. Their own AI assistant (Claude, ChatGPT or a script,
  over MCP or the HTTPS API) runs a brief and pays from the saved PayPal, only inside those
  limits. Outside them it gets a pay-and-sign link for the owner instead of money.
- **Proof.** Every PayPal call is a receipt in an Ed25519-signed, hash-chained ledger.
  PayPal's webhooks are verified offline against PayPal's RSA signature and re-read from
  PayPal's API. `studio verify` rechecks six money rules (P-V1…P-V6) on an exported ledger:
  no capture without the owner's activation signature, no vault charge outside a valid
  mandate, never the same charge twice, voids after revocation.

## How we built it

Python (FastAPI, SQLite), no front-end framework; WebCrypto Ed25519 keys that never leave
the owner's browser. PayPal: Orders v2 (create, authorize, capture, void, refund), Vault v3
(setup tokens, payment tokens, delete), Webhooks (offline RSA verification, pinned cert
chain). AI: Muse Spark 1.3 for planning, copy and review; Muse Image 1.0 for images; SAM 3.1
for product cut-outs; an open-weights second reviewer; an AI treasurer that explains a
funding's reconciliation but never sees a PayPal id and never moves money — code recomputes
every amount it states. MCP server (Streamable HTTP) and an OpenAPI for assistants.

## Challenges we ran into

- Proving money rules offline, not just enforcing them online: the verifier has to
  reconstruct who signed what for which PayPal order from the ledger alone.
- Browser security meets payment redirects: the strict CSP and `SameSite=Strict` session
  cookie both broke the Vault return from PayPal at first; fixed without loosening either
  (a sandbox-only `form-action`, a one-time same-site bounce).
- Webhooks as triggers only: the studio never trusts a webhook body, it re-reads the
  resource from PayPal and records what the API says.

## Accomplishments that we're proud of

- 600/600 tampered ledger exports rejected by `studio verify`; 25/25 clean exports accepted.
- 284/284 attack tests pass, including 12 PayPal money attack classes (over-cap charges,
  charges after revocation, a charge replayed against another mandate, captures without an
  activation signature).
- A real sandbox run end to end: order, payer approval, authorization, capture; and a real
  customer-not-present vault charge from an assistant under a signed mandate.
- One campaign run costs about $0.09 in model calls.

## What we learned

Agentic payments are safest when the agent can ask but cannot decide: limits signed by a
human, enforced by code, and checkable by anyone afterwards.

## What's next

Per-client accounts for small agencies, more ad channels behind the same signature, and
PayPal refunds as their own signed approvals in the UI.

## Built with

python · fastapi · sqlite · paypal-orders-v2 · paypal-vault-v3 · paypal-webhooks ·
webcrypto-ed25519 · mcp · muse-spark · sam-3.1 · meta-marketing-api

## Submission-period note

The base (agents, signed approvals, receipt ledger, Meta adapter, mandates) was written from
27 Oct 2026 for another hackathon and is tagged `base-meta-final` (8d5f854, 3 Nov 2026).
Everything PayPal was written from 3 Nov 2026; `git log base-meta-final..main` shows it.
