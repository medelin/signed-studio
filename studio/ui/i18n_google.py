"""UI strings for the Google Search channel, in English and Polish. Merged into
``i18n.EN`` / ``i18n.PL``; the same key and placeholder rules apply
(``tests/test_i18n.py``)."""

from __future__ import annotations

EN_GOOGLE: dict[str, str] = {
    # brief form
    "brief.s.channels": "Channels",
    "brief.f.google_search": "Also run on Google Search",
    "brief.h.google_search": "Meta stays the main channel. This adds one more approval for a "
                             "single Google Search campaign made from this same brief and the "
                             "reviewed copy: one ad group, keywords from your product and "
                             "places, one text ad, your countries. It is created paused and "
                             "starts only after a second signature.",
    "brief.f.google_budget": "Google daily budget (in the Google account's currency)",
    "brief.h.google_budget": "Separate from the Meta budget above. The same daily ceiling "
                             "applies.",
    "js.brief_err_google_account_currency": "The Google Ads account books budgets in "
                                            "{google_currency}, not in the brief's currency; "
                                            "use {google_currency} or untick Google Search.",
    "js.brief_err_google_geo": "Google Search cannot target one of these countries here.",
    # studio page: the Google approval
    "studio.google": "Google Search",
    "studio.google_lead": "A second approval for the same brief: one Search campaign, "
                          "created paused. Read the text ad and the keywords, then sign.",
    "studio.gg.name": "Campaign",
    "studio.gg.budget": "Daily budget",
    "studio.gg.geo": "Countries",
    "studio.gg.keywords": "Keywords",
    "studio.gg.headlines": "Headlines (at most 30 characters)",
    "studio.gg.descriptions": "Descriptions (at most 90 characters)",
    "studio.gg.spec": "Spec hash (signed)",
    "studio.google_button": "Sign and create the paused campaign",
    "studio.google_activate": "Activate the Google campaign (needs a second signature)",
    "js.g_budget": "{budget} {currency} a day",
    "js.g_error": "Google Search was not offered: {error}",
    "js.g_created": "Created paused via the {adapter} adapter (signature {sig}…).",
    "js.g_failed": "Signed, but not created: {error}",
    "js.g_signing_activation": "Signing activation of {budget} {currency}/day…",
    "js.g_activated": "Activated via the {adapter} adapter (signature {sig}…).",
    "err.google_error": "Google Ads did not answer the request; try again later.",
    "err.currency_changed": "The ad account books budgets in another currency than the one "
                            "signed; nothing was created.",
    "err.google_spec": "The Google campaign spec is not valid; nothing was created.",
    "err.google_geo": "Google Search cannot target one of the countries.",
    "err.google_headlines": "Headlines must be 3 to 15, at most 30 characters each.",
    "err.google_descriptions": "Descriptions must be 2 to 4, at most 90 characters each.",
    "err.google_keywords": "A keyword is not valid for Google Search.",
    "err.google_url": "The final URL must be https.",
    "err.google_account": "The spec names another Google Ads account than this studio's.",
    "err.over_ceiling": "The daily budget is above this studio's ceiling.",
    # /report
    "report.google": "Google Search",
    "report.google_lead": "The last seven days of each Search campaign, read from Google "
                          "Ads by day.",
    "report.google_synthetic": "demo fixture, not Google data",
    "report.g.date": "Day", "report.g.impressions": "Impressions", "report.g.clicks": "Clicks",
    "report.g.cost": "Cost", "report.g.conversions": "Conversions", "report.g.ctr": "CTR",
    "report.g.total": "Total",
}

