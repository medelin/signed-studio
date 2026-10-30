"""Public privacy, data-deletion and terms pages (``/privacy``, ``/data-deletion``,
``/terms``), EN and PL.

Plain statements of what this sandbox stores and how to remove it, and the terms of the
monthly subscription; no consent banner and no tracking. The same facts in both languages
(``tests/test_legal_pages.py`` checks parity). Paragraphs are plain text, or ``Markup``
from this file when they hold a link.
"""

from __future__ import annotations

import hashlib
import json
import string
from collections.abc import Mapping
from typing import Final

from markupsafe import Markup

CONTACT: Final = "muse@udun.eu"
UPDATED: Final = "2026-10-30"

# page -> lang -> (title, [(heading, [paragraph, …]), …])
PAGES: Final[dict[str, dict[str, tuple[str, list[tuple[str, list[str]]]]]]] = {
    "privacy": {
        "en": ("Privacy", [
            ("Who runs this site", [
                "Muse Studio Lite is an open-source (Apache-2.0) project. This copy at "
                "muse.udun.eu is a sandbox for the Meta hackathon; it is hosted in Finland "
                "and will be deleted after 16 November 2026.",
                f"Contact for anything about your data: {CONTACT}."]),
            ("What we store", [
                "Accounts: user name, display name, optional e-mail address, a password hash "
                "(argon2id), the two-step sign-in secret and recovery-code hashes when you turn "
                "them on, and your sessions (time and a short browser label such as "
                "\"Firefox · Linux\"; no IP address).",
                "Your work: briefs, generated texts and images, approvals you sign and the "
                "receipts log that records each step.",
                "Meta: identifiers of the campaigns, ad sets, ads and lead forms created from "
                "your signed approvals, aggregated ad results, and the leads and comments "
                "those ads receive, so that you can answer them."]),
            ("What we send to others", [
                "Brief text and generated content are sent to the Meta Model API (Muse models) "
                "and, for a second opinion, to an open-weights model on Baseten, only to "
                "produce the campaign you asked for.",
                "Campaigns are created in the advertiser's own Meta (and optionally Google) "
                "ad account only after a person signs the approval.",
                "Notification e-mails are sent from muse@udun.eu to the address on your "
                "account."]),
            ("Cookies and tracking", [
                "Only the cookies the site needs: the session, the CSRF token and your "
                "language and theme. No analytics, no advertising pixels, no third-party "
                "scripts."]),
            ("How long", [
                "Until you delete your account, and in any case until this sandbox is "
                "deleted after 16 November 2026."]),
            ("Your rights", [
                "You can export your data and delete your account yourself on the Account "
                f"page, or write to {CONTACT}. See the data deletion page for details."]),
        ]),
        "pl": ("Prywatność", [
            ("Kto prowadzi stronę", [
                "Muse Studio Lite to projekt open source (Apache-2.0). Ta kopia pod adresem "
                "muse.udun.eu to sandbox na hackathon Meta; działa na serwerze w Finlandii i "
                "zostanie usunięta po 16 listopada 2026 r.",
                f"Kontakt we wszystkich sprawach Twoich danych: {CONTACT}."]),
            ("Co przechowujemy", [
                "Konta: nazwa użytkownika, nazwa wyświetlana, opcjonalny adres e-mail, skrót "
                "hasła (argon2id), sekret logowania dwuetapowego i skróty kodów zapasowych, "
                "jeśli je włączysz, oraz Twoje sesje (czas i krótka etykieta przeglądarki, np. "
                "„Firefox · Linux”; bez adresu IP).",
                "Twoja praca: briefy, wygenerowane teksty i grafiki, podpisane przez Ciebie "
                "zatwierdzenia i dziennik pokwitowań zapisujący każdy krok.",
                "Meta: identyfikatory kampanii, zestawów reklam, reklam i formularzy kontaktowych "
                "utworzonych z Twoich podpisanych zatwierdzeń, zbiorcze wyniki reklam oraz "
                "leady i komentarze pod tymi reklamami, żebyś mógł na nie odpowiedzieć."]),
            ("Co przekazujemy innym", [
                "Treść briefu i wygenerowane materiały trafiają do Meta Model API (modele Muse) "
                "oraz, jako druga opinia, do modelu open-weights w Baseten, wyłącznie po to, "
                "żeby przygotować zamówioną kampanię.",
                "Kampanie powstają na własnym koncie reklamowym reklamodawcy w Meta (i "
                "opcjonalnie Google) dopiero po podpisaniu zatwierdzenia przez człowieka.",
                "Powiadomienia e-mail wysyłamy z muse@udun.eu na adres zapisany w Twoim "
                "koncie."]),
            ("Ciasteczka i śledzenie", [
                "Tylko ciasteczka potrzebne do działania strony: sesja, token CSRF oraz język i "
                "motyw. Bez analityki, bez pikseli reklamowych, bez skryptów firm trzecich."]),
            ("Jak długo", [
                "Do usunięcia konta, a najpóźniej do usunięcia tego sandboxa po 16 listopada "
                "2026 r."]),
            ("Twoje prawa", [
                "Dane wyeksportujesz i konto usuniesz sam na stronie Konto albo pisząc na "
                f"{CONTACT}. Szczegóły na stronie o usuwaniu danych."]),
        ]),
    },
    "data-deletion": {
        "en": ("Data deletion", [
            ("Delete it yourself", [
                "Sign in, open Account and choose Delete account. Your profile, sessions, "
                "two-step sign-in data and e-mail address are removed at once; the account "
                "name is kept as a closed tombstone so it cannot be reused.",
                "Before that you can download everything with Export on the same page."]),
            ("Ask us", [
                f"Write to {CONTACT} from the address on your account, or with your user "
                "name. We delete the account within 30 days and confirm by e-mail."]),
            ("If you connected Facebook", [
                "Remove Muse Studio Lite in Facebook: Settings and privacy → Settings → "
                "Business integrations (or Apps and websites) → Remove. Then write to "
                f"{CONTACT}; we delete the data we received through Meta (campaign "
                "identifiers, results, leads and comments) within 30 days.",
                "Campaigns already created in your own ad account stay there under your "
                "control; delete them in Meta Ads Manager."]),
        ]),
        "pl": ("Usuwanie danych", [
            ("Usuń sam", [
                "Zaloguj się, otwórz Konto i wybierz Usuń konto. Profil, sesje, dane "
                "logowania dwuetapowego i adres e-mail znikają od razu; nazwa konta zostaje "
                "jako zamknięty ślad, żeby nikt jej ponownie nie użył.",
                "Wcześniej możesz pobrać wszystko przyciskiem Eksport na tej samej stronie."]),
            ("Poproś nas", [
                f"Napisz na {CONTACT} z adresu zapisanego w koncie albo podaj nazwę "
                "użytkownika. Usuwamy konto w ciągu 30 dni i potwierdzamy e-mailem."]),
            ("Jeśli połączyłeś Facebooka", [
                "Usuń Muse Studio Lite na Facebooku: Ustawienia i prywatność → Ustawienia → "
                "Integracje biznesowe (lub Aplikacje i witryny) → Usuń. Potem napisz na "
                f"{CONTACT}; dane otrzymane przez Meta (identyfikatory kampanii, wyniki, "
                "leady i komentarze) usuniemy w ciągu 30 dni.",
                "Kampanie już utworzone na Twoim koncie reklamowym zostają tam pod Twoją "
                "kontrolą; usuniesz je w Menedżerze reklam Meta."]),
        ]),
    },
    # Placeholders are filled from the settings (``facts``, see ``page``); a paragraph whose
    # value is missing (no company number, no address) is left out, and a ``{when_…}``
    # placeholder only gates its paragraph (no ad-spend fee → no fee paragraphs).
    "terms": {
        "en": ("Terms of Service", [
            ("Who sells", [
                "Muse Studio Lite is a project of {seller_name}, a company registered in "
                "England and Wales, which sells the plans described here (\"we\", \"us\"). "
                "These terms are the agreement between you and us.",
                "Company number: {seller_number}.",
                "Registered office: {seller_address}.",
                "Contact: {contact}."]),
            ("The service", [
                "Muse Studio Lite is an online ad studio. From your brief it prepares a "
                "campaign plan, ad texts and images, and two reviewers check them against "
                "the facts you confirmed and Meta's rules.",
                "Nothing is published on its own. A campaign is created in your Meta ad "
                "account only after you sign the approval, and it is created paused. "
                "Turning it on is a second, separate signature.",
                "Every step leaves a receipt you can read on the Receipts page."]),
            ("Plans and commitment periods", [
                "You choose a plan when you pay: a fixed term of 1, 3, 6 or 12 months, paid "
                "upfront for the whole term, or the one-off month described below. The "
                "plans on offer and their prices are on the Pricing page and on the payment "
                "page before you pay.",
                "A plan renews automatically for the same term unless you cancel it before "
                "the renewal date. Before a 3-, 6- or 12-month plan renews, we send a "
                "reminder to the e-mail address on your account 7 days before the renewal.",
                "If you switch to another plan, the change takes effect at the next "
                "renewal.",
                "We change a price only from the next term, and we tell you by e-mail at "
                "least 30 days before."]),
            ("The one-off month", [
                "The one-off month is one payment for {once_days} days of access, starting "
                "when you pay. It does not renew.",
                "The ad-spend fee does not apply to the one-off month."]),
            ("Payment", [
                "Every payment is made by card through Stripe, in advance, and Stripe "
                "issues the invoice for it.",
                "Every plan covers the studio and its model costs for normal use, which we "
                "take to mean up to {fair_use_runs} campaign runs a month.",
                "{when_fee}On top of a subscription plan (not the one-off month) we charge "
                "{fee_pct}% of the ad spend of the campaigns you sign through the studio, "
                "billed monthly in arrears through "
                "Stripe. It is calculated from the spend Meta reports for those campaigns, "
                "day by day. The ad spend itself is paid to Meta."]),
            ("Cancelling", [
                "You can cancel a plan any time in the Stripe customer portal (Account → "
                "Billing). Cancelling stops the next renewal; the service runs to the end "
                "of the term you have paid for.",
                "We do not refund the unused part of a paid term, except under the 14-day "
                "right to cancel below and where the law requires it.",
                "{when_fee}The ad-spend fee for the last month is billed after it ends."]),
            ("Buying as a company or as a private person", [
                "When you pay, you tell us whether you buy as a private person or for a "
                "company.",
                "A company can give a VAT or tax number; it goes on the invoice with the "
                "company's name and address. A company buys for its business, so the "
                "14-day right to cancel below does not apply to it."]),
            ("Your right to cancel within 14 days", [
                "If you buy as a private person (a consumer) in the United Kingdom or the "
                "European Union, you may withdraw from the contract within 14 days of "
                "paying, without giving a reason. This applies to every plan, including the "
                "one-off month.",
                "The service starts straight away because you ask for it when you pay. You "
                "acknowledge there that you lose this right once the service has been fully "
                "provided. If you withdraw within the 14 days, we refund what you paid, "
                "less a proportional amount for the days you used.",
                "To withdraw, write to {contact} within the 14 days."]),
            ("Ad spend and Meta's terms", [
                "Ads run in your own Meta ad account. Meta charges you for them directly "
                "and issues its own invoices; this money does not pass through us and is "
                "not part of any plan.",
                "{when_no_fee}We take no commission on your ad spend.",
                "When you advertise on Meta, Meta's own terms and policies apply between "
                "you and Meta."]),
            ("Acceptable use", [
                "Do not use the studio to make ads that break Meta's Advertising Standards.",
                "The reviewers point out problems and explain them; the decision to sign and "
                "publish is yours."]),
            ("Your data", [
                Markup('How we handle your data is described on the <a href="/privacy">'
                       "Privacy</a> page.")]),
            ("Liability", [
                "Our total liability to you under these terms is limited to the fees you "
                "paid us in the 12 months before the claim, except where the law does not "
                "allow liability to be limited."]),
            ("Changes to these terms", [
                "If we change these terms, we tell you by e-mail at least 30 days before the "
                "change takes effect. You can cancel before then."]),
            ("Law", [
                "These terms are governed by the law of England and Wales. If you are a "
                "consumer, this does not take away the protection of the mandatory law of "
                "the country where you live."]),
        ]),
        "pl": ("Regulamin", [
            ("Kto sprzedaje", [
                "Muse Studio Lite to projekt spółki {seller_name}, zarejestrowanej w "
                "Anglii i Walii, która sprzedaje opisane tu plany („my”). Ten regulamin "
                "jest umową między Tobą a nami.",
                "Numer spółki: {seller_number}.",
                "Siedziba: {seller_address}.",
                "Kontakt: {contact}."]),
            ("Usługa", [
                "Muse Studio Lite to internetowe studio reklam. Na podstawie Twojego briefu "
                "przygotowuje plan kampanii, teksty i grafiki reklam, a dwóch recenzentów "
                "sprawdza je z potwierdzonymi przez Ciebie faktami i zasadami Mety.",
                "Nic nie jest publikowane samo. Kampania powstaje na Twoim koncie "
                "reklamowym w Mecie dopiero po podpisaniu zatwierdzenia i powstaje "
                "wstrzymana. Jej włączenie to drugi, osobny podpis.",
                "Każdy krok zostawia pokwitowanie, które przeczytasz na stronie "
                "Pokwitowania."]),
            ("Plany i okresy umowy", [
                "Plan wybierasz przy płatności: stały okres 1, 3, 6 lub 12 miesięcy, płatny "
                "z góry za cały okres, albo opisany niżej jednorazowy miesiąc. Oferowane "
                "plany i ich ceny są w Cenniku i na stronie płatności, zanim zapłacisz.",
                "Plan odnawia się automatycznie na ten sam okres, chyba że zrezygnujesz "
                "przed datą odnowienia. Przed odnowieniem planu 3-, 6- lub 12-miesięcznego "
                "wysyłamy przypomnienie na adres e-mail zapisany w koncie, 7 dni przed "
                "odnowieniem.",
                "Jeśli zmienisz plan na inny, zmiana działa od następnego odnowienia.",
                "Cenę zmieniamy tylko od następnego okresu i informujemy o tym e-mailem co "
                "najmniej 30 dni wcześniej."]),
            ("Jednorazowy miesiąc", [
                "Jednorazowy miesiąc to jedna płatność za {once_days} dni dostępu, liczonych "
                "od płatności. Nie odnawia się.",
                "Opłata od wydatków na reklamy nie dotyczy jednorazowego miesiąca."]),
            ("Płatności", [
                "Każda płatność odbywa się kartą przez Stripe, z góry, a Stripe wystawia za "
                "nią fakturę.",
                "Każdy plan obejmuje studio i koszty modeli przy zwykłym użyciu, czyli do "
                "{fair_use_runs} przebiegów kampanii miesięcznie.",
                "{when_fee}Oprócz planu abonamentowego (nie jednorazowego miesiąca) pobieramy "
                "{fee_pct}% wydatków na reklamy kampanii, "
                "które podpiszesz w studiu, rozliczane co miesiąc z dołu przez Stripe. "
                "Liczymy je z wydatków, które Meta podaje dla tych kampanii, dzień po dniu. "
                "Same wydatki na reklamy płacisz Mecie."]),
            ("Rezygnacja", [
                "Z planu zrezygnujesz w każdej chwili w portalu klienta Stripe (Konto → "
                "Rozliczenia). Rezygnacja zatrzymuje następne odnowienie; usługa działa do "
                "końca opłaconego okresu.",
                "Nie zwracamy niewykorzystanej części opłaconego okresu, z wyjątkiem "
                "opisanego niżej 14-dniowego prawa odstąpienia i przypadków, w których "
                "wymaga tego prawo.",
                "{when_fee}Opłatę od wydatków na reklamy za ostatni miesiąc rozliczamy po "
                "jego zakończeniu."]),
            ("Zakup jako firma lub osoba prywatna", [
                "Przy płatności podajesz, czy kupujesz jako osoba prywatna, czy dla firmy.",
                "Firma może podać numer VAT lub podatkowy; trafia on na fakturę z nazwą i "
                "adresem firmy. Firma kupuje w ramach swojej działalności, więc opisane "
                "niżej 14-dniowe prawo odstąpienia jej nie przysługuje."]),
            ("Prawo odstąpienia w ciągu 14 dni", [
                "Jeśli kupujesz jako osoba prywatna (konsument) w Wielkiej Brytanii lub w "
                "Unii Europejskiej, możesz odstąpić od umowy w ciągu 14 dni od płatności, "
                "bez podawania przyczyny. Dotyczy to każdego planu, także jednorazowego "
                "miesiąca.",
                "Usługa zaczyna się od razu, bo o to prosisz przy płatności. Potwierdzasz "
                "tam, że tracisz to prawo, gdy usługa zostanie w pełni wykonana. Jeśli "
                "odstąpisz w ciągu 14 dni, zwracamy zapłaconą kwotę pomniejszoną "
                "proporcjonalnie o wykorzystane dni.",
                "Aby odstąpić, napisz na {contact} w ciągu 14 dni."]),
            ("Wydatki na reklamy i zasady Mety", [
                "Reklamy działają na Twoim własnym koncie reklamowym w Mecie. Meta pobiera "
                "za nie opłaty bezpośrednio od Ciebie i wystawia własne faktury; te "
                "pieniądze nie przechodzą przez nas i nie są częścią żadnego planu.",
                "{when_no_fee}Nie pobieramy prowizji od Twoich wydatków na reklamy.",
                "Gdy reklamujesz się w Mecie, między Tobą a Metą obowiązują jej własne "
                "warunki i zasady."]),
            ("Dozwolone użycie", [
                "Nie używaj studia do reklam, które łamią Standardy reklamowe Mety.",
                "Recenzenci wskazują problemy i je wyjaśniają; decyzja o podpisaniu i "
                "publikacji należy do Ciebie."]),
            ("Twoje dane", [
                Markup('Jak postępujemy z Twoimi danymi, opisuje strona <a href="/privacy">'
                       "Prywatność</a>.")]),
            ("Odpowiedzialność", [
                "Nasza łączna odpowiedzialność wobec Ciebie na podstawie tego regulaminu "
                "jest ograniczona do opłat zapłaconych nam w ciągu 12 miesięcy przed "
                "roszczeniem, z wyjątkiem przypadków, w których prawo nie pozwala jej "
                "ograniczyć."]),
            ("Zmiany regulaminu", [
                "Jeśli zmienimy regulamin, powiadomimy Cię e-mailem co najmniej 30 dni "
                "przed wejściem zmiany w życie. Możesz zrezygnować przed tym terminem."]),
            ("Prawo właściwe", [
                "Regulamin podlega prawu Anglii i Walii. Jeśli jesteś konsumentem, nie "
                "odbiera Ci to ochrony, jaką dają bezwzględnie obowiązujące przepisy kraju, "
                "w którym mieszkasz."]),
        ]),
    },
}

