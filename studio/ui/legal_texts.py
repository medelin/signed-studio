"""Public privacy and data-deletion pages (``/privacy``, ``/data-deletion``), EN and PL.

Plain statements of what this sandbox stores and how to remove it; no consent banner and
no tracking. The same facts in both languages (``tests/test_legal_pages.py`` checks parity).
"""

from __future__ import annotations

from typing import Final

CONTACT: Final = "muse@udun.eu"
UPDATED: Final = "2026-10-29"

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
}


def page(name: str, lang: str) -> tuple[str, list[tuple[str, list[str]]]]:
    by_lang = PAGES[name]
    return by_lang.get(lang, by_lang["en"])
