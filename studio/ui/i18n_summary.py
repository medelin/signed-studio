"""EN/PL strings for step 2 of the brief: the short summary card and the assistant that
asks, one at a time, what the draft is missing (``static/brief_summary.js``)."""

from __future__ import annotations

EN_SUMMARY: dict[str, str] = {
    "brief.sum_title": "Your brief in short",
    "brief.sum_hint": "This is what the studio understood from your words. If something is "
                      "wrong, press Change next to it.",
    "brief.ask_title": "A few questions",
    "brief.ask_hint": "Only what is missing or needs your word, one at a time.",
    "brief.ask_back": "Back",
    "brief.ask_skip": "Skip",
    "brief.ask_next": "Next",
    "brief.details_summary": "All details (for checking every field)",
    # summary sentences
    "js.brief_sum_t_who": "who you are",
    "js.brief_sum_t_what": "what you offer",
    "js.brief_sum_t_whom": "who sees the ad",
    "js.brief_sum_t_goal": "the goal",
    "js.brief_sum_t_budget": "the budget",
    "js.brief_sum_t_leads": "where the ad leads",
    "js.brief_sum_who": "You advertise as {name} ({kind}).",
    "js.brief_sum_who_name": "You advertise as {name}.",
    "js.brief_sum_who_missing": "Your business name is still missing.",
    "js.brief_sum_what": "You offer {product}.",
    "js.brief_sum_what_offer": "You offer {product}: {offer}.",
    "js.brief_sum_what_missing": "What you sell is still missing.",
    "js.brief_sum_until": "The offer runs until {date}.",
    "js.brief_sum_whom": "For people aged {min}–{max} in {places}, in {languages}.",
    "js.brief_sum_whom_nolang": "For people aged {min}–{max} in {places}.",
    "js.brief_sum_whom_noplace": "For people aged {min}–{max}; where they live is still "
                                 "missing.",
    "js.brief_sum_goal": "Goal: {goal}.",
    "js.brief_sum_goal_sub": "Goal: {goal} ({sub}).",
    "js.brief_sum_budget_daily": "Budget: {amount} a day.",
    "js.brief_sum_budget_total": "Budget: {amount} in total, from {start} to {end}.",
    "js.brief_sum_budget_missing": "The budget is still missing.",
    "js.brief_sum_leads": "The ad leads to {host}.",
    "js.brief_sum_leads_missing": "The page the ad leads to is still missing.",
    "js.brief_sum_profile": "from your profile",
    "js.brief_sum_change": "Change",
    "js.brief_sum_change_label": "Change {what}",
    # the assistant
    "js.brief_ask_progress": "Question {i} of {n}",
    "js.brief_ask_left": "{n} still open",
    "js.brief_ask_done": "All set — check the summary and confirm.",
    "js.brief_ask_end_left": "{n} still open. Press Back, or fill them in under All details.",
    "js.brief_ask_need": "Answer this question or strike the claim, or press Skip.",
    "js.brief_ask_saved": "Saved: {label}.",
    "js.brief_ask_countries": "Which countries do you sell in?",
    "js.brief_ask_eu": "Whole EU",
    "js.brief_ask_other": "Other countries (names or two-letter codes, with commas between "
                          "them)",
    "js.brief_ask_unknown_country": "I do not know these: {names}. Use two-letter codes, "
                                    "for example FR.",
    "js.brief_ask_url": "Where should the ad lead? Your page address, starting with "
                        "https://",
    "js.brief_ask_amount": "How much do you want to spend on the ad a day?",
    "js.brief_ask_amount_total": "How much do you want to spend on the ad in total?",
    "js.brief_ask_amount_label": "Amount in {currency}",
    "js.brief_ask_amount_currency": "The ad account pays in {currency}, not {other}: give the "
                                    "amount in {currency}.",
    "js.brief_ask_chip_suggested": "{amount} (suggested)",
    "js.brief_ask_chip_learning": "{amount} (enough to learn)",
    "js.brief_ask_chip_minimum": "{amount} (the least)",
    "js.brief_ask_product": "What do you sell? A few words are enough.",
    "js.brief_ask_name": "What is your business called?",
    "js.brief_ask_generic": "Please fill in: {label}",
    "js.brief_ask_your_answer": "Your answer",
    "js.brief_ask_show": "Show this in the form",
    # no website, or a website without a pixel: switch the objective (brief_summary.js)
    "js.brief_needs_website": "{label} — needs your website",
    "js.brief_sum_leads_nosite": "You have no website: the ad keeps people on Facebook and "
                                 "Instagram.",
    "js.brief_sum_goal_no_website": "This goal sends people to a website, and you have none. "
                                    "Switch to:",
    "js.brief_sum_goal_no_pixel": "This goal counts purchases or sign-ups through a Meta "
                                  "Pixel, and none is set. Switch to:",
    "js.brief_ask_no_website": "Without a website, how should people reach you?",
    "js.brief_ask_no_website_why": "The goal in the draft sends people to a website. Pick "
                                   "one that keeps them in the ad: messages, an instant "
                                   "form or engagement with the post.",
    "js.brief_ask_no_pixel": "How should the ad count its results?",
    "js.brief_ask_no_pixel_why": "This goal counts purchases or sign-ups on your website "
                                 "through a Meta Pixel (dataset ID), and none is set. "
                                 "Switch with one click, or type the ID.",
    "js.brief_ask_pixel_label": "Dataset ID (Meta Pixel)",
    "js.brief_switch_traffic": "Traffic to your website (clicks, no pixel needed)",
    "js.brief_switch_form": "Instant form (people leave their details in the ad)",
    "js.brief_switch_messenger": "Messages on Messenger",
    "js.brief_switch_whatsapp": "Messages on WhatsApp",
    "js.brief_switch_instagram": "Messages on Instagram",
    "js.brief_switch_engagement": "Engagement with the post",
    "js.brief_switched": "The goal is now: {choice}.",
}

