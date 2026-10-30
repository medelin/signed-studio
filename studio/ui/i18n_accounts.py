"""UI strings of the account pages and the login's second step, English and Polish.

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key: same keys, same placeholders, nothing empty.
"""

from __future__ import annotations

# Meta Business Help: "Set up and install a Meta pixel" (the studio never creates one)
PIXEL_HELP = "https://www.facebook.com/business/help/952192354843755"

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
    "account.group.profile": "Profile & billing",
    "account.group.security": "Security",
    "account.group.assistants": "Assistants & API",
    "account.group.notifications": "Notifications",
    "account.group.data": "Your data",
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
    "account.profile.billing_note": "These details are yours to keep here; Stripe asks for "
                                    "the ones it puts on an invoice at checkout (Billing).",
    "account.profile.save": "Save profile",
    "account.profile.invalid": "Check this field: {field}.",
    "account.profile.online": "Your business online",
    "account.profile.online_lead": "Saved once, used by every new brief: the studio fills "
                                   "these in when a draft leaves them empty. You can change "
                                   "them in each brief.",
    "account.profile.website": "Website",
    "account.profile.website_hint": "The studio reads this site before drafting, to suggest "
                                    "facts you confirm.",
    "account.profile.sell_countries": "Countries you sell in",
    "account.profile.sell_countries_hint": "Two-letter country codes, separated by commas, "
                                           "for example: PL, DE. At most 10.",
    "account.profile.pixel_id": "Meta pixel",
    "account.profile.no_pixel": "No pixel",
    "account.profile.pixel_unnamed": "Pixel without a name",
    "account.profile.pixel_saved": "Saved pixel",
    "account.profile.pixel_hint_html": 'Used for campaigns that count sign-ups or sales on '
                                       'your website. The studio does not create pixels: '
                                       '<a href="' + PIXEL_HELP + '" rel="noopener '
                                       'noreferrer" target="_blank">create one in Meta</a>, '
                                       'then enter its number here.',
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
    "err.invalid_website": "The website must be a full address starting with https://.",
    "err.invalid_countries": "Use two-letter country codes separated by commas, for "
                             "example PL, DE (at most 10).",
    "err.invalid_pixel": "The pixel number has digits only (at most 20).",
    "err.demo_account": "The shared judge account cannot do this.",
    "err.demo_landing": "On the shared judge account a real campaign links only to "
                        "the studio's own landing page; set that page in the brief.",
    "err.demo_live_write": "The shared judge account creates real campaigns on Meta, "
                           "always paused. Turning one on, changing it or posting on the "
                           "Page needs the owner's own account.",
    "err.no_such_account": "There is no such account.",
    "err.confirmation_mismatch": "The confirmation does not match your user name.",
    "err.two_factor_state": "Two-step sign-in changed meanwhile; reload the page.",
    "err.billing_off": "Billing is not turned on for this studio.",
    "err.invalid_event": "That webhook event could not be read.",
    "err.invalid_form": "The form could not be read.",
    "err.unknown_session": "That session is not active any more.",
    "err.second_factor_locked": "Too many wrong codes for this account; wait a while and try "
                                "again.",
    "account.section.billing": "Billing",
    "account.done.billing": "Thank you. Your subscription appears here once Stripe confirms "
                            "the payment, usually within a minute.",
    "billing.lead": "Pay monthly, for 3, 6 or 12 months in advance, or once for one month, "
                    "by card through Stripe. Stripe issues the invoice for every payment; "
                    "you can cancel a plan any time.",
    "billing.subscribe": "Continue to payment",
    "billing.portal": "Manage card, invoices and cancellation",
    "billing.status": "Subscription",
    "billing.renews": "renews on",
    "billing.cancels": "ends on",
    "billing.last_invoice": "Latest invoice (Stripe)",
    "billing.test_mode": "Test mode: no real money moves. Pay with the test card 4242 4242 "
                         "4242 4242, any future date and any CVC.",
    "billing.error": "Billing is not reachable right now. Try again in a moment.",
    "billing.status.active": "active",
    "billing.status.trialing": "trial period",
    "billing.status.past_due": "payment overdue",
    "billing.status.canceled": "ended",
    "billing.status.unpaid": "unpaid",
    "billing.status.incomplete": "waiting for payment",
    "billing.status.incomplete_expired": "expired",
    "billing.status.paused": "paused",
    "social.heading": "New here? Register in one step",
    "social.lead": "Continue with Google or Facebook to sign in or create your account. A "
                   "new account starts on trial: campaigns are created paused, on the "
                   "studio's own page, until the operator lifts the trial.",
    "social.continue_google": "Continue with Google",
    "social.continue_facebook": "Continue with Facebook",
    "social.continue": "Continue to Muse Studio Lite",
    "social.error": "That sign-in did not go through. Try again, or use your user name "
                    "and password.",
    "social.google": "Google",
    "social.facebook": "Facebook",
    "social.linked": "connected",
    "social.reauth": "confirm it's you",
    "social.link_google": "Connect Google",
    "social.link_facebook": "Connect Facebook",
    "social.linked_lead": "Sign in with:",
    "social.trial": "This account is on trial: you can prepare and sign paused campaigns; "
                    "the operator lifts the trial.",
    "social.no_password": "This account signs in with Google or Facebook. To add a "
                          "password, sign in with your provider again and come back here "
                          "within 15 minutes.",
    "social.first_password": "You just signed in with your provider, so you can set a "
                             "password now without a current one.",
    "account.done.linked": "Connected. You can sign in with it from now on.",
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
    "account.group.profile": "Profil i rozliczenia",
    "account.group.security": "Bezpieczeństwo",
    "account.group.assistants": "Asystenci i API",
    "account.group.notifications": "Powiadomienia",
    "account.group.data": "Twoje dane",
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
    "account.profile.billing_note": "Te dane przechowujesz tutaj; te, które trafiają na "
                                    "fakturę, Stripe pobiera przy płatności (Rozliczenia).",
    "account.profile.save": "Zapisz profil",
    "account.profile.invalid": "Sprawdź to pole: {field}.",
    "account.profile.online": "Twoja firma w sieci",
    "account.profile.online_lead": "Zapisujesz raz, korzysta z tego każdy nowy brief: studio "
                                   "wpisuje te dane, gdy szkic zostawi je puste. W każdym "
                                   "briefie możesz je zmienić.",
    "account.profile.website": "Strona internetowa",
    "account.profile.website_hint": "Studio czyta tę stronę przed szkicem, żeby podpowiedzieć "
                                    "fakty, które potwierdzasz.",
    "account.profile.sell_countries": "Kraje, w których sprzedajesz",
    "account.profile.sell_countries_hint": "Dwuliterowe kody krajów rozdzielone przecinkami, "
                                           "na przykład: PL, DE. Najwyżej 10.",
    "account.profile.pixel_id": "Pixel Meta",
    "account.profile.no_pixel": "Bez pixela",
    "account.profile.pixel_unnamed": "Pixel bez nazwy",
    "account.profile.pixel_saved": "Zapisany pixel",
    "account.profile.pixel_hint_html": 'Potrzebny w kampaniach, które liczą zapisy lub '
                                       'sprzedaż na Twojej stronie. Studio nie tworzy '
                                       'pixeli: <a href="' + PIXEL_HELP + '" '
                                       'rel="noopener noreferrer" target="_blank">utwórz go '
                                       'w Mecie</a>, potem wpisz tu jego numer.',
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
    "err.invalid_website": "Adres strony musi być pełny i zaczynać się od https://.",
    "err.invalid_countries": "Wpisz dwuliterowe kody krajów rozdzielone przecinkami, na "
                             "przykład PL, DE (najwyżej 10).",
    "err.invalid_pixel": "Numer pixela składa się tylko z cyfr (najwyżej 20).",
    "err.demo_account": "Wspólne konto jury nie może tego zrobić.",
    "err.demo_landing": "Na wspólnym koncie jury prawdziwa kampania może prowadzić "
                        "tylko na stronę studia; ustaw ją w briefie.",
    "err.demo_live_write": "Wspólne konto jury tworzy w Mecie prawdziwe kampanie, zawsze "
                           "wstrzymane. Włączenie, zmiana albo wpis na stronie wymagają "
                           "własnego konta właściciela.",
    "err.no_such_account": "Nie ma takiego konta.",
    "err.confirmation_mismatch": "Potwierdzenie nie zgadza się z Twoją nazwą użytkownika.",
    "err.two_factor_state": "Logowanie dwuetapowe w międzyczasie się zmieniło; odśwież stronę.",
    "err.billing_off": "Rozliczenia nie są włączone w tym studiu.",
    "err.invalid_event": "Nie udało się odczytać tego zdarzenia webhooka.",
    "err.invalid_form": "Nie udało się odczytać formularza.",
    "err.unknown_session": "Ta sesja nie jest już aktywna.",
    "err.second_factor_locked": "Za dużo błędnych kodów dla tego konta; odczekaj chwilę i "
                                "spróbuj ponownie.",
    "account.section.billing": "Rozliczenia",
    "account.done.billing": "Dziękujemy. Subskrypcja pojawi się tu, gdy Stripe potwierdzi "
                            "płatność, zwykle w ciągu minuty.",
    "billing.lead": "Płać co miesiąc, z góry za 3, 6 lub 12 miesięcy albo jednorazowo za "
                    "jeden miesiąc, kartą przez Stripe. Stripe wystawia fakturę za każdą "
                    "płatność; z planu zrezygnujesz w każdej chwili.",
    "billing.subscribe": "Przejdź do płatności",
    "billing.portal": "Karta, faktury i rezygnacja",
    "billing.status": "Subskrypcja",
    "billing.renews": "odnawia się",
    "billing.cancels": "kończy się",
    "billing.last_invoice": "Ostatnia faktura (Stripe)",
    "billing.test_mode": "Tryb testowy: żadne prawdziwe pieniądze nie przepływają. Zapłać "
                         "kartą testową 4242 4242 4242 4242, dowolną przyszłą datą i "
                         "dowolnym CVC.",
    "billing.error": "Rozliczenia są teraz niedostępne. Spróbuj za chwilę.",
    "billing.status.active": "aktywna",
    "billing.status.trialing": "okres próbny",
    "billing.status.past_due": "zaległa płatność",
    "billing.status.canceled": "zakończona",
    "billing.status.unpaid": "nieopłacona",
    "billing.status.incomplete": "czeka na płatność",
    "billing.status.incomplete_expired": "wygasła",
    "billing.status.paused": "wstrzymana",
    "social.heading": "Pierwszy raz? Zarejestruj się jednym krokiem",
    "social.lead": "Kontynuuj przez Google lub Facebooka, żeby się zalogować albo założyć "
                   "konto. Nowe konto zaczyna od okresu próbnego: kampanie powstają "
                   "wstrzymane, na stronie studia, dopóki operator go nie zniesie.",
    "social.continue_google": "Kontynuuj przez Google",
    "social.continue_facebook": "Kontynuuj przez Facebooka",
    "social.continue": "Przejdź do Muse Studio Lite",
    "social.error": "To logowanie się nie udało. Spróbuj ponownie albo użyj nazwy "
                    "użytkownika i hasła.",
    "social.google": "Google",
    "social.facebook": "Facebook",
    "social.linked": "połączone",
    "social.reauth": "potwierdź, że to Ty",
    "social.link_google": "Połącz z Google",
    "social.link_facebook": "Połącz z Facebookiem",
    "social.linked_lead": "Logowanie przez:",
    "social.trial": "To konto jest w okresie próbnym: przygotujesz i podpiszesz kampanie "
                    "wstrzymane; okres próbny znosi operator.",
    "social.no_password": "To konto loguje się przez Google lub Facebooka. Żeby dodać "
                          "hasło, zaloguj się ponownie przez dostawcę i wróć tu w ciągu "
                          "15 minut.",
    "social.first_password": "Właśnie zalogowałeś się przez dostawcę, więc możesz teraz "
                             "ustawić hasło bez podawania obecnego.",
    "account.done.linked": "Połączono. Od teraz możesz się tak logować.",
}
