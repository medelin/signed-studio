"""Subjects and bodies of the notification e-mails (``studio.notify``), English and Polish.

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key. Bodies never carry a lead's name or contact: only counts and a link.
"""

from __future__ import annotations

EN_NOTIFY: dict[str, str] = {
    "notify.open": "Open it here:",
    "notify.footer": "You get this because notifications are on for your Muse Studio Lite "
                     "account. Choose what you receive:",
    "notify.footer_security": "This is a security notice about your Muse Studio Lite account "
                              "and cannot be turned off. If it was not you, change your "
                              "password and sign out the other sessions.",
    "notify.approval_waiting.subject": "Something is waiting for your signature",
    "notify.approval_waiting.body": "An approval is waiting for you. Nothing is sent or "
                                    "spent until you sign it.",
    "notify.campaign_created.subject": "Your campaign was created, paused",
    "notify.campaign_created.body": "The campaign you signed was created on the ad account "
                                    "and is PAUSED. It will not spend anything until you "
                                    "sign its activation.",
    "notify.campaign_launched.subject": "Your campaign is live",
    "notify.campaign_launched.body": "The campaign you signed to activate is now running.",
    "notify.campaign_paused.subject": "Your campaign was paused",
    "notify.campaign_paused.body": "A campaign was paused after you signed a "
                                   "recommendation. It stopped spending.",
    "notify.lead_new.subject": "New leads",
    "notify.lead_new.body": "New leads arrived: {count}. Open the Leads page to see and "
                            "score them.",
    "notify.insights_ready.subject": "New recommendations for your campaigns",
    "notify.insights_ready.body": "New recommendations from the latest insights: {count}. "
                                  "A recommendation changes nothing until you sign it.",
    "notify.password_changed.subject": "Your password was changed",
    "notify.password_changed.body": "The password of your account was just changed and your "
                                    "other sessions were signed out.",
    "notify.two_factor_on.subject": "Two-step sign-in is on",
    "notify.two_factor_on.body": "Two-step sign-in was turned on for your account.",
    "notify.two_factor_off.subject": "Two-step sign-in was turned off",
    "notify.two_factor_off.body": "Two-step sign-in was turned off for your account.",
    "notify.signin.subject": "New sign-in to your account",
    "notify.signin.body": "Your account was just signed in to from a new session. You can "
                          "see and sign out your sessions on the account page.",
    "notify.deletion_requested.subject": "Your account was deleted",
    "notify.deletion_requested.body": "Deletion of your account was confirmed with your "
                                      "password. Your profile, sessions and keys were "
                                      "erased; receipts stay, without your identity.",
    "notify.export_ready.subject": "Your data export was downloaded",
    "notify.export_ready.body": "A JSON export of your account data was just downloaded.",
    # the account page
    "account.section.notifications": "Notifications",
    "account.notify.lead": "E-mails are sent to the address in your "
                           "profile. They hold counts and links only, never a lead's name or "
                           "contact.",
    "account.notify.no_email": "Add an e-mail address to your profile to receive "
                               "notifications.",
    "account.notify.campaigns": "Campaigns: approvals waiting, a campaign created, launched "
                                "or paused, new recommendations",
    "account.notify.leads": "Leads: new leads arrived (at most one e-mail per ten minutes)",
    "account.notify.security": "Security: password changed, two-step sign-in on or off, a "
                               "new sign-in, export, account deletion (always on)",
    "account.notify.save": "Save notification settings",
    "account.done.notify": "Notification settings saved.",
}