PL_SUMMARY: dict[str, str] = {
    "brief.sum_title": "Twój brief w skrócie",
    "brief.sum_hint": "Tak studio zrozumiało Twoje słowa. Jeśli coś się nie zgadza, naciśnij "
                      "Zmień obok.",
    "brief.ask_title": "Kilka pytań",
    "brief.ask_hint": "Tylko o to, czego brakuje albo co musisz potwierdzić, po jednym "
                      "naraz.",
    "brief.ask_back": "Wstecz",
    "brief.ask_skip": "Pomiń",
    "brief.ask_next": "Dalej",
    "brief.details_summary": "Wszystkie szczegóły (do sprawdzenia każdego pola)",
    "js.brief_sum_t_who": "kim jesteś",
    "js.brief_sum_t_what": "co oferujesz",
    "js.brief_sum_t_whom": "kto zobaczy reklamę",
    "js.brief_sum_t_goal": "cel",
    "js.brief_sum_t_budget": "budżet",
    "js.brief_sum_t_leads": "dokąd prowadzi reklama",
    "js.brief_sum_who": "Reklamujesz się jako {name} ({kind}).",
    "js.brief_sum_who_name": "Reklamujesz się jako {name}.",
    "js.brief_sum_who_missing": "Brakuje jeszcze nazwy Twojej firmy.",
    "js.brief_sum_what": "Oferujesz: {product}.",
    "js.brief_sum_what_offer": "Oferujesz: {product} — {offer}.",
    "js.brief_sum_what_missing": "Brakuje jeszcze tego, co sprzedajesz.",
    "js.brief_sum_until": "Oferta trwa do {date}.",
    "js.brief_sum_whom": "Dla osób w wieku {min}–{max}: {places}, język: {languages}.",
    "js.brief_sum_whom_nolang": "Dla osób w wieku {min}–{max}: {places}.",
    "js.brief_sum_whom_noplace": "Dla osób w wieku {min}–{max}; brakuje jeszcze, gdzie "
                                 "mieszkają.",
    "js.brief_sum_goal": "Cel: {goal}.",
    "js.brief_sum_goal_sub": "Cel: {goal} ({sub}).",
    "js.brief_sum_budget_daily": "Budżet: {amount} dziennie.",
    "js.brief_sum_budget_total": "Budżet: {amount} łącznie, od {start} do {end}.",
    "js.brief_sum_budget_missing": "Brakuje jeszcze budżetu.",
    "js.brief_sum_leads": "Reklama prowadzi do {host}.",
    "js.brief_sum_leads_missing": "Brakuje jeszcze strony, do której prowadzi reklama.",
    "js.brief_sum_profile": "z Twojego profilu",
    "js.brief_sum_change": "Zmień",
    "js.brief_sum_change_label": "Zmień: {what}",
    "js.brief_ask_progress": "Pytanie {i} z {n}",
    "js.brief_ask_left": "otwarte: {n}",
    "js.brief_ask_done": "Gotowe — sprawdź skrót i potwierdź.",
    "js.brief_ask_end_left": "Wciąż otwarte: {n}. Naciśnij Wstecz albo uzupełnij je we "
                             "Wszystkich szczegółach.",
    "js.brief_ask_need": "Odpowiedz na to pytanie albo skreśl twierdzenie, albo naciśnij "
                         "Pomiń.",
    "js.brief_ask_saved": "Zapisano: {label}.",
    "js.brief_ask_countries": "W jakich krajach sprzedajesz?",
    "js.brief_ask_eu": "Cała UE",
    "js.brief_ask_other": "Inne kraje (nazwy albo dwuliterowe kody, oddzielone przecinkami)",
    "js.brief_ask_unknown_country": "Nie znam tych krajów: {names}. Użyj dwuliterowych "
                                    "kodów, na przykład FR.",
    "js.brief_ask_url": "Dokąd ma prowadzić reklama? Adres Twojej strony, zaczynający się "
                        "od https://",
    "js.brief_ask_amount": "Ile chcesz dziennie wydać na reklamę?",
    "js.brief_ask_amount_total": "Ile łącznie chcesz wydać na reklamę?",
    "js.brief_ask_amount_label": "Kwota w {currency}",
    "js.brief_ask_amount_currency": "Konto reklamowe płaci w {currency}, nie w {other}: podaj "
                                    "kwotę w {currency}.",
    "js.brief_ask_chip_suggested": "{amount} (proponowane)",
    "js.brief_ask_chip_learning": "{amount} (wystarczy do nauki)",
    "js.brief_ask_chip_minimum": "{amount} (najmniej)",
    "js.brief_ask_product": "Co sprzedajesz? Wystarczy kilka słów.",
    "js.brief_ask_name": "Jak nazywa się Twoja firma?",
    "js.brief_ask_generic": "Uzupełnij: {label}",
    "js.brief_ask_your_answer": "Twoja odpowiedź",
    "js.brief_ask_show": "Pokaż to w formularzu",
    "js.brief_needs_website": "{label} — wymaga Twojej strony",
    "js.brief_sum_leads_nosite": "Nie masz strony: reklama zatrzymuje ludzi na Facebooku i "
                                 "Instagramie.",
    "js.brief_sum_goal_no_website": "Ten cel kieruje ludzi na stronę, a Ty jej nie masz. "
                                    "Zmień na:",
    "js.brief_sum_goal_no_pixel": "Ten cel liczy zakupy lub zapisy przez Pixel Meta, a "
                                  "żaden nie jest ustawiony. Zmień na:",
    "js.brief_ask_no_website": "Bez strony: jak ludzie mają się z Tobą skontaktować?",
    "js.brief_ask_no_website_why": "Cel w szkicu kieruje ludzi na stronę. Wybierz taki, "
                                   "który zatrzymuje ich w reklamie: wiadomości, formularz "
                                   "błyskawiczny albo zaangażowanie w post.",
    "js.brief_ask_no_pixel": "Jak reklama ma liczyć wyniki?",
    "js.brief_ask_no_pixel_why": "Ten cel liczy zakupy lub zapisy na Twojej stronie przez "
                                 "Pixel Meta (ID zbioru danych), a żaden nie jest ustawiony. "
                                 "Zmień cel jednym kliknięciem albo wpisz ID.",
    "js.brief_ask_pixel_label": "ID zbioru danych (Pixel Meta)",
    "js.brief_switch_traffic": "Ruch na Twoją stronę (kliknięcia, bez pixela)",
    "js.brief_switch_form": "Formularz błyskawiczny (ludzie zostawiają dane w reklamie)",
    "js.brief_switch_messenger": "Wiadomości w Messengerze",
    "js.brief_switch_whatsapp": "Wiadomości na WhatsAppie",
    "js.brief_switch_instagram": "Wiadomości na Instagramie",
    "js.brief_switch_engagement": "Zaangażowanie w post",
    "js.brief_switched": "Cel to teraz: {choice}.",
}
