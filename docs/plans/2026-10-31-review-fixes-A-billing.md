# Group A — billing and Stripe
Owns: `studio/billing.py`, `studio/routes_billing.py`, `studio/pricing.py`,
their tests. Read the common rules first.

1. **One-off plan never upgrades the tier** (billing.py ~609). `checkout.session.completed`
   for mode=payment ('once') sets only `access_until`; `out.tier` stays '' so
   routes_billing never calls `set_tier`. Make a paid one-off grant `full` until
   `access_until`, and make expiry drop it back (find where tier is read; the
   simplest correct rule wins — document it).
2. **Fee currency** (billing.py ~412-436, report_spend). The fee is computed in the
   ad account's minor units and sent as meter units of the price currency. Fix:
   know the subscription/price currency (store it from the checkout/subscription
   event, or read it from settings for the metered price) and only report when
   the campaign currency equals it; otherwise skip that spend with a recorded
   reason (`currency_mismatch`) in the `billing.usage` receipt/status. No FX
   conversion. Also use Stripe's minor-unit rules (zero-decimal currencies) not
   Meta's offset when building the meter value.
3. **Event ordering** (billing.py ~582-641). Out-of-order/retried
   `customer.subscription.*` events overwrite newer state. Store the event
   `created` per subscription/account and ignore older events (still record
   them as seen).
4. **Event marked processed before tier/profile/mail** (routes_billing.py ~197-211).
   Make the tier change (and profile e-mail) part of `apply`'s transaction, or
   on a duplicate replay the stored outcome, so a failure after the insert is
   not lost. Mail may stay best-effort after commit.
5. **Disabled account drops events** (billing.py ~588-596). Update the billing
   row (status, subscription_id, period_end) for any existing non-deleted
   account; hold back only mails while disabled; make re-enable not leave a
   stale tier (recompute from billing status).
6. **report_spend aborts the whole run on one failure** (~448-455). Record the
   per-account failure (undo its spend_usage row, status `send_failed`, receipt)
   and continue; non-zero exit only at the end.
7. **`subscription_details: null`** (~643-656): use `(obj.get(...) or {})` at both sites.
Tests: one per item, Stripe fakes as in existing billing tests.
