"""The public tour (``/try``, ``studio.routes_try``), English and Polish.

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key. The recorded run itself (brief, copy, reviews) stays in the language it
was recorded in.
"""

from __future__ import annotations

EN_TOUR: dict[str, str] = {
    "try.link_html": '<a href="/try">Try it without an account</a>: one recorded run, '
                     'replayed in your browser, nothing sent anywhere.',
    "meta.try": "Try Muse Studio Lite without an account: one recorded run replayed in your "
                "browser, from the brief to the signature, with receipts you can verify.",
    "try.title": "Try it: one recorded run",
    "try.kicker": "Public tour · no account",
    "try.lead": "One real run of the studio, recorded on {date} and replayed here step by "
                "step: the brief, the confirmed facts, the plan, three copy variants, the "
                "images, two independent reviewers, the approval the owner would sign and "
                "the receipt chain.",
    "try.notice": "Recorded run, replayed — nothing is sent anywhere.",
    "try.notice_more": "No model is called, nothing is sent to Meta or Google, nothing is "
                       "saved and nothing is signed. This page only reads one file that "
                       "ships with the code.",
    "try.cta_judges": "Judges: sign in to run it yourself",
    "try.cta_code": "Source code on GitHub",
    "try.recorded_lang": "The run was recorded in Polish; its brief, copy and reviews are "
                         "shown as the models wrote them.",
    "try.controls": "Tour controls",
    "try.play": "Play",
    "try.pause": "Pause",
    "try.show_all": "Show all",
    "try.restart": "Start over",
    "try.steps": "Steps",
    "try.step_of": "Step {n} of {total}",
    "try.s.brief": "Brief",
    "try.s.facts": "Brief v1",
    "try.s.plan": "Plan",
    "try.s.copy": "Copy",
    "try.s.images": "Images",
    "try.s.review": "Reviews",
    "try.s.approval": "Approval",
    "try.s.receipts": "Receipts",
    "try.brief.h": "1. The owner's words",
    "try.brief.p": "What the owner typed into the studio, unedited.",
    "try.facts.h": "2. Brief v1: what the ad may claim",
    "try.facts.p": "The producer turned the words into a structured brief. Only confirmed "
                   "facts may appear in the ad; anything unconfirmed comes back as a [FACT] "
                   "question the owner answers or strikes.",
    "try.facts.confirmed": "Confirmed facts",
    "try.facts.questions": "[FACT] questions",
    "try.facts.no_questions": "None: every claim in this brief was already stated by the "
                              "owner.",
    "try.facts.proposition": "Proposition",
    "try.facts.banned": "Must not claim",
    "try.facts.tone": "Tone",
    "try.facts.budget": "Budget",
    "try.facts.budget_v": "{amount} {currency} a day, {start} to {end}",
    "try.facts.hash": "Hash of the confirmed brief",
    "try.plan.h": "3. The plan",
    "try.plan.p": "Audience, three angles and the formats to make.",
    "try.plan.audience": "Audience",
    "try.plan.angles": "Angles",
    "try.plan.chosen": "chosen",
    "try.plan.placements": "Formats",
    "try.plan.tasks": "Tasks",
    "try.copy.h": "4. Three copy variants",
    "try.copy.p": "The copywriter wrote three variants for the chosen angle and named the "
                  "policy risks it saw.",
    "try.copy.variant": "Variant {n}",
    "try.copy.reviewed": "reviewed and in the approval",
    "try.copy.risks": "Risks the copywriter flagged",
    "try.images.h": "5. The images",
    "try.images.p": "One image per format, made by the image model around the product "
                    "cut-out. Shown here as the recorded 512-px copies.",
    "try.images.alt": "Generated ad image for {placement}",
    "try.images.size": "{w}×{h} px as delivered",
    "try.review.h": "6. Two reviewers, independently",
    "try.review.p": "Each format is checked against the ad policy rules by two different "
                    "models. Where they disagree, the owner sees it before signing.",
    "try.review.verdict": "Overall verdict",
    "try.review.primary": "Reviewer 1 · Muse Spark 1.3",
    "try.review.second": "Reviewer 2 · Muse Spark Open (Baseten)",
    "try.review.no_findings": "No findings.",
    "try.review.disagreements": "Where they disagree",
    "try.approval.h": "7. What the owner would sign",
    "try.approval.p": "Nothing reaches Meta without the owner's Ed25519 signature over these "
                      "exact fields. In this tour the signature step stops here: nothing is "
                      "signed, nothing is created.",
    "try.approval.action": "Action",
    "try.approval.campaign": "Campaign",
    "try.approval.campaign_v": "{objective}, created {status}",
    "try.approval.budget": "Daily budget",
    "try.approval.dates": "Dates",
    "try.approval.ad": "Ad",
    "try.approval.content": "Hash of the full payload",
    "try.approval.brief": "Hash of the confirmed brief",
    "try.approval.spec": "Hash of the delivery spec",
    "try.approval.head": "Receipt chain head",
    "try.approval.message": "The exact bytes the owner would sign",
    "try.approval.unsigned": "Not signed in this tour",
    "try.receipts.h": "8. The receipts",
    "try.receipts.p": "Every step left a receipt: who did it, with which model, what went in "
                      "and out (as hashes) and what it cost. Each receipt names the hash of "
                      "the previous one and is signed by the ledger key.",
    "try.receipts.key": "Ledger key (throwaway, made when this replay was built)",
    "try.receipts.count": "{n} receipts · recorded model cost ${cost}",
    "try.receipts.cols": "#",
    "try.verify.h": "Verify in your browser",
    "try.verify.p": "Your browser re-hashes every receipt with SHA-256, checks each link to "
                    "the previous one and each Ed25519 signature, then the hashes the "
                    "approval names. Nothing leaves this page.",
    "try.verify.run": "Verify the chain",
    "try.verify.tamper": "Change one receipt and verify again",
    "try.verify.nojs": "Verification needs JavaScript; the same data is at /try/data.json.",
    "try.verify.cli": "Or offline: studio verify on an export (see the README).",
    "try.end.h": "That is the whole loop.",
    "try.end.p": "In the studio, the owner signs with a key that never leaves their browser, "
                 "the campaign is created paused, and every later step adds to the same "
                 "chain.",
    "js.tour_step_of": "Step {n} of {total}",
    "js.tour_play": "Play",
    "js.tour_pause": "Pause",
    "js.tour_checking": "Checking…",
    "js.tour_ok": "OK: {n} receipts, every hash, link and signature checks out.",
    "js.tour_fail": "FAILED: {n} problem(s) found.",
    "js.tour_no_crypto": "This browser has no Web Crypto here (it needs https).",
    "js.tour_no_ed25519": "this browser cannot check Ed25519 signatures; hashes and links "
                          "were checked",
    "js.tour_c_hash": "receipt {i}: hash matches its contents",
    "js.tour_c_hash_bad": "receipt {i}: hash does not match its contents",
    "js.tour_c_link": "receipt {i}: points at the previous receipt",
    "js.tour_c_link_bad": "receipt {i}: does not point at the previous receipt",
    "js.tour_c_sig": "receipt {i}: ledger signature valid",
    "js.tour_c_sig_bad": "receipt {i}: ledger signature invalid",
    "js.tour_c_fields_bad": "receipt {i}: the shown fields differ from the hashed bytes",
    "js.tour_c_manifest": "manifest: signed, {count} receipts, head matches the last receipt",
    "js.tour_c_manifest_bad": "manifest: signature, count or head does not match",
    "js.tour_c_brief": "the confirmed brief hashes to the value the approval signs",
    "js.tour_c_brief_bad": "the confirmed brief does not hash to the signed value",
    "js.tour_c_content": "the payload hashes to the value the approval signs",
    "js.tour_c_content_bad": "the payload does not hash to the signed value",
    "js.tour_c_head": "the approval pins the chain at receipt {i}, as it stood when the "
                      "approval was issued",
    "js.tour_c_head_bad": "the approval names a chain head that is not in this chain",
    "js.tour_tampered": "Receipt {i} was changed in memory (its cost). Verify again to see "
                        "it fail.",
    "js.tour_load_failed": "Could not load the recorded run.",
}

