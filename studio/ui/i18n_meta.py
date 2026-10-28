"""UI strings for Meta's objectives, lead ads, audiences, formats and measurement, in
English and Polish. Merged into ``i18n.EN`` / ``i18n.PL``; the same key and placeholder
rules apply (``tests/test_i18n.py``)."""

from __future__ import annotations

EN_META: dict[str, str] = {
    # Brief v1: objective and sub-choice
    "brief.goal.app_promotion": "app promotion",
    "brief.f.sub": "What Meta optimises for",
    "brief.h.sub": "Each objective has its own options; code maps your choice to Meta's "
                   "fields. Leave it on the first option if unsure.",
    "brief.sub.default": "the objective's default",
    "brief.sub.reach": "reach (most people)",
    "brief.sub.ad_recall_lift": "ad recall (people who remember the ad)",
    "brief.sub.link_clicks": "link clicks",
    "brief.sub.landing_page_views": "landing page views (the page loaded)",
    "brief.sub.post_engagement": "post engagement",
    "brief.sub.messenger": "messages on Messenger",
    "brief.sub.instagram_direct": "messages on Instagram",
    "brief.sub.whatsapp": "messages on WhatsApp",
    "brief.sub.event_responses": "event responses",
    "brief.sub.instant_form": "instant form on Facebook/Instagram",
    "brief.sub.website": "on your website (pixel)",
    "brief.sub.app_installs": "app installs",
    "brief.sub.catalog": "catalog products (pixel and catalog)",
    "brief.s.promotion": "Meta ids for this objective",
    "brief.promotion_hint": "Only the ids your objective needs; the form says which are "
                            "missing. The studio never guesses an id.",
    "brief.f.pixel_id": "Meta pixel id (digits)",
    "brief.f.custom_event_type": "Pixel event that counts",
    "brief.event.default": "the objective's default (Lead / Purchase)",
    "brief.f.application_id": "App id (digits)",
    "brief.f.object_store_url": "App store link (App Store or Google Play)",
    "brief.f.event_id": "Facebook event id (digits)",
    "brief.f.product_catalog_id": "Catalog id (digits)",
    "brief.f.product_set_id": "Product set id (digits)",
    "brief.f.privacy_policy_url": "Your privacy policy page (https)",
    "brief.h.privacy_policy_url": "Meta requires it for an instant form. It must be your own "
                                  "page; the studio adds no notices of its own.",
    "brief.f.lead_fields": "Questions in the instant form",
    "brief.lf.FULL_NAME": "full name", "brief.lf.EMAIL": "e-mail", "brief.lf.PHONE": "phone",
    "brief.lf.CITY": "city", "brief.lf.COMPANY_NAME": "company name",
    "js.brief_err_required_for_objective": "This objective needs this id. Type it in; the "
                                           "studio never guesses one.",
    "js.brief_err_bad_id": "Meta ids are digits only.",
    "js.brief_err_bad_store_url": "Use an https link to apps.apple.com or play.google.com.",
    "js.brief_err_bad_event": "This event does not fit the objective.",
    "js.brief_err_bad_sub": "This option does not belong to the chosen objective.",
    "js.brief_err_bad_lead_fields": "Pick each question once.",
    # approval screen
    "studio.ap.delivery": "Objective mapping (code, not a model)",
    "studio.ap.spec": "delivery sha256 (signed too)",
    "js.delivery": "{objective} · {goal}{destination} · promoted: {promoted}",
    "js.delivery_none": "none",
    "err.spec_changed": "The campaign's objective mapping is not the one you signed; "
                        "nothing was created.",
    # lead ads
    "leads.poll": "Fetch instant-form leads",
    "leads.poll_hint": "Reads new leads from the instant forms this studio created (no "
                       "write on Meta); each one is scored by the same rules.",
    "enum.lead_source.lead_form": "instant form",
    "js.polling": "Fetching leads from the instant forms…",
    "js.polling_failed": "Could not fetch leads: {error}",
    "js.polled": "{forms} forms read, {n} new leads.",
    "err.meta_error": "Meta did not answer the request; try again later.",
    "err.webhook_refused": "Webhook refused: unsigned, wrongly signed or not configured.",
    # audiences
    "brief.f.advantage": "Advantage+ audience (recommended by Meta)",
    "brief.h.advantage": "Meta may reach people beyond your hints when that works better. "
                         "Locations, the minimum age (18-25 here), languages and excluded "
                         "audiences stay fixed; ages, genders and interests become "
                         "suggestions.",
    "brief.f.genders": "Genders (none checked: everyone)",
    "brief.gender.female": "women", "brief.gender.male": "men",
    "brief.f.interests": "Interests (hints)",
    "brief.h.interests": "Plain words; the studio looks each one up in Meta's targeting "
                         "search and shows you the match before you sign.",
    "brief.f.excluded_interests": "Exclude people interested in",
    "brief.f.excluded_countries": "Exclude countries",
    "brief.f.custom_audience_ids": "Your custom audiences (ids)",
    "brief.h.audience_ids": "Only audiences you already have in Ads Manager; the studio "
                            "never uploads customer data.",
    "brief.f.excluded_custom_audience_ids": "Exclude your custom audiences (ids)",
    "brief.f.lookalike_source_id": "Build a lookalike from your audience (id)",
    "brief.h.lookalike": "Optional: one of your own audiences; Meta finds similar people in "
                         "your first country.",
    "brief.f.lookalike_percent": "Lookalike size (% of the country, 1-10)",
    "js.brief_err_excluded_sold": "You cannot exclude a country you sell in.",
    "js.brief_err_lookalike_percent": "Use a whole number from 1 to 10.",
    "studio.ap.audience": "Audience, as Meta will get it",
    "js.aud_interest": "interest “{hint}” → {name} ({id})",
    "js.aud_excluded": "excluded “{hint}” → {name} ({id})",
    "js.aud_locale": "language “{hint}” → {name} ({id})",
    "js.aud_unresolved": "“{hint}”: no match in Meta's targeting search, not used",
    "js.aud_advantage_on": "Advantage+ audience on: ages, genders and interests are "
                           "suggestions",
    "js.aud_advantage_off": "Advantage+ audience off: the brief's ages, genders and "
                            "interests are hard limits",
    "js.aud_lookalike": "lookalike of your audience {id}, {pct}% of the country",
}

