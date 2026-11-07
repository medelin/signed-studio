# Demo video script: the PayPal edition (2:54)

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

## Ten beats (as recorded on 7 Nov 2026, 2:54)

Every beat is a live sandbox run on https://pay.udun.eu recorded with Playwright on a
virtual screen (`scratchpad/video/record.py`), the voice is synthetic (Kokoro `am_michael`),
the cut adds eased push-ins on the element that matters, crossfades and a title per beat
(`edit2.py`). The spoken line of each beat is below; captions carry the same words.

**1. The promise (0:00–0:12)** — title card, the studio home.
- "Signed Studio is an AI ad agency for one-person businesses that cannot spend a cent you
  did not sign for. PayPal holds the money; my signature releases it."

**2. Brief to draft (0:12–0:30)** — Studio, the example brief, Draft, the [FACT] questions.
- "I describe what I sell. Agents plan, write and draw, and every step is a receipt.
  Nothing is sent yet."

**3. Save PayPal once · sign limits (0:30–0:57)** — Account → Assistants & API: Save my
PayPal → the sandbox buyer's PayPal consent page (in Polish: the sandbox account's country is
PL) → back with "Saved PayPal"; the mandate form (goal, country, hosts, 25 PLN a day, 3
campaigns, 7 days, pay from the saved PayPal up to 200 PLN per campaign and 400 PLN a week);
Prepare → Approve and sign → "Signed. The mandate is active".
- "Once, I save my PayPal, approving it in PayPal itself. Then I sign limits for my
  assistant: the goal, the country, the sites, a daily budget and caps."

**4. The assistant pays inside the mandate (0:57–1:12)** — terminal: `run_brief` →
`outcome: mandate_charge`, `fits: true`, the checked limits, `paypal.vault_charge`, order id;
then the money page: 147.00 PLN held, "Paid via: Mandate".
- "My own assistant runs a brief over MCP. It fits the mandate, so it pays from my saved
  PayPal, buyer not present. PayPal holds the money; the campaign starts paused."

**5. Outside the limits: a link for the owner (1:12–1:33)** — terminal: a 30 PLN/day brief →
`outcome: pay_and_sign`, `mandate_reasons: daily_budget, max_charge`, the link; the run page
down to Approve and sign and the PayPal box (220.50 PLN, the order id).
- "A bigger budget is outside my limits, so the assistant gets a link for me, never money.
  The approval screen computes the amount in code, and creates a PayPal order carrying the
  ad's hash."

**6. PayPal checkout · signature holds the money (1:33–1:49)** — the inline card form
(sandbox Visa), PAYPAL FUNDING SHA256 in the signed bytes, Approve and sign → "money held",
campaign PAUSED.
- "I approve the payment in PayPal. My signature covers the PayPal order. Signing holds the
  money, and creates the campaign paused."

**7. PayPal refuses the capture · then captures (1:49–2:04)** — "Sandbox: simulate PayPal
refusing the capture" ticked → Request activation and sign → PayPal's reason
(TRANSACTION_REFUSED) and debug_id, money still held; unticked → Activated, captured.
- "PayPal's sandbox can refuse the capture on demand. The studio shows PayPal's reason, and
  nothing is taken. Without it, my second signature captures."

**8. Refund: the assistant proposes, the owner signs (2:04–2:24)** — terminal:
`paypal_transactions()` (Agent Toolkit behind the gate: matched, held, the lines),
`propose_refund(…, "47.00")` → a sign link; the owner opens it and signs → "refunded".
- "The assistant reads PayPal through PayPal's own Agent Toolkit, behind our gate. It can
  only propose a refund. Nothing moves until I sign."

**9. Reconcile with PayPal · verify offline (2:24–2:44)** — /money: Check with PayPal
(matched / pending), Explain (the AI treasurer); terminal: `studio verify receipts.jsonl`
→ `chain OK`; one byte changed → `chain INVALID`.
- "Every PayPal call is a signed, hash-chained receipt. The money page reconciles them with
  PayPal's own transaction list. Studio verify rechecks the money rules offline, and one
  changed byte fails it."

**10. End card (2:44–2:54)** — https://pay.udun.eu · github.com/medelin/signed-studio ·
"PayPal sandbox only · built 3–12 Nov 2026 on a base tagged base-meta-final".
- "Signed Studio. AI does the work; PayPal and my signature hold the money."

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
