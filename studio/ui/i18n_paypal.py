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
    "js.paypal_declined": "PayPal declined the funding source ({issue}: {reason}). Fund the "
                         "campaign again with another PayPal method.",
    "js.paypal_capture_declined": "PayPal declined taking the money ({issue}: {reason}). The "
                                  "money is still held and the campaign is still paused; sign "
                                  "the activation again. PayPal debug id: {debug}.",
    "js.paypal_unconfirmed": "PayPal did not confirm the payment ({issue}). Check the PayPal "
                             "account before trying again.",
    "js.paypal_capture_unconfirmed": "PayPal did not confirm taking the money ({issue}; PayPal "
                                     "debug id {debug}). The campaign stays paused. Check the "
                                     "PayPal account before signing the activation again.",
    "js.reason.INSTRUMENT_DECLINED": "PayPal declined the buyer's funding source",
    "js.reason.TRANSACTION_REFUSED": "PayPal refused the transaction",
    "js.reason.PAYER_CANNOT_PAY": "the buyer cannot pay with this funding source",
    "paypal.simulate_decline": "Sandbox: simulate PayPal refusing the capture",
    "js.refund_signed": "Signed and verified. PayPal refunded the buyer; {amount} {currency} "
                        "refunded from this funding so far.",
    "err.invalid_simulate": "That sandbox simulation is not one the studio offers.",
    "err.paypal": "PayPal refused this step. Try again in a moment.",
    "err.refund_amount": "The refund must be more than zero and at most what is left.",
    "err.amount": "An amount must be a whole number of minor units (pence, grosze), from 0 "
                  "up to the studio's ceiling.",
    "err.not_fundable": "Only a campaign is paid through PayPal.",
    "err.not_approved_yet": "PayPal has not approved the order yet.",
    "err.no_order": "There is no PayPal order for this approval yet.",
    "err.funded": "This approval is already funded.",
    "err.refund_changed": "The capture changed since the refund was signed.",
    "nav.money": "Money",
    "meta.money": "Your PayPal sandbox fundings: holds, captures, voids and mandate charges, with "
        "their receipts.",
    "money.title": "Money",
    "money.lead": "Every campaign funding of your account (PayPal sandbox): what was held, taken, "
        "voided or refunded, and whether it came through checkout or a signed mandate. "
        "Nothing here moves money.",
    "money.check": "Check with PayPal",
    "money.table": "Fundings table",
    "money.empty": "No fundings yet. A campaign you fund through PayPal appears here.",
    "js.money_loading": "Loading…",
    "js.money_checking": "Asking PayPal's transaction list…",
    "js.money_checked": "Checked against PayPal's transaction list.",
    "js.money_failed": "Could not load the money table: {error}",
    "js.money_explain": "Explain",
    "js.money_explaining": "The treasurer is reading this funding…",
    "js.money_col_created": "Created",
    "js.money_col_funding": "Funding",
    "js.money_col_state": "State",
    "js.money_col_amount": "Amount",
    "js.money_col_authorized": "Held",
    "js.money_col_captured": "Taken",
    "js.money_col_refunded": "Refunded",
    "js.money_col_via": "Paid via",
    "js.money_col_receipts": "Receipts",
    "js.money_col_recon": "PayPal check",
    "js.money_via_checkout": "Checkout",
    "js.money_via_mandate": "Mandate",
    "js.money_state_created": "Created",
    "js.money_state_payer_approved": "Buyer approved",
    "js.money_state_authorized": "Held",
    "js.money_state_captured": "Taken",
    "js.money_state_partially_refunded": "Partly refunded",
    "js.money_state_refunded": "Refunded",
    "js.money_state_voided": "Voided",
    "js.money_state_expired": "Expired",
    "js.money_state_failed": "Failed",
    "js.money_recon_matched": "Matched",
    "js.money_recon_pending": "Pending (PayPal lags up to 3 h)",
    "js.money_recon_held": "Held, not captured",
    "js.money_recon_missing": "Missing",
    "js.money_recon_amount_mismatch": "Amount differs",
    "js.money_recon_unknown": "Cannot tell",
    "err.paypal_unavailable": "PayPal is not switched on in this studio.",
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
    "js.paypal_declined": "PayPal odrzucił źródło płatności ({issue}: {reason}). Opłać "
                          "kampanię ponownie inną metodą PayPal.",
    "js.paypal_capture_declined": "PayPal odrzucił pobranie pieniędzy ({issue}: {reason}). "
                                  "Pieniądze nadal są zablokowane, a kampania wstrzymana; "
                                  "podpisz aktywację jeszcze raz. Identyfikator PayPal: "
                                  "{debug}.",
    "js.paypal_unconfirmed": "PayPal nie potwierdził płatności ({issue}). Sprawdź konto PayPal "
                             "przed ponowną próbą.",
    "js.paypal_capture_unconfirmed": "PayPal nie potwierdził pobrania pieniędzy ({issue}; "
                                     "identyfikator PayPal {debug}). Kampania pozostaje "
                                     "wstrzymana. Sprawdź konto PayPal, zanim ponownie "
                                     "podpiszesz aktywację.",
    "js.reason.INSTRUMENT_DECLINED": "PayPal odrzucił źródło płatności kupującego",
    "js.reason.TRANSACTION_REFUSED": "PayPal odmówił wykonania transakcji",
    "js.reason.PAYER_CANNOT_PAY": "kupujący nie może zapłacić tym źródłem",
    "paypal.simulate_decline": "Piaskownica: zasymuluj odmowę pobrania przez PayPal",
    "js.refund_signed": "Podpisano i zweryfikowano. PayPal zwrócił pieniądze kupującemu; "
                        "z tego finansowania zwrócono dotąd {amount} {currency}.",
    "err.invalid_simulate": "Studio nie oferuje takiej symulacji w piaskownicy.",
    "err.paypal": "PayPal odmówił tego kroku. Spróbuj za chwilę.",
    "err.refund_amount": "Zwrot musi być większy od zera i nie większy niż to, co zostało.",
    "err.amount": "Kwota musi być liczbą całkowitą w groszach (centach), od 0 do limitu "
                  "studia.",
    "err.not_fundable": "Przez PayPal opłaca się tylko kampanię.",
    "err.not_approved_yet": "PayPal jeszcze nie zatwierdził zamówienia.",
    "err.no_order": "Dla tego zatwierdzenia nie ma jeszcze zamówienia PayPal.",
    "err.funded": "To zatwierdzenie jest już opłacone.",
    "err.refund_changed": "Płatność zmieniła się od podpisania zwrotu.",
    "nav.money": "Pieniądze",
    "meta.money": "Twoje finansowania w piaskownicy PayPal: blokady, pobrania, anulowania i "
        "obciążenia z mandatu, wraz z pokwitowaniami.",
    "money.title": "Pieniądze",
    "money.lead": "Każde finansowanie kampanii na Twoim koncie (piaskownica PayPal): co "
        "zablokowano, pobrano, anulowano lub zwrócono i czy poszło przez kasę, czy przez "
        "podpisany mandat. Nic tu nie rusza pieniędzy.",
    "money.check": "Sprawdź w PayPal",
    "money.table": "Tabela finansowań",
    "money.empty": "Brak finansowań. Kampania opłacona przez PayPal pojawi się tutaj.",
    "js.money_loading": "Wczytuję…",
    "js.money_checking": "Pytam listę transakcji PayPal…",
    "js.money_checked": "Sprawdzono z listą transakcji PayPal.",
    "js.money_failed": "Nie udało się wczytać tabeli pieniędzy: {error}",
    "js.money_explain": "Wyjaśnij",
    "js.money_explaining": "Skarbnik czyta to finansowanie…",
    "js.money_col_created": "Utworzono",
    "js.money_col_funding": "Finansowanie",
    "js.money_col_state": "Stan",
    "js.money_col_amount": "Kwota",
    "js.money_col_authorized": "Zablokowano",
    "js.money_col_captured": "Pobrano",
    "js.money_col_refunded": "Zwrócono",
    "js.money_col_via": "Zapłacono przez",
    "js.money_col_receipts": "Pokwitowania",
    "js.money_col_recon": "Sprawdzenie w PayPal",
    "js.money_via_checkout": "Kasa",
    "js.money_via_mandate": "Mandat",
    "js.money_state_created": "Utworzone",
    "js.money_state_payer_approved": "Kupujący zatwierdził",
    "js.money_state_authorized": "Zablokowane",
    "js.money_state_captured": "Pobrane",
    "js.money_state_partially_refunded": "Zwrot częściowy",
    "js.money_state_refunded": "Zwrócone",
    "js.money_state_voided": "Anulowane",
    "js.money_state_expired": "Wygasłe",
    "js.money_state_failed": "Nieudane",
    "js.money_recon_matched": "Zgadza się",
    "js.money_recon_pending": "Oczekuje (PayPal spóźnia się do 3 h)",
    "js.money_recon_held": "Zablokowane, niepobrane",
    "js.money_recon_missing": "Brak",
    "js.money_recon_amount_mismatch": "Kwota się różni",
    "js.money_recon_unknown": "Nie da się ocenić",
    "err.paypal_unavailable": "PayPal nie jest włączony w tym studiu.",
}
