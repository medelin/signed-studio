"""UI strings of the account pages and the login's second step, English and Polish.

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key: same keys, same placeholders, nothing empty.
"""

from __future__ import annotations

EN_ACCOUNTS: dict[str, str] = {
    "nav.account": "Account",
    "meta.account": "Your account: profile, password, two-step sign-in, sessions, data export "
                    "and deletion.",
    "meta.login_2fa": "The second step of signing in: a code from your authenticator app.",
    "meta.account_deleted": "The account was deleted.",
    # the login's second step
    "login.2fa_title": "Two-step sign-in",
    "login.2fa_hint": "Your password was accepted. Enter the 6-digit code from your "
                      "authenticator app, or one of your recovery codes.",
    "login.2fa_code": "Code",
    "login.2fa_code_hint": "6 digits, or a recovery code like ABCD-EFGH-IJKL-MNOP. Each code "
                           "works once.",
    "login.2fa_submit": "Continue",
    "login.2fa_failed": "That did not work. Check the code and try again, or sign in again "
                        "in a minute.",
    "login.2fa_back": "Start again with your password",
    # the account page
    "account.title": "Your account",
    "account.lead": "Signed in as {name}. Everything about your account is on this page.",
    "account.demo_note": "This is the shared judge demo account. Its password is set by the "
                         "operator, two-step sign-in stays off and it cannot be deleted, so no "
                         "judge can lock the others out. Your own account can do all of it.",
    "account.sections": "Account sections",
    "account.art": "Two owners side by side, each looking after their own account.",
    "account.help_html": 'Questions? See <a href="/help#accounts">accounts</a>, '
                         '<a href="/help#two-step-sign-in">two-step sign-in</a> and '
                         '<a href="/help#delete-account">deleting an account</a> in the help.',
    "account.section.profile": "Profile",
    "account.section.password": "Password",
    "account.section.two-factor": "Two-step sign-in",
    "account.section.sessions": "Sessions",
    "account.section.export": "Your data",
    "account.section.delete": "Delete account",
    # profile
    "account.profile.user": "User name",
    "account.profile.you": "About you",
    "account.profile.display_name": "Display name",
    "account.profile.email": "E-mail (optional)",
    "account.profile.email_hint": "Only stored; the studio sends no e-mail yet.",
    "account.profile.defaults": "Defaults when you sign in",
    "account.profile.lang": "Language",
    "account.profile.theme": "Mode",
    "account.profile.palette": "Colours",
    "account.profile.keep": "as chosen in the header",
    "account.profile.advertiser": "Who advertises",
    "account.profile.person": "a person",
    "account.profile.company": "a company",
    "account.profile.company_hint": "Only for a company; cleared when you choose a person.",
    "account.profile.legal_name": "Legal name",
    "account.profile.country": "Country",
    "account.profile.tax_id": "Tax id",
    "account.profile.tax_hint": "Any country's tax or VAT number, as you write it. It is not "
                                "checked.",
    "account.profile.billing_note": "Invoices and billing come after the hackathon. These "
                                    "details are only stored for then; nothing is charged "
                                    "or sent.",
    "account.profile.save": "Save profile",
    "account.profile.invalid": "Check this field: {field}.",
    # password
    "account.password.lead": "Changing the password signs out every other session and "
                             "revokes their signing keys.",
    "account.password.current": "Current password",
    "account.password.new": "New password",
    "account.password.new_hint": "At least {min} characters; very common passwords and your "
                                 "user name are refused.",
    "account.password.confirm": "New password again",
    "account.password.submit": "Change password",
    "account.password.demo": "The operator sets this account's password (JUDGE_PASSWORD).",
    # two-step sign-in
    "account.2fa.lead": "With two-step sign-in, signing in needs your password and a code "
                        "from an authenticator app on your phone (TOTP, RFC 6238).",
    "account.2fa.on": "Two-step sign-in is on.",
    "account.2fa.off": "Two-step sign-in is off.",
    "account.2fa.demo": "Two-step sign-in stays off for the shared judge account (a sandbox "
                        "setting), so a judge can never lock the others out.",
    "account.2fa.password": "Your password",
    "account.2fa.start": "Set up two-step sign-in",
    "account.2fa.scan": "Scan this code with your authenticator app, or type the key below "
                        "into it. Then enter the 6-digit code it shows.",
    "account.2fa.qr_label": "QR code for your authenticator app",
    "account.2fa.secret_label": "Key to type in",
    "account.2fa.code": "6-digit code",
    "account.2fa.confirm": "Turn on",
    "account.2fa.cancel": "Cancel set-up",
    "account.2fa.codes_title": "Your recovery codes",
    "account.2fa.codes_lead": "Two-step sign-in is on. Keep these ten codes somewhere safe, "
                              "offline. Each one signs you in once if you lose your phone. "
                              "They are shown only now.",
    "account.2fa.codes_left": "Recovery codes left: {n}.",
    "account.2fa.disable_lead": "To turn it off, enter your password and a code (from the app "
                                "or a recovery code).",
    "account.2fa.code_or_recovery": "Code or recovery code",
    "account.2fa.disable": "Turn off",
    # sessions
    "account.sessions.lead": "Where you are signed in. A session ends after 8 hours, when you "
                             "sign out, or when the server restarts.",
    "account.sessions.caption": "Active sessions",
    "account.sessions.agent": "Browser",
    "account.sessions.created": "Signed in (UTC)",
    "account.sessions.last_seen": "Last seen (UTC)",
    "account.sessions.action": "Action",
    "account.sessions.this": "this session",
    "account.sessions.revoke": "Sign out",
    "account.sessions.revoke_label": "Sign out the session from {agent}, signed in {created}",
    "account.sessions.revoke_others": "Sign out all other sessions",
    "account.sessions.none_other": "No other sessions.",
    # export
    "account.export.lead": "One JSON file with your profile, your sessions and the approvals "
                           "you signed (with their receipt ids).",
    "account.export.download": "Download my data (JSON)",
    # delete
    "account.delete.lead": "Deleting removes your profile, your sessions, your signing keys "
                           "and your two-step sign-in. It cannot be undone.",
    "account.delete.receipts": "Receipts stay. The receipt log can only be added to, and each "
                               "receipt is chained to the one before it, so removing one would "
                               "break the proof for everyone. Receipts never held your name: "
                               "an approval names only the fingerprint of the signing key. "
                               "After deletion that fingerprint leads to a random account id "
                               "and nothing else.",
    "account.delete.confirm": "Type your user name ({user}) to confirm",
    "account.delete.password": "Your password",
    "account.delete.code": "Code or recovery code",
    "account.delete.submit": "Delete my account",
    "account.delete.demo": "The shared judge account cannot be deleted.",
    "account.delete.art": "An owner and a record whose chain of receipts stays intact.",
    # results
    "account.done.profile": "Profile saved.",
    "account.done.password": "Password changed. Every other session was signed out.",
    "account.done.2fa_started": "Almost done: scan the code and confirm it below.",
    "account.done.2fa_cancelled": "Set-up cancelled.",
    "account.done.2fa_off": "Two-step sign-in is off.",
    "account.done.session_revoked": "That session was signed out.",
    "account.done.sessions_revoked": "Every other session was signed out.",
    # after deletion
    "account.deleted.title": "Account deleted",
    "account.deleted.lead": "Your account was deleted and you are signed out. Receipts of "
                            "what you approved stay in the log under a key fingerprint that "
                            "no longer leads to you.",
    "account.deleted.back": "Back to the start page",
    # errors (also shown by E() in scripts)
    "err.account_error": "That did not work.",
    "err.invalid_username": "User names are 2 to 32 characters: a-z, 0-9, dot, dash, "
                            "underscore.",
    "err.username_taken": "That user name is taken.",
    "err.weak_password": "The new password is too short (at least 12 characters) or has "
                         "control characters.",
    "err.common_password": "That password is too easy to guess; choose another.",
    "err.password_mismatch": "The two new passwords differ.",
    "err.wrong_password": "The password is not correct.",
    "err.wrong_code": "The code is not correct, or it was used already.",
    "err.invalid_code": "The code is not correct, or it was used already.",
    "err.invalid_profile": "A profile field is not valid.",
    "err.demo_account": "The shared judge account cannot do this.",
    "err.no_such_account": "There is no such account.",
    "err.confirmation_mismatch": "The confirmation does not match your user name.",
    "err.two_factor_state": "Two-step sign-in changed meanwhile; reload the page.",
    "err.invalid_form": "The form could not be read.",
    "err.unknown_session": "That session is not active any more.",
}

