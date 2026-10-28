"""Help / FAQ for a non-technical owner, English and Polish, in one module.

Every answer is true to the code in this repository (file references in the comments);
anything not built is marked as not built. Answers are trusted HTML (``<code>``, ``<a>``,
``<strong>`` only); questions are plain text. Each entry is illustrated by one cast member
(``studio/ui/cast.py``) in the two-views scene that fits.
"""

from __future__ import annotations

from dataclasses import dataclass

from studio.ui.help_accounts import ACCOUNT_ENTRIES


@dataclass(frozen=True)
class Entry:
    anchor: str
    person: str  # cast key
    scene: str  # cast.SCENES
    q: dict[str, str]
    a: dict[str, str]


LANGS = ("en", "pl")  # page chrome strings are in studio/ui/i18n.py

ENTRIES: tuple[Entry, ...] = (
    Entry(
        "what-it-does", "amara", "signed",
        {"en": "What does Muse Studio Lite do for me?",
         "pl": "Co Muse Studio Lite dla mnie robi?"},
        {"en": "You give it one brief, typed or spoken. A team of Muse agents writes a plan "
               "(producer), three ad texts (copywriter), cuts your product out of its photo "
               "and draws three placements for feed and stories (art director), and checks "
               "everything against ad policies (two reviewers). It ends with one approval "
               "screen. Only after <strong>you</strong> sign does code create the campaign "
               "on Facebook and Instagram, <strong>paused</strong>. It can also draft "
               "replies to comments, score leads and write a daily report.",
         "pl": "Dajesz jeden brief, wpisany albo nagrany głosem. Zespół agentów Muse pisze "
               "plan (producent), trzy teksty reklamy (copywriter), wycina produkt ze zdjęcia "
               "i rysuje trzy formaty do feedu i relacji (dyrektor artystyczny) oraz "
               "sprawdza wszystko z zasadami reklam (dwóch recenzentów). Na końcu jest jeden "
               "ekran zatwierdzenia. Dopiero gdy <strong>Ty</strong> podpiszesz, kod tworzy "
               "kampanię na Facebooku i Instagramie, <strong>wstrzymaną</strong>. Studio "
               "umie też przygotować odpowiedzi na komentarze, ocenić leady i napisać raport "
               "dnia."}),
    # -- Brief v1 (studio/brief.py, routes_studio.confirm_and_start, ui/static/brief.js) --
    Entry(
        "brief-sections", "grace", "facts",
        {"en": "What is in a Brief v1, section by section?",
         "pl": "Co jest w Briefie v1, sekcja po sekcji?"},
        {"en": "<strong>Advertiser</strong>: a person or a company, the name people see (as "
               "on your page or your legal name; a mismatch can get ads rejected), the "
               "countries you sell in and your landing page. No tax or registry numbers. "
               "<strong>Confirmed facts</strong>: prices, times, guarantees, numbers; the "
               "only facts an ad may state. <strong>Offer</strong>: the product, the offer "
               "and until when it is valid, and reasons to believe, each resting on a "
               "confirmed fact. <strong>Objective</strong>: exactly one Meta objective "
               "(awareness, traffic, engagement, leads or sales) and how you measure "
               "success; this version creates traffic campaigns and records your objective "
               "in the signed content. <strong>Audience</strong>: places, ages (13 to 65), "
               "languages, and who they are, what they believe, what holds them back, what "
               "would move them; Meta treats this as suggestions. <strong>Proposition"
               "</strong>: one sentence. <strong>Tone and brand</strong>, <strong>mandatory "
               "items</strong> (disclaimers, banned claims, dates), <strong>budget</strong> "
               "(daily, or a total spread over the days; in the ad account's currency, read "
               "from the account; a daily amount above the server's ceiling is refused), "
               "<strong>placements</strong> (automatic by default, plus the images to make) "
               "and <strong>assets</strong>.",
         "pl": "<strong>Reklamodawca</strong>: osoba albo firma, nazwa, którą widzą ludzie "
               "(taka jak na Twojej stronie albo nazwa prawna; niezgodność może skończyć się "
               "odrzuceniem reklamy), kraje, w których sprzedajesz, i strona docelowa. Bez "
               "numerów podatkowych i rejestrowych. <strong>Potwierdzone fakty</strong>: "
               "ceny, godziny, gwarancje, liczby; jedyne fakty, które może podać reklama. "
               "<strong>Oferta</strong>: produkt, oferta i do kiedy jest ważna oraz powody, "
               "by uwierzyć, każdy oparty na potwierdzonym fakcie. <strong>Cel</strong>: "
               "dokładnie jeden cel w Mecie (rozpoznawalność, ruch, zaangażowanie, kontakty "
               "albo sprzedaż) i miara sukcesu; ta wersja tworzy kampanie na ruch, a Twój "
               "cel zapisuje w podpisywanej treści. <strong>Odbiorcy</strong>: miejsca, wiek "
               "(od 13 do 65 lat), języki oraz to, kim są, w co wierzą, co ich powstrzymuje "
               "i co by ich poruszyło; Meta traktuje to jako wskazówki. <strong>Myśl "
               "przewodnia</strong>: jedno zdanie. <strong>Ton i marka</strong>, "
               "<strong>elementy obowiązkowe</strong> (zastrzeżenia, zakazane twierdzenia, "
               "daty), <strong>budżet</strong> (dzienny albo łączny rozłożony na dni; w "
               "walucie konta reklamowego, odczytanej z konta; kwota dzienna powyżej limitu "
               "serwera "
               "jest odrzucana), <strong>umiejscowienia</strong> (domyślnie automatyczne, "
               "plus obrazy do przygotowania) i <strong>materiały</strong>."}),
    Entry(
        "brief-text-or-voice", "ola", "voice",
        {"en": "How do I fill in the brief, by typing or by speaking?",
         "pl": "Jak wypełnić brief: pisząc czy mówiąc?"},
        {"en": "Either way; both are equal. Type in your own words, or press "
               "<strong>Record a voice brief</strong> and speak in Polish or English (up to "
               "10 minutes per recording; record again to add more). "
               "<code>muse-voice-transcribe-1.0</code> writes the text into the same box, "
               "tuned to the language of this page, and you can correct it before anything "
               "else happens. Then <strong>Draft Brief v1</strong>: the producer "
               "(<code>muse-spark-1.3</code>) fills the sections from your words only. "
               "Every field of the draft can be changed by typing, and every text field "
               "has a <strong>Dictate</strong> button. If there is no microphone, or the "
               "browser does not allow it, the studio says so and typing keeps working.",
         "pl": "Jak wolisz; obie drogi są równorzędne. Wpisz brief własnymi słowami albo "
               "naciśnij <strong>Nagraj brief głosem</strong> i mów po polsku lub po "
               "angielsku (do 10 minut na nagranie; nagraj jeszcze raz, żeby coś dodać). "
               "<code>muse-voice-transcribe-1.0</code> wpisuje tekst do tego samego pola, "
               "nastawiony na język tej strony, a Ty możesz go poprawić, zanim cokolwiek się "
               "wydarzy. Potem <strong>Przygotuj Brief v1</strong>: producent "
               "(<code>muse-spark-1.3</code>) wypełnia sekcje wyłącznie na podstawie Twoich "
               "słów. Każde pole szkicu możesz zmienić, pisząc, a każde pole tekstowe ma "
               "przycisk <strong>Podyktuj</strong>. Jeśli nie ma mikrofonu albo przeglądarka "
               "na niego nie pozwala, studio to mówi, a pisanie dalej działa."}),
    Entry(
        "fact-questions", "kofi", "facts",
        {"en": "What are [FACT] questions?",
         "pl": "Czym są pytania [FACT]?"},
        {"en": "A [FACT] question is a claim the producer could not confirm from your "
               "words, or a fact the brief needs that you did not give. \"Everything "
               "included\" becomes \"Are clay and firing included in the price?\". Code "
               "checks the draft too: a fact with a number you never wrote is turned into "
               "a question. Answer each one in a short sentence (it becomes a confirmed "
               "fact) or strike the claim (it will not be used). The run starts only when "
               "none is left open. Afterwards the copywriter may use only confirmed facts, "
               "and both reviewers check every claim against them (rule P10); a number "
               "that is not in your facts, or a claim you banned, blocks the ad.",
         "pl": "Pytanie [FACT] to twierdzenie, którego producent nie mógł potwierdzić na "
               "podstawie Twoich słów, albo fakt, którego brief potrzebuje, a którego nie "
               "podano. „Wszystko w cenie” staje się pytaniem „Czy glina i wypał są w "
               "cenie?”. Szkic sprawdza też kod: fakt z liczbą, której nie było w Twoich "
               "słowach, zamienia się w pytanie. Odpowiedz na każde krótkim zdaniem (stanie się "
               "potwierdzonym faktem) albo skreśl twierdzenie (nie zostanie użyte). "
               "Przebieg startuje dopiero wtedy, gdy żadne nie zostało otwarte. Potem "
               "copywriter może użyć tylko potwierdzonych faktów, a obaj recenzenci "
               "sprawdzają z nimi każde twierdzenie (zasada P10); liczba spoza Twoich "
               "faktów albo zakazane przez Ciebie twierdzenie blokuje reklamę."}),
    Entry(
        "signed-facts", "harjit", "verify",
        {"en": "Why are the facts signed?",
         "pl": "Dlaczego fakty są podpisywane?"},
        {"en": "Because an ad is only as true as the facts it stands on. When you confirm "
               "the brief, its SHA-256 hash (<code>brief_sha256</code>, over the canonical "
               "JSON) goes into a <code>brief.confirmed</code> receipt with the number of "
               "facts and answered and struck questions. Your signature on the campaign "
               "covers the ad's content hash <strong>and</strong> <code>brief_sha256</code>. "
               "If a fact changes after you signed, the server refuses the approval and "
               "<code>studio verify</code> rejects the log. The brief's text never enters "
               "a receipt, only its hash.",
         "pl": "Bo reklama jest tak prawdziwa jak fakty, na których stoi. Gdy potwierdzasz "
               "brief, jego skrót SHA-256 (<code>brief_sha256</code>, liczony z kanonicznego "
               "JSON-a) trafia do pokwitowania <code>brief.confirmed</code> razem z liczbą "
               "faktów oraz pytań z odpowiedzią i skreślonych. Twój podpis pod kampanią "
               "obejmuje skrót treści reklamy <strong>i</strong> <code>brief_sha256</code>. "
               "Jeśli fakt zmieni się po podpisie, serwer odrzuca zatwierdzenie, a "
               "<code>studio verify</code> odrzuca rejestr. Treść briefu nigdy nie trafia do "
               "pokwitowania, tylko jej skrót."}),
    Entry(
        "brief-accessibility", "priya", "signed",
        {"en": "Can I make an ad with a keyboard, a screen reader or my voice?",
         "pl": "Czy zrobię reklamę klawiaturą, z czytnikiem ekranu albo głosem?"},
        {"en": "Yes. Every control is a labelled form field or button, grouped in "
               "fieldsets with legends, reachable with Tab and visibly focused. An error is "
               "written next to its field and tied to it, and a list of all errors at the "
               "top of the form links to each field. A live region announces each step, "
               "the new [FACT] questions and what happened after every action. No step has "
               "a time limit: if the 15-minute signing window closes, the studio issues a "
               "fresh one for the same content. Voice works for the brief, for every text "
               "field and for answering questions; <strong>Read the brief aloud</strong> "
               "uses your browser's own speech (no network) and is hidden where the "
               "browser cannot speak. Buttons and fields are at least 44 pixels high, and "
               "the page follows your reduced-motion setting.",
         "pl": "Tak. Każdy element to opisane pole formularza albo przycisk, pogrupowane w "
               "sekcje z nagłówkami (fieldset i legend), dostępne klawiszem Tab i z "
               "widocznym fokusem. Błąd pojawia się przy swoim polu i jest z nim powiązany, "
               "a lista wszystkich błędów na górze formularza prowadzi do każdego pola. "
               "Obszar na żywo ogłasza każdy krok, nowe pytania [FACT] i wynik każdej "
               "czynności. Żaden krok nie ma limitu czasu: jeśli 15-minutowe okno podpisu "
               "się zamknie, studio wydaje nowe dla tej samej treści. Głos działa przy "
               "briefie, w każdym polu tekstowym i przy odpowiedziach na pytania; "
               "<strong>Przeczytaj brief na głos</strong> korzysta z mowy wbudowanej w "
               "przeglądarkę (bez sieci) i jest ukryte tam, gdzie przeglądarka nie umie "
               "mówić. Przyciski i pola mają co najmniej 44 piksele wysokości, a strona "
               "respektuje ustawienie ograniczenia ruchu."}),
    Entry(
        "spend-without-me", "tomasz", "unsigned",
        {"en": "Can it spend my money or post without me?",
         "pl": "Czy może wydać moje pieniądze albo coś opublikować beze mnie?"},
        {"en": "No. No agent can write to Meta: only code does, and only after a signature "
               "made in your browser with a key that never leaves it. The signature covers "
               "the exact bytes you saw (their SHA-256 hash), is single use (a one-time "
               "nonce) and expires after 15 minutes. Any change after signing, a replay or "
               "a late signature is refused. Campaigns are always created "
               "<strong>PAUSED</strong>; turning one on is a second signature, and the "
               "daily budget can never exceed the ceiling set on the server (50 in the "
               "ad account's currency by default).",
         "pl": "Nie. Żaden agent nie zapisuje niczego w Mecie: robi to tylko kod i tylko po "
               "podpisie złożonym w Twojej przeglądarce kluczem, który jej nie opuszcza. "
               "Podpis obejmuje dokładnie te bajty, które widzisz (ich skrót SHA-256), działa "
               "jeden raz (jednorazowy nonce) i wygasa po 15 minutach. Zmiana po podpisie, "
               "powtórka albo spóźniony podpis są odrzucane. Kampania powstaje zawsze "
               "<strong>WSTRZYMANA</strong>; włączenie jej to drugi podpis, a budżet dzienny "
               "nigdy nie przekroczy limitu ustawionego na serwerze (domyślnie 50 w walucie "
               "konta reklamowego)."}),
    Entry(
        "receipts", "harjit", "verify",
        {"en": "What is a receipt, and how do I check one?",
         "pl": "Co to jest pokwitowanie i jak je sprawdzić?"},
        {"en": "Every model call, tool call, review, signature and write leaves a receipt: "
               "who did it, which model, how many tokens, what it cost, and the hash of the "
               "receipt before it, signed by the server. Download them from "
               "<a href=\"/api/receipts.jsonl\">receipts.jsonl</a> and run "
               "<code>studio verify receipts.jsonl --server https://muse.udun.eu</code>. "
               "Exit code 0 means the chain is intact. Change one character in the file and "
               "run it again: it exits 2 and names the broken receipt. "
               "<code>make eval</code> repeats this for 20 kinds of tampering.",
         "pl": "Każde wywołanie modelu i narzędzia, recenzja, podpis i zapis zostawia "
               "pokwitowanie: kto, jakim modelem, ile tokenów, ile kosztowało i skrót "
               "poprzedniego pokwitowania, podpisane przez serwer. Pobierz je z "
               "<a href=\"/api/receipts.jsonl\">receipts.jsonl</a> i uruchom "
               "<code>studio verify receipts.jsonl --server https://muse.udun.eu</code>. "
               "Kod wyjścia 0 znaczy, że łańcuch jest cały. Zmień jeden znak w pliku i "
               "uruchom znowu: wyjdzie z kodem 2 i wskaże zepsute pokwitowanie. "
               "<code>make eval</code> powtarza to dla 20 rodzajów fałszerstw."}),
    Entry(
        "reviewers", "leila", "blocked",
        {"en": "What do the reviewers check?",
         "pl": "Co sprawdzają recenzenci?"},
        {"en": "Two reviewers (Muse Spark with vision, and the open-weights Muse Spark Open "
               "as a second opinion) check every image with its text against nine rules: "
               "P1 personal attributes, P2 before/after and body image, P3 unrealistic "
               "promises, P4 price and offer accuracy, P5 brand name, P6 text in the image, "
               "P7 prohibited or sensitive content, P8 regulated claims, and P9: no claims "
               "of registering companies or providing government documents, and no absolute "
               "tax or legal promises (\"pay no tax\", \"100% legal\"). P9 is also checked "
               "by code. A block from either reviewer wins, and disagreements are shown "
               "before you sign.",
         "pl": "Dwóch recenzentów (Muse Spark z widzeniem i otwarty Muse Spark Open jako "
               "druga opinia) sprawdza każdy obraz z tekstem według dziewięciu zasad: P1 "
               "cechy osobiste, P2 przed/po i wizerunek ciała, P3 nierealne obietnice, P4 "
               "zgodność ceny i oferty, P5 nazwa marki, P6 tekst na obrazie, P7 treści "
               "zakazane lub drażliwe, P8 twierdzenia regulowane oraz P9: żadnych obietnic "
               "rejestracji firmy ani dokumentów urzędowych i żadnych bezwzględnych "
               "obietnic podatkowych lub prawnych („bez podatku”, „100% legalnie”). P9 "
               "sprawdza też kod. Blokada któregokolwiek recenzenta wygrywa, a różnice "
               "zdań widzisz przed podpisem."}),
    Entry(
        "rejected", "diego", "blocked",
        {"en": "What happens if a reviewer rejects my ad?",
         "pl": "Co się dzieje, gdy recenzent odrzuci moją reklamę?"},
        {"en": "A blocked ad never reaches the approval screen: the studio refuses to ask "
               "for your signature and tells the producer to fix it first. You see the "
               "findings with their rule ids. If the run ends without an approval, change "
               "the brief (for example drop the promise that was flagged) and run it again. "
               "Nothing was published, and the blocked attempt stays in the receipts.",
         "pl": "Zablokowana reklama nie trafia na ekran zatwierdzenia: studio odmawia prośby "
               "o podpis i każe producentowi najpierw ją poprawić. Widzisz uwagi z numerami "
               "zasad. Jeśli przebieg skończy się bez zatwierdzenia, zmień brief (np. usuń "
               "zakwestionowaną obietnicę) i uruchom go ponownie. Nic nie zostało "
               "opublikowane, a zablokowana próba zostaje w pokwitowaniach."}),
    Entry(
        "edit-before-signing", "kofi", "edit",
        {"en": "Can I edit the ad before I sign it?",
         "pl": "Czy mogę poprawić reklamę przed podpisem?"},
        {"en": "Replies to comments: yes. The draft in the inbox is a text box; what you "
               "sign is exactly the text you left there. Campaign ads: not in this "
               "version. The ad you sign is the one the reviewers checked; to change it, "
               "change the brief and run again. Editing a reviewed ad in place would need a "
               "new review and is not built. Either way, nothing can change after you sign.",
         "pl": "Odpowiedzi na komentarze: tak. Szkic w skrzynce to pole tekstowe; "
               "podpisujesz dokładnie ten tekst, który tam zostawisz. Reklamy w kampanii: "
               "nie w tej wersji. Podpisujesz tę reklamę, którą sprawdzili recenzenci; aby "
               "ją zmienić, zmień brief i uruchom ponownie. Poprawianie zrecenzowanej "
               "reklamy na miejscu wymagałoby nowej recenzji i nie jest zbudowane. "
               "Po podpisie nic się już nie zmieni."}),
    Entry(
        "models", "priya", "signed",
        {"en": "Which Meta models do what?",
         "pl": "Które modele Mety co robią?"},
        {"en": "<code>muse-spark-1.3</code> plans, reviews images, triages comments, explains "
               "lead scores and writes the report. <code>muse-spark-1.3-contributor</code> "
               "writes ad copy, only when there is no personal data. "
               "<code>muse-image-1.0</code> draws the placements, <code>sam-3.1</code> cuts "
               "the product out of its photo, <code>muse-voice-transcribe-1.0</code> turns "
               "your spoken brief into text, and <code>meta-models/muse-spark-open</code> (open "
               "weights, on Baseten) is the second reviewer. Before either reviewer, code "
               "checks each placement's format limits (rule F1: lengths and sizes from the "
               "platform's own specifications; a hard limit blocks, a recommended one warns) "
               "and the P9 phrases. Each receipt names the model it used.",
         "pl": "<code>muse-spark-1.3</code> planuje, recenzuje obrazy, segreguje komentarze, "
               "uzasadnia ocenę leadów i pisze raport. <code>muse-spark-1.3-contributor</code> "
               "pisze teksty reklam, tylko gdy nie ma danych osobowych. "
               "<code>muse-image-1.0</code> rysuje formaty, <code>sam-3.1</code> wycina "
               "produkt ze zdjęcia, <code>muse-voice-transcribe-1.0</code> zamienia nagrany "
               "brief na tekst, a <code>meta-models/muse-spark-open</code> (otwarte wagi, na "
               "Basetenie) jest drugim recenzentem. Przed recenzentami kod sprawdza limity "
               "formatu każdego miejsca reklamy (zasada F1: długości i rozmiary ze "
               "specyfikacji platformy; twardy limit blokuje, zalecany ostrzega) oraz frazy "
               "P9. Każde pokwitowanie podaje użyty model."}),
    Entry(
        "personal-data", "mei", "private",
        {"en": "What happens to my customers' personal data?",
         "pl": "Co się dzieje z danymi osobowymi moich klientów?"},
        {"en": "Before any model call, code decides which tier may see the text. Anything "
               "your customers wrote (comments, leads, voice transcripts), anything with an "
               "e-mail address, a phone number, a NIP or PESEL number, or a long run of "
               "digits is never sent to the contributor tier; it stays on the standard "
               "tier. The receipt records why, naming only the kind of data, never its "
               "value. Your voice recording is deleted after transcription.",
         "pl": "Przed każdym wywołaniem modelu kod decyduje, który poziom może zobaczyć "
               "tekst. Wszystko, co napisali Twoi klienci (komentarze, leady, transkrypcje), "
               "i wszystko z adresem e-mail, numerem telefonu, NIP-em, PESEL-em albo długim "
               "ciągiem cyfr nigdy nie trafia do poziomu contributor; zostaje na poziomie "
               "standard. Pokwitowanie zapisuje powód, podając tylko rodzaj danych, nigdy "
               "ich wartość. Nagranie głosu jest usuwane po transkrypcji."}),
    Entry(
        "cost", "grace", "budget",
        {"en": "What does it cost, and is there a daily cap?",
         "pl": "Ile to kosztuje i czy jest dzienny limit?"},
        {"en": "One campaign run (brief to approval screen) costs about $0.07 in Model API "
               "calls (see the evaluation in the repository). Model spending has a daily "
               "cap, $8 per UTC day by default (<code>STUDIO_DAILY_BUDGET_USD</code>); each "
               "call reserves its worst case before it is sent, and past the cap calls are "
               "refused. The cap is rebuilt from the receipts after a restart. Ad spend on "
               "Meta is separate: campaigns start paused and their daily budget is capped "
               "by the server.",
         "pl": "Jeden przebieg kampanii (od briefu do ekranu zatwierdzenia) kosztuje około "
               "0,07 USD w wywołaniach Model API (zob. ewaluację w repozytorium). Wydatki na "
               "modele mają dzienny limit, domyślnie 8 USD na dobę UTC "
               "(<code>STUDIO_DAILY_BUDGET_USD</code>); każde wywołanie rezerwuje najgorszy "
               "przypadek przed wysłaniem, a po przekroczeniu limitu wywołania są "
               "odrzucane. Po restarcie limit jest odtwarzany z pokwitowań. Budżet reklam w "
               "Mecie to osobna sprawa: kampanie startują wstrzymane, a ich budżet dzienny "
               "ogranicza serwer."}),
    Entry(
        "languages", "ola", "signed",
        {"en": "Which languages does it speak?",
         "pl": "W jakich językach działa?"},
        {"en": "You can speak or type the brief in Polish or English; voice transcription "
               "is tuned for Polish. Ad copy is written in English unless the brief asks for "
               "Polish. The studio screens are in English; this help page is in English and "
               "Polish.",
         "pl": "Brief możesz nagrać albo wpisać po polsku lub po angielsku; transkrypcja "
               "głosu jest nastawiona na polski. Teksty reklam powstają po angielsku, chyba "
               "że brief prosi o polski. Ekrany studia są po angielsku; ta strona pomocy "
               "jest po angielsku i po polsku."}),
    Entry(
        "sandbox-reset", "tomasz", "reset",
        {"en": "What does the judge sandbox reset?",
         "pl": "Co resetuje piaskownica dla jury?"},
        {"en": "On <a href=\"https://muse.udun.eu\">muse.udun.eu</a> demo data is wiped and "
               "re-seeded every hour: briefs, creatives, campaigns, comments, leads, tasks, "
               "reports and pending approvals. Kept: receipts and runs (so the chain stays "
               "verifiable), used nonces (so old signatures cannot be replayed) and approver "
               "keys (so old signatures still verify). The sandbox never holds a Meta token: "
               "campaign writes go to the mock adapter, paused.",
         "pl": "Na <a href=\"https://muse.udun.eu\">muse.udun.eu</a> dane demonstracyjne są co "
               "godzinę czyszczone i wypełniane od nowa: briefy, kreacje, kampanie, "
               "komentarze, leady, zadania, raporty i oczekujące zatwierdzenia. Zostają: "
               "pokwitowania i przebiegi (łańcuch da się sprawdzić), zużyte nonce (starych "
               "podpisów nie da się powtórzyć) i klucze zatwierdzających (stare podpisy dalej "
               "się weryfikują). Piaskownica nie ma tokenu Mety: zapisy kampanii idą do "
               "atrapy adaptera, wstrzymane."}),
)

ENTRIES += tuple(Entry(*e) for e in ACCOUNT_ENTRIES)  # studio/ui/help_accounts.py


def entries(lang: str) -> list[dict[str, str]]:
    lang = lang if lang in LANGS else "en"
    return [{"anchor": e.anchor, "person": e.person, "scene": e.scene, "q": e.q[lang],
             "a": e.a[lang]} for e in ENTRIES]
