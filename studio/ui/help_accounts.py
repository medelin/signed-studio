"""Help/FAQ entries about accounts, English and Polish (``studio.ui.help_texts`` appends
them). Plain data: ``(anchor, cast key, scene, questions, answers)``; answers are trusted
HTML. True to ``studio/accounts.py`` and ``studio/routes_account.py``.
"""

from __future__ import annotations

ACCOUNT_ENTRIES: tuple[tuple[str, str, str, dict[str, str], dict[str, str]], ...] = (
    (
        "accounts", "kofi", "private",
        {"en": "Who has an account, and how do I get one?",
         "pl": "Kto ma konto i jak je dostać?"},
        {"en": "There is no public sign-up. The operator creates each account on the server "
               "(<code>studio account create</code>) and gives you the user name and a first "
               "password. Change it on <a href=\"/account#password\">your account page</a>: "
               "at least 12 characters, not a common password. Changing it signs out your "
               "other sessions. The profile keeps your display name, an optional e-mail, "
               "your language and display defaults, and whether you advertise as a person "
               "or a company (legal name, country and a tax id in any country's format, not "
               "checked). Billing comes after the hackathon; for now these details are only "
               "stored. Judges use the shared <code>judge</code> account.",
         "pl": "Nie ma publicznej rejestracji. Operator zakłada każde konto na serwerze "
               "(<code>studio account create</code>) i przekazuje nazwę użytkownika oraz "
               "pierwsze hasło. Zmień je na <a href=\"/account#password\">stronie konta</a>: "
               "co najmniej 12 znaków, nie popularne hasło. Zmiana wylogowuje pozostałe "
               "sesje. Profil przechowuje wyświetlaną nazwę, nieobowiązkowy e-mail, "
               "ustawienia języka i wyglądu oraz to, czy reklamujesz się jako osoba czy "
               "firma (nazwa prawna, kraj i numer podatkowy w formacie dowolnego kraju, bez "
               "sprawdzania). Rozliczenia pojawią się po hackathonie; na razie te dane są "
               "tylko zapisywane. Jury używa wspólnego konta <code>judge</code>."},
    ),
    (
        "two-step-sign-in", "priya", "signed",
        {"en": "How does two-step sign-in work?",
         "pl": "Jak działa logowanie dwuetapowe?"},
        {"en": "Turn it on in <a href=\"/account#two-factor\">your account</a>: enter your "
               "password, scan the QR code with an authenticator app (or type the key), then "
               "confirm with the 6-digit code it shows. You get ten recovery codes, shown "
               "once; keep them offline. From then on, signing in asks for the password and "
               "then a code. Each code works once: a code that was already used is refused, "
               "and so is a recovery code used before. Wrong codes count towards the same "
               "attempt limit as wrong passwords. Turning it off needs the password and a "
               "code. If you lose both the phone and the codes, the operator can reset it.",
         "pl": "Włącz je na <a href=\"/account#two-factor\">stronie konta</a>: wpisz hasło, "
               "zeskanuj kod QR aplikacją uwierzytelniającą (albo wpisz klucz) i potwierdź "
               "6-cyfrowym kodem, który pokaże. Dostajesz dziesięć kodów odzyskiwania, "
               "widocznych tylko raz; przechowuj je poza komputerem. Od tej chwili logowanie "
               "wymaga hasła, a potem kodu. Każdy kod działa raz: kod już użyty jest "
               "odrzucany, podobnie jak wcześniej użyty kod odzyskiwania. Błędne kody liczą "
               "się do tego samego limitu prób co błędne hasła. Wyłączenie wymaga hasła i "
               "kodu. Jeśli zgubisz i telefon, i kody, operator może je zresetować."},
    ),
    (
        "delete-account", "tomasz", "verify",
        {"en": "What happens to my receipts when I delete my account?",
         "pl": "Co dzieje się z pokwitowaniami, gdy usunę konto?"},
        {"en": "Deleting (type your user name, then your password, plus a code if two-step "
               "sign-in is on) removes your profile, sessions, signing keys and two-step "
               "data. Receipts stay: the log can only be added to, and every receipt is "
               "chained to the one before, so removing one would break the proof for "
               "everyone. Receipts never held your name; an approval names only the "
               "fingerprint of the key that signed it. After deletion that fingerprint leads "
               "to a random account id and nothing else. Before deleting you can "
               "<a href=\"/account#export\">download your data</a> as one JSON file.",
         "pl": "Usunięcie (wpisz nazwę użytkownika, potem hasło, a przy logowaniu "
               "dwuetapowym także kod) kasuje profil, sesje, klucze do podpisu i dane "
               "logowania dwuetapowego. Pokwitowania zostają: do rejestru można tylko "
               "dopisywać, a każde pokwitowanie jest powiązane z poprzednim, więc usunięcie "
               "jednego zepsułoby dowód dla wszystkich. Pokwitowania nigdy nie zawierały "
               "Twojego nazwiska; zatwierdzenie podaje tylko odcisk klucza, którym "
               "podpisano. Po usunięciu ten odcisk prowadzi do losowego identyfikatora konta "
               "i do niczego więcej. Przed usunięciem możesz "
               "<a href=\"/account#export\">pobrać swoje dane</a> jako jeden plik JSON."},
    ),
    (
        "judge-account", "mei", "reset",
        {"en": "Why can't the judge account turn on two-step sign-in?",
         "pl": "Dlaczego konto jury nie może włączyć logowania dwuetapowego?"},
        {"en": "All judges share one account, so one judge's phone must never become the "
               "only way in for the others. On the judge account, two-step sign-in stays off "
               "and cannot be turned on from the page, the password is set by the operator "
               "(<code>JUDGE_PASSWORD</code>, applied at every start) and the account cannot "
               "be deleted. Everything else works: the profile, the sessions list and the "
               "data export. To try two-step sign-in or deletion, ask the operator for an "
               "account of your own. When the studio is connected to a real Meta ad "
               "account, the judge account creates real campaigns there, always paused, but "
               "it cannot sign turning one on, changing one, or posting or hiding a comment "
               "on the Page: no judge can spend money or speak for the Page.",
         "pl": "Wszyscy jurorzy dzielą jedno konto, więc telefon jednej osoby nie może stać "
               "się jedyną drogą wejścia dla pozostałych. Na koncie jury logowanie "
               "dwuetapowe pozostaje wyłączone i nie da się go włączyć ze strony, hasło "
               "ustawia operator (<code>JUDGE_PASSWORD</code>, stosowane przy każdym starcie), "
               "a konta nie da się usunąć. Reszta działa: profil, lista sesji i eksport "
               "danych. Aby wypróbować logowanie dwuetapowe albo usuwanie, poproś operatora "
               "o własne konto. Gdy studio jest połączone z prawdziwym kontem reklamowym "
               "Mety, konto jury tworzy tam prawdziwe kampanie, zawsze wstrzymane, ale nie "
               "może podpisać ich włączenia, zmiany ani wpisu czy ukrycia komentarza na "
               "stronie: żaden juror nie wyda pieniędzy ani nie przemówi w imieniu strony."},
    ),
    (
        "notifications", "priya", "signed",
        {"en": "Which e-mails does the studio send me?",
         "pl": "Jakie e-maile wysyła mi studio?"},
        {"en": "If the operator has set up mail and your profile has an address, the studio "
               "writes to you when something needs you: an approval is waiting for your "
               "signature, a campaign was created (paused), launched or paused, new leads "
               "arrived, or the insights have new recommendations. Those mails carry a "
               "count and a link, never a lead's name or contact, and leads and "
               "recommendations are at most one mail per ten minutes (the counts add up). "
               "Security notices (password changed, two-step sign-in on or off, a new "
               "sign-in, a data export, account deletion) always come. Choose campaigns "
               "and leads under <a href=\"/account#notifications\">Notifications</a> on "
               "your account page. Each mail sent, or failed, leaves a receipt that holds no "
               "address. The shared judge account never sends mail.",
         "pl": "Jeśli operator skonfigurował pocztę, a w profilu masz adres, studio pisze "
               "do Ciebie, gdy coś Cię potrzebuje: zatwierdzenie czeka na podpis, kampania "
               "została utworzona (wstrzymana), uruchomiona lub wstrzymana, pojawiły się "
               "nowe leady albo statystyki mają nowe rekomendacje. Takie wiadomości zawierają "
               "liczbę i link, nigdy imienia ani kontaktu leada, a o leadach i rekomendacjach "
               "dostajesz najwyżej jeden e-mail na dziesięć minut (liczby się sumują). "
               "Powiadomienia o bezpieczeństwie (zmiana hasła, logowanie dwuetapowe włączone "
               "lub wyłączone, nowe logowanie, eksport danych, usunięcie konta) przychodzą "
               "zawsze. Kampanie i leady wybierasz w sekcji <a "
               "href=\"/account#notifications\">Powiadomienia</a> na stronie konta. Każdy "
               "wysłany lub nieudany e-mail zostawia pokwitowanie bez adresu. Wspólne konto "
               "jury nigdy nie wysyła poczty."},
    ),
)