PL_META: dict[str, str] = {
    "brief.goal.app_promotion": "promocja aplikacji",
    "brief.f.sub": "Na co Meta optymalizuje",
    "brief.h.sub": "Każdy cel ma własne opcje; kod zamienia twój wybór na pola Mety. Jeśli "
                   "nie wiesz, zostaw pierwszą opcję.",
    "brief.sub.default": "domyślnie dla celu",
    "brief.sub.reach": "zasięg (jak najwięcej osób)",
    "brief.sub.ad_recall_lift": "zapamiętanie reklamy",
    "brief.sub.link_clicks": "kliknięcia w link",
    "brief.sub.landing_page_views": "wyświetlenia strony docelowej (strona się załadowała)",
    "brief.sub.post_engagement": "zaangażowanie w post",
    "brief.sub.messenger": "wiadomości w Messengerze",
    "brief.sub.instagram_direct": "wiadomości na Instagramie",
    "brief.sub.whatsapp": "wiadomości na WhatsAppie",
    "brief.sub.event_responses": "odpowiedzi na wydarzenie",
    "brief.sub.instant_form": "formularz błyskawiczny na Facebooku/Instagramie",
    "brief.sub.website": "na twojej stronie (piksel)",
    "brief.sub.app_installs": "instalacje aplikacji",
    "brief.sub.catalog": "produkty z katalogu (piksel i katalog)",
    "brief.s.promotion": "Identyfikatory Mety dla tego celu",
    "brief.promotion_hint": "Tylko te, których wymaga twój cel; formularz pokaże, których "
                            "brakuje. Studio nigdy nie zgaduje identyfikatora.",
    "brief.f.pixel_id": "Identyfikator piksela Mety (cyfry)",
    "brief.f.custom_event_type": "Zdarzenie piksela, które się liczy",
    "brief.event.default": "domyślne dla celu (Lead / Purchase)",
    "brief.f.application_id": "Identyfikator aplikacji (cyfry)",
    "brief.f.object_store_url": "Link do sklepu z aplikacją (App Store albo Google Play)",
    "brief.f.event_id": "Identyfikator wydarzenia na Facebooku (cyfry)",
    "brief.f.product_catalog_id": "Identyfikator katalogu (cyfry)",
    "brief.f.product_set_id": "Identyfikator zestawu produktów (cyfry)",
    "brief.f.privacy_policy_url": "Twoja strona z polityką prywatności (https)",
    "brief.h.privacy_policy_url": "Meta wymaga jej przy formularzu błyskawicznym. Musi to "
                                  "być twoja strona; studio nie dodaje własnych klauzul.",
    "brief.f.lead_fields": "Pytania w formularzu błyskawicznym",
    "brief.lf.FULL_NAME": "imię i nazwisko", "brief.lf.EMAIL": "e-mail",
    "brief.lf.PHONE": "telefon", "brief.lf.CITY": "miasto",
    "brief.lf.COMPANY_NAME": "nazwa firmy",
    "js.brief_err_required_for_objective": "Ten cel wymaga tego identyfikatora. Wpisz go; "
                                           "studio nigdy go nie zgaduje.",
    "js.brief_err_bad_id": "Identyfikatory Mety składają się tylko z cyfr.",
    "js.brief_err_bad_store_url": "Podaj link https do apps.apple.com albo play.google.com.",
    "js.brief_err_bad_event": "To zdarzenie nie pasuje do celu.",
    "js.brief_err_bad_sub": "Ta opcja nie należy do wybranego celu.",
    "js.brief_err_bad_lead_fields": "Każde pytanie wybierz tylko raz.",
    "studio.ap.delivery": "Mapowanie celu (kod, nie model)",
    "studio.ap.spec": "sha256 ustawień emisji (też podpisywane)",
    "js.delivery": "{objective} · {goal}{destination} · promowane: {promoted}",
    "js.delivery_none": "nic",
    "err.spec_changed": "Mapowanie celu kampanii nie jest tym, które podpisano; nic nie "
                        "utworzono.",
    "leads.poll": "Pobierz leady z formularzy błyskawicznych",
    "leads.poll_hint": "Czyta nowe leady z formularzy utworzonych przez studio (bez zapisu "
                       "w Mecie); każdy jest oceniany tymi samymi regułami.",
    "enum.lead_source.lead_form": "formularz błyskawiczny",
    "js.polling": "Pobieram leady z formularzy błyskawicznych…",
    "js.polling_failed": "Nie udało się pobrać leadów: {error}",
    "js.polled": "Odczytane formularze: {forms}, nowe leady: {n}.",
    "err.meta_error": "Meta nie odpowiedziała; spróbuj później.",
    "err.webhook_refused": "Webhook odrzucony: bez podpisu, ze złym podpisem albo "
                           "nieskonfigurowany.",
    "brief.f.advantage": "Grupa odbiorców Advantage+ (zalecana przez Metę)",
    "brief.h.advantage": "Meta może dotrzeć poza twoje podpowiedzi, gdy to działa lepiej. "
                         "Lokalizacje, minimalny wiek (tu 18-25), języki i wykluczone grupy "
                         "zostają sztywne; wiek, płeć i zainteresowania stają się "
                         "podpowiedziami.",
    "brief.f.genders": "Płeć (nic nie zaznaczone: wszyscy)",
    "brief.gender.female": "kobiety", "brief.gender.male": "mężczyźni",
    "brief.f.interests": "Zainteresowania (podpowiedzi)",
    "brief.h.interests": "Zwykłe słowa; studio sprawdza każde w wyszukiwarce targetowania "
                         "Mety i pokazuje dopasowanie przed podpisem.",
    "brief.f.excluded_interests": "Wyklucz osoby zainteresowane",
    "brief.f.excluded_countries": "Wyklucz kraje",
    "brief.f.custom_audience_ids": "Twoje niestandardowe grupy odbiorców (identyfikatory)",
    "brief.h.audience_ids": "Tylko grupy, które już masz w Menedżerze reklam; studio nigdy "
                            "nie wysyła danych klientów.",
    "brief.f.excluded_custom_audience_ids": "Wyklucz twoje grupy odbiorców "
                                            "(identyfikatory)",
    "brief.f.lookalike_source_id": "Zbuduj podobnych odbiorców z twojej grupy "
                                   "(identyfikator)",
    "brief.h.lookalike": "Opcjonalnie: jedna z twoich grup; Meta znajdzie podobne osoby w "
                         "pierwszym z twoich krajów.",
    "brief.f.lookalike_percent": "Wielkość grupy podobnych (% kraju, 1-10)",
    "js.brief_err_excluded_sold": "Nie można wykluczyć kraju, w którym sprzedajesz.",
    "js.brief_err_lookalike_percent": "Podaj liczbę całkowitą od 1 do 10.",
    "studio.ap.audience": "Grupa odbiorców, tak jak dostanie ją Meta",
    "js.aud_interest": "zainteresowanie „{hint}” → {name} ({id})",
    "js.aud_excluded": "wykluczone „{hint}” → {name} ({id})",
    "js.aud_locale": "język „{hint}” → {name} ({id})",
    "js.aud_unresolved": "„{hint}”: brak dopasowania w wyszukiwarce targetowania Mety, "
                         "nieużyte",
    "js.aud_advantage_on": "Advantage+ włączone: wiek, płeć i zainteresowania to "
                           "podpowiedzi",
    "js.aud_advantage_off": "Advantage+ wyłączone: wiek, płeć i zainteresowania z briefu "
                            "są sztywne",
    "js.aud_lookalike": "podobni do twojej grupy {id}, {pct}% kraju",
}
