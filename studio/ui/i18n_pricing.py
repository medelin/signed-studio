"""UI strings for ``/pricing``, the plan choice and the payment consent before checkout and
the price hints (``studio.pricing``, ``studio.routes_billing``), English and Polish. The
Terms text itself is in ``legal_texts.py``."""

from __future__ import annotations

EN_PRICING: dict[str, str] = {
    "foot.pricing": "Pricing", "foot.terms": "Terms",
    "meta.pricing": "Muse Studio Lite plans: monthly, 3, 6 or 12 months, or one month paid "
                    "once. Your ad spend goes to Meta, not to us.",
    # /pricing
    "pricing.title": "Pricing",
    "pricing.lead": "One studio, a few ways to pay for it. Your ad budget stays yours.",
    "pricing.no_amount": "Price shown at checkout",
    "pricing.no_amount_hint": "You see the exact price on the payment page, before you pay.",
    "pricing.equivalent": "That is {price} a month.",
    "pricing.other_currencies": "At checkout you can also pay in: {list}.",
    "pricing.fee": "Every plan: plus {pct}% of the ad spend of campaigns you sign through "
                   "the studio, billed monthly in arrears; the ad spend itself is paid to "
                   "Meta.",
    "pricing.fee_once": "The one-off month has no ad-spend fee.",
    "pricing.included": "Included in every plan",
    "pricing.inc.studio": "The whole studio: briefs, ad texts, images, the two reviewers, "
                          "approvals, receipts, the inbox and the daily report.",
    "pricing.inc.models": "The model costs for normal use, up to {runs} campaign runs a "
                          "month (one run costs us about $0.08–0.13 in model calls).",
    "pricing.inc.invoices": "An invoice from Stripe for every payment.",
    "pricing.not_included": "Not included",
    "pricing.exc.ad_spend": "Meta ad spend. You pay it to Meta directly, on Meta's own "
                            "invoices; it never passes through us.",
    "pricing.exc.fees": "No agency fee and no commission on your ad spend.",
    "pricing.exc.fees_with_fee": "No agency fee: the {pct}% above is the only charge on "
                                 "your ad spend.",
    "pricing.cancel": "Cancel any time in the Stripe customer portal: your plan then does "
                      "not renew, and the service runs to the end of the period you have "
                      "paid for. The one-off month never renews.",
    "pricing.buyers": "Buying for a company? Stripe takes your VAT or tax number for the "
                      "invoice. Buying as a private person? You have a 14-day right to "
                      "cancel, explained in the Terms.",
    "pricing.compare_h": "How it compares",
    "pricing.compare": "Most AI ad tools charge $20–40 a month and still need you to check "
                       "every claim yourself. Here two reviewers check each ad against the "
                       "facts you confirmed and Meta's rules, and explain what they found, "
                       "before you sign.",
    "pricing.compare_fee": "Ad tools that take a share of the ad spend usually charge "
                           "6–12%, and agencies 10–20%.",
    "pricing.promises_h": "Three promises",
    "pricing.promise.signature": "Nothing is spent without your signature.",
    "pricing.promise.receipt": "Every cost has a receipt.",
    "pricing.promise.meta": "Your ad spend goes to Meta, not to us.",
    "pricing.cta": "Choose a plan",
    "pricing.cta_visitor_hint": "Create your account first; you choose the plan and pay on "
                                "your account page.",
    "pricing.seller": "Sold by {name}.",
    "pricing.seller_number": "Company number {number}.",
    "pricing.seller_address": "Registered office: {address}.",
    "pricing.terms_html": 'See the <a href="/terms">Terms</a> and the '
                          '<a href="/privacy">Privacy</a> page.',
    # plans (pricing page and account page)
    "billing.plan.legend": "Plan",
    "billing.plan.monthly": "Monthly",
    "billing.plan.3m": "3 months",
    "billing.plan.6m": "6 months",
    "billing.plan.12m": "12 months",
    "billing.plan.once": "One month, paid once",
    "billing.plan.per_month": "{price} a month",
    "billing.plan.per_term": "{price} every {months} months",
    "billing.plan.equivalent": "{price} a month",
    "billing.plan.once_price": "{price} once, {days} days of access",
    "billing.plan.free_one": "1 month free",
    "billing.plan.free_many": "{n} months free",
    "billing.plan.invalid": "Choose one of the plans offered.",
    "billing.once_until": "paid once, access until",
    # buyer
    "billing.buyer.legend": "You are buying as",
    "billing.buyer.person": "A private person",
    "billing.buyer.company": "A company",
    "billing.buyer.tax_id": "VAT or tax number (optional)",
    "billing.buyer.tax_hint": "Stripe asks for it on the payment page and puts it on the "
                              "invoice with your company's name and address. A company "
                              "has no 14-day right to cancel.",
    "billing.email.missing": "Add your e-mail address in Profile first: we send the "
                             "renewal reminder and any change to the Terms there.",
    "billing.buyer.missing": "Choose whether you are buying as a private person or as a "
                             "company.",
    # the payment consent (also the line above Stripe's pay button)
    "billing.fee_line": "Plus {pct}% of the ad spend of campaigns you sign through the "
                        "studio, billed monthly in arrears; the ad spend itself is paid to "
                        "Meta.",
    "billing.consent.recurring": "I agree to a recurring payment of {price} per month, "
                                 "charged in advance until I cancel{fee}, and to the Terms.",
    "billing.consent.recurring_no_price": "I agree to a recurring monthly payment at the "
                                          "price shown on the payment page, charged in "
                                          "advance until I cancel{fee}, and to the Terms.",
    "billing.consent.term": "I agree to a payment of {price} every {months} months, charged "
                            "in advance and renewing for the same period until I "
                            "cancel{fee}, and to the Terms.",
    "billing.consent.term_no_price": "I agree to a payment every {months} months at the "
                                     "price shown on the payment page, charged in advance "
                                     "and renewing for the same period until I cancel{fee}, "
                                     "and to the Terms.",
    "billing.consent.once": "One payment of {price} for {days} days of access; it does not "
                            "renew{fee}. I agree to the Terms.",
    "billing.consent.once_no_price": "One payment, at the price shown on the payment page, "
                                     "for {days} days of access; it does not renew{fee}. I "
                                     "agree to the Terms.",
    "billing.consent.generic": "I agree to pay for the plan chosen above as described "
                               "there: a plan renews for the same period until I cancel, "
                               "the one-off month does not renew{fee}. I agree to the "
                               "Terms.",
    "billing.consent.fee": ", plus {pct}% of the ad spend of campaigns I sign through the "
                           "studio, billed monthly in arrears (the ad spend itself is paid "
                           "to Meta)",
    "billing.consent.immediate": "Start the service now: I understand I lose the 14-day "
                                 "right to cancel for the part already provided.",
    "billing.consent.immediate_hint_html": 'More in the <a href="/terms#withdrawal">Terms</a>'
                                           ", under the right to cancel.",
    "billing.consent.missing_recurring_consent": "Tick the box to agree to the payment and "
                                                 "the Terms.",
    "billing.consent.missing_immediate_start": "Tick the box to start the service now.",
    "billing.consent.missing_both": "Tick both boxes: the payment with the Terms, and "
                                    "starting the service now.",
    "login.price_hint_html": '{price}/month{fee} after the trial — see <a '
                             'href="/pricing">Pricing</a>.',
    "login.fee_hint": " + {pct}% of ad spend",
}

