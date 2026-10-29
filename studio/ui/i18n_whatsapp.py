"""WhatsApp: the account settings, the replies to a brief sent from WhatsApp and the
e-mail of the ``ad.disapproved`` hook, English and Polish (``studio.whatsapp``).

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key. A reply carries counts and a link, never the brief's text.
"""

from __future__ import annotations

EN_WHATSAPP: dict[str, str] = {
    "account.whatsapp.legend": "WhatsApp",
    "account.whatsapp.hint": "Get the same notices on WhatsApp: an approval waiting (with a "
                             "link to sign it here), new leads, a disapproved ad. Send a "
                             "voice note to the studio's number and it becomes a brief "
                             "draft. Nothing is ever signed or run from WhatsApp.",
    "account.whatsapp.off": "WhatsApp is not configured on this server yet.",
    "account.whatsapp.number": "Your WhatsApp number (international format, e.g. +48…)",
    "account.whatsapp.on": "Send my notifications on WhatsApp too",
    "account.whatsapp.invalid": "Enter the number in international format, starting with + "
                                "and the country code (e.g. +48 600 000 000); it is needed "
                                "to turn WhatsApp on.",
    "whatsapp.reply.ready": "Brief ready: {facts} facts, {questions} questions — open "
                            "{link}",
    "whatsapp.reply.failed": "Muse Studio Lite could not make a brief from this message "
                             "({code}). Try again, or type the brief in the studio: {link}",
    "notify.ad_disapproved.subject": "Meta did not approve one of your ads",
    "notify.ad_disapproved.body": "Meta did not approve one of your ads. Open the studio to "
                                  "see the reason and the suggested fix.",
}

PL_WHATSAPP: dict[str, str] = {
    "account.whatsapp.legend": "WhatsApp",
    "account.whatsapp.hint": "Otrzymuj te same powiadomienia na WhatsAppie: akceptacja czeka "
                             "(z linkiem do podpisu tutaj), nowe leady, odrzucona reklama. "
                             "Wyślij notatkę głosową na numer studia, a stanie się szkicem "
                             "briefu. Z WhatsAppa nic nie jest podpisywane ani uruchamiane.",
    "account.whatsapp.off": "WhatsApp nie jest jeszcze skonfigurowany na tym serwerze.",
    "account.whatsapp.number": "Twój numer WhatsApp (format międzynarodowy, np. +48…)",
    "account.whatsapp.on": "Wysyłaj moje powiadomienia także na WhatsAppie",
    "account.whatsapp.invalid": "Podaj numer w formacie międzynarodowym, zaczynając od + i "
                                "kodu kraju (np. +48 600 000 000); jest potrzebny, by "
                                "włączyć WhatsAppa.",
    "whatsapp.reply.ready": "Brief gotowy — fakty: {facts}, pytania: {questions}. Otwórz: "
                            "{link}",
    "whatsapp.reply.failed": "Muse Studio Lite nie zrobiło briefu z tej wiadomości ({code}). "
                             "Spróbuj ponownie albo wpisz brief w studiu: {link}",
    "notify.ad_disapproved.subject": "Meta nie zatwierdziła jednej z Twoich reklam",
    "notify.ad_disapproved.body": "Meta nie zatwierdziła jednej z Twoich reklam. Otwórz "
                                  "studio, aby zobaczyć powód i proponowaną poprawkę.",
}
