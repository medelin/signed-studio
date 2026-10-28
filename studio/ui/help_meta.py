"""Help/FAQ entries about Meta's objectives, lead ads, audiences, formats and measurement,
English and Polish (``studio.ui.help_texts`` appends them). Plain data: ``(anchor, cast
key, scene, questions, answers)``; answers are trusted HTML. True to
``studio/meta/objectives.py``, ``studio/leadads.py``, ``studio/meta/audience.py``,
``studio/agents/video.py`` and ``studio/meta/measurement.py``.
"""

from __future__ import annotations

META_ENTRIES: tuple[tuple[str, str, str, dict[str, str], dict[str, str]], ...] = (
    (
        "objectives", "amara", "signed",
        {"en": "Which campaign objectives can the studio create?",
         "pl": "Jakie cele kampanii potrafi utworzyć studio?"},
        {"en": "All six of Meta's: <strong>awareness</strong> (reach, or ad recall), "
               "<strong>traffic</strong> (link clicks, or landing page views), "
               "<strong>engagement</strong> (post engagement, messages on Messenger, "
               "Instagram or WhatsApp, or event responses), <strong>leads</strong> (an "
               "instant form, or leads on your website with a pixel), <strong>app "
               "promotion</strong> (installs: your app id and store link) and "
               "<strong>sales</strong> (your pixel and the event that counts; optionally a "
               "catalog and product set). Code, never a model, turns your choice into "
               "Meta's fields: objective, optimisation goal, billing, destination and the "
               "promoted object. You see that mapping on the approval screen and sign it "
               "together with the ad; a campaign created with anything else would not match "
               "your signature. A missing id is a field for you to fill in: the studio never "
               "guesses one. Every campaign is still created paused.",
         "pl": "Wszystkie sześć celów Mety: <strong>rozpoznawalność</strong> (zasięg albo "
               "zapamiętanie reklamy), <strong>ruch</strong> (kliknięcia w link albo "
               "wyświetlenia strony docelowej), <strong>zaangażowanie</strong> (zaangażowanie "
               "w post, wiadomości w Messengerze, na Instagramie lub WhatsAppie, albo "
               "odpowiedzi na wydarzenie), <strong>kontakty</strong> (formularz "
               "błyskawiczny albo leady na twojej stronie z pikselem), <strong>promocja "
               "aplikacji</strong> (instalacje: identyfikator aplikacji i link do sklepu) i "
               "<strong>sprzedaż</strong> (twój piksel i zdarzenie, które się liczy; "
               "opcjonalnie katalog i zestaw produktów). Wybór zamienia na pola Mety kod, "
               "nigdy model: cel, cel optymalizacji, rozliczenie, miejsce docelowe i "
               "promowany obiekt. To mapowanie widzisz na ekranie zatwierdzenia i podpisujesz "
               "razem z reklamą; kampania utworzona z czymkolwiek innym nie pasowałaby do "
               "podpisu. Brakujący identyfikator to pole do uzupełnienia: studio nigdy go "
               "nie zgaduje. Każda kampania nadal powstaje wstrzymana."},
    ),
    (
        "lead-ads", "kofi", "private",
        {"en": "How do instant forms and their leads work?",
         "pl": "Jak działają formularze błyskawiczne i ich leady?"},
        {"en": "Choose <em>leads → instant form</em>, pick the questions (name, e-mail, "
               "phone, city, company) and give your own privacy-policy page: Meta requires "
               "it, and the studio adds no notices of its own. The form is part of what you "
               "sign and is created before the ad. New leads are read from the form "
               "(<em>Fetch instant-form leads</em> on the Leads page) or arrive through "
               "Meta's lead webhook, which the studio accepts only when Meta's signature "
               "checks out. Each lead is stored once and scored by the same rules as a "
               "comment lead; receipts hold counts, never the contact data.",
         "pl": "Wybierz <em>kontakty → formularz błyskawiczny</em>, zaznacz pytania (imię i "
               "nazwisko, e-mail, telefon, miasto, firma) i podaj swoją stronę z polityką "
               "prywatności: Meta jej wymaga, a studio nie dodaje własnych klauzul. "
               "Formularz jest częścią tego, co podpisujesz, i powstaje przed reklamą. Nowe "
               "leady studio czyta z formularza (<em>Pobierz leady z formularzy</em> na "
               "stronie Leady) albo przyjmuje z webhooka Mety, tylko gdy zgadza się podpis "
               "Mety. Każdy lead jest zapisywany raz i oceniany tymi samymi regułami co lead "
               "z komentarza; pokwitowania zawierają liczby, nigdy dane kontaktowe."},
    ),
    (
        "audiences", "leila", "signed",
        {"en": "Who sees my ad? Can I use my own audiences?",
         "pl": "Kto zobaczy reklamę? Czy mogę użyć własnych grup odbiorców?"},
        {"en": "By default Meta's Advantage+ audience: your countries, minimum age (18-25) "
               "and languages stay fixed; your age range, genders and interests are "
               "suggestions Meta may go beyond. Interests are plain words: code looks each "
               "one up in Meta's targeting search and shows the match and its id before you "
               "sign; a word with no match is listed and not used. You can exclude "
               "countries, interests and your own custom audiences, include audiences you "
               "already have, or build a lookalike from one of them, all by id. The studio "
               "never uploads customer data. Turn Advantage+ off to make the brief's values "
               "hard limits.",
         "pl": "Domyślnie grupa Advantage+ Mety: twoje kraje, minimalny wiek (18-25) i "
               "języki są sztywne; przedział wieku, płeć i zainteresowania to podpowiedzi, "
               "poza które Meta może wyjść. Zainteresowania to zwykłe słowa: kod sprawdza "
               "każde w wyszukiwarce targetowania Mety i przed podpisem pokazuje dopasowanie "
               "z identyfikatorem; słowo bez dopasowania jest wymienione i nieużyte. Możesz "
               "wykluczyć kraje, zainteresowania i własne grupy, dodać grupy, które już "
               "masz, albo zbudować z jednej z nich grupę podobnych, wszystko po "
               "identyfikatorze. Studio nigdy nie wysyła danych klientów. Wyłącz Advantage+, "
               "żeby wartości z briefu były sztywne."},
    ),
    (
        "formats", "diego", "edit",
        {"en": "Carousel, video, several texts: what can it make?",
         "pl": "Karuzela, wideo, kilka tekstów: co potrafi przygotować?"},
        {"en": "Besides the images for feed and stories: a <strong>carousel</strong> of 2 "
               "to 10 cards (each one more Muse Image render, 1080×1080), a short "
               "<strong>9:16 video</strong> that code cuts from the renders (Muse Wonder, "
               "which reads but does not render video, orders the shots; its first frame is "
               "the poster), and <strong>copy A/B</strong> with up to three texts. Both "
               "reviewers check every image, card and poster and every text before you can "
               "sign, and code checks each format against Meta's published limits. Texts run "
               "as dynamic creative where Meta supports it for the objective, otherwise as "
               "separate ads in one ad set; activating the campaign activates all of them.",
         "pl": "Oprócz obrazów do feedu i relacji: <strong>karuzelę</strong> z 2 do 10 kart "
               "(każda to kolejny obraz Muse Image, 1080×1080), krótkie <strong>wideo "
               "9:16</strong>, które kod składa z obrazów (kolejność ujęć ustala Muse "
               "Wonder, który czyta wideo, ale go nie tworzy; pierwsza klatka to okładka), "
               "oraz <strong>test A/B tekstu</strong> z maksymalnie trzema wariantami. Obaj "
               "recenzenci sprawdzają każdy obraz, kartę, okładkę i tekst, zanim możesz "
               "podpisać, a kod sprawdza każdy format z opublikowanymi limitami Mety. "
               "Warianty działają jako kreacja dynamiczna, gdy Meta ją wspiera dla celu, "
               "inaczej jako osobne reklamy w jednym zestawie; włączenie kampanii włącza "
               "wszystkie."},
    ),
    (
        "measurement", "grace", "budget",
        {"en": "How is a campaign measured, and who changes its budget?",
         "pl": "Jak mierzona jest kampania i kto zmienia jej budżet?"},
        {"en": "By its own objective: the result Meta counts for it (a purchase, a lead, a "
               "conversation, a click…), cost per result, reach and frequency, CPM, spend "
               "and CTR, broken down by placement, age and gender, and region, with the "
               "attribution setting. A snapshot is read hourly or with <em>Read insights "
               "now</em> on the Report page, and each one is a receipt. Code rules then "
               "suggest moves (a costly placement, no results, ad fatigue, room to scale) "
               "and the analyst explains them. Nothing changes by itself: a budget change or "
               "a pause becomes a new approval that you sign, under the same daily ceiling; "
               "turning a campaign on stays its own signature.",
         "pl": "Według jej własnego celu: wynik, który Meta dla niego liczy (zakup, lead, "
               "rozmowa, kliknięcie…), koszt wyniku, zasięg i częstotliwość, CPM, wydatki i "
               "CTR, w podziale na umiejscowienia, wiek i płeć oraz regiony, z modelem "
               "atrybucji. Odczyt dzieje się co godzinę albo po <em>Odczytaj statystyki "
               "teraz</em> na stronie Raport i każdy jest pokwitowaniem. Reguły w kodzie "
               "proponują ruchy (drogie umiejscowienie, brak wyników, zmęczenie reklamą, "
               "miejsce na wzrost), a analityk je objaśnia. Nic nie zmienia się samo: zmiana "
               "budżetu albo wstrzymanie to nowe zatwierdzenie, które podpisujesz, pod tym "
               "samym limitem dziennym; włączenie kampanii to nadal osobny podpis."},
    ),
)