PL_PRICING: dict[str, str] = {
    "foot.pricing": "Cennik", "foot.terms": "Regulamin",
    "meta.pricing": "Plany Muse Studio Lite: miesięczny, na 3, 6 lub 12 miesięcy albo jeden "
                    "miesiąc płatny jednorazowo. Budżet reklamowy płacisz Mecie, nie nam.",
    "pricing.title": "Cennik",
    "pricing.lead": "Jedno studio, kilka sposobów płatności. Budżet reklamowy zostaje Twój.",
    "pricing.no_amount": "Cena widoczna przy płatności",
    "pricing.no_amount_hint": "Dokładną cenę zobaczysz na stronie płatności, zanim zapłacisz.",
    "pricing.equivalent": "To {price} miesięcznie.",
    "pricing.other_currencies": "Przy płatności możesz też zapłacić w: {list}.",
    "pricing.fee": "Każdy plan: do tego {pct}% wydatków na reklamy kampanii, które "
                   "podpiszesz w studiu, rozliczane co miesiąc z dołu; same wydatki na "
                   "reklamy płacisz Mecie.",
    "pricing.fee_once": "Jednorazowy miesiąc jest bez opłaty od wydatków na reklamy.",
    "pricing.included": "W każdym planie",
    "pricing.inc.studio": "Całe studio: briefy, teksty reklam, grafiki, dwóch recenzentów, "
                          "zatwierdzenia, pokwitowania, skrzynka i raport dnia.",
    "pricing.inc.models": "Koszty modeli przy zwykłym użyciu, do {runs} przebiegów kampanii "
                          "miesięcznie (jeden przebieg kosztuje nas ok. 0,08–0,13 USD za "
                          "wywołania modeli).",
    "pricing.inc.invoices": "Faktura od Stripe za każdą płatność.",
    "pricing.not_included": "Poza ceną",
    "pricing.exc.ad_spend": "Wydatki na reklamy w Mecie. Płacisz je bezpośrednio Mecie, na "
                            "jej fakturach; te pieniądze nie przechodzą przez nas.",
    "pricing.exc.fees": "Bez opłaty agencyjnej i bez prowizji od budżetu reklamowego.",
    "pricing.exc.fees_with_fee": "Bez opłaty agencyjnej: powyższe {pct}% to jedyna opłata "
                                 "od budżetu reklamowego.",
    "pricing.cancel": "Zrezygnujesz w każdej chwili w portalu klienta Stripe: plan się "
                      "wtedy nie odnowi, a usługa działa do końca opłaconego okresu. "
                      "Jednorazowy miesiąc nigdy się nie odnawia.",
    "pricing.buyers": "Kupujesz dla firmy? Stripe przyjmie numer VAT lub podatkowy do "
                      "faktury. Kupujesz jako osoba prywatna? Masz 14 dni na odstąpienie od "
                      "umowy, co opisuje Regulamin.",
    "pricing.compare_h": "Na tle innych",
    "pricing.compare": "Większość narzędzi AI do reklam kosztuje 20–40 USD miesięcznie, a i "
                       "tak każde twierdzenie musisz sprawdzić sam. Tu dwóch recenzentów "
                       "sprawdza każdą reklamę z potwierdzonymi przez Ciebie faktami i "
                       "zasadami Mety i wyjaśnia, co znaleźli, zanim podpiszesz.",
    "pricing.compare_fee": "Narzędzia reklamowe, które biorą udział w budżecie, pobierają "
                           "zwykle 6–12%, a agencje 10–20%.",
    "pricing.promises_h": "Trzy obietnice",
    "pricing.promise.signature": "Nic nie jest wydawane bez Twojego podpisu.",
    "pricing.promise.receipt": "Każdy koszt ma pokwitowanie.",
    "pricing.promise.meta": "Budżet reklamowy trafia do Mety, nie do nas.",
    "pricing.cta": "Wybierz plan",
    "pricing.cta_visitor_hint": "Najpierw załóż konto; plan wybierzesz i opłacisz na stronie "
                                "konta.",
    "pricing.seller": "Sprzedawca: {name}.",
    "pricing.seller_number": "Numer spółki {number}.",
    "pricing.seller_address": "Siedziba: {address}.",
    "pricing.terms_html": 'Zobacz <a href="/terms">Regulamin</a> i stronę '
                          '<a href="/privacy">Prywatność</a>.',
    "billing.plan.legend": "Plan",
    "billing.plan.monthly": "Miesięczny",
    "billing.plan.3m": "3 miesiące",
    "billing.plan.6m": "6 miesięcy",
    "billing.plan.12m": "12 miesięcy",
    "billing.plan.once": "Jeden miesiąc, płatny jednorazowo",
    "billing.plan.per_month": "{price} miesięcznie",
    "billing.plan.per_term": "{price} co {months} mies.",
    "billing.plan.equivalent": "{price} miesięcznie",
    "billing.plan.once_price": "{price} jednorazowo, {days} dni dostępu",
    "billing.plan.free_one": "1 miesiąc gratis",
    "billing.plan.free_many": "{n} miesiące gratis",
    "billing.plan.invalid": "Wybierz jeden z oferowanych planów.",
    "billing.once_until": "opłacone jednorazowo, dostęp do",
    "billing.buyer.legend": "Kupujesz jako",
    "billing.buyer.person": "Osoba prywatna",
    "billing.buyer.company": "Firma",
    "billing.buyer.tax_id": "Numer VAT lub podatkowy (opcjonalnie)",
    "billing.buyer.tax_hint": "Stripe poprosi o niego na stronie płatności i umieści go na "
                              "fakturze z nazwą i adresem firmy. Firma nie ma 14-dniowego "
                              "prawa odstąpienia.",
    "billing.email.missing": "Najpierw dodaj adres e-mail w Profilu: wysyłamy na niego "
                             "przypomnienie o odnowieniu i zmiany regulaminu.",
    "billing.buyer.missing": "Wybierz, czy kupujesz jako osoba prywatna, czy jako firma.",
    "billing.fee_line": "Do tego {pct}% wydatków na reklamy kampanii, które podpiszesz w "
                        "studiu, rozliczane co miesiąc z dołu; same wydatki na reklamy "
                        "płacisz Mecie.",
    "billing.consent.recurring": "Zgadzam się na cykliczną płatność {price} miesięcznie, "
                                 "pobieraną z góry do czasu rezygnacji{fee}, i akceptuję "
                                 "Regulamin.",
    "billing.consent.recurring_no_price": "Zgadzam się na cykliczną miesięczną płatność w "
                                          "cenie podanej na stronie płatności, pobieraną z "
                                          "góry do czasu rezygnacji{fee}, i akceptuję "
                                          "Regulamin.",
    "billing.consent.term": "Zgadzam się na płatność {price} co {months} miesięcy, pobieraną "
                            "z góry i odnawianą na ten sam okres do czasu rezygnacji{fee}, "
                            "i akceptuję Regulamin.",
    "billing.consent.term_no_price": "Zgadzam się na płatność co {months} miesięcy w cenie "
                                     "podanej na stronie płatności, pobieraną z góry i "
                                     "odnawianą na ten sam okres do czasu rezygnacji{fee}, "
                                     "i akceptuję Regulamin.",
    "billing.consent.once": "Jedna płatność {price} za {days} dni dostępu; nie odnawia "
                            "się{fee}. Akceptuję Regulamin.",
    "billing.consent.once_no_price": "Jedna płatność, w cenie podanej na stronie płatności, "
                                     "za {days} dni dostępu; nie odnawia się{fee}. Akceptuję "
                                     "Regulamin.",
    "billing.consent.generic": "Zgadzam się zapłacić za plan wybrany powyżej, tak jak go "
                               "tam opisano: plan odnawia się na ten sam okres do czasu "
                               "rezygnacji, jednorazowy miesiąc się nie odnawia{fee}. "
                               "Akceptuję Regulamin.",
    "billing.consent.fee": ", oraz na opłatę {pct}% wydatków na reklamy kampanii, które "
                           "podpiszę w studiu, rozliczaną co miesiąc z dołu (same wydatki "
                           "na reklamy płacę Mecie)",
    "billing.consent.immediate": "Rozpocznij usługę od razu: rozumiem, że tracę 14-dniowe "
                                 "prawo odstąpienia w części, która została już wykonana.",
    "billing.consent.immediate_hint_html": 'Więcej w <a href="/terms#withdrawal">Regulaminie'
                                           "</a>, w części o prawie odstąpienia.",
    "billing.consent.missing_recurring_consent": "Zaznacz zgodę na płatność i Regulamin.",
    "billing.consent.missing_immediate_start": "Zaznacz zgodę na rozpoczęcie usługi od "
                                               "razu.",
    "billing.consent.missing_both": "Zaznacz oba pola: płatność z Regulaminem i "
                                    "rozpoczęcie usługi od razu.",
    "login.price_hint_html": '{price} miesięcznie{fee} po okresie próbnym — zobacz <a '
                             'href="/pricing">Cennik</a>.',
    "login.fee_hint": " + {pct}% wydatków na reklamy",
}