# page -> one anchor per section (the same in every language), for links such as
# /terms#withdrawal
ANCHORS: Final[dict[str, tuple[str, ...]]] = {
    "terms": ("seller", "service", "plans", "one-off", "payment", "cancelling", "buyers",
              "withdrawal", "ad-spend", "acceptable-use", "data", "liability", "changes",
              "law"),
}


def digest(name: str) -> str:
    """SHA-256 of the page's text in every language (placeholders unfilled): which wording
    a consent receipt refers to."""
    body = json.dumps(PAGES[name], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def page(name: str, lang: str, facts: Mapping[str, object] | None = None
         ) -> tuple[str, list[tuple[str, list[str]]]]:
    """The page in ``lang`` (English when missing). With ``facts``, ``{name}`` placeholders
    are filled and a paragraph whose value is empty is dropped (an optional line); a
    ``{when_x}`` placeholder prints nothing and keeps its paragraph only when ``x`` holds."""
    by_lang = PAGES[name]
    title, sections = by_lang.get(lang, by_lang["en"])
    if facts is None:
        return title, sections
    out: list[tuple[str, list[str]]] = []
    for heading, paragraphs in sections:
        kept: list[str] = []
        for para in paragraphs:
            names = {n for _, n, _, _ in string.Formatter().parse(para) if n}
            if any(not facts.get(n, "") for n in names):
                continue
            values = {n: "" if n.startswith("when_") else facts[n] for n in names}
            kept.append(para.format(**values) if names else para)
        out.append((heading, kept))
    return title, out
