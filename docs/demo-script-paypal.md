# Demo video script: the PayPal edition (2:55)

**Where:** public YouTube video (unlisted is not enough if the rules say public), under
3:00, English voice or English subtitles. Link it in the Devpost form.

**First line (on screen and spoken, 0:00):** "Signed Studio is an AI ad agency for
one-person businesses that cannot spend a cent you did not sign for. PayPal holds the
money; my signature releases it."

Record 1080p30, browser zoom 110 %, a fresh browser profile, **https://pay.udun.eu** signed
in as `judge` for the buyer-present part and as your own account for the mandate part
(assistant tokens are off for `judge`). PayPal: sandbox only, the sandbox Personal buyer
(its PayPal pages are in Polish, because the sandbox account's country is PL; say so once).
Each beat lists the screenshot in `docs/screenshots/paypal/` it matches, as a backup shot.

## Seven beats

**1. Problem (0:00-0:15)**
- On screen: the first line on a title card, then the studio home.
- Say: "Small businesses would love an AI that runs their ads. Nobody sane gives an AI
  their card. So here, the AI never touches money: PayPal does, and only after I sign."

**2. A brief becomes a campaign draft (0:15-0:40)**
- On screen: Studio, a short brief (mugs, 30 PLN a day), Draft my brief, the [FACT]
  questions (answer one, strike one), Confirm, the agents' receipts ticking by with model
  ids (`muse-spark-1.3`, `muse-image-1.0`), the three copy variants and images.
- Say: "Agents plan, write and draw. Every step is a receipt with its model id. Nothing is
  sent anywhere yet."

**3. The approval screen creates the PayPal order (0:40-1:05)** — shot `1-approve-and-pay.png`
- On screen: Approve and sign: content hash, chain head, then the PayPal box: budget
  210.00 PLN for 7 days + 5 % fee = 220.50 PLN, the order id, the PayPal buttons. The sign
  button is disabled.
- Say: "Code, not a model, computes the amount. The PayPal order carries the ad's content
  hash as custom_id, and I cannot sign until PayPal says the payer approved."

**4. Pay in PayPal, then sign: money held, campaign PAUSED (1:05-1:40)** — shots 2, 4, 5
- On screen: PayPal sandbox checkout (log in as the sandbox buyer, approve); back in the
  studio a new row PAYPAL FUNDING SHA256 joins the signed hashes; Approve and sign; result:
  campaign, ad set, creative and ad created PAUSED; "money held (authorized, not captured)".
- Say: "Now my signature covers the PayPal order too. Signing authorizes the money and
  creates the campaign paused. Turning it on is a second signature, and only that one
  captures."
- Optional 5 s: Activate → capture COMPLETED.

**5. My assistant pays without me, inside a mandate I signed (1:40-2:20)** — shots 6, 7, 8
- On screen: /account → Assistants & API: Save my PayPal → PayPal consent page (Polish,
  "Akceptuję i chcę kontynuować") → back: saved. Mandates: goals, countries, example.com,
  30 PLN a day, 3 campaigns, pay at most 300 PLN per campaign and 500 PLN per 7 days →
  Prepare → the signing screen lists every limit in words → Approve and sign. Then a
  terminal (or Claude Desktop over MCP): `run_brief` with the assistant token → JSON
  `"outcome": "mandate_charge"`, the PayPal order id, 220.50 PLN, campaign PAUSED.
- Say: "I save PayPal once, Vault v3, and sign limits in plain words. My own AI assistant
  then runs a brief over MCP and pays from my saved PayPal, buyer not present. If it asks
  for more than I signed, it gets a link for me instead of money."

**6. Proof anyone can check offline (2:20-2:45)**
- On screen: receipts page → the `paypal.vault_charge` receipt (mandate id, order,
  authorization, hashes); footer → receipts.jsonl; terminal:
  `studio verify receipts.jsonl --server https://pay.udun.eu` → `chain OK, N receipts,
  M signatures valid`; edit one byte → `chain INVALID` (exit 2).
- Say: "Every PayPal call is a signed, hash-chained receipt. studio verify rechecks the
  money rules P-V1 to P-V6 offline: no capture without my activation signature, no vault
  charge outside the mandate, never the same charge twice. One changed byte fails it."

**7. End card (2:45-2:55)**
- On screen: https://pay.udun.eu · the repository URL · "PayPal sandbox only · built
  3–12 Nov 2026 on a base tagged base-meta-final".
- Say: "Signed Studio. AI does the work; PayPal and my signature hold the money."

## Never on screen

The judge password, the sandbox buyer's password, the assistant token (`msl_tok_…`; make a
throwaway one for the take and revoke it afterwards), PayPal client id/secret, `.env`,
real people's names.

## Before recording

- `curl -s https://pay.udun.eu/healthz` → ok; your own account signed in on a second
  profile; the judge account's runs not near the daily cap.
- The sandbox buyer is logged in on sandbox.paypal.com in the recording profile (saves the
  login screen), or log in on camera with the password field off screen.
- A mandate run needs a brief drafted by the assistant over the API (`POST /api/v1/briefs`,
  then `/confirm`, then `/run`); a brief made in the web UI is refused with `not_confirmed`.
- Revoke the assistant token and remove the saved PayPal after the take if the account is
  shared.
