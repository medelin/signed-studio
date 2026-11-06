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

## Nine beats

**1. Problem (0:00-0:12)**
- On screen: the first line on a title card, then the studio home.
- Say: "Nobody sane gives an AI their card. So here the AI never touches money: PayPal
  does, and only after I sign."

**2. A brief becomes a campaign draft (0:12-0:30)**
- On screen: Studio, a short brief, Draft my brief, the [FACT] questions, Confirm, the
  agents' receipts ticking by with model ids, the copy variants and images.
- Say: "Agents plan, write and draw. Every step is a receipt. Nothing is sent yet."

**3. The approval screen creates the PayPal order (0:30-0:50)** — shot `1-approve-and-pay.png`
- On screen: Approve and sign: hashes, then the PayPal box: 220.50 PLN, the order id, the
  buttons; the sign button is disabled.
- Say: "Code computes the amount. The order carries the ad's hash as custom_id."

**4. Pay in PayPal, then sign: money held, campaign PAUSED (0:50-1:10)** — shots 2, 4, 5
- On screen: PayPal sandbox checkout; back in the studio the row PAYPAL FUNDING SHA256;
  Approve and sign; campaign created PAUSED, "money held".
- Say: "My signature covers the PayPal order. Signing holds the money and creates the
  campaign paused."

**5. One declined capture (1:10-1:25)**
- On screen: Activate with "simulate PayPal refusing the capture" ticked; the sign; the
  message with PayPal's reason (TRANSACTION_REFUSED) and debug id; the money still held.
- Say: "PayPal's sandbox can refuse the capture on demand. The studio shows PayPal's reason
  and nothing is captured."

**6. My assistant pays inside a mandate I signed (1:25-1:55)** — shots 6, 7, 8
- On screen: Save my PayPal (consent page), the mandate's limits in words, Approve and
  sign; then Claude Desktop with our MCP server: `run_brief` → `"outcome":
  "mandate_charge"`, order id, 220.50 PLN, PAUSED.
- Say: "I sign limits once. My assistant runs a brief and pays from my saved PayPal, buyer
  not present. Over the limit, it gets a link for me instead of money."

**7. The assistant reads PayPal and proposes a refund (1:55-2:20)**
- On screen: in the same chat, "what did PayPal record this week?" → our assistant op `paypal_transactions`, built on PayPal's Agent Toolkit tools
  (matched, held); "refund that" → `propose_refund` → a pending approval with a link; open
  it, sign.
- Say: "The assistant reads PayPal through our server, and it can only propose a refund:
  nothing moves until I sign."

**8. The money page and proof (2:20-2:48)**
- On screen: /money (AG Grid): holds, captures, a void, the charge, "Check with PayPal"
  and "Explain"; then `studio verify receipts.jsonl --server https://pay.udun.eu` → `chain
  OK`; one byte edited → `chain INVALID`.
- Say: "Every PayPal call is a signed, hash-chained receipt. studio verify rechecks the
  money rules offline. One changed byte fails it."

**9. End card (2:48-2:55)**
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
- Beats 5 and 7 need the sandbox decline switch, the assistant token with `paypal_transactions`
  and a funded capture from beat 4/6 to refund; rehearse them once.
