"""The pixel check on the approval screen (``studio.meta.pixel``), English and Polish.

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key. ``js.pixel_c_<code>`` is the line of one check entry; the script falls
back to the entry's English ``detail`` for a code it has no line for.
"""

from __future__ import annotations

EN_PIXEL: dict[str, str] = {
    "studio.ap.pixel": "Meta pixel check (read-only)",
    "js.pixel_ok": "pixel OK",
    "js.pixel_warn": "pixel: check the warnings",
    "js.pixel_fail": "pixel check failed: this cannot be signed",
    "js.pixel_head": "pixel {id} · last event {fired} · {events} events in 7 days",
    "js.pixel_never": "never",
    "js.pixel_unknown": "unknown",
    "js.pixel_c_pixel_found": "the pixel exists and is available",
    "js.pixel_c_pixel_not_found": "the pixel does not exist or this account cannot see it",
    "js.pixel_c_pixel_unavailable": "Meta marks the pixel as unavailable",
    "js.pixel_c_pixel_unverified": "the pixel could not be checked; run again later",
    "js.pixel_c_account_ok": "the ad account can use the pixel",
    "js.pixel_c_pixel_not_in_account": "the pixel is not among the ad account's pixels; "
                                       "share it with the ad account in Events Manager",
    "js.pixel_c_account_unknown": "the ad account's pixels could not be listed",
    "js.pixel_c_fired_recently": "the pixel sent an event in the last 7 days",
    "js.pixel_c_no_recent_events": "no event in the last 7 days",
    "js.pixel_c_never_fired": "the pixel has never sent an event",
    "js.pixel_c_fired_unknown": "the time of the pixel's last event is unreadable",
    "js.pixel_c_events_7d": "events were counted in the last 7 days",
    "js.pixel_c_no_events_7d": "no events counted in the last 7 days",
    "js.pixel_c_stats_unknown": "event counts for the last 7 days are unknown",
    "js.pixel_c_event_seen": "the event this campaign optimises for was seen",
    "js.pixel_c_event_not_seen": "the event this campaign optimises for was not seen in 7 "
                                 "days",
    "js.pixel_c_landing_found": "the pixel id is in the landing page's HTML",
    "js.pixel_c_landing_not_found": "the pixel id is not in the landing page's HTML (it may "
                                    "be loaded by a tag manager)",
    "js.pixel_c_landing_refused": "the landing page was not fetched (not a public https "
                                  "address)",
    "js.pixel_c_landing_unreachable": "the landing page could not be read",
    "err.pixel_check_failed": "The pixel check failed, so this campaign cannot be signed. "
                              "Fix the pixel and run the brief again.",
    "err.pixel_check_missing": "This campaign needs a pixel check before it can be signed. "
                               "Run the brief again.",
    "err.pixel_check_mismatch": "The pixel check is for another pixel. Run the brief again.",
}

PL_PIXEL: dict[str, str] = {
    "studio.ap.pixel": "Sprawdzenie piksela Mety (tylko odczyt)",
    "js.pixel_ok": "piksel OK",
    "js.pixel_warn": "piksel: sprawdź ostrzeżenia",
    "js.pixel_fail": "piksel nie przeszedł sprawdzenia: tego nie można podpisać",
    "js.pixel_head": "piksel {id} · ostatnie zdarzenie {fired} · {events} zdarzeń w 7 dni",
    "js.pixel_never": "nigdy",
    "js.pixel_unknown": "nieznane",
    "js.pixel_c_pixel_found": "piksel istnieje i jest dostępny",
    "js.pixel_c_pixel_not_found": "piksel nie istnieje albo to konto go nie widzi",
    "js.pixel_c_pixel_unavailable": "Meta oznacza piksel jako niedostępny",
    "js.pixel_c_pixel_unverified": "nie udało się sprawdzić piksela; spróbuj później",
    "js.pixel_c_account_ok": "konto reklamowe może używać tego piksela",
    "js.pixel_c_pixel_not_in_account": "piksela nie ma wśród pikseli konta reklamowego; "
                                       "udostępnij go kontu w Menedżerze zdarzeń",
    "js.pixel_c_account_unknown": "nie udało się pobrać listy pikseli konta reklamowego",
    "js.pixel_c_fired_recently": "piksel wysłał zdarzenie w ostatnich 7 dniach",
    "js.pixel_c_no_recent_events": "brak zdarzeń w ostatnich 7 dniach",
    "js.pixel_c_never_fired": "piksel nigdy nie wysłał zdarzenia",
    "js.pixel_c_fired_unknown": "nie da się odczytać czasu ostatniego zdarzenia piksela",
    "js.pixel_c_events_7d": "w ostatnich 7 dniach są zliczone zdarzenia",
    "js.pixel_c_no_events_7d": "w ostatnich 7 dniach nie zliczono żadnych zdarzeń",
    "js.pixel_c_stats_unknown": "liczba zdarzeń z ostatnich 7 dni jest nieznana",
    "js.pixel_c_event_seen": "zdarzenie, pod które optymalizuje kampania, zostało odnotowane",
    "js.pixel_c_event_not_seen": "zdarzenia, pod które optymalizuje kampania, nie było "
                                 "przez 7 dni",
    "js.pixel_c_landing_found": "identyfikator piksela jest w HTML strony docelowej",
    "js.pixel_c_landing_not_found": "identyfikatora piksela nie ma w HTML strony docelowej "
                                    "(może go ładować menedżer tagów)",
    "js.pixel_c_landing_refused": "strona docelowa nie została pobrana (to nie jest "
                                  "publiczny adres https)",
    "js.pixel_c_landing_unreachable": "nie udało się odczytać strony docelowej",
    "err.pixel_check_failed": "Piksel nie przeszedł sprawdzenia, więc tej kampanii nie można "
                              "podpisać. Popraw piksel i uruchom brief ponownie.",
    "err.pixel_check_missing": "Ta kampania wymaga sprawdzenia piksela przed podpisem. "
                               "Uruchom brief ponownie.",
    "err.pixel_check_mismatch": "Sprawdzenie dotyczy innego piksela. Uruchom brief "
                                "ponownie.",
}