PL_ACCOUNTS: dict[str, str] = {
    "nav.account": "Konto",
    "meta.account": "Twoje konto: profil, hasło, logowanie dwuetapowe, sesje, eksport danych "
                    "i usunięcie.",
    "meta.login_2fa": "Drugi krok logowania: kod z aplikacji uwierzytelniającej.",
    "meta.account_deleted": "Konto zostało usunięte.",
    "login.2fa_title": "Logowanie dwuetapowe",
    "login.2fa_hint": "Hasło przyjęte. Wpisz 6-cyfrowy kod z aplikacji uwierzytelniającej "
                      "albo jeden z kodów odzyskiwania.",
    "login.2fa_code": "Kod",
    "login.2fa_code_hint": "6 cyfr albo kod odzyskiwania w rodzaju ABCD-EFGH-IJKL-MNOP. Każdy "
                           "kod działa raz.",
    "login.2fa_submit": "Dalej",
    "login.2fa_failed": "Nie udało się. Sprawdź kod i spróbuj ponownie albo zaloguj się "
                        "jeszcze raz za minutę.",
    "login.2fa_back": "Zacznij od nowa od hasła",
    "account.title": "Twoje konto",
    "account.lead": "Zalogowano jako {name}. Wszystko o Twoim koncie jest na tej stronie.",
    "account.demo_note": "To wspólne konto demonstracyjne jury. Jego hasło ustawia operator, "
                         "logowanie dwuetapowe pozostaje wyłączone i konta nie da się usunąć, "
                         "więc nikt z jury nie zablokuje pozostałych. Własne konto może to "
                         "wszystko.",
    "account.sections": "Części konta",
    "account.art": "Dwoje właścicieli obok siebie, każde dba o własne konto.",
    "account.help_html": 'Pytania? Zobacz w pomocy: <a href="/pl/help#accounts">konta</a>, '
                         '<a href="/pl/help#two-step-sign-in">logowanie dwuetapowe</a> i '
                         '<a href="/pl/help#delete-account">usuwanie konta</a>.',
    "account.section.profile": "Profil",
    "account.section.password": "Hasło",
    "account.section.two-factor": "Logowanie dwuetapowe",
    "account.section.sessions": "Sesje",
    "account.section.export": "Twoje dane",
    "account.section.delete": "Usunięcie konta",
    "account.profile.user": "Nazwa użytkownika",
    "account.profile.you": "O Tobie",
    "account.profile.display_name": "Wyświetlana nazwa",
    "account.profile.email": "E-mail (nieobowiązkowy)",
    "account.profile.email_hint": "Tylko zapisany; studio nie wysyła jeszcze e-maili.",
    "account.profile.defaults": "Ustawienia po zalogowaniu",
    "account.profile.lang": "Język",
    "account.profile.theme": "Tryb",
    "account.profile.palette": "Kolory",
    "account.profile.keep": "jak wybrano w nagłówku",
    "account.profile.advertiser": "Kto się reklamuje",
    "account.profile.person": "osoba",
    "account.profile.company": "firma",
    "account.profile.company_hint": "Tylko dla firmy; czyszczone po wybraniu osoby.",
    "account.profile.legal_name": "Nazwa prawna",
    "account.profile.country": "Kraj",
    "account.profile.tax_id": "Numer podatkowy",
    "account.profile.tax_hint": "Numer podatkowy lub VAT dowolnego kraju, tak jak go zapisujesz. "
                                "Nie jest sprawdzany.",
    "account.profile.billing_note": "Faktury i rozliczenia pojawią się po hackathonie. Te dane "
                                    "są tylko zapisywane na później; nic nie jest pobierane "
                                    "ani wysyłane.",
    "account.profile.save": "Zapisz profil",
    "account.profile.invalid": "Sprawdź to pole: {field}.",
    "account.password.lead": "Zmiana hasła wylogowuje wszystkie inne sesje i unieważnia ich "
                             "klucze do podpisu.",
    "account.password.current": "Obecne hasło",
    "account.password.new": "Nowe hasło",
    "account.password.new_hint": "Co najmniej {min} znaków; bardzo popularne hasła i nazwa "
                                 "użytkownika są odrzucane.",
    "account.password.confirm": "Nowe hasło jeszcze raz",
    "account.password.submit": "Zmień hasło",
    "account.password.demo": "Hasło tego konta ustawia operator (JUDGE_PASSWORD).",
    "account.2fa.lead": "Przy logowaniu dwuetapowym potrzebne są hasło i kod z aplikacji "
                        "uwierzytelniającej w telefonie (TOTP, RFC 6238).",
    "account.2fa.on": "Logowanie dwuetapowe jest włączone.",
    "account.2fa.off": "Logowanie dwuetapowe jest wyłączone.",
    "account.2fa.demo": "Na wspólnym koncie jury logowanie dwuetapowe pozostaje wyłączone "
                        "(ustawienie piaskownicy), więc nikt z jury nie zablokuje pozostałych.",
    "account.2fa.password": "Twoje hasło",
    "account.2fa.start": "Włącz logowanie dwuetapowe",
    "account.2fa.scan": "Zeskanuj ten kod aplikacją uwierzytelniającą albo wpisz do niej "
                        "poniższy klucz. Potem wpisz 6-cyfrowy kod, który pokaże.",
    "account.2fa.qr_label": "Kod QR dla aplikacji uwierzytelniającej",
    "account.2fa.secret_label": "Klucz do wpisania",
    "account.2fa.code": "6-cyfrowy kod",
    "account.2fa.confirm": "Włącz",
    "account.2fa.cancel": "Przerwij konfigurację",
    "account.2fa.codes_title": "Twoje kody odzyskiwania",
    "account.2fa.codes_lead": "Logowanie dwuetapowe jest włączone. Przechowuj te dziesięć kodów "
                              "w bezpiecznym miejscu, poza komputerem. Każdy z nich raz "
                              "zaloguje Cię, jeśli zgubisz telefon. Widać je tylko teraz.",
    "account.2fa.codes_left": "Pozostałe kody odzyskiwania: {n}.",
    "account.2fa.disable_lead": "Aby je wyłączyć, wpisz hasło i kod (z aplikacji albo kod "
                                "odzyskiwania).",
    "account.2fa.code_or_recovery": "Kod albo kod odzyskiwania",
    "account.2fa.disable": "Wyłącz",
    "account.sessions.lead": "Gdzie jesteś zalogowany. Sesja kończy się po 8 godzinach, po "
                             "wylogowaniu albo po restarcie serwera.",
    "account.sessions.caption": "Aktywne sesje",
    "account.sessions.agent": "Przeglądarka",
    "account.sessions.created": "Zalogowano (UTC)",
    "account.sessions.last_seen": "Ostatnio widziana (UTC)",
    "account.sessions.action": "Działanie",
    "account.sessions.this": "ta sesja",
    "account.sessions.revoke": "Wyloguj",
    "account.sessions.revoke_label": "Wyloguj sesję z {agent}, zalogowaną {created}",
    "account.sessions.revoke_others": "Wyloguj wszystkie inne sesje",
    "account.sessions.none_other": "Brak innych sesji.",
    "account.export.lead": "Jeden plik JSON z Twoim profilem, sesjami i zatwierdzeniami, które "
                           "podpisałeś (z identyfikatorami ich pokwitowań).",
    "account.export.download": "Pobierz moje dane (JSON)",
    "account.delete.lead": "Usunięcie kasuje Twój profil, sesje, klucze do podpisu i logowanie "
                           "dwuetapowe. Nie da się tego cofnąć.",
    "account.delete.receipts": "Pokwitowania zostają. Do rejestru pokwitowań można tylko "
                               "dopisywać, a każde pokwitowanie jest powiązane z poprzednim, "
                               "więc usunięcie jednego zepsułoby dowód dla wszystkich. "
                               "Pokwitowania nigdy nie zawierały Twojego nazwiska: "
                               "zatwierdzenie podaje tylko odcisk klucza, którym podpisano. Po "
                               "usunięciu ten odcisk prowadzi do losowego identyfikatora konta "
                               "i do niczego więcej.",
    "account.delete.confirm": "Wpisz swoją nazwę użytkownika ({user}), aby potwierdzić",
    "account.delete.password": "Twoje hasło",
    "account.delete.code": "Kod albo kod odzyskiwania",
    "account.delete.submit": "Usuń moje konto",
    "account.delete.demo": "Wspólnego konta jury nie da się usunąć.",
    "account.delete.art": "Właściciel i rekord, którego łańcuch pokwitowań pozostaje cały.",
    "account.done.profile": "Profil zapisany.",
    "account.done.password": "Hasło zmienione. Wszystkie inne sesje zostały wylogowane.",
    "account.done.2fa_started": "Prawie gotowe: zeskanuj kod i potwierdź go poniżej.",
    "account.done.2fa_cancelled": "Konfiguracja przerwana.",
    "account.done.2fa_off": "Logowanie dwuetapowe jest wyłączone.",
    "account.done.session_revoked": "Ta sesja została wylogowana.",
    "account.done.sessions_revoked": "Wszystkie inne sesje zostały wylogowane.",
    "account.deleted.title": "Konto usunięte",
    "account.deleted.lead": "Twoje konto zostało usunięte i jesteś wylogowany. Pokwitowania "
                            "tego, co zatwierdziłeś, zostają w rejestrze pod odciskiem klucza, "
                            "który już do Ciebie nie prowadzi.",
    "account.deleted.back": "Wróć na stronę główną",
    "err.account_error": "Nie udało się.",
    "err.invalid_username": "Nazwa użytkownika ma od 2 do 32 znaków: a-z, 0-9, kropka, "
                            "myślnik, podkreślenie.",
    "err.username_taken": "Ta nazwa użytkownika jest zajęta.",
    "err.weak_password": "Nowe hasło jest za krótkie (co najmniej 12 znaków) albo zawiera znaki "
                         "sterujące.",
    "err.common_password": "To hasło łatwo odgadnąć; wybierz inne.",
    "err.password_mismatch": "Dwa nowe hasła się różnią.",
    "err.wrong_password": "Hasło jest niepoprawne.",
    "err.wrong_code": "Kod jest niepoprawny albo został już użyty.",
    "err.invalid_code": "Kod jest niepoprawny albo został już użyty.",
    "err.invalid_profile": "Jedno z pól profilu jest niepoprawne.",
    "err.demo_account": "Wspólne konto jury nie może tego zrobić.",
    "err.no_such_account": "Nie ma takiego konta.",
    "err.confirmation_mismatch": "Potwierdzenie nie zgadza się z Twoją nazwą użytkownika.",
    "err.two_factor_state": "Logowanie dwuetapowe w międzyczasie się zmieniło; odśwież stronę.",
    "err.invalid_form": "Nie udało się odczytać formularza.",
    "err.unknown_session": "Ta sesja nie jest już aktywna.",
}