PL_NOTIFY: dict[str, str] = {
    "notify.open": "Otwórz tutaj:",
    "notify.footer": "Dostajesz to, bo powiadomienia są włączone na Twoim koncie Muse Studio "
                     "Lite. Wybierz, co chcesz dostawać:",
    "notify.footer_security": "To powiadomienie o bezpieczeństwie Twojego konta Muse Studio "
                              "Lite i nie da się go wyłączyć. Jeśli to nie Ty, zmień hasło "
                              "i wyloguj pozostałe sesje.",
    "notify.approval_waiting.subject": "Coś czeka na Twój podpis",
    "notify.approval_waiting.body": "Zatwierdzenie czeka na Ciebie. Nic nie zostanie wysłane "
                                    "ani wydane, dopóki go nie podpiszesz.",
    "notify.campaign_created.subject": "Kampania została utworzona, wstrzymana",
    "notify.campaign_created.body": "Kampania podpisana przez Ciebie została utworzona na koncie "
                                    "reklamowym i jest WSTRZYMANA. Nic nie wyda, dopóki nie "
                                    "podpiszesz jej uruchomienia.",
    "notify.campaign_launched.subject": "Kampania działa",
    "notify.campaign_launched.body": "Kampania, której uruchomienie podpisano, już działa.",
    "notify.campaign_paused.subject": "Kampania została wstrzymana",
    "notify.campaign_paused.body": "Kampania została wstrzymana po podpisaniu rekomendacji. "
                                   "Przestała wydawać budżet.",
    "notify.lead_new.subject": "Nowe leady",
    "notify.lead_new.body": "Pojawiły się nowe leady: {count}. Otwórz stronę Leady, aby je "
                            "zobaczyć i ocenić.",
    "notify.insights_ready.subject": "Nowe rekomendacje dla Twoich kampanii",
    "notify.insights_ready.body": "Nowe rekomendacje z ostatnich statystyk: {count}. "
                                  "Rekomendacja nic nie zmienia, dopóki jej nie podpiszesz.",
    "notify.password_changed.subject": "Hasło zostało zmienione",
    "notify.password_changed.body": "Hasło Twojego konta właśnie zmieniono, a pozostałe "
                                    "sesje zostały wylogowane.",
    "notify.two_factor_on.subject": "Logowanie dwuetapowe jest włączone",
    "notify.two_factor_on.body": "Na Twoim koncie włączono logowanie dwuetapowe.",
    "notify.two_factor_off.subject": "Logowanie dwuetapowe zostało wyłączone",
    "notify.two_factor_off.body": "Na Twoim koncie wyłączono logowanie dwuetapowe.",
    "notify.signin.subject": "Nowe logowanie na Twoje konto",
    "notify.signin.body": "Ktoś właśnie zalogował się na Twoje konto w nowej sesji. Sesje "
                          "zobaczysz i wylogujesz na stronie konta.",
    "notify.deletion_requested.subject": "Konto zostało usunięte",
    "notify.deletion_requested.body": "Usunięcie konta potwierdzono hasłem. Profil, sesje i "
                                      "klucze zostały skasowane; pokwitowania zostają, bez "
                                      "Twojej tożsamości.",
    "notify.export_ready.subject": "Eksport danych został pobrany",
    "notify.export_ready.body": "Właśnie pobrano eksport JSON danych Twojego konta.",
    # the account page
    "account.section.notifications": "Powiadomienia",
    "account.notify.lead": "E-maile przychodzą na adres z Twojego profilu. "
                           "Zawierają tylko liczby i linki, nigdy imienia ani kontaktu leada.",
    "account.notify.no_email": "Dodaj adres e-mail do profilu, aby dostawać powiadomienia.",
    "account.notify.campaigns": "Kampanie: zatwierdzenia czekające na podpis, kampania "
                                "utworzona, uruchomiona lub wstrzymana, nowe rekomendacje",
    "account.notify.leads": "Leady: pojawiły się nowe leady (najwyżej jeden e-mail na dziesięć "
                            "minut)",
    "account.notify.security": "Bezpieczeństwo: zmiana hasła, logowanie dwuetapowe włączone "
                               "lub wyłączone, nowe logowanie, eksport, usunięcie konta "
                               "(zawsze włączone)",
    "account.notify.save": "Zapisz ustawienia powiadomień",
    "account.done.notify": "Ustawienia powiadomień zapisane.",
}
