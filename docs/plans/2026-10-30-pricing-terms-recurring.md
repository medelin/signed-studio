# Plan C — Pricing page, Terms, and consent to a recurring monthly payment

**Goal.** Muse Studio Lite is sold as one monthly subscription (Stripe Checkout in
subscription mode already exists: `studio/billing.py`, `studio/routes_billing.py`). Add
what a buyer must see and agree to before the first payment: a public pricing page,
Terms of Service, and an explicit "recurring payment" consent checked server-side.

## Facts to use
- Seller: **Dwarven Stronghold Ltd** (UK), the studio is its project. Company number and
  registered address come from settings (`STUDIO_SELLER_NAME`, `STUDIO_SELLER_NUMBER`,
  `STUDIO_SELLER_ADDRESS`, all optional; default name "Dwarven Stronghold Ltd"); when a
  value is missing the page simply omits that line. Contact: `legal_texts.CONTACT`.
- Price: read from Stripe once and cached (12 h) via `GET /v1/prices/{STRIPE_PRICE_ID}`
  (`unit_amount`, `currency`, `recurring.interval`, and `currency_options` if present);
  if billing is off or Stripe is unreachable, show `STUDIO_PRICE_DISPLAY` (env, e.g.
  "£9 / month") or hide the amount. Today's test price: 900 GBP minor units, monthly.
- What the price covers: the studio and its model costs for normal use (a fair-use line:
  `STUDIO_FAIR_USE_RUNS`, default 60 campaign runs per month; a run costs the studio about
  $0.08–0.13 in model calls). **Meta ad spend is not included**: it is paid by the owner to
  Meta directly, on Meta's invoices. No agency fee, no commission on ad spend, cancel any
  time in the Stripe customer portal (effective at the end of the paid month).

## Scope
1. **`/pricing`** (EN; PL via the existing `?lang=pl`/cookie mechanism — copy in EN+PL):
   one plan card (price per month, what is included, what is not, "cancel any time"), a
   short comparison paragraph without naming competitors' prices as facts ("most AI ad
   tools charge $20–40 a month and still need you to check every claim yourself" is fine
   as a general statement), the three promises: nothing is spent without your signature;
   every cost has a receipt; your ad spend goes to Meta, not to us. CTA: "Subscribe" →
   signed-in: `/account#billing`; visitor: sign-up.
2. **`/terms`** — add a `"terms"` page to `studio/ui/legal_texts.py` (EN+PL, same facts,
   parity test in `tests/test_legal_pages.py` must cover it) with sections: who sells;
   the service (an ad studio; campaigns are created PAUSED and only after the owner's
   signature; activation is a second signature); price and billing (monthly in advance,
   renews automatically until cancelled, card via Stripe, Stripe issues the invoice);
   cancellation (portal, end of the paid period); consumer right of withdrawal (14 days;
   the service starts immediately at the buyer's express request and the buyer
   acknowledges that the right is lost once the service has been fully performed — in
   the UK and EU wording, plain language, no legalese invented beyond that); ad spend and
   Meta's own terms (the owner's account, Meta bills it); acceptable use (no ads that
   break Meta's Advertising Standards; the reviewers explain, the owner decides); data
   (link `/privacy`); liability limited to the fees paid in the last 12 months except
   where the law forbids limiting it; changes to the terms (30 days' notice by e-mail);
   governing law England and Wales, without depriving a consumer of the protection of
   their country's mandatory law. Add `UPDATED` date bump. Route in
   `studio/routes_help.py` next to `/privacy`.
3. **Recurring consent before checkout**: the billing section of the account page
   (`account.html`, section `#billing`) shows the price line and, above the Subscribe
   button, two required checkboxes:
   - "I agree to a recurring payment of {price} per month, charged in advance until I
     cancel, and to the Terms." (link)
   - "Start the service now: I understand I lose the 14-day right to cancel for the part
     already provided."
   `POST /account/billing/checkout` refuses (400 with the existing error mechanism,
   field-specific messages) unless both are present (`recurring_consent=1`,
   `immediate_start=1`). On success append a `billing.consent` receipt (agent `owner`,
   kind `tool_call`; details: `terms_updated` date, price id, interval, both flags; no card
   data, no e-mail) before creating the Checkout session, and pass
   `subscription_data[description]` = "Muse Studio Lite — monthly subscription" and
   `custom_text[submit][message]` = the same recurring sentence to Stripe so it appears on
   Stripe's page too (form-encoded, see `StripeApi`).
4. Footer links: Pricing · Terms (next to Privacy) in `base.html`; the sign-up box on the
   login page gets one hint line "£9/month after the trial — see Pricing" built from the
   cached price (no hard-coded amount).
5. Help: one FAQ entry "How much does it cost?" (EN+PL) pointing to /pricing.

## Out of scope
Changing Stripe prices or the dashboard (the operator does that), tax handling, coupons.

## Tests
- `/pricing` and `/terms` render in EN and PL; legal parity; no amount rendered when
  billing is off and no `STUDIO_PRICE_DISPLAY`.
- Price fetch: mocked Stripe (respx, as `tests/test_billing.py` does), cached, errors →
  fallback.
- Checkout without either checkbox → 400 and no Stripe call; with both → the
  `billing.consent` receipt exists and the Stripe form body carries the description and
  custom text.
- i18n parity for every new key.

## Constraints (all mandatory)
Same as plans A/B: `~/code/muse-studio-lite`, `.venv/bin/ruff check studio tests`,
`.venv/bin/mypy studio`, full `.venv/bin/python -m pytest -q` (~13 min) green before the
final commit; English code; EN+PL copy in plain words; CSP (no inline JS/CSS);
accessibility (labels, `aria-describedby`, keyboard). Signed commits with the configured
identity, no Co-Authored-By / Generated-with lines. Never push, deploy, touch `.env` or
secrets, or call live Stripe/Meta APIs. **Reserved for the parallel agent (plan D), do not
edit:** `studio/routes_api.py`, `studio/routes_inbox.py`, `studio/routes_report.py`,
`studio/ui/templates/leads.html`, `studio/ui/templates/report.html`, `studio/db.py`.
Report: files changed, test line, open points (especially anything in the Terms you think
the operator must decide).