PL_TOUR: dict[str, str] = {
    "try.link_html": '<a href="/pl/try">Wypróbuj bez konta</a>: jeden nagrany przebieg, '
                     'odtwarzany w przeglądarce, nic nie jest nigdzie wysyłane.',
    "meta.try": "Wypróbuj Muse Studio Lite bez konta: jeden nagrany przebieg odtworzony w "
                "przeglądarce, od briefu do podpisu, z pokwitowaniami do sprawdzenia.",
    "try.title": "Wypróbuj: jeden nagrany przebieg",
    "try.kicker": "Publiczna wycieczka · bez konta",
    "try.lead": "Jeden prawdziwy przebieg studia, nagrany {date} i odtwarzany tu krok po "
                "kroku: brief, potwierdzone fakty, plan, trzy warianty tekstu, grafiki, dwie "
                "niezależne recenzje, zatwierdzenie, które podpisałby właściciel, i "
                "łańcuch pokwitowań.",
    "try.notice": "Nagrany przebieg, odtwarzany — nic nie jest nigdzie wysyłane.",
    "try.notice_more": "Żaden model nie jest wywoływany, nic nie trafia do Meta ani Google, "
                       "nic nie jest zapisywane ani podpisywane. Ta strona czyta tylko jeden "
                       "plik dołączony do kodu.",
    "try.cta_judges": "Jury: zaloguj się i uruchom samodzielnie",
    "try.cta_code": "Kod źródłowy na GitHubie",
    "try.recorded_lang": "Przebieg nagrano po polsku; brief, teksty i recenzje są pokazane "
                         "tak, jak napisały je modele.",
    "try.controls": "Sterowanie wycieczką",
    "try.play": "Odtwórz",
    "try.pause": "Pauza",
    "try.show_all": "Pokaż wszystko",
    "try.restart": "Od początku",
    "try.steps": "Kroki",
    "try.step_of": "Krok {n} z {total}",
    "try.s.brief": "Brief",
    "try.s.facts": "Brief v1",
    "try.s.plan": "Plan",
    "try.s.copy": "Teksty",
    "try.s.images": "Grafiki",
    "try.s.review": "Recenzje",
    "try.s.approval": "Zatwierdzenie",
    "try.s.receipts": "Pokwitowania",
    "try.brief.h": "1. Słowa właściciela",
    "try.brief.p": "To, co właściciel wpisał w studiu, bez zmian.",
    "try.facts.h": "2. Brief v1: co reklama może twierdzić",
    "try.facts.p": "Producent zamienił te słowa na uporządkowany brief. W reklamie mogą się "
                   "znaleźć tylko potwierdzone fakty; wszystko niepotwierdzone wraca jako "
                   "pytanie [FACT], na które właściciel odpowiada albo je skreśla.",
    "try.facts.confirmed": "Potwierdzone fakty",
    "try.facts.questions": "Pytania [FACT]",
    "try.facts.no_questions": "Brak: każde twierdzenie w tym briefie właściciel podał sam.",
    "try.facts.proposition": "Propozycja",
    "try.facts.banned": "Nie wolno twierdzić",
    "try.facts.tone": "Ton",
    "try.facts.budget": "Budżet",
    "try.facts.budget_v": "{amount} {currency} dziennie, od {start} do {end}",
    "try.facts.hash": "Skrót potwierdzonego briefu",
    "try.plan.h": "3. Plan",
    "try.plan.p": "Odbiorcy, trzy pomysły i formaty do przygotowania.",
    "try.plan.audience": "Odbiorcy",
    "try.plan.angles": "Pomysły",
    "try.plan.chosen": "wybrany",
    "try.plan.placements": "Formaty",
    "try.plan.tasks": "Zadania",
    "try.copy.h": "4. Trzy warianty tekstu",
    "try.copy.p": "Copywriter napisał trzy warianty dla wybranego pomysłu i nazwał "
                  "zauważone ryzyka zasad.",
    "try.copy.variant": "Wariant {n}",
    "try.copy.reviewed": "zrecenzowany i w zatwierdzeniu",
    "try.copy.risks": "Ryzyka wskazane przez copywritera",
    "try.images.h": "5. Grafiki",
    "try.images.p": "Jedna grafika na format, zrobiona przez model obrazu wokół wyciętego "
                    "produktu. Pokazane jako nagrane kopie 512 px.",
    "try.images.alt": "Wygenerowana grafika reklamy dla {placement}",
    "try.images.size": "{w}×{h} px w oryginale",
    "try.review.h": "6. Dwie niezależne recenzje",
    "try.review.p": "Każdy format sprawdzają według zasad reklam dwa różne modele. Gdy się "
                    "nie zgadzają, właściciel widzi to przed podpisem.",
    "try.review.verdict": "Werdykt łączny",
    "try.review.primary": "Recenzent 1 · Muse Spark 1.3",
    "try.review.second": "Recenzent 2 · Muse Spark Open (Baseten)",
    "try.review.no_findings": "Bez uwag.",
    "try.review.disagreements": "Gdzie się różnią",
    "try.approval.h": "7. Co podpisałby właściciel",
    "try.approval.p": "Nic nie trafia do Meta bez podpisu Ed25519 właściciela pod dokładnie "
                      "tymi polami. W tej wycieczce krok podpisu kończy się tutaj: nic nie "
                      "jest podpisywane ani tworzone.",
    "try.approval.action": "Czynność",
    "try.approval.campaign": "Kampania",
    "try.approval.campaign_v": "{objective}, tworzona jako {status}",
    "try.approval.budget": "Budżet dzienny",
    "try.approval.dates": "Daty",
    "try.approval.ad": "Reklama",
    "try.approval.content": "Skrót całej treści",
    "try.approval.brief": "Skrót potwierdzonego briefu",
    "try.approval.spec": "Skrót specyfikacji emisji",
    "try.approval.head": "Głowa łańcucha pokwitowań",
    "try.approval.message": "Dokładne bajty, które podpisałby właściciel",
    "try.approval.unsigned": "W tej wycieczce niepodpisane",
    "try.receipts.h": "8. Pokwitowania",
    "try.receipts.p": "Każdy krok zostawił pokwitowanie: kto, jakim modelem, co weszło i "
                      "wyszło (jako skróty) i ile kosztowało. Każde pokwitowanie wskazuje "
                      "skrót poprzedniego i jest podpisane kluczem rejestru.",
    "try.receipts.key": "Klucz rejestru (jednorazowy, utworzony przy budowie tego odtworzenia)",
    "try.receipts.count": "{n} pokwitowań · nagrany koszt modeli ${cost}",
    "try.receipts.cols": "#",
    "try.verify.h": "Sprawdź w przeglądarce",
    "try.verify.p": "Przeglądarka liczy od nowa SHA-256 każdego pokwitowania, sprawdza każde "
                    "powiązanie z poprzednim i każdy podpis Ed25519, a potem skróty, które "
                    "wskazuje zatwierdzenie. Nic nie opuszcza tej strony.",
    "try.verify.run": "Sprawdź łańcuch",
    "try.verify.tamper": "Zmień jedno pokwitowanie i sprawdź ponownie",
    "try.verify.nojs": "Sprawdzanie wymaga JavaScriptu; te same dane są pod /try/data.json.",
    "try.verify.cli": "Albo offline: studio verify na eksporcie (zob. README).",
    "try.end.h": "To cała pętla.",
    "try.end.p": "W studiu właściciel podpisuje kluczem, który nie opuszcza jego "
                 "przeglądarki, kampania powstaje wstrzymana, a każdy kolejny krok dopisuje "
                 "się do tego samego łańcucha.",
    "js.tour_step_of": "Krok {n} z {total}",
    "js.tour_play": "Odtwórz",
    "js.tour_pause": "Pauza",
    "js.tour_checking": "Sprawdzam…",
    "js.tour_ok": "OK: {n} pokwitowań, każdy skrót, powiązanie i podpis się zgadzają.",
    "js.tour_fail": "BŁĄD: znaleziono problemy ({n}).",
    "js.tour_no_crypto": "Ta przeglądarka nie ma tu Web Crypto (potrzebne https).",
    "js.tour_no_ed25519": "ta przeglądarka nie sprawdza podpisów Ed25519; skróty i "
                          "powiązania sprawdzono",
    "js.tour_c_hash": "pokwitowanie {i}: skrót zgadza się z treścią",
    "js.tour_c_hash_bad": "pokwitowanie {i}: skrót nie zgadza się z treścią",
    "js.tour_c_link": "pokwitowanie {i}: wskazuje poprzednie pokwitowanie",
    "js.tour_c_link_bad": "pokwitowanie {i}: nie wskazuje poprzedniego pokwitowania",
    "js.tour_c_sig": "pokwitowanie {i}: podpis rejestru poprawny",
    "js.tour_c_sig_bad": "pokwitowanie {i}: podpis rejestru niepoprawny",
    "js.tour_c_fields_bad": "pokwitowanie {i}: pokazane pola różnią się od skrótowanych bajtów",
    "js.tour_c_manifest": "manifest: podpisany, {count} pokwitowań, głowa to ostatnie "
                          "pokwitowanie",
    "js.tour_c_manifest_bad": "manifest: podpis, liczba albo głowa się nie zgadza",
    "js.tour_c_brief": "potwierdzony brief daje skrót, który podpisuje zatwierdzenie",
    "js.tour_c_brief_bad": "potwierdzony brief nie daje podpisywanego skrótu",
    "js.tour_c_content": "treść daje skrót, który podpisuje zatwierdzenie",
    "js.tour_c_content_bad": "treść nie daje podpisywanego skrótu",
    "js.tour_c_head": "zatwierdzenie przypina łańcuch na pokwitowaniu {i}, takim, jaki był "
                      "w chwili wystawienia",
    "js.tour_c_head_bad": "zatwierdzenie wskazuje głowę łańcucha spoza tego łańcucha",
    "js.tour_tampered": "Pokwitowanie {i} zmieniono w pamięci (koszt). Sprawdź ponownie, "
                        "żeby zobaczyć błąd.",
    "js.tour_load_failed": "Nie udało się wczytać nagranego przebiegu.",
}