PL_GOOGLE: dict[str, str] = {
    "brief.s.channels": "Kanały",
    "brief.f.google_search": "Pokaż też w Google (wyszukiwarka)",
    "brief.h.google_search": "Meta zostaje głównym kanałem. To dodaje jeszcze jedno "
                             "zatwierdzenie dla jednej kampanii w wyszukiwarce Google, "
                             "zrobionej z tego samego briefu i sprawdzonych tekstów: jedna "
                             "grupa reklam, słowa kluczowe z twojego produktu i miejsc, jedna "
                             "reklama tekstowa, twoje kraje. Powstaje wstrzymana i rusza "
                             "dopiero po drugim podpisie.",
    "brief.f.google_budget": "Dzienny budżet w Google (w walucie konta Google)",
    "brief.h.google_budget": "Osobny od budżetu Mety powyżej. Obowiązuje ten sam dzienny "
                             "limit.",
    "js.brief_err_google_account_currency": "Konto Google Ads rozlicza budżety w "
                                            "{google_currency}, a nie w walucie briefu; "
                                            "użyj {google_currency} albo odznacz "
                                            "wyszukiwarkę Google.",
    "js.brief_err_google_geo": "Wyszukiwarka Google nie obsługuje tu jednego z tych krajów.",
    "studio.google": "Wyszukiwarka Google",
    "studio.google_lead": "Drugie zatwierdzenie dla tego samego briefu: jedna kampania w "
                          "wyszukiwarce, utworzona wstrzymana. Przeczytaj reklamę tekstową i "
                          "słowa kluczowe, potem podpisz.",
    "studio.gg.name": "Kampania",
    "studio.gg.budget": "Budżet dzienny",
    "studio.gg.geo": "Kraje",
    "studio.gg.keywords": "Słowa kluczowe",
    "studio.gg.headlines": "Nagłówki (do 30 znaków)",
    "studio.gg.descriptions": "Opisy (do 90 znaków)",
    "studio.gg.spec": "Skrót specyfikacji (podpisany)",
    "studio.google_button": "Podpisz i utwórz wstrzymaną kampanię",
    "studio.google_activate": "Włącz kampanię w Google (wymaga drugiego podpisu)",
    "js.g_budget": "{budget} {currency} dziennie",
    "js.g_error": "Wyszukiwarka Google nie została zaproponowana: {error}",
    "js.g_created": "Utworzono wstrzymaną przez adapter {adapter} (podpis {sig}…).",
    "js.g_failed": "Podpisano, ale nie utworzono: {error}",
    "js.g_signing_activation": "Podpisywanie włączenia {budget} {currency}/dzień…",
    "js.g_activated": "Włączono przez adapter {adapter} (podpis {sig}…).",
    "err.google_error": "Google Ads nie odpowiedziało; spróbuj później.",
    "err.currency_changed": "Konto reklamowe rozlicza budżety w innej walucie niż "
                            "podpisana; nic nie utworzono.",
    "err.google_spec": "Specyfikacja kampanii Google jest nieprawidłowa; nic nie utworzono.",
    "err.google_geo": "Wyszukiwarka Google nie obsługuje jednego z krajów.",
    "err.google_headlines": "Nagłówków ma być od 3 do 15, każdy do 30 znaków.",
    "err.google_descriptions": "Opisów ma być od 2 do 4, każdy do 90 znaków.",
    "err.google_keywords": "Słowo kluczowe nie jest dozwolone w wyszukiwarce Google.",
    "err.google_url": "Adres docelowy musi zaczynać się od https.",
    "err.google_account": "Specyfikacja wskazuje inne konto Google Ads niż to studio.",
    "err.over_ceiling": "Budżet dzienny przekracza limit tego studia.",
    "report.google": "Wyszukiwarka Google",
    "report.google_lead": "Ostatnie siedem dni każdej kampanii w wyszukiwarce, odczytane z "
                          "Google Ads dzień po dniu.",
    "report.google_synthetic": "dane demonstracyjne, nie z Google",
    "report.g.date": "Dzień", "report.g.impressions": "Wyświetlenia",
    "report.g.clicks": "Kliknięcia", "report.g.cost": "Koszt",
    "report.g.conversions": "Konwersje", "report.g.ctr": "CTR", "report.g.total": "Razem",
}
