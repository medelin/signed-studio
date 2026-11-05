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
    "paypal.h": "Pay with PayPal first (sandbox)",
    "paypal.lead": "The money is only held when you sign, and taken only when you sign again "
                   "to turn the campaign on. Your signature names this PayPal order.",
    "paypal.mock_button": "Approve in the PayPal sandbox (simulated buyer)",
    "paypal.ap_funding": "PayPal funding sha256",
    "js.paypal_creating": "Creating the PayPal order…",
    "js.paypal_amount": "{total}: ad budget {budget} for {days} day(s) + studio fee {fee} "
                        "({pct}%). PayPal order {order}.",
    "js.paypal_held_on_sign": "{amount} will be held in PayPal when you sign (order "
                              "{order}).",
    "js.paypal_pay_first": "Approve the payment in PayPal first; then sign.",
    "js.paypal_mock_hint": "Mock PayPal: the button plays the sandbox buyer.",
    "js.paypal_checking": "Checking the order with PayPal…",
    "js.paypal_approved": "PayPal approved order {order}. Review and sign.",
    "js.paypal_ready": "Funded: signing holds the money and creates the campaign PAUSED.",
    "js.paypal_cancelled": "PayPal was closed before approving. Try again when ready.",
    "js.paypal_sdk_failed": "PayPal could not be loaded. Try again in a moment.",
    "js.paypal_declined": "PayPal declined the funding source ({issue}). Fund the campaign "
                         "again with another PayPal method.",
    "err.paypal": "PayPal refused this step. Try again in a moment.",
    "err.refund_amount": "The refund must be more than zero and at most what is left.",
    "err.amount": "An amount must be a whole number of minor units (pence, grosze), from 0 "
                  "up to the studio's ceiling.",
    "err.not_fundable": "Only a campaign is paid through PayPal.",
    "err.not_approved_yet": "PayPal has not approved the order yet.",
    "err.no_order": "There is no PayPal order for this approval yet.",
    "err.funded": "This approval is already funded.",
    "err.refund_changed": "The capture changed since the refund was signed.",
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
    "paypal.h": "Najpierw zapłać przez PayPal (piaskownica)",
    "paypal.lead": "Pieniądze są tylko blokowane, gdy podpisujesz, a pobierane dopiero, gdy "
                   "podpiszesz ponownie, by włączyć kampanię. Twój podpis wskazuje to "
                   "zamówienie PayPal.",
    "paypal.mock_button": "Zatwierdź w piaskownicy PayPal (symulowany kupujący)",
    "paypal.ap_funding": "sha256 finansowania PayPal",
    "js.paypal_creating": "Tworzę zamówienie PayPal…",
    "js.paypal_amount": "{total}: budżet reklamowy {budget} na {days} dni + opłata studia "
                        "{fee} ({pct}%). Zamówienie PayPal {order}.",
    "js.paypal_held_on_sign": "{amount} zostanie zablokowane w PayPal, gdy podpiszesz "
                              "(zamówienie {order}).",
    "js.paypal_pay_first": "Najpierw zatwierdź płatność w PayPal, potem podpisz.",
    "js.paypal_mock_hint": "Atrapa PayPal: przycisk gra rolę kupującego z piaskownicy.",
    "js.paypal_checking": "Sprawdzam zamówienie w PayPal…",
    "js.paypal_approved": "PayPal zatwierdził zamówienie {order}. Sprawdź i podpisz.",
    "js.paypal_ready": "Opłacone: podpis blokuje pieniądze i tworzy kampanię WSTRZYMANĄ.",
    "js.paypal_cancelled": "PayPal zamknięto przed zatwierdzeniem. Spróbuj, gdy będziesz "
                           "gotów.",
    "js.paypal_sdk_failed": "Nie udało się wczytać PayPal. Spróbuj za chwilę.",
    "js.paypal_declined": "PayPal odrzucił źródło płatności ({issue}). Opłać kampanię "
                          "ponownie inną metodą PayPal.",
    "err.paypal": "PayPal odmówił tego kroku. Spróbuj za chwilę.",
    "err.refund_amount": "Zwrot musi być większy od zera i nie większy niż to, co zostało.",
    "err.amount": "Kwota musi być liczbą całkowitą w groszach (centach), od 0 do limitu "
                  "studia.",
    "err.not_fundable": "Przez PayPal opłaca się tylko kampanię.",
    "err.not_approved_yet": "PayPal jeszcze nie zatwierdził zamówienia.",
    "err.no_order": "Dla tego zatwierdzenia nie ma jeszcze zamówienia PayPal.",
    "err.funded": "To zatwierdzenie jest już opłacone.",
    "err.refund_changed": "Płatność zmieniła się od podpisania zwrotu.",
}
