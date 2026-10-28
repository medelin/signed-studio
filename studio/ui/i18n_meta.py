"""UI strings for Meta's objectives, lead ads, audiences, formats and measurement, in
English and Polish. Merged into ``i18n.EN`` / ``i18n.PL``; the same key and placeholder
rules apply (``tests/test_i18n.py``)."""

from __future__ import annotations

EN_META: dict[str, str] = {
    "studio.brief_example_muse": (
        "Muse Studio Lite is a free, open-source tool for one-person "
        "businesses. From one brief it makes a campaign plan, 3 copy "
        "variants, images and 2 independent compliance reviews, and "
        "nothing reaches the ads without your signature. The code is open, "
        "under the Apache 2.0 licence. We want traffic to the sandbox at "
        "https://muse.udun.eu. For people running a one-person business in "
        "Poland, aged 25-55. Budget 6 GBP a day for 7 days, from 4 to 10 "
        "November 2026. The ad in Polish. Matter-of-fact and calm, no "
        "promises of profit. Do not name any company."),
    "studio.example_label": "Example brief",
    "studio.example_hint": "Two examples to start from; both are made-up owners' words you can "
                           "edit.",
    "studio.example.ceramics": "a ceramics shop (PLN)",
    "studio.example.muse": "this studio, advertised as an open-source tool (GBP)",
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
    # formats
    "brief.f.carousel_cards": "Carousel cards (0 = no carousel, else 2-10)",
    "brief.h.carousel": "Each card is one more Muse Image render, reviewed by both "
                        "reviewers; about $0.01 per card plus two reviews.",
    "brief.f.video": "Also make a 9:16 video ad",
    "brief.h.video": "Code cuts a few-second video from the rendered images; Muse Wonder "
                     "orders the shots. Its first frame is reviewed like every image.",
    "brief.f.copy_variants": "Copy variants to test against each other (1-3)",
    "brief.h.copy_variants": "More than one: every variant is reviewed and runs, as "
                             "dynamic creative where Meta supports it for the objective, "
                             "otherwise as separate ads in one ad set.",
    "js.brief_err_carousel_cards": "Use 0, or 2 to 10 cards.",
    "js.brief_err_copy_variants": "Use 1, 2 or 3.",
    "js.brief_err_format_not_for_objective": "Event and catalog ads take a single image "
                                             "and one copy variant.",
    "js.video_alt": "9:16 video ad, {s} seconds",
    "js.video_caption": "video_9x16 · {s} s · shots ordered by {model}",
    # measurement (/report)
    "ms.title": "Measurement by objective",
    "ms.lead": "Insights for each campaign's own objective: its result, cost per result, "
               "reach and frequency, by placement, age and gender, and region, with the "
               "attribution setting. Every snapshot is a receipt. A recommendation changes "
               "nothing until you sign it.",
    "ms.pull": "Read insights now", "ms.empty": "No snapshot yet.",
    "ms.result": "Result", "ms.cpr": "Cost per result", "ms.reach": "Reach",
    "ms.frequency": "frequency", "ms.spend": "Spend", "ms.link_clicks": "link clicks",
    "ms.attribution": "Attribution", "ms.segment": "Segment",
    "ms.by.placement": "By placement (platform · position)",
    "ms.by.age_gender": "By age and gender", "ms.by.region": "By region",
    "ms.recs": "Recommendations (code rules)", "ms.no_recs": "Nothing to change.",
    "ms.rec.placement_costly": "{placement} costs {ratio}× the campaign's cost per result "
                               "and takes {share} % of the spend. Advantage+ placements "
                               "shift budget away by themselves; to exclude it, choose "
                               "manual placements in the next brief.",
    "ms.rec.no_results": "{spend} spent with no result yet: pause the campaign and check "
                         "the objective and the audience.",
    "ms.rec.fatigue": "People saw the ad {frequency} times on average: lower the daily "
                      "budget so it tires less.",
    "ms.rec.scale": "{results} results at a frequency of {frequency}: there is room to "
                    "raise the daily budget.",
    "ms.change.budget": "daily budget {frm} → {to} {currency}",
    "ms.change.pause": "pause the campaign",
    "ms.execute": "Sign and execute",
    "enum.rec_status.open": "open", "enum.rec_status.requested": "waiting for signature",
    "enum.rec_status.executed": "executed", "enum.rec_status.failed": "failed",
    "js.ms_pulling": "Reading insights for every campaign…",
    "js.ms_pull_failed": "Could not read insights: {error}",
    "js.ms_signing": "Preparing the approval and signing it in this browser…",
    "js.ms_refused": "Not executable: {error}",
    "js.ms_not_executed": "Signed, not executed: {error}",
    "js.ms_executed": "Signed and executed (signature {sig}…).",
    "err.not_executable": "This recommendation is information only or already handled.",
    "err.campaign_changed": "The campaign changed since this was proposed; read insights "
                            "again.",
}

