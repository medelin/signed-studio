# Plan: fit Signed Studio to the PayPal AI Hackathon judging (5–12 Nov 2026)

Sources: Devpost overview, rules, resources and updates (read 5 Nov 2026); the four webinar
recordings with captions (Build with PayPal, Oct 6 and Oct 13 repeat; APIMatic; AG Grid);
paypal/AI-Toolkit, paypal/agent-toolkit, paypaldev/getting-started-with-paypal-sandbox; the
hackathon Discord (read only). Research notes:
`scratchpad/hack/webinars-research.md` (not in the repo).

**Deadline:** Thu 12 Nov 2026, 12:00 PST = **21:00 CET**. Submitting earlier gets an early
eligibility check; the submission stays editable until the deadline.

## How we are judged

- **Stage One (pass/fail):** PayPal and AI both used meaningfully ("PayPal central, not bolted
  on"); judges can run it (hosted URL or run instructions); public GitHub/GitLab/Bitbucket repo
  with an OSI license visible in About; public YouTube video under 3:00 showing it working; text
  description; tools named and explained; existing projects must say what changed during the
  period; English.
- **Stage Two, five equal criteria:** Technological implementation · Design (a complete product,
  not a proof of concept) · Potential impact (a real problem for a real audience, shown working)
  · Innovation · Presentation (the video: problem and audience first, end to end).
- **Prizes we aim at:** Grand prizes; **Best Use of Agentic Commerce**; **Best Use of PayPal + AI**;
  Best Demo Delivery; sponsor prizes where the cost is small (APIMatic, AG Grid).

## Where we stand

| Requirement | Status |
|---|---|
| PayPal central | Orders v2 authorize/capture/void/refund, Vault v3, verified webhooks, `funding/v1` in the signature — strong |
| AI central | agents plan/write/draw/review; AI treasurer; owner's assistant pays under a mandate over MCP — strong |
| Hosted demo + creds | https://pay.udun.eu, `judge` + sandbox buyer — done |
| Public repo + license | https://github.com/medelin/signed-studio, Apache-2.0 detected — done |
| What changed in the period | README, PAYPAL.md, BUILD-LOG (`base-meta-final` 8d5f854) — done |
| Video | script `docs/demo-script-paypal.md`, own music in progress — **to record** |
| Uses PayPal's own AI tooling | **no** — the gap DevRel stressed most |
| Unhappy paths on screen | covered by tests, **not shown** |

## Work, in priority order

### P0 — eligibility (5–6 Nov)

1. **Submit a draft on Devpost as early as possible** (early eligibility check): description and
   tools from `docs/devpost.md`, repo, demo URL, test credentials in the judges' field. If the
   form requires the video URL, upload a first cut (below) as soon as it exists and replace it
   later.
2. **Keep the demo up past judging:** pay.udun.eu and its sandbox app until **21 Dec 2026**
   (winners announced); the logrus vhost comment says "remove after 15.12" — move to 22.12.

### P1 — the biggest scoring gap: PayPal's own agent tooling (6–8 Nov)

3. **PayPal Agent Toolkit inside the studio, gated by the mandate.** Add the Python
   `paypal-agent-toolkit` (sandbox) behind our own MCP server as a curated, receipted subset:
   - read tools for the owner's assistant and the AI treasurer: `list_transaction`,
     `get_merchant_insights` — the treasurer reconciles fundings against PayPal's own
     transaction list and explains differences;
   - money-moving tools (`create_refund`, `create_invoice`) only through a signed approval or
     inside a mandate's caps, each call a receipt and checked by `studio verify`.
   Pitch: "PayPal's agent tools, but only within a budget a human signed."
4. **Build with the PayPal AI Toolkit installed** (Claude Code plugin) and run `/paypal:doctor`
   on the repo; record the result in BUILD-LOG and the submission (cheap, and DevRel asked for
   it explicitly).
5. **Agentic-commerce framing:** a README section mapping our signed spending mandate to
   PayPal's Agent Ready delegated tokens (max amount, currency, expiry) — same idea on public
   Orders v2 + Vault v3 while Agent Ready/Store Sync are gated; say so plainly.

### P2 — show what judges asked to see (8–9 Nov)

6. **Unhappy paths in the demo:** a sandbox decline via `PayPal-Mock-Response`
   (`INSTRUMENT_DECLINED`) on capture, shown in the UI and as a receipt; an assistant asking over
   the mandate cap → `pay_and_sign` link instead of money; a charge after revocation → refused;
   a refund as its own signed approval. Add a judge-visible way to trigger the decline in the
   sandbox (a demo-only toggle, never in live mode).
7. **Merchant side in view:** the sandbox merchant dashboard with the same order/authorization
   ids next to the studio receipts (for the video).

### P3 — design and sponsor prizes (9–10 Nov, only if P1–P2 are done)

8. **Money page** (`/money`): one table of fundings, holds, captures, voids and mandate charges
   with their receipts. Built with **AG Grid Community** (MIT, vendored — our CSP allows only
   'self' scripts) to enter the AG Grid prize; the AI treasurer's explanation beside it.
9. **APIMatic context plugin:** install while building P1, answer the form question (qualifies
   for 3 × $1k at no product cost).

### P4 — presentation (10–11 Nov)

10. **Video** per `docs/demo-script-paypal.md`, updated with P1/P2 beats: problem and audience
    in the first 15 s; PayPal and AI named on screen; buyer-present pay → sign → PAUSED + held;
    assistant pays under the mandate with PayPal's agent tools; one decline and one refusal;
    `studio verify`. Own background music (ACE-Step on shadow), no third-party trademarks or
    music. Upload early (processing takes hours), public.
11. **Final submission** on 11 Nov: built-with tags (PayPal, Orders v2, Vault, Webhooks, Agent
    Toolkit, MCP, FastAPI, Python, AG Grid, APIMatic), sponsor tools ticked, video link, the
    "what changed" note, test credentials.

## Not doing

Live PayPal keys; Store Sync / Agent Ready (gated, need a business review); gift-card commerce
tools (`x-feature-flags: commerce:true`) unless the sandbox allows them without an application.
