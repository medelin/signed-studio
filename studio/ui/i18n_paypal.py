"""UI strings for ``/pricing`` (per-campaign PayPal funding, ``studio.paypal``), English and
Polish. The Terms text itself is in ``legal_texts.py``."""

from __future__ import annotations

EN_PAYPAL: dict[str, str] = {
    "foot.pricing": "Pricing", "foot.terms": "Terms",
    "meta.pricing": "No subscription: each campaign is funded through PayPal, ad budget plus "
                    "a studio fee, and the money moves only behind your signature.",
    "pricing.title": "Pricing",
    "pricing.lead": "No subscription and no plan. You fund each campaign through PayPal: the "
                    "ad budget plus a studio fee of {pct}% of it.",
    "pricing.how_h": "How the money moves",
    "pricing.step.hold": "You approve the payment in PayPal, then sign the campaign. Only "
                         "then is the money held (authorized), not taken; the campaign is "
                         "created paused.",
    "pricing.step.capture": "When you sign again to turn the campaign on, the money is "
                            "taken (captured). If you never turn it on, the hold is released.",
    "pricing.step.refund": "After the campaign, the budget Meta did not spend comes back to "
                           "you, with its share of the fee, through a PayPal refund you sign.",
    "pricing.step.receipt": "Every step is a receipt that names the PayPal order and the exact "
                            "ad you signed.",
    "pricing.example": "Example: £15 a day for 3 days is a £45.00 budget plus a £2.25 fee "
                       "(5%), so PayPal holds £47.25.",
    "pricing.sandbox": "This studio uses the PayPal sandbox: no real money moves.",
    "pricing.seller": "Sold by {name}.",
    "pricing.seller_number": "Company number {number}.",
    "pricing.seller_address": "Registered office: {address}.",
    "pricing.terms_html": 'See the <a href="/terms">Terms</a> and the '
                          '<a href="/privacy">Privacy</a> page.',
}

PL_PAYPAL: dict[str, str] = {
    "foot.pricing": "Cennik", "foot.terms": "Regulamin",
    "meta.pricing": "Bez abonamentu: każdą kampanię opłacasz przez PayPal, budżet reklamowy "
                    "plus opłata studia, a pieniądze ruszają się tylko po Twoim podpisie.",
    "pricing.title": "Cennik",
    "pricing.lead": "Bez abonamentu i bez planu. Każdą kampanię opłacasz przez PayPal: budżet "
                    "reklamowy plus opłata studia w wysokości {pct}% budżetu.",
    "pricing.how_h": "Jak przepływają pieniądze",
    "pricing.step.hold": "Zatwierdzasz płatność w PayPal, a potem podpisujesz kampanię. "
                         "Dopiero wtedy pieniądze są zablokowane (autoryzowane), a nie "
                         "pobrane; kampania powstaje wstrzymana.",
    "pricing.step.capture": "Gdy podpiszesz ponownie, by włączyć kampanię, pieniądze są "
                            "pobierane. Jeśli jej nie włączysz, blokada zostaje zwolniona.",
    "pricing.step.refund": "Po kampanii budżet, którego Meta nie wydała, wraca do Ciebie wraz "
                           "z odpowiednią częścią opłaty, jako zwrot w PayPal, który "
                           "podpisujesz.",
    "pricing.step.receipt": "Każdy krok to pokwitowanie, które wskazuje zamówienie PayPal i "
                            "dokładnie tę reklamę, którą podpisałeś.",
    "pricing.example": "Przykład: 15 £ dziennie przez 3 dni to budżet 45,00 £ plus opłata "
                       "2,25 £ (5%), więc PayPal blokuje 47,25 £.",
    "pricing.sandbox": "To studio korzysta z piaskownicy PayPal: prawdziwe pieniądze się nie "
                       "ruszają.",
    "pricing.seller": "Sprzedawca: {name}.",
    "pricing.seller_number": "Numer spółki {number}.",
    "pricing.seller_address": "Siedziba: {address}.",
    "pricing.terms_html": 'Zobacz <a href="/terms">Regulamin</a> i stronę '
                          '<a href="/privacy">Prywatność</a>.',
}