PL_META: dict[str, str] = {
    "studio.brief_example_muse": (
        "Muse Studio Lite to darmowe narzędzie open source dla "
        "jednoosobowych firm. Z jednego briefu robi plan kampanii, 3 "
        "warianty tekstu, grafiki i 2 niezależne recenzje zgodności, a "
        "nic nie trafia do reklam bez twojego podpisu. Kod jest otwarty, na"
        " licencji Apache 2.0. Chcemy ruchu do piaskownicy na "
        "https://muse.udun.eu. Dla osób prowadzących jednoosobową "
        "działalność w Polsce, 25-55 lat. Budżet 6 GBP dziennie przez 7 "
        "dni, od 4 do 10 listopada 2026. Rzeczowo i spokojnie, bez obietnic"
        " zysku. Reklama po polsku. Nie wymieniajcie nazwy żadnej firmy."),
    "studio.example_label": "Przykładowy brief",
    "studio.example_hint": "Dwa przykłady na start; oba to wymyślone słowa właściciela, które "
                           "możesz zmienić.",
    "studio.example.ceramics": "sklep z ceramiką (PLN)",
    "studio.example.muse": "to studio jako narzędzie open source (GBP)",
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
    "brief.f.carousel_cards": "Karty karuzeli (0 = bez karuzeli, inaczej 2-10)",
    "brief.h.carousel": "Każda karta to jeszcze jeden obraz Muse Image, sprawdzany przez "
                        "obu recenzentów; około 0,01 $ za kartę plus dwie recenzje.",
    "brief.f.video": "Zrób też reklamę wideo 9:16",
    "brief.h.video": "Kod składa kilkusekundowe wideo z wygenerowanych obrazów; kolejność "
                     "ujęć ustala Muse Wonder. Pierwsza klatka jest sprawdzana jak każdy "
                     "obraz.",
    "brief.f.copy_variants": "Warianty tekstu do porównania (1-3)",
    "brief.h.copy_variants": "Więcej niż jeden: każdy wariant jest sprawdzany i emitowany, "
                             "jako kreacja dynamiczna, gdy Meta ją wspiera dla celu, "
                             "inaczej jako osobne reklamy w jednym zestawie.",
    "js.brief_err_carousel_cards": "Podaj 0 albo od 2 do 10 kart.",
    "js.brief_err_copy_variants": "Podaj 1, 2 albo 3.",
    "js.brief_err_format_not_for_objective": "Reklamy wydarzeń i katalogu mają jeden obraz "
                                             "i jeden wariant tekstu.",
    "js.video_alt": "Reklama wideo 9:16, {s} s",
    "js.video_caption": "video_9x16 · {s} s · kolejność ujęć: {model}",
    "ms.title": "Pomiar według celu",
    "ms.lead": "Statystyki dla celu każdej kampanii: jej wynik, koszt wyniku, zasięg i "
               "częstotliwość, według umiejscowienia, wieku i płci oraz regionu, z modelem "
               "atrybucji. Każdy odczyt to pokwitowanie. Rekomendacja niczego nie zmienia, "
               "dopóki jej nie podpiszesz.",
    "ms.pull": "Odczytaj statystyki teraz", "ms.empty": "Brak odczytu.",
    "ms.result": "Wynik", "ms.cpr": "Koszt wyniku", "ms.reach": "Zasięg",
    "ms.frequency": "częstotliwość", "ms.spend": "Wydatki", "ms.link_clicks": "kliknięcia w link",
    "ms.attribution": "Atrybucja", "ms.segment": "Segment",
    "ms.by.placement": "Według umiejscowienia (platforma · pozycja)",
    "ms.by.age_gender": "Według wieku i płci", "ms.by.region": "Według regionu",
    "ms.recs": "Rekomendacje (reguły w kodzie)", "ms.no_recs": "Nic do zmiany.",
    "ms.rec.placement_costly": "{placement} kosztuje {ratio}× koszt wyniku kampanii i bierze "
                               "{share} % wydatków. Umiejscowienia Advantage+ same "
                               "przesuwają budżet; żeby je wykluczyć, wybierz ręczne "
                               "umiejscowienia w następnym briefie.",
    "ms.rec.no_results": "Wydano {spend} bez żadnego wyniku: wstrzymaj kampanię i sprawdź "
                         "cel i grupę odbiorców.",
    "ms.rec.fatigue": "Ludzie widzieli reklamę średnio {frequency} razy: obniż budżet "
                      "dzienny, żeby mniej się opatrzyła.",
    "ms.rec.scale": "{results} wyników przy częstotliwości {frequency}: można podnieść "
                    "budżet dzienny.",
    "ms.change.budget": "budżet dzienny {frm} → {to} {currency}",
    "ms.change.pause": "wstrzymaj kampanię",
    "ms.execute": "Podpisz i wykonaj",
    "enum.rec_status.open": "otwarta", "enum.rec_status.requested": "czeka na podpis",
    "enum.rec_status.executed": "wykonana", "enum.rec_status.failed": "nieudana",
    "js.ms_pulling": "Odczytuję statystyki wszystkich kampanii…",
    "js.ms_pull_failed": "Nie udało się odczytać statystyk: {error}",
    "js.ms_signing": "Przygotowuję zatwierdzenie i podpisuję je w tej przeglądarce…",
    "js.ms_refused": "Nie do wykonania: {error}",
    "js.ms_not_executed": "Podpisano, nie wykonano: {error}",
    "js.ms_executed": "Podpisano i wykonano (podpis {sig}…).",
    "err.not_executable": "Ta rekomendacja jest tylko informacją albo już ją obsłużono.",
    "err.campaign_changed": "Kampania zmieniła się od tej propozycji; odczytaj statystyki "
                            "ponownie.",
}
