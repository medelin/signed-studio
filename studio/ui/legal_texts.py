"""Public privacy, data-deletion and terms pages (``/privacy``, ``/data-deletion``,
``/terms``), EN and PL.

Plain statements of what this sandbox stores and how to remove it, and the terms of the
per-campaign PayPal funding; no consent banner and no tracking. The same facts in both languages
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
UPDATED: Final = "2026-11-07"

# page -> lang -> (title, [(heading, [paragraph, …]), …])
PAGES: Final[dict[str, dict[str, tuple[str, list[tuple[str, list[str]]]]]]] = {
    "privacy": {
        "en": ("Privacy", [
            ("Who runs this site", [
                "Signed Studio (Muse Studio Lite) is an open-source (Apache-2.0) project. "
                "This copy at pay.udun.eu is a sandbox: it runs against PayPal's sandbox, "
                "moves no real money, and may be reset or deleted at any time. It is hosted "
                "in Finland.",
                "Every signed-in account works in the same studio workspace: briefs, "
                "fundings and receipts created in the web interface are visible to all "
                "signed-in accounts. Do not enter anything you would not show them.",
                f"Contact for anything about your data: {CONTACT}."]),
            ("What we store", [
                "Accounts: user name, display name, optional e-mail address, a password hash "
                "(argon2id), the two-step sign-in secret and recovery-code hashes when you turn "
                "them on, and your sessions (time and a short browser label such as "
                "\"Firefox · Linux\"; no IP address).",
                "Your work: briefs, generated texts and images, approvals you sign and the "
                "receipts log that records each step. The receipts are hash-chained: each "
                "one commits to the one before it.",
                "PayPal sandbox: the reference of each saved PayPal (vault reference) and a "
                "SHA-256 digest of PayPal's vault id with its status and dates; PayPal's own "
                "vault id and setup id are kept only to charge or delete the saved PayPal "
                "and are never shown, exported or written into a receipt. For each funding: "
                "the amounts and PayPal's order, authorization, capture and refund ids. For "
                "each mandate you sign: its limits and the charges made under it.",
                "PayPal webhooks: when PayPal calls us about a payment, we keep the "
                "signed message exactly as PayPal sent it, with its signature headers. "
                "These messages can hold payer data that PayPal includes (for example the "
                "payer's name, e-mail address and country in the sandbox). They are "
                "readable only by the studio's operator.",
                "Meta: identifiers of the campaigns, ad sets, ads and lead forms created from "
                "your signed approvals, aggregated ad results, and the leads and comments "
                "those ads receive, so that you can answer them."]),
            ("What we send to others", [
                "Payments: PayPal (Europe) S.à r.l. et Cie, S.C.A. processes the sandbox "
                "payments (order, authorization, capture, refund and the saved PayPal). "
                "What you enter in PayPal's own windows goes to PayPal, not to us. PayPal's "
                "privacy statement applies to that data.",
                "Brief text and generated content are sent to the Meta Model API (Muse models) "
                "and, for a second opinion, to an open-weights model on Baseten, only to "
                "produce the campaign you asked for.",
                "Campaigns are created in the advertiser's own Meta (and optionally Google) "
                "ad account only after a person signs the approval.",
                "Notification e-mails are sent from muse@udun.eu to the address on your "
                "account."]),
            ("Cookies and tracking", [
                "Only the cookies the site needs: the session, the CSRF token and your "
                "language and theme. No analytics and no advertising pixels.",
                "One third-party script: on the approval page, PayPal's JavaScript SDK is "
                "loaded from paypal.com so that you can approve the payment. PayPal sees "
                "your browser's request and may set its own cookies there. Nothing else "
                "on the site loads third-party scripts."]),
            ("How long", [
                "Accounts, sessions and profile data: until you delete your account. "
                "Receipts and signed approvals: kept, because they are the audit trail "
                "(see below).",
                "Saved PayPal records, mandates, the signed webhook messages and the "
                "receipts: for the life of the sandbox database. The hourly sandbox reset "
                "clears the demo data (briefs, generated content, fundings and their "
                "payment steps); it does not clear receipts, mandates, saved PayPal records "
                "or webhook messages. The whole database is deleted when the sandbox is."]),
            ("Your rights", [
                "You can export your data (including your mandates, saved PayPal references, "
                "fundings and charges; never PayPal's own token ids) and delete your account "
                f"yourself on the Account page, or write to {CONTACT}.",
                "Deleting your account revokes your mandates, marks your saved PayPal "
                "records as deleted so nothing can be charged, and then asks PayPal to "
                "delete the stored payment method (best effort: if PayPal does not answer, "
                "it stays deleted on our side and the failure is receipted). It erases "
                "your profile, e-mail address and sessions. The hash-chained receipts stay: "
                "they are the audit trail and name you only by an opaque account id. "
                "Signed webhook messages stay until the sandbox is deleted. See the data "
                "deletion page for details."]),
        ]),
        "pl": ("Prywatność", [
            ("Kto prowadzi stronę", [
                "Signed Studio (Muse Studio Lite) to projekt open source (Apache-2.0). Ta "
                "kopia pod adresem pay.udun.eu to sandbox: działa na sandboxie PayPal, nie "
                "przenosi prawdziwych pieniędzy i może zostać zresetowana lub usunięta w "
                "każdej chwili. Działa na serwerze w Finlandii.",
                "Wszystkie zalogowane konta pracują w tej samej przestrzeni studia: briefy, "
                "finansowania i pokwitowania utworzone w interfejsie www widzą wszystkie "
                "zalogowane konta. Nie wpisuj niczego, czego nie pokazałbyś im.",
                f"Kontakt we wszystkich sprawach Twoich danych: {CONTACT}."]),
            ("Co przechowujemy", [
                "Konta: nazwa użytkownika, nazwa wyświetlana, opcjonalny adres e-mail, skrót "
                "hasła (argon2id), sekret logowania dwuetapowego i skróty kodów zapasowych, "
                "jeśli je włączysz, oraz Twoje sesje (czas i krótka etykieta przeglądarki, np. "
                "„Firefox · Linux”; bez adresu IP).",
                "Twoja praca: briefy, wygenerowane teksty i grafiki, podpisane przez Ciebie "
                "zatwierdzenia i dziennik pokwitowań zapisujący każdy krok. Pokwitowania są "
                "powiązane łańcuchem skrótów: każde zawiera skrót poprzedniego.",
                "Sandbox PayPal: odwołanie do każdego zapisanego PayPal (vault reference) i "
                "skrót SHA-256 identyfikatora z PayPal, ze statusem i datami; własny "
                "identyfikator i identyfikator konfiguracji z PayPal trzymamy tylko po to, "
                "by obciążyć lub usunąć zapisany PayPal, i nigdy ich nie pokazujemy, nie "
                "eksportujemy ani nie wpisujemy do pokwitowań. Przy każdym finansowaniu: "
                "kwoty oraz identyfikatory zamówienia, autoryzacji, pobrania i zwrotu z "
                "PayPal. Przy każdym podpisanym mandacie: jego limity i obciążenia "
                "wykonane na jego podstawie.",
                "Webhooki PayPal: gdy PayPal powiadamia nas o płatności, zapisujemy "
                "podpisaną wiadomość dokładnie w takiej postaci, jak ją wysłał, wraz z "
                "nagłówkami podpisu. Takie wiadomości mogą zawierać dane płatnika, które "
                "PayPal w nich umieszcza (w sandboxie np. imię i nazwisko, e-mail i kraj "
                "płatnika). Czyta je tylko operator studia.",
                "Meta: identyfikatory kampanii, zestawów reklam, reklam i formularzy kontaktowych "
                "utworzonych z Twoich podpisanych zatwierdzeń, zbiorcze wyniki reklam oraz "
                "leady i komentarze pod tymi reklamami, żebyś mógł na nie odpowiedzieć."]),
            ("Co przekazujemy innym", [
                "Płatności: sandboxowe płatności (zamówienie, autoryzacja, pobranie, zwrot "
                "i zapisany PayPal) obsługuje PayPal (Europe) S.à r.l. et Cie, S.C.A. To, "
                "co wpisujesz w oknach PayPal, trafia do PayPal, nie do nas; do tych "
                "danych stosuje się polityka prywatności PayPal.",
                "Treść briefu i wygenerowane materiały trafiają do Meta Model API (modele Muse) "
                "oraz, jako druga opinia, do modelu open-weights w Baseten, wyłącznie po to, "
                "żeby przygotować zamówioną kampanię.",
                "Kampanie powstają na własnym koncie reklamowym reklamodawcy w Meta (i "
                "opcjonalnie Google) dopiero po podpisaniu zatwierdzenia przez człowieka.",
                "Powiadomienia e-mail wysyłamy z muse@udun.eu na adres zapisany w Twoim "
                "koncie."]),
            ("Ciasteczka i śledzenie", [
                "Tylko ciasteczka potrzebne do działania strony: sesja, token CSRF oraz język i "
                "motyw. Bez analityki i bez pikseli reklamowych.",
                "Jeden skrypt firmy trzeciej: na stronie zatwierdzenia ładujemy z paypal.com "
                "PayPal JavaScript SDK, żebyś mógł zatwierdzić płatność. PayPal widzi "
                "zapytanie Twojej przeglądarki i może tam ustawić własne ciasteczka. Nic "
                "innego na stronie nie ładuje skryptów firm trzecich."]),
            ("Jak długo", [
                "Konta, sesje i dane profilu: do usunięcia konta. Pokwitowania i podpisane "
                "zatwierdzenia zostają, bo są śladem audytowym (patrz niżej).",
                "Zapisane rekordy PayPal, mandaty, podpisane wiadomości webhooków i "
                "pokwitowania: przez cały czas życia bazy sandboxa. Cogodzinny reset "
                "sandboxa czyści dane demonstracyjne (briefy, wygenerowane materiały, "
                "finansowania i ich kroki płatnicze); nie czyści pokwitowań, mandatów, "
                "zapisanych rekordów PayPal ani wiadomości webhooków. Cała baza znika "
                "razem z sandboxem."]),
            ("Twoje prawa", [
                "Dane wyeksportujesz (także mandaty, odwołania do zapisanych PayPal, "
                "finansowania i obciążenia; nigdy własnych identyfikatorów tokenów PayPal) "
                f"i konto usuniesz sam na stronie Konto albo pisząc na {CONTACT}.",
                "Usunięcie konta unieważnia Twoje mandaty, oznacza zapisane rekordy PayPal "
                "jako usunięte (nic nie da się już obciążyć), a potem prosi PayPal o "
                "usunięcie zapisanej metody płatności (w miarę możliwości: jeśli PayPal nie "
                "odpowie, po naszej stronie pozostaje usunięta, a błąd trafia do "
                "pokwitowania). Usuwa profil, adres e-mail i sesje. Pokwitowania powiązane "
                "łańcuchem skrótów zostają: to ślad audytowy, który wskazuje Cię tylko "
                "nieczytelnym identyfikatorem konta. Podpisane wiadomości webhooków zostają "
                "do usunięcia sandboxa. Szczegóły na stronie o usuwaniu danych."]),
        ]),
    },
    "data-deletion": {
        "en": ("Data deletion", [
            ("Delete it yourself", [
                "Sign in, open Account and choose Delete account. Your profile, sessions, "
                "two-step sign-in data and e-mail address are removed at once; the account "
                "name is kept as a closed tombstone so it cannot be reused. In the same "
                "step your mandates are revoked and your saved PayPal is marked deleted, "
                "so nothing can be charged; then PayPal is asked to delete the stored "
                "payment method (best effort, the outcome is receipted).",
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
                "jako zamknięty ślad, żeby nikt jej ponownie nie użył. W tym samym kroku "
                "Twoje mandaty zostają unieważnione, a zapisany PayPal oznaczony jako "
                "usunięty, więc nic nie da się obciążyć; potem prosimy PayPal o usunięcie "
                "zapisanej metody płatności (w miarę możliwości, wynik trafia do "
                "pokwitowania).",
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
                "England and Wales, which provides the service described here (\"we\", \"us\"). "
                "These terms are the agreement between you and us.",
                "Company number: {seller_number}.",
                "Registered office: {seller_address}.",
                "Contact: {contact}."]),
            ("The service", [
                "Muse Studio Lite is an online ad studio. From your brief it prepares a "
                "campaign plan, ad texts and images, and two reviewers check them against "
                "the facts you confirmed and Meta's rules.",
                "Nothing is published on its own. A campaign is created on Meta only after "
                "you sign the approval, and it is created paused. "
                "Turning it on is a second, separate signature.",
                "Every step leaves a receipt you can read on the Receipts page."]),
            ("How you pay", [
                "There is no subscription and no plan. You fund each campaign separately "
                "through PayPal: the ad budget (daily budget times days) plus a studio fee "
                "of {fee_pct}% of that budget. The amount is computed by the studio, shown "
                "before you approve it in PayPal, and named in what you sign.",
                "When you approve the payment in PayPal and then sign the campaign, the "
                "money is held (authorized), not taken. It is taken (captured) only when "
                "you sign a second time to turn the campaign on.",
                "PayPal handles the payment; its own terms apply between you and PayPal."]),
            ("Holds, refunds and fees", [
                "If you never turn the campaign on, the hold is released and nothing is "
                "taken.",
                "After the campaign, the budget Meta did not spend is refunded to you "
                "through PayPal, with its share of the studio fee: the fee is due only on "
                "what was spent. A refund is proposed to you and made after you sign it.",
                "Every hold, payment and refund leaves a receipt that names the PayPal "
                "order and the exact ad you signed."]),
            ("Your right to cancel within 14 days", [
                "If you buy as a private person (a consumer) in the United Kingdom or the "
                "European Union, you may withdraw within 14 days of paying, without giving "
                "a reason.",
                "A campaign starts only when you turn it on, at your request. Once it has "
                "run, you lose the right to withdraw for the budget already spent; budget "
                "that was not spent is refunded in any case.",
                "To withdraw, write to {contact} within the 14 days."]),
            ("Ad spend and Meta's terms", [
                "We run the campaign you signed on Meta and pay Meta for it from the budget "
                "you funded. The studio fee is our only charge.",
                "Meta's own advertising terms and policies apply to every ad."]),
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
                "Anglii i Walii, która świadczy opisaną tu usługę („my”). Ten regulamin "
                "jest umową między Tobą a nami.",
                "Numer spółki: {seller_number}.",
                "Siedziba: {seller_address}.",
                "Kontakt: {contact}."]),
            ("Usługa", [
                "Muse Studio Lite to internetowe studio reklam. Na podstawie Twojego briefu "
                "przygotowuje plan kampanii, teksty i grafiki reklam, a dwóch recenzentów "
                "sprawdza je z potwierdzonymi przez Ciebie faktami i zasadami Mety.",
                "Nic nie jest publikowane samo. Kampania powstaje w Mecie dopiero po "
                "podpisaniu zatwierdzenia i powstaje wstrzymana. Jej włączenie to drugi, "
                "osobny podpis.",
                "Każdy krok zostawia pokwitowanie, które przeczytasz na stronie "
                "Pokwitowania."]),
            ("Jak płacisz", [
                "Nie ma abonamentu ani planu. Każdą kampanię opłacasz osobno przez PayPal: "
                "budżet reklamowy (budżet dzienny razy liczba dni) plus opłata studia w "
                "wysokości {fee_pct}% tego budżetu. Kwotę wylicza studio, pokazuje ją przed "
                "zatwierdzeniem w PayPal i wskazuje w tym, co podpisujesz.",
                "Gdy zatwierdzisz płatność w PayPal, a potem podpiszesz kampanię, pieniądze "
                "są blokowane (autoryzowane), a nie pobierane. Pobieramy je dopiero, gdy "
                "podpiszesz po raz drugi, by włączyć kampanię.",
                "Płatność obsługuje PayPal; między Tobą a PayPal obowiązują jego własne "
                "warunki."]),
            ("Blokady, zwroty i opłaty", [
                "Jeśli nie włączysz kampanii, blokada zostaje zwolniona i nic nie jest "
                "pobierane.",
                "Po kampanii budżet, którego Meta nie wydała, wraca do Ciebie przez PayPal "
                "wraz z odpowiednią częścią opłaty studia: opłata należy się tylko od tego, "
                "co wydano. Zwrot jest Ci proponowany i wykonywany po Twoim podpisie.",
                "Każda blokada, płatność i zwrot zostawia pokwitowanie, które wskazuje "
                "zamówienie PayPal i dokładnie tę reklamę, którą podpisałeś."]),
            ("Prawo odstąpienia w ciągu 14 dni", [
                "Jeśli kupujesz jako osoba prywatna (konsument) w Wielkiej Brytanii lub w "
                "Unii Europejskiej, możesz odstąpić od umowy w ciągu 14 dni od płatności, "
                "bez podawania przyczyny.",
                "Kampania zaczyna się dopiero, gdy ją włączysz, na Twoją prośbę. Gdy już "
                "działała, tracisz prawo odstąpienia co do budżetu już wydanego; budżet "
                "niewydany wraca do Ciebie w każdym przypadku.",
                "Aby odstąpić, napisz na {contact} w ciągu 14 dni."]),
            ("Wydatki na reklamy i zasady Mety", [
                "Kampanię, którą podpisałeś, prowadzimy w Mecie i płacimy za nią Mecie z "
                "budżetu, który opłaciłeś. Opłata studia to nasza jedyna należność.",
                "Do każdej reklamy stosują się własne warunki i zasady reklamowe Mety."]),
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
    "terms": ("seller", "service", "payment", "refunds", "withdrawal", "ad-spend",
              "acceptable-use", "data", "liability", "changes", "law"),
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
